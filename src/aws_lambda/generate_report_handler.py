"""Step Functions Task Lambda wrapping report_generator.py's HTML/JSON
generation unchanged - only the write target changes: S3 instead of
local disk, matching report_node.py's local behavior of writing
report.html/report.json under a per-run prefix.
"""

from __future__ import annotations

import os

import boto3

from src.aws_lambda.common import get_state_store, merge_update
from src.config.manifest import TestManifest
from src.reporting.report_generator import ReportGenerator

s3 = boto3.client("s3")

REPORTS_BUCKET = os.environ["REPORTS_BUCKET"]


def handler(event: dict, context) -> dict:
    manifest = TestManifest.model_validate(event["manifest"])
    run_id = event["run_id"]
    state_store = get_state_store()

    html_report, json_report = ReportGenerator().generate(run_id, manifest, event)

    html_key = f"{run_id}/report.html"
    json_key = f"{run_id}/report.json"
    s3.put_object(Bucket=REPORTS_BUCKET, Key=html_key, Body=html_report.encode("utf-8"), ContentType="text/html")
    s3.put_object(Bucket=REPORTS_BUCKET, Key=json_key, Body=json_report.encode("utf-8"), ContentType="application/json")

    report_path = f"s3://{REPORTS_BUCKET}/{html_key}"

    state_store.record_event(run_id, phase="REPORT", event="report_generated", report_path=report_path)
    state_store.update_run_status(run_id, phase="PR", report_path=report_path)

    return merge_update(event, {"report_path": report_path, "phase": "PR"})
