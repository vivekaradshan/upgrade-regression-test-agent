import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.analysis.corrective_actions import CorrectiveActionApplier
from src.analysis.llm_analyzer import LLMAnalyzer
from src.analysis.log_reader import LogReader
from src.analysis.pattern_matcher import PatternMatcher
from src.config.manifest import ManifestLoader

MANIFEST_PATH = "manifests/spark-3.5-to-4.0.yaml"
FIXTURE_LOG_PATH = "tests/fixtures/spark_ansi_error.log"


def test_pattern_matcher_finds_ansi_arithmetic_exception_in_real_log():
    manifest = ManifestLoader.load_from_file(MANIFEST_PATH)
    matcher = PatternMatcher(manifest.log_analysis.known_failure_patterns)

    log_content = Path(FIXTURE_LOG_PATH).read_text()
    result = matcher.match(log_content)

    assert result is not None
    assert result.action == "config_fix"
    assert result.auto_fix is True
    assert result.fix_config == {"key": "spark.sql.ansi.enabled", "value": "false"}


def test_pattern_matcher_returns_none_for_unknown_error():
    manifest = ManifestLoader.load_from_file(MANIFEST_PATH)
    matcher = PatternMatcher(manifest.log_analysis.known_failure_patterns)

    result = matcher.match("Traceback (most recent call last):\nKeyError: 'totally_unrelated_field'")

    assert result is None


def test_log_reader_truncates_large_log_to_expected_format(tmp_path):
    lines = [f"line {i}: normal log output" for i in range(10_000)]
    lines[5000] = "Traceback (most recent call last):"
    lines[5001] = "  File \"job.py\", line 10, in <module>"
    lines[5002] = "ValueError: something broke"
    lines[5003] = ""
    lines[5004] = "26/07/05 12:00:00 INFO more log noise"

    log_path = tmp_path / "big.log"
    log_path.write_text("\n".join(lines))

    result = LogReader.read_log(str(log_path), max_bytes=1000)

    assert "line 0: normal log output" in result
    assert "line 49: normal log output" in result
    assert "line 9999: normal log output" in result
    assert "ValueError: something broke" in result
    assert "[TRUNCATED]" in result
    # the noisy middle (far from start/end/exception) should be dropped
    assert "line 2500: normal log output" not in result


def test_log_reader_returns_full_content_when_under_max_bytes(tmp_path):
    log_path = tmp_path / "small.log"
    log_path.write_text("just a few lines\nof output\n")

    result = LogReader.read_log(str(log_path), max_bytes=500_000)

    assert result == "just a few lines\nof output\n"


def test_corrective_action_applier_modifies_config_yaml(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("spark_version: \"4.0.0\"\nspark_config:\n  spark.master: \"local[*]\"\n")

    CorrectiveActionApplier.apply_config_fix(
        str(config_path), key="spark.sql.ansi.enabled", value="false"
    )

    import yaml

    updated = yaml.safe_load(config_path.read_text())
    assert updated["spark_config"]["spark.sql.ansi.enabled"] == "false"
    assert updated["spark_config"]["spark.master"] == "local[*]"


def test_corrective_action_applier_to_spark_config_returns_new_dict():
    original = {"spark.master": "local[*]"}

    updated = CorrectiveActionApplier.apply_to_spark_config(
        original, key="spark.sql.ansi.enabled", value="false"
    )

    assert updated == {"spark.master": "local[*]", "spark.sql.ansi.enabled": "false"}
    assert original == {"spark.master": "local[*]"}


def _mock_openai_response(content: dict, prompt_tokens: int = 100, completion_tokens: int = 50):
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content=json.dumps(content)))]
    mock_response.usage = MagicMock(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return mock_response


def test_llm_analyzer_parses_mocked_response():
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_openai_response(
        {
            "root_cause": "Unrecognized Spark 4.0 behavior change",
            "classification": "config_fix",
            "fix_suggestion": "set spark.sql.someFlag=false",
            "confidence": 0.75,
        }
    )

    analyzer = LLMAnalyzer(
        api_key="unused",
        migration_notes=["ANSI SQL mode is ON by default in 4.0"],
        client=mock_client,
    )

    diagnosis = analyzer.analyze(
        "some unrecognized failure log",
        {"baseline_spark_version": "3.5.4", "target_spark_version": "4.0.0"},
    )

    assert diagnosis.classification == "config_fix"
    assert diagnosis.confidence == 0.75
    mock_client.chat.completions.create.assert_called_once()


def test_llm_analyzer_captures_token_usage_and_estimates_cost():
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_openai_response(
        {"root_cause": "x", "classification": "escalate", "fix_suggestion": None, "confidence": 0.5},
        prompt_tokens=1000,
        completion_tokens=500,
    )

    analyzer = LLMAnalyzer(api_key="unused", client=mock_client)
    diagnosis = analyzer.analyze("log", {})

    assert diagnosis.model == "gpt-4o-mini"
    assert diagnosis.prompt_tokens == 1000
    assert diagnosis.completion_tokens == 500
    # 1000 * $0.150/1M + 500 * $0.600/1M
    assert diagnosis.estimated_cost_usd == pytest.approx(0.00045)


def test_llm_analyzer_handles_response_with_no_usage_attribute():
    mock_response = MagicMock(spec=["choices"])
    mock_response.choices = [
        MagicMock(
            message=MagicMock(
                content=json.dumps(
                    {"root_cause": "x", "classification": "escalate", "fix_suggestion": None, "confidence": 0.5}
                )
            )
        )
    ]
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_response

    analyzer = LLMAnalyzer(api_key="unused", client=mock_client)
    diagnosis = analyzer.analyze("log", {})

    assert diagnosis.prompt_tokens == 0
    assert diagnosis.completion_tokens == 0
    assert diagnosis.estimated_cost_usd == 0.0


def test_llm_analyzer_without_api_key_returns_escalate_placeholder():
    analyzer = LLMAnalyzer(api_key="")

    diagnosis = analyzer.analyze("some failure log", {})

    assert diagnosis.classification == "escalate"
    assert diagnosis.confidence == 0.0
