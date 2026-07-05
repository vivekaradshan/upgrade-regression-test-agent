"""DynamoDB-backed persistence for upgrade test run state.

Single table "upgrade-test-runs", partition key run_id, sort key
record_type. record_type="_metadata" holds run-level state; any other
record_type value is a pipeline_id and holds that pipeline's state. This
lets get_all_pipelines query everything for a run_id in one call while
still separating run-level from pipeline-level fields.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

TABLE_NAME = "upgrade-test-runs"
METADATA_RECORD_TYPE = "_metadata"


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
        response = self._table.query(
            KeyConditionExpression="run_id = :run_id",
            ExpressionAttributeValues={":run_id": run_id},
        )
        items = [_from_dynamo_safe(item) for item in response.get("Items", [])]
        return [item for item in items if item["record_type"] != METADATA_RECORD_TYPE]

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
