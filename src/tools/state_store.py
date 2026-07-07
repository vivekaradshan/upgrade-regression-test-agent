"""DynamoDB-backed persistence for upgrade test run state.

Single table "upgrade-test-runs", partition key run_id, sort key
record_type. record_type="_metadata" holds run-level state; a pipeline_id
holds that pipeline's current state; "event#<iso-timestamp>#<uuid>" holds
one immutable audit-log entry. Current-state records are overwritten in
place (update_pipeline_status / update_run_status) and only ever reflect
the latest values - they can't tell you a job failed and was retried,
only that it currently succeeded. Events are append-only and never
updated, so record_event is what preserves the full timeline (when each
phase started/ended, transient failures, retries) for auditing.

get_all_pipelines queries everything for a run_id in one call and filters
out both _metadata and event# records, leaving just pipeline state.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

TABLE_NAME = "upgrade-test-runs"
METADATA_RECORD_TYPE = "_metadata"
EVENT_RECORD_PREFIX = "event#"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_dynamo_safe(value: Any) -> Any:
    """DynamoDB's resource API rejects native floats; it wants Decimal."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamo_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_dynamo_safe(v) for v in value]
    return value


def _from_dynamo_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value) if value % 1 != 0 else int(value)
    if isinstance(value, dict):
        return {k: _from_dynamo_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_dynamo_safe(v) for v in value]
    return value


class StateStore:
    def __init__(self, dynamodb_resource):
        self._dynamodb = dynamodb_resource
        self._table = self._dynamodb.Table(TABLE_NAME)

    def create_table(self) -> None:
        self._table = self._dynamodb.create_table(
            TableName=TABLE_NAME,
            KeySchema=[
                {"AttributeName": "run_id", "KeyType": "HASH"},
                {"AttributeName": "record_type", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "run_id", "AttributeType": "S"},
                {"AttributeName": "record_type", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        self._table.wait_until_exists()

    def init_run(self, run_id: str, manifest) -> None:
        now = _now_iso()

        self._table.put_item(
            Item={
                "run_id": run_id,
                "record_type": METADATA_RECORD_TYPE,
                "status": "RUNNING",
                "phase": "BRANCH",
                "config": _to_dynamo_safe(manifest.model_dump(mode="json")),
                "created_at": now,
                "updated_at": now,
                "overall_status": "RUNNING",
            }
        )

        self._table.put_item(
            Item={
                "run_id": run_id,
                "record_type": manifest.pipeline.id,
                "pipeline_id": manifest.pipeline.id,
                "status": "PENDING",
                "phase": "BRANCH",
                "variant": None,
                "baseline_status": "PENDING",
                "target_status": "PENDING",
                "started_at": now,
                "updated_at": now,
                "completed_at": None,
                "baseline_log_path": None,
                "target_log_path": None,
                "baseline_output_path": None,
                "target_output_path": None,
                "retry_count": 0,
                "error_message": None,
                "diagnosis": None,
                "corrective_action": None,
                "metrics": {},
                "validation_results": {},
            }
        )

    def update_pipeline_status(self, run_id: str, pipeline_id: str, **kwargs) -> None:
        self._update_record(run_id, pipeline_id, kwargs)

    def update_run_status(self, run_id: str, **kwargs) -> None:
        self._update_record(run_id, METADATA_RECORD_TYPE, kwargs)

    def get_pipeline_status(self, run_id: str, pipeline_id: str) -> dict:
        return self._get_record(run_id, pipeline_id)

    def get_run_metadata(self, run_id: str) -> dict:
        return self._get_record(run_id, METADATA_RECORD_TYPE)

    def get_all_pipelines(self, run_id: str) -> list[dict]:
        items = self._query_run(run_id)
        return [
            item
            for item in items
            if item["record_type"] != METADATA_RECORD_TYPE
            and not item["record_type"].startswith(EVENT_RECORD_PREFIX)
        ]

    def export_snapshot(self, run_id: str, path: str) -> None:
        """Dumps this run's current metadata + pipeline records to a JSON
        file. Needed because the dashboard runs in a separate process from
        the orchestrator, and moto's mocked DynamoDB only lives in-memory
        within the process that started it - the dashboard has no way to
        read the orchestrator's live DynamoDB state directly, so this file
        is the actual channel between the two."""
        snapshot = {
            "run_id": run_id,
            "metadata": self.get_run_metadata(run_id),
            "pipelines": self.get_all_pipelines(run_id),
        }

        snapshot_path = Path(path)
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(json.dumps(snapshot, indent=2, default=str))

    def record_event(self, run_id: str, phase: str, event: str, **details) -> None:
        """Append an immutable audit-log entry. Never updated, never overwritten -
        this is the source of truth for "what happened when", including
        transient failures and retries that current-state records lose."""
        now = _now_iso()
        record_type = f"{EVENT_RECORD_PREFIX}{now}#{uuid.uuid4().hex[:8]}"

        item = {
            "run_id": run_id,
            "record_type": record_type,
            "timestamp": now,
            "phase": phase,
            "event": event,
            **details,
        }
        self._table.put_item(Item=_to_dynamo_safe(item))

    def get_events(self, run_id: str) -> list[dict]:
        """Returns this run's audit log in chronological order (ISO timestamps
        in the sort key mean DynamoDB's default ascending query order is
        already chronological)."""
        items = self._query_run(run_id)
        return [item for item in items if item["record_type"].startswith(EVENT_RECORD_PREFIX)]

    def _query_run(self, run_id: str) -> list[dict]:
        response = self._table.query(
            KeyConditionExpression="run_id = :run_id",
            ExpressionAttributeValues={":run_id": run_id},
        )
        return [_from_dynamo_safe(item) for item in response.get("Items", [])]

    def _get_record(self, run_id: str, record_type: str) -> dict:
        response = self._table.get_item(Key={"run_id": run_id, "record_type": record_type})
        item = response.get("Item")
        if item is None:
            raise KeyError(f"No record for run_id={run_id!r} record_type={record_type!r}")
        return _from_dynamo_safe(item)

    def _update_record(self, run_id: str, record_type: str, fields: dict) -> None:
        if not fields:
            return

        fields = dict(fields)
        fields["updated_at"] = _now_iso()

        update_expression_parts = []
        expression_attribute_names = {}
        expression_attribute_values = {}

        for key, value in fields.items():
            placeholder_name = f"#{key}"
            placeholder_value = f":{key}"
            update_expression_parts.append(f"{placeholder_name} = {placeholder_value}")
            expression_attribute_names[placeholder_name] = key
            expression_attribute_values[placeholder_value] = _to_dynamo_safe(value)

        self._table.update_item(
            Key={"run_id": run_id, "record_type": record_type},
            UpdateExpression="SET " + ", ".join(update_expression_parts),
            ExpressionAttributeNames=expression_attribute_names,
            ExpressionAttributeValues=expression_attribute_values,
        )
