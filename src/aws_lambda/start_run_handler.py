"""API Gateway Lambda proxy integration for POST /runs - the AWS
equivalent of graph.py's run_upgrade_test() entry point.

Unlike the other Lambdas in this package, this one isn't a Step Functions
Task - it's what *starts* a Step Functions execution. It does the two
things run_upgrade_test() does before entering the graph: build the
initial UpgradeTestState dict, and call state_store.init_run() so the
run has a queryable DynamoDB record (metadata + one pending pipeline
record) before any state-machine Task Lambda ever runs - without this,
the CLI's `status --target aws` would have nothing to read for a run
that hasn't reached CreateBranches yet, and `cleanup --target aws`
wouldn't be able to recover the run's source_control config.

The manifest is supplied in the request body rather than read from a
fixed S3 location - the CLI already loads and validates it locally
(same as the --target local path), so this just forwards that already-
validated content instead of requiring a separate manifest upload step.
"""

from __future__ import annotations

import json
import os
import uuid

import boto3
from pydantic import ValidationError

from src.aws_lambda.common import get_state_store
from src.config.manifest import TestManifest

sfn = boto3.client("stepfunctions")

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]


def handler(event: dict, context) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
        manifest = TestManifest.model_validate(body["manifest"])
    except (json.JSONDecodeError, KeyError, ValidationError) as e:
        return _response(400, {"error": f"invalid manifest: {e}"})

    run_id = f"run-{uuid.uuid4().hex[:12]}"

    state_store = get_state_store()
    state_store.init_run(run_id, manifest)

    initial_state = {
        "run_id": run_id,
        "manifest": manifest.model_dump(mode="json"),
        "phase": "BRANCH",
        "status": "RUNNING",
        "baseline_branch": "",
        "target_branch": "",
        "build_status": "PENDING",
        "baseline_execution": {},
        "target_execution": {},
        "analysis_result": {},
        "retry_count": 0,
        "validation_results": {},
        "report_path": "",
        "pr_url": "",
        "error": "",
    }

    execution = sfn.start_execution(
        stateMachineArn=STATE_MACHINE_ARN,
        name=run_id,
        input=json.dumps(initial_state),
    )

    return _response(202, {"run_id": run_id, "execution_arn": execution["executionArn"]})


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
