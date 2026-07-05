import csv
import sys
import time
from pathlib import Path

import pytest

from src.mock_infra.mock_emr import LocalSparkRunner
from src.mock_infra.mock_event_bridge import MockEventBridge
from src.mock_infra.mock_step_functions import MockStepFunctions

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_REPO = PROJECT_ROOT / "workspace" / "customer-transactions-pipeline"
ENTRY_SCRIPT = str(PIPELINE_REPO / "pipeline" / "spark_job.py")


def _skip_if_workspace_missing():
    if not PIPELINE_REPO.exists():
        pytest.skip("workspace/customer-transactions-pipeline not cloned locally")


@pytest.fixture
def transactions_csv(tmp_path):
    _skip_if_workspace_missing()

    rows = [
        {
            "customer_id": "C001",
            "transaction_date": "2024-01-01",
            "product": "Widget",
            "quantity": 2,
            "total_amount": 20.0,
            "category": "Electronics",
        },
        {
            "customer_id": "C001",
            "transaction_date": "2024-01-02",
            "product": "Gadget",
            "quantity": 0,
            "total_amount": 50.0,
            "category": "Electronics",
        },
        {
            "customer_id": "C002",
            "transaction_date": "2024-01-03",
            "product": "Gizmo",
            "quantity": 5,
            "total_amount": 100.0,
            "category": "Books",
        },
    ]
    csv_path = tmp_path / "transactions.csv"
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return str(csv_path)


@pytest.fixture
def runner():
    return LocalSparkRunner(
        python_executable=sys.executable,
        spark4_libs_path=str(PROJECT_ROOT / ".spark4_libs"),
    )


def test_step_functions_runs_baseline_job_successfully(transactions_csv, tmp_path, runner):
    event_bridge = MockEventBridge()
    step_functions = MockStepFunctions(spark_runner=runner, event_bridge=event_bridge)

    received_events = []
    event_bridge.register_callback("*", received_events.append)

    execution = step_functions.start_execution(
        state_machine_arn="arn:aws:states:local:mock:stateMachine:baseline",
        input={
            "entry_script": ENTRY_SCRIPT,
            "input_path": transactions_csv,
            "output_path": str(tmp_path / "output" / "baseline"),
            "spark_config": {"spark.master": "local[2]"},
            "spark_version": "3.5.4",
            "log_dir": str(tmp_path / "logs"),
            "cwd": str(PIPELINE_REPO),
            "timeout_seconds": 120,
        },
    )

    _wait_for_completion(step_functions, execution["executionArn"])

    result = step_functions.describe_execution(execution["executionArn"])
    assert result["status"] == "SUCCEEDED"
    assert Path(result["output"]["log_path"]).exists()

    assert len(received_events) == 1
    assert received_events[0]["detail"]["status"] == "SUCCEEDED"


def test_step_functions_runs_target_job_and_fails_on_ansi(transactions_csv, tmp_path, runner):
    event_bridge = MockEventBridge()
    step_functions = MockStepFunctions(spark_runner=runner, event_bridge=event_bridge)

    received_events = []
    event_bridge.register_callback("mock.stepfunctions", received_events.append)

    execution = step_functions.start_execution(
        state_machine_arn="arn:aws:states:local:mock:stateMachine:target",
        input={
            "entry_script": ENTRY_SCRIPT,
            "input_path": transactions_csv,
            "output_path": str(tmp_path / "output" / "target"),
            "spark_config": {"spark.master": "local[2]"},
            "spark_version": "4.0.0",
            "log_dir": str(tmp_path / "logs"),
            "cwd": str(PIPELINE_REPO),
            "timeout_seconds": 120,
        },
    )

    _wait_for_completion(step_functions, execution["executionArn"])

    result = step_functions.describe_execution(execution["executionArn"])
    assert result["status"] == "FAILED"

    log_content = Path(result["output"]["log_path"]).read_text()
    assert "ArithmeticException" in log_content or "divide by zero" in log_content

    assert len(received_events) == 1
    assert received_events[0]["detail"]["status"] == "FAILED"


def _wait_for_completion(step_functions: MockStepFunctions, execution_arn: str, timeout: float = 90.0):
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if step_functions.describe_execution(execution_arn)["status"] != "RUNNING":
            return
        time.sleep(0.5)
    raise TimeoutError(f"Execution {execution_arn} did not complete within {timeout}s")
