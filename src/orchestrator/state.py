"""Shared state passed between every orchestrator node.

Each node receives the full state and returns a partial dict of the
fields it updated; LangGraph merges that into the running state before
calling the next node.
"""

from __future__ import annotations

from typing import TypedDict


class UpgradeTestState(TypedDict):
    run_id: str
    manifest: dict  # serialized TestManifest (model_dump)
    phase: str  # BRANCH | BUILD | EXECUTE | ANALYZE | VALIDATE | REPORT | PR
    status: str  # RUNNING | SUCCEEDED | FAILED
    baseline_branch: str
    target_branch: str
    build_status: str  # PENDING | SUCCEEDED | FAILED
    baseline_execution: dict  # {status, log_path, output_path}
    target_execution: dict  # {status, log_path, output_path}
    analysis_result: dict  # pattern match or LLM diagnosis
    retry_count: int
    validation_results: dict
    report_path: str
    pr_url: str
    error: str
