"""Eval harness for LLMAnalyzer.analyze() - the promotion-policy gate every
later Step 15 phase's autonomy claims get checked against.

Runs against the real OpenAI API (skipped if no key is configured) rather
than a mocked client, matching this project's discipline of verifying
against real state instead of mocks - a mocked LLM call can't tell you
whether the prompt actually produces the right classification.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.analysis.llm_analyzer import LLMAnalyzer
from src.config.settings import Settings
from tests.evals.cases import CASES, MIGRATION_NOTES, UPGRADE_CONTEXT

pytestmark = pytest.mark.evals

_settings = Settings()


@pytest.fixture(scope="module")
def analyzer():
    if not _settings.llm_api_key:
        pytest.skip("OPENAI_API_KEY not configured - evals require a real LLM call")
    return LLMAnalyzer(api_key=_settings.llm_api_key, migration_notes=MIGRATION_NOTES)


@pytest.fixture(scope="module")
def scoreboard():
    board = {"total": 0, "passed": 0, "results": []}
    yield board
    if board["total"]:
        rate = board["passed"] / board["total"]
        print(f"\n\nLLMAnalyzer eval pass rate: {board['passed']}/{board['total']} ({rate:.0%})")
        for name, ok, detail in board["results"]:
            print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_diagnosis_matches_golden_expectation(analyzer, scoreboard, case):
    log_content = Path(case.log_path).read_text()
    diagnosis = analyzer.analyze(log_content, UPGRADE_CONTEXT)

    checks = [diagnosis.classification in case.acceptable_classifications]
    if case.min_confidence is not None:
        checks.append(diagnosis.confidence >= case.min_confidence)
    if case.max_confidence is not None:
        checks.append(diagnosis.confidence <= case.max_confidence)
    if case.expect_fix_config:
        checks.append(diagnosis.fix_key is not None and diagnosis.fix_value is not None)
    else:
        checks.append(diagnosis.classification != "config_fix" or diagnosis.fix_key is None)

    ok = all(checks)
    scoreboard["total"] += 1
    scoreboard["passed"] += int(ok)
    scoreboard["results"].append(
        (case.name, ok, f"classification={diagnosis.classification} confidence={diagnosis.confidence}")
    )

    assert diagnosis.classification in case.acceptable_classifications, (
        f"expected classification in {case.acceptable_classifications!r}, got {diagnosis.classification!r} "
        f"(root_cause={diagnosis.root_cause!r})"
    )
    if case.min_confidence is not None:
        assert diagnosis.confidence >= case.min_confidence
    if case.max_confidence is not None:
        assert diagnosis.confidence <= case.max_confidence
    if case.expect_fix_config:
        assert diagnosis.fix_key is not None and diagnosis.fix_value is not None
