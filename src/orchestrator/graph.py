"""LangGraph state graph wiring the orchestrator nodes together.

Step 8 scope: create_branches -> mock_build -> execute_jobs -> END.
Step 9 will insert analyze_logs after execute_jobs with a conditional
edge back to execute_jobs (the retry loop); later steps append
validate_data -> generate_report -> raise_pr before END.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from src.config.manifest import ManifestLoader
from src.config.settings import Settings
from src.mock_infra.aws_clients import AWSClientFactory
from src.mock_infra.mock_emr import LocalSparkRunner
from src.mock_infra.mock_event_bridge import MockEventBridge
from src.mock_infra.mock_step_functions import MockStepFunctions
from src.orchestrator.nodes.branch_node import make_create_branches_node
from src.orchestrator.nodes.build_node import make_mock_build_node
from src.orchestrator.nodes.execute_node import make_execute_jobs_node
from src.orchestrator.state import UpgradeTestState
from src.tools.github_client import GitHubClient
from src.tools.state_store import StateStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def build_graph(
    github_client: GitHubClient,
    state_store: StateStore,
    step_functions: MockStepFunctions,
    workspace_dir: str,
):
    graph = StateGraph(UpgradeTestState)

    graph.add_node("create_branches", make_create_branches_node(github_client, state_store))
    graph.add_node("mock_build", make_mock_build_node(github_client, state_store))
    graph.add_node(
        "execute_jobs",
        make_execute_jobs_node(step_functions, state_store, github_client, workspace_dir),
    )

    graph.add_edge(START, "create_branches")
    graph.add_edge("create_branches", "mock_build")
    graph.add_edge("mock_build", "execute_jobs")
    graph.add_edge("execute_jobs", END)

    return graph.compile()


def run_upgrade_test(manifest_path: str, workspace_dir: str | None = None) -> dict:
    manifest = ManifestLoader.load_from_file(manifest_path)
    settings = Settings()
    workspace_dir = workspace_dir or str(PROJECT_ROOT / "workspace" / "runs")

    github_client = GitHubClient(
        token=settings.github_token,
        owner=manifest.source_control.owner,
        repo=manifest.source_control.repo,
    )

    aws_factory = AWSClientFactory(use_mocks=True)
    state_store = StateStore(aws_factory.get_dynamodb_resource())
    state_store.create_table()

    runner = LocalSparkRunner(
        python_executable=sys.executable,
        spark4_libs_path=str(PROJECT_ROOT / ".spark4_libs"),
    )
    event_bridge = MockEventBridge()
    step_functions = MockStepFunctions(spark_runner=runner, event_bridge=event_bridge)

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    state_store.init_run(run_id, manifest)

    graph = build_graph(github_client, state_store, step_functions, workspace_dir)

    initial_state: UpgradeTestState = {
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

    try:
        return graph.invoke(initial_state)
    finally:
        github_client.close()
        aws_factory.stop()
