from unittest.mock import MagicMock

import pytest

from src.analysis.llm_analyzer import Diagnosis
from src.config.manifest import ManifestLoader
from src.mock_infra.aws_clients import AWSClientFactory
from src.orchestrator.nodes.analyze_node import make_analyze_logs_node
from src.tools.state_store import StateStore

MANIFEST_PATH = "manifests/spark-3.5-to-4.0.yaml"


@pytest.fixture
def manifest():
    return ManifestLoader.load_from_file(MANIFEST_PATH)


@pytest.fixture
def state_store():
    with AWSClientFactory(use_mocks=True) as factory:
        store = StateStore(factory.get_dynamodb_resource())
        store.create_table()
        yield store


def test_llm_diagnosis_routes_to_await_approval_not_failed(manifest, state_store, tmp_path):
    state_store.init_run("run-1", manifest)

    log_path = tmp_path / "target.log"
    log_path.write_text("java.lang.IllegalArgumentException: Codec [lz4raw] is not available")

    github_client = MagicMock()
    llm_analyzer = MagicMock()
    llm_analyzer.analyze.return_value = Diagnosis(
        root_cause="Codec [lz4raw] is not available",
        classification="config_fix",
        fix_suggestion="Change lz4raw to lz4_raw",
        confidence=0.9,
        model="gpt-4o-mini",
        prompt_tokens=100,
        completion_tokens=50,
        estimated_cost_usd=0.00005,
        fix_key="spark.sql.parquet.compression.codec",
        fix_value="lz4_raw",
        is_mitigation=False,
    )

    analyze_logs = make_analyze_logs_node(github_client, state_store, llm_analyzer)
    result = analyze_logs(
        {
            "run_id": "run-1",
            "manifest": manifest.model_dump(mode="json"),
            "target_branch": "target-branch",
            "target_execution": {"status": "FAILED", "log_path": str(log_path)},
            "retry_count": 0,
        }
    )

    # Never auto-applied, even at 0.9 confidence - always routes to human
    # approval instead of the auto-fix branch or the FAILED escalation.
    assert result["phase"] == "AWAIT_APPROVAL"
    assert result["status"] == "AWAITING_APPROVAL"
    assert result["analysis_result"]["fix_config"] == {
        "key": "spark.sql.parquet.compression.codec",
        "value": "lz4_raw",
    }
    assert result["analysis_result"]["confidence"] == 0.9
    # A human approving this needs to be able to check the diagnosis
    # against real evidence, not just trust the prose - see
    # dashboard/app.py's "Log evidence the LLM analyzed" expander.
    assert "Codec [lz4raw] is not available" in result["analysis_result"]["log_excerpt"]
    github_client.update_file.assert_not_called()

    metadata = state_store.get_run_metadata("run-1")
    assert metadata["phase"] == "AWAIT_APPROVAL"
    assert metadata["status"] == "AWAITING_APPROVAL"


def test_llm_diagnosis_with_no_structured_fix_still_awaits_approval(manifest, state_store, tmp_path):
    state_store.init_run("run-1", manifest)

    log_path = tmp_path / "target.log"
    log_path.write_text("some totally novel failure")

    github_client = MagicMock()
    llm_analyzer = MagicMock()
    llm_analyzer.analyze.return_value = Diagnosis(
        root_cause="Unrecognized failure",
        classification="code_change",
        fix_suggestion="Needs a code change, not a config fix",
        confidence=0.4,
        model="gpt-4o-mini",
        fix_key=None,
        fix_value=None,
    )
    # confidence 0.4 is below REACT_LOOP_CONFIDENCE_THRESHOLD, so the
    # single-shot diagnosis above gets handed to the ReAct loop - mock it
    # directly rather than letting analyze_node build a real one from
    # llm_analyzer's (mocked) ._client, which would try to run the real
    # tool-calling loop against MagicMock objects.
    react_analyzer = MagicMock()
    react_analyzer.analyze.return_value = Diagnosis(
        root_cause="Unrecognized failure, confirmed via investigation",
        classification="code_change",
        fix_suggestion="Needs a code change, not a config fix",
        confidence=0.4,
        model="gpt-4o-mini",
        fix_key=None,
        fix_value=None,
    )

    analyze_logs = make_analyze_logs_node(github_client, state_store, llm_analyzer, react_analyzer)
    result = analyze_logs(
        {
            "run_id": "run-1",
            "manifest": manifest.model_dump(mode="json"),
            "target_branch": "target-branch",
            "target_execution": {"status": "FAILED", "log_path": str(log_path)},
            "retry_count": 0,
        }
    )

    assert result["phase"] == "AWAIT_APPROVAL"
    assert result["analysis_result"]["fix_config"] is None


def test_pattern_matcher_escalation_unaffected_by_await_approval_change(manifest, state_store, tmp_path):
    """A pattern-matched failure that's genuinely not auto-fixable (e.g.
    ClassNotFoundException...javax.servlet, action=escalate in the
    manifest) must still go straight to FAILED - the new AWAIT_APPROVAL
    branch only applies to source == "llm"."""
    state_store.init_run("run-1", manifest)

    log_path = tmp_path / "target.log"
    log_path.write_text("java.lang.ClassNotFoundException: javax.servlet.Servlet")

    github_client = MagicMock()
    llm_analyzer = MagicMock()

    analyze_logs = make_analyze_logs_node(github_client, state_store, llm_analyzer)
    result = analyze_logs(
        {
            "run_id": "run-1",
            "manifest": manifest.model_dump(mode="json"),
            "target_branch": "target-branch",
            "target_execution": {"status": "FAILED", "log_path": str(log_path)},
            "retry_count": 0,
        }
    )

    assert result["phase"] == "REPORT"
    assert result["status"] == "FAILED"
    llm_analyzer.analyze.assert_not_called()
