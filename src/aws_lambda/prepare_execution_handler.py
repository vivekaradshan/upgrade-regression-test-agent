"""Step Functions Task Lambda that prepares everything the native EMR
Serverless .sync task needs for one job (baseline, target, or validate).

Locally, execute_node.py does two things this handler replaces:
  1. `git clone`/`checkout` the branch locally - replaced with fetching
     just the one file EMR actually needs (spark_job.py) via the GitHub
     Contents API and uploading it to S3. No git required at all.
  2. Compute the "effective" spark_config by merging the branch's
     checked-out config.yaml over the manifest's static config (so a
     committed auto-fix from analyze_logs actually takes effect on
     retry) - same merge semantics as execute_node.py's
     _effective_spark_config, just reading the branch via the GitHub API
     instead of a local file.

Mock input data is *not* generated per run here: it's deterministic
(fixed seed, doesn't depend on run_id or branch) and uploaded once to a
fixed S3 location via infra/scripts/upload_mock_data.sh - every job run
just references that same path directly.

Returns EMR job parameters under event["emr_job"] for the Step Functions
state machine's native EMR Serverless task to reference directly.
"""

from __future__ import annotations

import json
import os

import boto3
import yaml

from src.aws_lambda.common import get_github_client, get_state_store, merge_update
from src.config.manifest import TestManifest

s3 = boto3.client("s3")

BASELINE_APPLICATION_ID = os.environ["BASELINE_APPLICATION_ID"]
TARGET_APPLICATION_ID = os.environ["TARGET_APPLICATION_ID"]
EMR_EXECUTION_ROLE_ARN = os.environ["EMR_EXECUTION_ROLE_ARN"]
ARTIFACTS_BUCKET = os.environ["ARTIFACTS_BUCKET"]
PYYAML_PYFILES_S3_URI = os.environ["PYYAML_PYFILES_S3_URI"]
VALIDATOR_PYFILES_S3_URI = os.environ["VALIDATOR_PYFILES_S3_URI"]

# Uploaded once via infra/scripts/upload_mock_data.sh, not per run.
SHARED_MOCK_DATA_S3_URI = f"s3://{ARTIFACTS_BUCKET}/shared/data/transactions.csv"


def handler(event: dict, context) -> dict:
    manifest = TestManifest.model_validate(event["manifest"])
    run_id = event["run_id"]
    variant = event["variant"]  # "baseline" | "target" | "validate"

    github_client = get_github_client(event["manifest"])
    try:
        if variant == "validate":
            emr_job = _prepare_validate_job(event, manifest, run_id)
        else:
            # execute_node.py (the local-only node) only writes
            # baseline_status/target_status once a job *finishes* - nothing
            # on the AWS path marked it "RUNNING" while the native EMR
            # .sync task blocks, so the dashboard showed PENDING for the
            # job's entire actual runtime. This runs right before that
            # .sync task starts, so it's the right place to fix that.
            state_store = get_state_store()
            state_store.update_pipeline_status(
                run_id, manifest.pipeline.id, **{f"{variant}_status": "RUNNING"}
            )
            emr_job = _prepare_pipeline_job(event, manifest, run_id, variant, github_client)
    finally:
        github_client.close()

    return merge_update(event, {"emr_job": emr_job})


def _prepare_pipeline_job(
    event: dict, manifest: TestManifest, run_id: str, variant: str, github_client
) -> dict:
    branch = event["baseline_branch"] if variant == "baseline" else event["target_branch"]
    spark_version = (
        manifest.execution.baseline_spark_version
        if variant == "baseline"
        else manifest.execution.target_spark_version
    )
    application_id = BASELINE_APPLICATION_ID if variant == "baseline" else TARGET_APPLICATION_ID

    entry_script_content, _ = github_client.get_file_content(manifest.pipeline.entry_script, branch)
    entry_point_s3_uri = f"s3://{ARTIFACTS_BUCKET}/{run_id}/{variant}/spark_job.py"
    s3.put_object(
        Bucket=ARTIFACTS_BUCKET,
        Key=f"{run_id}/{variant}/spark_job.py",
        Body=entry_script_content.encode("utf-8"),
    )

    effective_spark_config = _effective_spark_config(github_client, manifest, branch, spark_version)

    output_s3_uri = f"s3://{ARTIFACTS_BUCKET}/{run_id}/{variant}/output"

    return {
        "applicationId": application_id,
        "executionRoleArn": EMR_EXECUTION_ROLE_ARN,
        "entryPoint": entry_point_s3_uri,
        "entryPointArguments": ["--input", SHARED_MOCK_DATA_S3_URI, "--output", output_s3_uri],
        "sparkSubmitParameters": _build_spark_submit_parameters(effective_spark_config),
        "outputPath": output_s3_uri,
        # Referenced by Phase 14.4's Step Functions task as
        # configurationOverrides.monitoringConfiguration.s3MonitoringConfiguration.logUri,
        # and by analyze_logs_handler to locate the driver's stderr log
        # after the job completes.
        "logUri": f"s3://{ARTIFACTS_BUCKET}/logs",
    }


def _prepare_validate_job(event: dict, manifest: TestManifest, run_id: str) -> dict:
    driver_s3_uri = f"s3://{ARTIFACTS_BUCKET}/drivers/validate_job.py"
    baseline_output = f"s3://{ARTIFACTS_BUCKET}/{run_id}/baseline/output/transactions"
    target_output = f"s3://{ARTIFACTS_BUCKET}/{run_id}/target/output/transactions"
    checks_json = json.dumps([check.model_dump() for check in manifest.validation.checks])
    results_s3_uri = f"s3://{ARTIFACTS_BUCKET}/{run_id}/validate/results.json"

    return {
        "applicationId": BASELINE_APPLICATION_ID,
        "executionRoleArn": EMR_EXECUTION_ROLE_ARN,
        "entryPoint": driver_s3_uri,
        "entryPointArguments": [
            "--baseline-path", baseline_output,
            "--target-path", target_output,
            "--checks-json", checks_json,
            "--output-path", results_s3_uri,
        ],
        # validate_job.py imports src.tools.data_validator, which needs to
        # be on the job's Python path - packaged the same way pyyaml is
        # for the pipeline jobs, via infra/scripts/build_validator_pyfiles.sh.
        "sparkSubmitParameters": (
            f"--py-files {VALIDATOR_PYFILES_S3_URI} "
            "--conf spark.executor.cores=1 --conf spark.executor.memory=2g"
        ),
        "resultsPath": results_s3_uri,
        "logUri": f"s3://{ARTIFACTS_BUCKET}/logs",
    }


# EMR Serverless manages its own execution environment and rejects this
# override outright (confirmed by a real failed job run:
# EMRServerless.ValidationException: "Option 'spark.master' is not
# supported"). The manifest sets it for local[*] execution locally; EMR
# has no equivalent concept to set.
UNSUPPORTED_ON_EMR_SERVERLESS = {"spark.master"}


def _effective_spark_config(github_client, manifest: TestManifest, branch: str, spark_version: str) -> dict:
    manifest_spark_config = dict(manifest.pipeline.spark_config)

    config_file = None
    for modification in manifest.source_control.target.modifications:
        if modification.file.endswith((".yaml", ".yml")):
            config_file = modification.file
            break

    branch_spark_config = {}
    if config_file:
        content, _ = github_client.get_file_content(config_file, branch)
        parsed = yaml.safe_load(content) or {}
        branch_spark_config = parsed.get("spark_config", {})

    effective = {**manifest_spark_config, **branch_spark_config}

    for key in UNSUPPORTED_ON_EMR_SERVERLESS:
        effective.pop(key, None)

    # Kept as an explicit safety net even though real Spark 4 already
    # defaults ansi.enabled=true - matches the same belt-and-suspenders
    # injection LocalSparkRunner does locally (mock_emr.py).
    if spark_version.startswith("4.") and "spark.sql.ansi.enabled" not in effective:
        effective["spark.sql.ansi.enabled"] = "true"

    return effective


def _build_spark_submit_parameters(spark_config: dict) -> str:
    conf_flags = " ".join(f"--conf {key}={value}" for key, value in spark_config.items())
    return f"--py-files {PYYAML_PYFILES_S3_URI} {conf_flags}".strip()
