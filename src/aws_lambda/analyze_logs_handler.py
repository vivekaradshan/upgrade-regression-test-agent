"""Step Functions Task Lambda wrapping analyze_node.py's analyze_logs
logic. Only real change from the local version: the target job's log
lives in S3 (EMR Serverless writes driver stdout/stderr there as gzip),
not a local file path.

EMR Serverless's S3 log path depends on applicationId and jobRunId,
neither of which exist until after Step Functions' native EMR Serverless
.sync task has actually submitted and run the job - prepare_execution_handler
runs *before* submission, so it can't precompute this path. Instead, the
Step Functions state machine (Phase 14.4) is expected to merge the EMR
task's own result (which includes jobRunId) into target_execution, and
this handler constructs the log path from that plus the fixed logUri
prefix every EMR job is submitted with (see prepare_execution_handler's
emr_job.logUri).

LogReader.read_log() itself needs no changes - it just reads whatever
local path it's given, so this handler's only job is downloading and
decompressing the S3 log into /tmp first.
"""

from __future__ import annotations

import gzip
import json
import os

import boto3

from src.aws_lambda.common import (
    get_github_client,
    get_llm_analyzer,
    get_state_store,
    merge_update,
)
from src.orchestrator.nodes.analyze_node import make_analyze_logs_node

s3 = boto3.client("s3")

ARTIFACTS_BUCKET = os.environ["ARTIFACTS_BUCKET"]
LOGS_PREFIX = "logs"


def _recover_job_run_id(target_execution: dict) -> dict:
    """The emr-serverless:startJobRun.sync Task's Catch block only exposes
    the failure as an opaque Error/Cause string pair - it can't distinguish
    "the job ran and Spark itself failed" (Cause is the JSON job-run
    description, State=FAILED, JobRunId present) from "the task failed
    before any job run started" (Cause is an arbitrary, non-JSON message,
    e.g. an EMRServerless.ValidationException). The state machine passes
    the raw Cause through as target_execution["cause"] either way; recover
    jobRunId/status from it here where malformed JSON can be handled
    without aborting the whole execution (ASL Pass states can't Catch).
    """
    if "jobRunId" in target_execution or "cause" not in target_execution:
        return target_execution

    try:
        parsed = json.loads(target_execution["cause"])
        job_run_id = parsed["JobRunId"]
        status = parsed["State"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return target_execution

    return {**target_execution, "status": status, "jobRunId": job_run_id}


def _translate_status(emr_status: str) -> str:
    """EMR Serverless reports "SUCCESS"/"FAILED" - the dashboard's status
    badges (dashboard/app.py's STATUS_ICONS) expect the local mock's
    "SUCCEEDED"/"FAILED" vocabulary, same translation
    prepare_execution_handler.py/this module already do for
    target_execution before handing it to the shared analyze_node."""
    return "SUCCEEDED" if emr_status == "SUCCESS" else emr_status


def handler(event: dict, context) -> dict:
    manifest = event["manifest"]
    run_id = event["run_id"]
    target_execution = _recover_job_run_id(event["target_execution"])

    # Neither this handler nor any other AWS Lambda wrote the *final*
    # baseline_status/target_status once a job actually finished -
    # prepare_execution_handler.py marks it "RUNNING" when the job starts,
    # but without this, the dashboard would show RUNNING forever after
    # that, even once the job (and possibly the whole run) completed. This
    # runs unconditionally, before either branch below, so it applies on
    # both the escalation path and the ordinary analysis path, and on
    # every retry (event["baseline_execution"] is only present on the
    # first pass - already-SUCCEEDED status doesn't need rewriting on
    # retry-only passes where only the target job reran).
    state_store = get_state_store()
    pipeline_status_update = {}
    if event.get("baseline_execution", {}).get("status"):
        pipeline_status_update["baseline_status"] = _translate_status(event["baseline_execution"]["status"])
    if target_execution.get("status"):
        pipeline_status_update["target_status"] = _translate_status(target_execution["status"])
    if pipeline_status_update:
        state_store.update_pipeline_status(run_id, manifest["pipeline"]["id"], **pipeline_status_update)

    # target_execution can still be {"status": "FAILED"} with no jobRunId at
    # all after recovery - the EMR *task itself* failing before a job run
    # ever started (e.g. an invalid spark-submit parameter rejected by the
    # EMR Serverless API, as opposed to a job that started and whose Spark
    # code then failed - see _recover_job_run_id). There's no Spark log to
    # fetch or analyze in that case - escalate immediately with a diagnosis
    # describing the infrastructure failure, in the same shape
    # analyze_node.py's own escalation path returns, rather than forcing
    # this through log analysis that has nothing to read.
    if target_execution.get("status") != "SUCCESS" and "jobRunId" not in target_execution:
        diagnosis = "Target EMR job never started (Step Functions task failure, not a Spark job failure)"
        analysis_result = {
            "source": "infrastructure",
            "diagnosis": diagnosis,
            "action": "escalate",
            "auto_fix": False,
            "fix_config": None,
        }
        state_store.update_pipeline_status(
            run_id, manifest["pipeline"]["id"], status="FAILED", diagnosis=diagnosis, error_message=diagnosis
        )
        state_store.record_event(run_id, phase="ANALYZE", event="escalated", reason="emr_task_failure")
        state_store.update_run_status(run_id, phase="REPORT", status="FAILED")
        return merge_update(
            event, {"analysis_result": analysis_result, "phase": "REPORT", "status": "FAILED", "error": diagnosis}
        )

    github_client = get_github_client(manifest)
    llm_analyzer = get_llm_analyzer(manifest)

    # analyze_node.py (shared, unmodified) checks target_execution["status"]
    # against the local mock vocabulary ("SUCCEEDED"/"FAILED") - EMR
    # Serverless reports "SUCCESS"/"FAILED" instead, so normalize before
    # handing state to the shared node.
    local_state = dict(event)
    if target_execution.get("status") == "SUCCESS":
        local_state["target_execution"] = {**target_execution, "status": "SUCCEEDED"}
    else:
        local_log_path = _download_and_decompress_stderr(
            application_id=event["emr_job"]["applicationId"],
            job_run_id=target_execution["jobRunId"],
        )
        local_state["target_execution"] = {**target_execution, "log_path": local_log_path}

    analyze_logs = make_analyze_logs_node(github_client, state_store, llm_analyzer)
    try:
        update = analyze_logs(local_state)
    finally:
        github_client.close()

    return merge_update(event, update)


def _download_and_decompress_stderr(application_id: str, job_run_id: str) -> str:
    key = f"{LOGS_PREFIX}/applications/{application_id}/jobs/{job_run_id}/SPARK_DRIVER/stderr.gz"
    local_gz_path = f"/tmp/{job_run_id}-stderr.gz"
    local_log_path = f"/tmp/{job_run_id}-stderr.log"

    s3.download_file(ARTIFACTS_BUCKET, key, local_gz_path)
    with gzip.open(local_gz_path, "rt") as gz_file, open(local_log_path, "w") as out_file:
        out_file.write(gz_file.read())

    return local_log_path
