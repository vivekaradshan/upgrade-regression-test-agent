"""LangGraph state graph wiring the orchestrator nodes together.

create_branches -> mock_build -> execute_jobs -> analyze_logs, then a
conditional edge: phase == RETRY loops back to execute_jobs (up to the
manifest's max_retries); phase == VALIDATE (target succeeded) proceeds to
validate_data; phase == REPORT (escalated failure, nothing to validate)
skips straight to generate_report. Both validate_data and the escalation
path converge on generate_report -> raise_pr -> END, so every run gets a
report and a PR regardless of pass/fail.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from src.analysis.llm_analyzer import LLMAnalyzer
from src.config.manifest import ManifestLoader
from src.config.settings import Settings
from src.mock_infra.aws_clients import AWSClientFactory
from src.mock_infra.mock_emr import LocalSparkRunner
from src.mock_infra.mock_event_bridge import MockEventBridge
from src.mock_infra.mock_step_functions import MockStepFunctions
from src.orchestrator.nodes.analyze_node import make_analyze_logs_node
from src.orchestrator.nodes.branch_node import make_create_branches_node
from src.orchestrator.nodes.build_node import make_mock_build_node
from src.orchestrator.nodes.execute_node import make_execute_jobs_node
from src.orchestrator.nodes.pr_node import make_raise_pr_node
from src.orchestrator.nodes.report_node import make_generate_report_node
from src.orchestrator.nodes.validate_node import make_validate_data_node
from src.orchestrator.state import UpgradeTestState
from src.tools.github_client import GitHubClient
from src.tools.state_store import StateStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _route_after_analysis(state: UpgradeTestState) -> str:
    if state["phase"] == "RETRY":
        return "execute_jobs"
    if state["phase"] == "VALIDATE":
        return "validate_data"
    return "generate_report"


def build_graph(
    github_client: GitHubClient,
    state_store: StateStore,
    step_functions: MockStepFunctions,
    llm_analyzer: LLMAnalyzer,
    workspace_dir: str,
    reports_dir: str,
):
    graph = StateGraph(UpgradeTestState)

    graph.add_node("create_branches", make_create_branches_node(github_client, state_store))
    graph.add_node("mock_build", make_mock_build_node(github_client, state_store))
    graph.add_node(
        "execute_jobs",
        make_execute_jobs_node(step_functions, state_store, github_client, workspace_dir),
    )
    graph.add_node(
        "analyze_logs", make_analyze_logs_node(github_client, state_store, llm_analyzer)
    )
    graph.add_node("validate_data", make_validate_data_node(state_store))
    graph.add_node("generate_report", make_generate_report_node(state_store, reports_dir))
    graph.add_node("raise_pr", make_raise_pr_node(github_client, state_store))

    graph.add_edge(START, "create_branches")
    graph.add_edge("create_branches", "mock_build")
    graph.add_edge("mock_build", "execute_jobs")
    graph.add_edge("execute_jobs", "analyze_logs")
    graph.add_conditional_edges(
        "analyze_logs",
        _route_after_analysis,
        {
            "execute_jobs": "execute_jobs",
            "validate_data": "validate_data",
            "generate_report": "generate_report",
        },
    )
    graph.add_edge("validate_data", "generate_report")
    graph.add_edge("generate_report", "raise_pr")
    graph.add_edge("raise_pr", END)

    return graph.compile()


def run_upgrade_test(
    manifest_path: str, workspace_dir: str | None = None, state_dir: str | None = None
) -> dict:
    manifest = ManifestLoader.load_from_file(manifest_path)
    settings = Settings()
    workspace_dir = workspace_dir or str(PROJECT_ROOT / "workspace" / "runs")
    # Deliberately independent of workspace_dir: the dashboard (a separate
    # process) needs a stable, known location to scan for run snapshots
    # regardless of what workspace_dir a given run used for its checkouts.
    state_dir = state_dir or str(PROJECT_ROOT / "workspace" / "state")

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

    llm_analyzer = LLMAnalyzer(
        api_key=settings.llm_api_key,
        migration_notes=manifest.upgrade_strategy.migration_notes,
    )

    run_id = f"run-{uuid.uuid4().hex[:12]}"
    state_store.init_run(run_id, manifest)

    reports_dir = str(PROJECT_ROOT / manifest.reporting.output_dir)
    graph = build_graph(
        github_client, state_store, step_functions, llm_analyzer, workspace_dir, reports_dir
    )

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

    snapshot_path = str(Path(state_dir) / f"{run_id}.json")
    state_store.export_snapshot(run_id, snapshot_path)

    try:
        final_state = initial_state
        for step_state in graph.stream(initial_state, stream_mode="values"):
            final_state = step_state
            state_store.export_snapshot(run_id, snapshot_path)
        return final_state
    finally:
        github_client.close()
        aws_factory.stop()
