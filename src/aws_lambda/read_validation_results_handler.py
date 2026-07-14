"""Step Functions Task Lambda that reads the validate EMR job's
results.json from S3 and merges it into state as validation_results.

The native EMR Serverless .sync task only reports job SUCCESS/FAILURE -
it doesn't surface the file validate_job.py actually wrote. Step
Functions has a native S3 GetObject SDK integration, but it can't
cleanly parse a JSON response body within ASL itself, so this small
Lambda does that instead.
"""

from __future__ import annotations

import json

import boto3

from src.aws_lambda.common import get_state_store, merge_update

s3 = boto3.client("s3")


def handler(event: dict, context) -> dict:
    run_id = event["run_id"]
    manifest = event["manifest"]
    results_s3_uri = event["emr_job"]["resultsPath"]

    bucket, key = results_s3_uri.replace("s3://", "", 1).split("/", 1)
    response = s3.get_object(Bucket=bucket, Key=key)
    validation_results = json.loads(response["Body"].read())

    state_store = get_state_store()
    state_store.update_pipeline_status(
        run_id,
        manifest["pipeline"]["id"],
        validation_results=validation_results,
        status=validation_results["overall_status"],
    )
    state_store.record_event(
        run_id,
        phase="VALIDATE",
        event="validation_completed",
        overall_status=validation_results["overall_status"],
    )
    state_store.update_run_status(run_id, phase="REPORT", overall_status=validation_results["overall_status"])

    return merge_update(
        event,
        {
            "validation_results": validation_results,
            "phase": "REPORT",
            "status": validation_results["overall_status"],
        },
    )
