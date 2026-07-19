"""Step Functions Task Lambda invoked via the `lambda:invoke.waitForTaskToken`
service integration pattern (not `.sync`, which every other Task Lambda in
this state machine uses). This handler does NOT complete the task itself -
Step Functions pauses the state machine at this state until something
external calls states:SendTaskSuccess or SendTaskFailure with the token
this handler receives (see approve_run_handler.py, invoked by the
dashboard's Approve/Reject button).

All this handler does is persist the token and the full orchestrator
state at this point so approve_run_handler can resume the execution
later with the human's decision folded in - there's no other channel to
hand that off through, since the task token only exists in this one
Lambda invocation's event.
"""

from __future__ import annotations

import json

from src.aws_lambda.common import get_state_store, merge_update


def handler(event: dict, context) -> dict:
    task_token = event["taskToken"]
    state = event["state"]
    run_id = state["run_id"]

    state_store = get_state_store()
    state_store.update_run_status(
        run_id,
        pending_approval_task_token=task_token,
        pending_approval_state=json.dumps(state),
    )
    state_store.record_event(run_id, phase="ANALYZE", event="approval_pending")

    # Not the actual task completion - Step Functions only completes this
    # state when approve_run_handler.py calls SendTaskSuccess/Failure with
    # the token above. This return value is discarded.
    return merge_update(event, {})
