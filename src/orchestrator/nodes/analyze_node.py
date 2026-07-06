"""Step 4 of the orchestrator: diagnose a target-job failure and, when
possible, apply an auto-fix and loop back for a retry.

Pattern matching (free, instant, deterministic) always runs first; the
LLM is only consulted when nothing in the manifest's known_failure_patterns
matches. An auto-fix is committed to the target branch via the GitHub API
(not the local checkout) since execute_jobs re-syncs the local checkout
from the remote branch on every run (git reset --hard) - a local-only
edit would just be discarded before the retry.
"""

from __future__ import annotations

import yaml

from src.analysis.llm_analyzer import LLMAnalyzer
from src.analysis.log_reader import LogReader
from src.analysis.pattern_matcher import PatternMatcher
from src.config.manifest import TestManifest
from src.orchestrator.state import UpgradeTestState
from src.tools.github_client import GitHubClient
from src.tools.state_store import StateStore


def make_analyze_logs_node(
    github_client: GitHubClient, state_store: StateStore, llm_analyzer: LLMAnalyzer
):
    def analyze_logs(state: UpgradeTestState) -> dict:
        manifest = TestManifest.model_validate(state["manifest"])
        run_id = state["run_id"]

        if state["target_execution"]["status"] == "SUCCEEDED":
            state_store.update_run_status(run_id, phase="VALIDATE")
            return {"phase": "VALIDATE"}

        log_content = LogReader.read_log(state["target_execution"]["log_path"])
        matcher = PatternMatcher(manifest.log_analysis.known_failure_patterns)
        match = matcher.match(log_content)

        if match is not None:
            analysis_result = {
                "source": "pattern_matcher",
                "diagnosis": match.diagnosis,
                "action": match.action,
                "auto_fix": match.auto_fix,
                "fix_config": match.fix_config,
            }
        else:
            diagnosis = llm_analyzer.analyze(
                log_content,
                {
                    "baseline_spark_version": manifest.execution.baseline_spark_version,
                    "target_spark_version": manifest.execution.target_spark_version,
                },
            )
            analysis_result = {
                "source": "llm",
                "diagnosis": diagnosis.root_cause,
                "action": diagnosis.classification,
                # The LLM's fix_suggestion is free text, not a structured
                # {key, value} pair, so it can't be auto-applied the way a
                # manifest-defined fix_config can - always escalate for
                # human review instead of guessing how to parse it.
                "auto_fix": False,
                "fix_config": None,
            }

        retry_count = state["retry_count"]
        max_retries = manifest.log_analysis.retry.max_retries

        state_store.record_event(
            run_id,
            phase="ANALYZE",
            event="failure_analyzed",
            source=analysis_result["source"],
            diagnosis=analysis_result["diagnosis"],
        )

        if analysis_result["auto_fix"] and retry_count < max_retries:
            fix_config = analysis_result["fix_config"]
            config_file = manifest.source_control.target.modifications[0].file
            _apply_fix_to_target_branch(
                github_client,
                branch=state["target_branch"],
                config_file_path=config_file,
                key=fix_config["key"],
                value=fix_config["value"],
            )

            new_retry_count = retry_count + 1
            state_store.update_pipeline_status(
                run_id,
                manifest.pipeline.id,
                retry_count=new_retry_count,
                diagnosis=analysis_result["diagnosis"],
                corrective_action=f"set {fix_config['key']}={fix_config['value']}",
            )
            state_store.record_event(
                run_id,
                phase="ANALYZE",
                event="auto_fix_applied",
                fix_key=fix_config["key"],
                fix_value=fix_config["value"],
                retry_count=new_retry_count,
            )
            state_store.update_run_status(run_id, phase="EXECUTE")

            return {
                "analysis_result": analysis_result,
                "retry_count": new_retry_count,
                "phase": "RETRY",
            }

        reason = "max_retries_exhausted" if analysis_result["auto_fix"] else "not_auto_fixable"
        state_store.update_pipeline_status(
            run_id,
            manifest.pipeline.id,
            status="FAILED",
            diagnosis=analysis_result["diagnosis"],
            error_message=analysis_result["diagnosis"],
        )
        state_store.record_event(run_id, phase="ANALYZE", event="escalated", reason=reason)
        state_store.update_run_status(run_id, phase="REPORT", status="FAILED")

        return {
            "analysis_result": analysis_result,
            "phase": "REPORT",
            "status": "FAILED",
            "error": analysis_result["diagnosis"],
        }

    return analyze_logs


def _apply_fix_to_target_branch(
    github_client: GitHubClient, branch: str, config_file_path: str, key: str, value: str
) -> None:
    content, sha = github_client.get_file_content(config_file_path, branch)
    config = yaml.safe_load(content)
    config.setdefault("spark_config", {})[key] = value

    github_client.update_file(
        path=config_file_path,
        branch=branch,
        content=yaml.safe_dump(config, sort_keys=False),
        sha=sha,
        message=f"auto-fix: set {key}={value}",
    )
