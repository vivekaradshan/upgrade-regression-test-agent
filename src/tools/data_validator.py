"""Compares baseline vs target Parquet output for data-parity regression checks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

NUMERIC_TYPE_MARKERS = ("DoubleType", "FloatType", "LongType", "IntegerType", "DecimalType")


@dataclass
class CheckResult:
    name: str
    status: str  # PASSED | FAILED | WARNING
    details: str
    severity: str  # critical | warning | info


@dataclass
class ValidationReport:
    overall_status: str  # PASSED | FAILED | WARNING
    checks: list[CheckResult] = field(default_factory=list)


class DataValidator:
    def __init__(self, spark: SparkSession):
        self._spark = spark

    def validate(
        self, baseline_path: str, target_path: str, checks: list[dict[str, Any]]
    ) -> ValidationReport:
        baseline_df = self._spark.read.parquet(baseline_path)
        target_df = self._spark.read.parquet(target_path)

        results = [self._run_check(check, baseline_df, target_df) for check in checks]
        return ValidationReport(
            overall_status=self._compute_overall_status(results), checks=results
        )

    def _run_check(self, check: dict[str, Any], baseline_df: DataFrame, target_df: DataFrame) -> CheckResult:
        name = check["name"]
        severity = check.get("severity", "warning")

        if name == "row_count_match":
            return self._check_row_count(baseline_df, target_df, severity)
        if name == "schema_match":
            return self._check_schema(baseline_df, target_df, severity)
        if name == "column_level_diff":
            tolerance = check.get("config", {}).get("tolerance", 0.0001)
            return self._check_column_diff(baseline_df, target_df, severity, tolerance)
        if name == "null_count_diff":
            return self._check_null_counts(baseline_df, target_df, severity)

        return CheckResult(name=name, status="FAILED", details=f"Unknown check type: {name}", severity=severity)

    def _check_row_count(self, baseline_df: DataFrame, target_df: DataFrame, severity: str) -> CheckResult:
        baseline_count = baseline_df.count()
        target_count = target_df.count()

        if baseline_count == target_count:
            return CheckResult("row_count_match", "PASSED", f"{baseline_count} rows in both", severity)
        return CheckResult(
            "row_count_match",
            "FAILED",
            f"baseline={baseline_count} target={target_count}",
            severity,
        )

    def _check_schema(self, baseline_df: DataFrame, target_df: DataFrame, severity: str) -> CheckResult:
        baseline_schema = {(f.name, str(f.dataType)) for f in baseline_df.schema.fields}
        target_schema = {(f.name, str(f.dataType)) for f in target_df.schema.fields}

        if baseline_schema == target_schema:
            return CheckResult("schema_match", "PASSED", "schemas identical", severity)

        missing = baseline_schema - target_schema
        added = target_schema - baseline_schema
        return CheckResult(
            "schema_match", "FAILED", f"missing_in_target={missing} added_in_target={added}", severity
        )

    def _check_column_diff(
        self, baseline_df: DataFrame, target_df: DataFrame, severity: str, tolerance: float
    ) -> CheckResult:
        numeric_columns = [
            f.name
            for f in baseline_df.schema.fields
            if any(marker in str(f.dataType) for marker in NUMERIC_TYPE_MARKERS)
            and f.name in target_df.columns
        ]

        if not numeric_columns:
            return CheckResult("column_level_diff", "PASSED", "no numeric columns to compare", severity)

        # One agg() covering every column, not one agg().collect() per
        # column: each .collect() is a separate full-table Spark action, so
        # N columns previously meant 2N table scans (N on baseline, N on
        # target) instead of just 2.
        baseline_sums = baseline_df.agg(*[F.sum(c) for c in numeric_columns]).collect()[0]
        target_sums = target_df.agg(*[F.sum(c) for c in numeric_columns]).collect()[0]

        diffs = {}
        for i, column in enumerate(numeric_columns):
            baseline_sum = baseline_sums[i] or 0.0
            target_sum = target_sums[i] or 0.0

            if baseline_sum == 0:
                relative_diff = 0.0 if target_sum == 0 else float("inf")
            else:
                relative_diff = abs(target_sum - baseline_sum) / abs(baseline_sum)

            if relative_diff > tolerance:
                diffs[column] = {
                    "baseline_sum": baseline_sum,
                    "target_sum": target_sum,
                    "relative_diff": relative_diff,
                }

        if not diffs:
            return CheckResult("column_level_diff", "PASSED", "all numeric columns within tolerance", severity)

        status = "FAILED" if severity == "critical" else "WARNING"
        return CheckResult("column_level_diff", status, f"columns differ beyond tolerance: {diffs}", severity)

    def _check_null_counts(self, baseline_df: DataFrame, target_df: DataFrame, severity: str) -> CheckResult:
        shared_columns = [c for c in baseline_df.columns if c in target_df.columns]

        diffs = {}
        for column in shared_columns:
            baseline_nulls = baseline_df.filter(F.col(column).isNull()).count()
            target_nulls = target_df.filter(F.col(column).isNull()).count()
            if baseline_nulls != target_nulls:
                diffs[column] = {"baseline_nulls": baseline_nulls, "target_nulls": target_nulls}

        if not diffs:
            return CheckResult("null_count_diff", "PASSED", "null counts match", severity)

        status = "FAILED" if severity == "critical" else "WARNING"
        return CheckResult("null_count_diff", status, f"null count differences: {diffs}", severity)

    def _compute_overall_status(self, results: list[CheckResult]) -> str:
        if any(r.status == "FAILED" and r.severity == "critical" for r in results):
            return "FAILED"
        if any(r.status in ("FAILED", "WARNING") for r in results):
            return "WARNING"
        return "PASSED"
