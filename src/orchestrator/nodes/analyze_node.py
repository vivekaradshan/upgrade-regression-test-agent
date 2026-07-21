"""Step 4 of the orchestrator: diagnose a target-job failure and, when
possible, apply an auto-fix and loop back for a retry.

Pattern matching (free, instant, deterministic) always runs first; the
LLM is only consulted when nothing in the manifest's known_failure_patterns
matches. An auto-fix is committed to the target branch via the GitHub API
(not the local checkout) since execute_jobs re-syncs the local checkout
from the remote branch on every run (git reset --hard) - a local-only
edit would just be discarded before the retry.

A pattern-matcher fix is pre-approved by whoever wrote that regex into the
manifest, so it applies immediately. An LLM-sourced fix never auto-applies
regardless of confidence - it always routes to phase "AWAIT_APPROVAL"
instead of "REPORT"/"FAILED", so a human can review the proposed fix
before it touches the target branch. Locally, there's no approval
mechanism (that's AWS-only - see src/aws_lambda/await_approval_handler.py
and approve_run_handler.py), so build_graph's _route_after_analysis simply
falls through to generate_report for any phase it doesn't recognize,
same as it always has for "REPORT" - the only local-visible difference is
a more accurate status label ("AWAITING_APPROVAL" instead of "FAILED") for
a failure nobody's pattern anticipated and a human hasn't looked at yet.
"""

from __future__ import annotations

from src.analysis.llm_analyzer import LLMAnalyzer
from src.analysis.log_reader import LogReader
from src.analysis.pattern_matcher import PatternMatcher
from src.config.manifest import TestManifest
from src.orchestrator.state import UpgradeTestState
from src.tools.config_fix import apply_fix_to_target_branch, find_spark_config_file
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
                # Never auto-applied regardless of confidence - always
                # routes through the AWAIT_APPROVAL branch below instead
                # of the auto-fix branch a pattern-matcher fix can take.
                "auto_fix": False,
                "fix_config": (
                    {"key": diagnosis.fix_key, "value": diagnosis.fix_value}
                    if diagnosis.fix_key and diagnosis.fix_value
                    else None
                ),
                "confidence": diagnosis.confidence,
                "is_mitigation": diagnosis.is_mitigation,
                # A human approving this fix has no way to sanity-check the
                # LLM's diagnosis against real evidence without this - found
                # the hard way: a diagnosis can be stated confidently while
                # being wrong, and a reviewer can only catch that by seeing
                # what the model actually saw, not just its prose
                # conclusion. Truncated well below LogReader's own 500KB
                # cap - this only needs to be human-skimmable in the
                # approval UI, not the full log the LLM was given.
                "log_excerpt": log_content[:3000],
            }
            # Recorded on the append-only event log (not a current-state
            # field) since a run can call the LLM multiple times across
            # retries - report_node.py sums these via get_events() for the
            # report's "LLM usage" section (Phase 15.0's tracing half).
            state_store.record_event(
                run_id,
                phase="ANALYZE",
                event="llm_call",
                model=diagnosis.model,
                prompt_tokens=diagnosis.prompt_tokens,
                completion_tokens=diagnosis.completion_tokens,
                estimated_cost_usd=diagnosis.estimated_cost_usd,
            )

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
            config_file = find_spark_config_file(
                github_client, state["target_branch"], manifest.source_control.target.modifications
            )
            apply_fix_to_target_branch(
                github_client,
                branch=state["target_branch"],
                config_file_path=config_file,
                key=fix_config["key"],
                value=fix_config["value"],
                commit_message=f"auto-fix: set {fix_config['key']}={fix_config['value']}",
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

        if analysis_result["source"] == "llm":
            state_store.update_pipeline_status(
                run_id,
                manifest.pipeline.id,
                status="AWAITING_APPROVAL",
                diagnosis=analysis_result["diagnosis"],
                corrective_action=(
                    f"proposed: set {analysis_result['fix_config']['key']}={analysis_result['fix_config']['value']}"
                    if analysis_result["fix_config"]
                    else "proposed: no structured fix available - review required"
                ),
            )
            state_store.record_event(
                run_id, phase="ANALYZE", event="awaiting_approval", confidence=analysis_result["confidence"]
            )
            state_store.update_run_status(run_id, phase="AWAIT_APPROVAL", status="AWAITING_APPROVAL")

            return {
                "analysis_result": analysis_result,
                "phase": "AWAIT_APPROVAL",
                "status": "AWAITING_APPROVAL",
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
