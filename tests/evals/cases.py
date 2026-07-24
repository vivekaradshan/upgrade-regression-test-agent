"""Golden cases for the LLMAnalyzer eval harness.

Each case pairs a real (or realistic) failure log with the diagnosis a
correct analysis should produce. Only the fields that matter for a given
case are asserted - e.g. an escalate case doesn't care what fix_key the
model dreamed up, only that it didn't confidently invent a config fix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

FIXTURES_DIR = "tests/evals/fixtures"

UPGRADE_CONTEXT = {"baseline_spark_version": "3.5.4", "target_spark_version": "4.0.0"}

MIGRATION_NOTES = [
    "ANSI SQL mode is ON by default in 4.0 - division by zero, overflow, "
    "and invalid casts now throw exceptions instead of returning NULL",
    "spark.sql.parquet.compression.codec no longer accepts 'lz4raw' - use 'lz4_raw'",
    "Default shuffle service backend changed from LevelDB to RocksDB",
    "CREATE TABLE without USING clause now defaults to spark.sql.sources.default instead of Hive",
]


@dataclass
class EvalCase:
    name: str
    log_path: str
    # A set of classifications, not just one - the manifest's own
    # known_failure_patterns policy (e.g. "servlet migration => escalate,
    # never auto_fix") reflects a human choice about what to do with a
    # diagnosis, not an objective property of the raw log the LLM sees.
    # Two different classifications can both be a defensible read of the
    # same log; accept any of them rather than pinning to one.
    acceptable_classifications: tuple[str, ...]
    # None means "don't check confidence direction for this case"
    min_confidence: Optional[float] = None
    max_confidence: Optional[float] = None
    expect_fix_config: bool = False

    @property
    def expected_classification(self) -> str:
        """First acceptable classification - used only for failure messages."""
        return self.acceptable_classifications[0]


CASES = [
    EvalCase(
        name="ansi_divide_by_zero",
        log_path="tests/fixtures/spark_ansi_error.log",
        acceptable_classifications=("config_fix",),
        min_confidence=0.7,
        expect_fix_config=True,
    ),
    EvalCase(
        name="evidence_scarce_clean_shutdown",
        log_path=f"{FIXTURES_DIR}/evidence_scarce_clean_shutdown.log",
        acceptable_classifications=("escalate",),
        max_confidence=0.3,
        expect_fix_config=False,
    ),
    EvalCase(
        name="servlet_package_migration",
        log_path=f"{FIXTURES_DIR}/servlet_package_migration.log",
        # The manifest's own known_failure_patterns entry for this error
        # says "escalate, never auto_fix" - but that's a policy choice,
        # not something derivable from the raw log alone. "dependency_fix"
        # is an equally defensible read of a NoClassDefFoundError; what
        # actually matters is that the model doesn't invent a config_fix.
        acceptable_classifications=("escalate", "dependency_fix"),
        expect_fix_config=False,
    ),
    EvalCase(
        name="user_code_key_error",
        log_path=f"{FIXTURES_DIR}/user_code_key_error.log",
        acceptable_classifications=("code_change",),
        expect_fix_config=False,
    ),
]
