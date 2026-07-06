import pytest
from pyspark.sql import Row, SparkSession

from src.tools.data_validator import DataValidator

CHECKS = [
    {"name": "row_count_match", "severity": "critical"},
    {"name": "schema_match", "severity": "critical"},
    {"name": "column_level_diff", "severity": "warning", "config": {"tolerance": 0.0001}},
    {"name": "null_count_diff", "severity": "warning"},
]


@pytest.fixture(scope="module")
def spark():
    session = SparkSession.builder.appName("test-data-validator").master("local[2]").getOrCreate()
    yield session
    session.stop()


def _write_parquet(spark, rows, path):
    spark.createDataFrame(rows).write.mode("overwrite").parquet(path)


def test_identical_outputs_pass_all_checks(spark, tmp_path):
    rows = [Row(id=1, amount=10.0), Row(id=2, amount=20.0)]
    baseline_path = str(tmp_path / "baseline")
    target_path = str(tmp_path / "target")
    _write_parquet(spark, rows, baseline_path)
    _write_parquet(spark, rows, target_path)

    report = DataValidator(spark).validate(baseline_path, target_path, CHECKS)

    assert report.overall_status == "PASSED"
    assert all(c.status == "PASSED" for c in report.checks)


def test_different_row_counts_fails_critical_check(spark, tmp_path):
    baseline_path = str(tmp_path / "baseline")
    target_path = str(tmp_path / "target")
    _write_parquet(spark, [Row(id=1, amount=10.0), Row(id=2, amount=20.0)], baseline_path)
    _write_parquet(spark, [Row(id=1, amount=10.0)], target_path)

    report = DataValidator(spark).validate(baseline_path, target_path, CHECKS)

    assert report.overall_status == "FAILED"
    row_count_check = next(c for c in report.checks if c.name == "row_count_match")
    assert row_count_check.status == "FAILED"


def test_schema_mismatch_fails_critical_check(spark, tmp_path):
    baseline_path = str(tmp_path / "baseline")
    target_path = str(tmp_path / "target")
    _write_parquet(spark, [Row(id=1, amount=10.0)], baseline_path)
    _write_parquet(spark, [Row(id=1, amount="10.0")], target_path)

    report = DataValidator(spark).validate(baseline_path, target_path, CHECKS)

    assert report.overall_status == "FAILED"
    schema_check = next(c for c in report.checks if c.name == "schema_match")
    assert schema_check.status == "FAILED"


def test_small_numeric_diff_within_tolerance_passes_with_warning_severity(spark, tmp_path):
    baseline_path = str(tmp_path / "baseline")
    target_path = str(tmp_path / "target")
    _write_parquet(spark, [Row(id=1, amount=1000.0)], baseline_path)
    # relative diff = 0.00001, well under the 0.0001 tolerance
    _write_parquet(spark, [Row(id=1, amount=1000.01)], target_path)

    report = DataValidator(spark).validate(baseline_path, target_path, CHECKS)

    column_diff_check = next(c for c in report.checks if c.name == "column_level_diff")
    assert column_diff_check.status == "PASSED"
    assert report.overall_status == "PASSED"


def test_large_numeric_diff_beyond_tolerance_gives_warning_not_failure(spark, tmp_path):
    baseline_path = str(tmp_path / "baseline")
    target_path = str(tmp_path / "target")
    _write_parquet(spark, [Row(id=1, amount=1000.0)], baseline_path)
    _write_parquet(spark, [Row(id=1, amount=2000.0)], target_path)

    report = DataValidator(spark).validate(baseline_path, target_path, CHECKS)

    column_diff_check = next(c for c in report.checks if c.name == "column_level_diff")
    assert column_diff_check.status == "WARNING"
    # column_level_diff severity is "warning" in the manifest, not "critical",
    # so it downgrades overall_status to WARNING rather than FAILED
    assert report.overall_status == "WARNING"


def test_null_count_diff_detected(spark, tmp_path):
    baseline_path = str(tmp_path / "baseline")
    target_path = str(tmp_path / "target")
    _write_parquet(spark, [Row(id=1, amount=10.0), Row(id=2, amount=None)], baseline_path)
    _write_parquet(spark, [Row(id=1, amount=10.0), Row(id=2, amount=20.0)], target_path)

    report = DataValidator(spark).validate(baseline_path, target_path, CHECKS)

    null_check = next(c for c in report.checks if c.name == "null_count_diff")
    assert null_check.status == "WARNING"
