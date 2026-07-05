"""Simulates the Step Functions API: dispatch to a background thread, no polling.

When the job finishes, the result is stored and an EventBridge event is
emitted - mirroring the real dispatch -> exit -> callback -> resume pattern
used in the AWS version (Step Functions completes -> EventBridge fires ->
Fargate resumes).
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from src.mock_infra.mock_emr import LocalSparkRunner
from src.mock_infra.mock_event_bridge import MockEventBridge


class MockStepFunctions:
    def __init__(self, spark_runner: LocalSparkRunner, event_bridge: MockEventBridge):
        self._runner = spark_runner
        self._event_bridge = event_bridge
        self._executions: dict[str, dict] = {}
        self._lock = threading.Lock()

    def start_execution(self, state_machine_arn: str, input: dict) -> dict:
        execution_arn = f"arn:aws:states:local:mock:execution:{uuid.uuid4()}"
        start_date = datetime.now(timezone.utc)

        with self._lock:
            self._executions[execution_arn] = {
                "status": "RUNNING",
                "output": None,
                "startDate": start_date,
                "stopDate": None,
            }

        thread = threading.Thread(
            target=self._run_and_notify,
            args=(execution_arn, state_machine_arn, input),
            daemon=True,
        )
        thread.start()

        return {"executionArn": execution_arn, "startDate": start_date}

    def describe_execution(self, execution_arn: str) -> dict:
        with self._lock:
            record = dict(self._executions[execution_arn])
        return {
            "status": record["status"],
            "output": record["output"],
            "startDate": record["startDate"],
            "stopDate": record["stopDate"],
        }

    def _run_and_notify(self, execution_arn: str, state_machine_arn: str, input: dict) -> None:
        result = self._runner.run_spark_job(**input)

        with self._lock:
            self._executions[execution_arn]["status"] = result["status"]
            self._executions[execution_arn]["output"] = result
            self._executions[execution_arn]["stopDate"] = datetime.now(timezone.utc)

        self._event_bridge.emit_event(
            source="mock.stepfunctions",
            detail_type="Step Functions Execution Status Change",
            detail={
                "executionArn": execution_arn,
                "stateMachineArn": state_machine_arn,
                **result,
            },
        )
