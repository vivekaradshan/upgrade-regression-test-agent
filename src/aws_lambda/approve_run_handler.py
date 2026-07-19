"""API Gateway Lambda proxy integration for POST /runs/{run_id}/approve -
the human decision point for an LLM-proposed fix (see analyze_node.py's
AWAIT_APPROVAL branch and await_approval_handler.py, which parked the
Step Functions execution's task token in DynamoDB waiting for this).

Approve: commits the LLM's proposed {key, value} fix to the target
branch (the exact same apply_fix_to_target_branch() call a pattern-
matcher auto-fix uses - see src/tools/config_fix.py), bumps retry_count,
and resumes the paused execution via states:SendTaskSuccess with output
shaped to flow into the existing CheckRetry -> ... retry loop, same as
any other retry.

Reject: resumes via states:SendTaskFailure, which the state machine's
Catch on the AwaitApproval state routes to the same REPORT/FAILED path
an unfixable escalation already takes.

Either way, this is the only place besides start_run_handler.py that
calls into Step Functions from outside the state machine itself.
"""

from __future__ import annotations

import json

import boto3

from src.aws_lambda.common import get_github_client, get_state_store
from src.config.manifest import TestManifest
from src.tools.config_fix import apply_fix_to_target_branch, find_spark_config_file

sfn = boto3.client("stepfunctions")


def handler(event: dict, context) -> dict:
    run_id = (event.get("pathParameters") or {}).get("run_id")
    if not run_id:
        return _response(400, {"error": "run_id is required in the path"})

    try:
        body = json.loads(event.get("body") or "{}")
        approved = bool(body["approved"])
    except (json.JSONDecodeError, KeyError) as e:
        return _response(400, {"error": f"invalid request body: {e}"})

    state_store = get_state_store()
    try:
        metadata = state_store.get_run_metadata(run_id)
    except KeyError:
        return _response(404, {"error": f"no run found for run_id={run_id}"})

    task_token = metadata.get("pending_approval_task_token")
    pending_state_json = metadata.get("pending_approval_state")
    if not task_token or not pending_state_json:
        return _response(409, {"error": "this run has no pending approval"})

    pending_state = json.loads(pending_state_json)

    if approved:
        return _approve(state_store, run_id, task_token, pending_state)
    return _reject(state_store, run_id, task_token, pending_state)


def _approve(state_store, run_id: str, task_token: str, pending_state: dict) -> dict:
    analysis_result = pending_state.get("analysis_result") or {}
    fix_config = analysis_result.get("fix_config")
    if not fix_config:
        return _response(
            400, {"error": "this diagnosis has no structured fix to apply - it cannot be approved, only rejected"}
        )

    manifest = TestManifest.model_validate(pending_state["manifest"])
    github_client = get_github_client(pending_state["manifest"])
    try:
        config_file = find_spark_config_file(
            github_client, pending_state["target_branch"], manifest.source_control.target.modifications
        )
        apply_fix_to_target_branch(
            github_client,
            branch=pending_state["target_branch"],
            config_file_path=config_file,
            key=fix_config["key"],
            value=fix_config["value"],
            commit_message=f"human-approved LLM fix: set {fix_config['key']}={fix_config['value']}",
        )
    finally:
        github_client.close()

    new_retry_count = pending_state["retry_count"] + 1
    resume_state = {**pending_state, "retry_count": new_retry_count, "phase": "RETRY"}

    sfn.send_task_success(taskToken=task_token, output=json.dumps(resume_state))

    state_store.update_pipeline_status(
        run_id,
        manifest.pipeline.id,
        retry_count=new_retry_count,
        corrective_action=f"approved: set {fix_config['key']}={fix_config['value']}",
    )
    state_store.record_event(
        run_id, phase="ANALYZE", event="approval_approved", fix_key=fix_config["key"], fix_value=fix_config["value"]
    )
    # approved_llm_fix flags this run for raise_pr_handler.py, which opens
    # a second PR proposing this fix be added to known_failure_patterns if
    # the retry goes on to actually pass - see Phase 15.5's pattern-
    # library-growth slice.
    state_store.update_run_status(
        run_id,
        phase="EXECUTE",
        status="RUNNING",
        pending_approval_task_token=None,
        pending_approval_state=None,
        approved_llm_fix=True,
    )

    return _response(200, {"run_id": run_id, "status": "approved", "retry_count": new_retry_count})


def _reject(state_store, run_id: str, task_token: str, pending_state: dict) -> dict:
    sfn.send_task_failure(taskToken=task_token, error="ApprovalRejected", cause="Human rejected the proposed LLM fix")

    manifest = TestManifest.model_validate(pending_state["manifest"])
    diagnosis = (pending_state.get("analysis_result") or {}).get("diagnosis", "Human rejected the proposed LLM fix")

    state_store.update_pipeline_status(
        run_id, manifest.pipeline.id, status="FAILED", diagnosis=diagnosis, error_message="Approval rejected by human"
    )
    state_store.record_event(run_id, phase="ANALYZE", event="approval_rejected")
    state_store.update_run_status(
        run_id,
        phase="REPORT",
        status="FAILED",
        pending_approval_task_token=None,
        pending_approval_state=None,
    )

    return _response(200, {"run_id": run_id, "status": "rejected"})


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
