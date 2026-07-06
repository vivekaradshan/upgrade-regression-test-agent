"""Step 3 of the orchestrator: run the baseline and target Spark jobs.

GitHub only stores the branches' code remotely; running it locally
requires a real local checkout (see mock_emr.py's docstring for why).
This node clones/syncs both branches into workspace_dir, generates one
shared input dataset (from the baseline checkout - the generator itself
doesn't change between versions), then dispatches both jobs through
MockStepFunctions and waits for both to finish.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import yaml

from src.config.manifest import TestManifest
from src.mock_infra.mock_step_functions import MockStepFunctions
from src.orchestrator.state import UpgradeTestState
from src.tools.github_client import GitHubClient
from src.tools.state_store import StateStore


def make_execute_jobs_node(
    step_functions: MockStepFunctions,
    state_store: StateStore,
    github_client: GitHubClient,
    workspace_dir: str,
    poll_interval_seconds: float = 1.0,
):
    def execute_jobs(state: UpgradeTestState) -> dict:
        manifest = TestManifest.model_validate(state["manifest"])
        run_id = state["run_id"]
        source_control = manifest.source_control

        clone_url = _authenticated_clone_url(github_client, source_control.owner, source_control.repo)

        baseline_dir = os.path.join(workspace_dir, run_id, "baseline")
        target_dir = os.path.join(workspace_dir, run_id, "target")
        _sync_local_checkout(clone_url, state["baseline_branch"], baseline_dir)
        _sync_local_checkout(clone_url, state["target_branch"], target_dir)

        data_path = os.path.join(workspace_dir, run_id, "data", "transactions.csv")
        _generate_mock_data(baseline_dir, data_path)

        log_dir = os.path.join(workspace_dir, run_id, "logs")
        timeout_seconds = manifest.execution.timeouts.single_pipeline_seconds

        baseline_output = os.path.join(manifest.execution.output_base, run_id, "baseline")
        target_output = os.path.join(manifest.execution.output_base, run_id, "target")

        baseline_arn = step_functions.start_execution(
            state_machine_arn="arn:aws:states:local:mock:stateMachine:baseline",
            input={
                "entry_script": os.path.join(baseline_dir, manifest.pipeline.entry_script),
                "input_path": data_path,
                "output_path": baseline_output,
                "spark_config": _effective_spark_config(baseline_dir, manifest.pipeline.spark_config),
                "spark_version": manifest.execution.baseline_spark_version,
                "log_dir": log_dir,
                "cwd": baseline_dir,
                "timeout_seconds": timeout_seconds,
            },
        )["executionArn"]

        target_arn = step_functions.start_execution(
            state_machine_arn="arn:aws:states:local:mock:stateMachine:target",
            input={
                "entry_script": os.path.join(target_dir, manifest.pipeline.entry_script),
                "input_path": data_path,
                "output_path": target_output,
                "spark_config": _effective_spark_config(target_dir, manifest.pipeline.spark_config),
                "spark_version": manifest.execution.target_spark_version,
                "log_dir": log_dir,
                "cwd": target_dir,
                "timeout_seconds": timeout_seconds,
            },
        )["executionArn"]

        baseline_result = _wait_for_completion(step_functions, baseline_arn, poll_interval_seconds)
        target_result = _wait_for_completion(step_functions, target_arn, poll_interval_seconds)

        state_store.update_pipeline_status(
            run_id,
            manifest.pipeline.id,
            baseline_status=baseline_result["status"],
            target_status=target_result["status"],
            baseline_log_path=baseline_result["output"]["log_path"],
            target_log_path=target_result["output"]["log_path"],
            baseline_output_path=baseline_output,
            target_output_path=target_output,
        )
        state_store.record_event(
            run_id,
            phase="EXECUTE",
            event="execution_completed",
            baseline_status=baseline_result["status"],
            target_status=target_result["status"],
        )
        state_store.update_run_status(run_id, phase="ANALYZE")

        return {
            "baseline_execution": {
                "status": baseline_result["status"],
                "log_path": baseline_result["output"]["log_path"],
                "output_path": baseline_output,
            },
            "target_execution": {
                "status": target_result["status"],
                "log_path": target_result["output"]["log_path"],
                "output_path": target_output,
            },
            "phase": "ANALYZE",
        }

    return execute_jobs


def _authenticated_clone_url(github_client: GitHubClient, owner: str, repo: str) -> str:
    return f"https://{github_client.token}@github.com/{owner}/{repo}.git"


def _sync_local_checkout(clone_url: str, branch: str, target_dir: str) -> None:
    if not os.path.exists(os.path.join(target_dir, ".git")):
        Path(target_dir).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--branch", branch, "--single-branch", clone_url, target_dir],
            check=True,
            capture_output=True,
        )
    else:
        subprocess.run(
            ["git", "fetch", "origin", branch], cwd=target_dir, check=True, capture_output=True
        )
        subprocess.run(["git", "checkout", branch], cwd=target_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "reset", "--hard", f"origin/{branch}"],
            cwd=target_dir,
            check=True,
            capture_output=True,
        )


def _effective_spark_config(pipeline_dir: str, manifest_spark_config: dict[str, str]) -> dict[str, str]:
    """A corrective fix commits a new spark_config value to the branch's
    config.yaml (see analyze_node.py) - it never touches the manifest, which
    is fixed for the whole run. So the checked-out branch's own config.yaml
    is the actual current source of truth and must override the manifest's
    static config, or a committed fix would silently never take effect."""
    config_path = Path(pipeline_dir) / "pipeline" / "config.yaml"
    if not config_path.exists():
        return dict(manifest_spark_config)

    branch_config = yaml.safe_load(config_path.read_text()) or {}
    branch_spark_config = branch_config.get("spark_config", {})

    return {**manifest_spark_config, **branch_spark_config}


def _generate_mock_data(pipeline_dir: str, output_csv_path: str) -> None:
    Path(output_csv_path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "pipeline/generate_mock_data.py", "--output", output_csv_path],
        cwd=pipeline_dir,
        check=True,
        capture_output=True,
    )


def _wait_for_completion(
    step_functions: MockStepFunctions, execution_arn: str, poll_interval_seconds: float
) -> dict:
    while True:
        result = step_functions.describe_execution(execution_arn)
        if result["status"] != "RUNNING":
            return result
        time.sleep(poll_interval_seconds)
