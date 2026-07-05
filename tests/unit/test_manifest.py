import pytest
import yaml

from src.config.manifest import ManifestLoader, ManifestLoadError, TestManifest

VALID_MANIFEST_YAML = """
test:
  name: spark-3.5-to-4.0-upgrade
  description: Validate Spark 3.5 to 4.0 upgrade for customer transactions pipeline
  owner: data-platform-team

source_control:
  provider: github
  owner: vivekaradshan
  repo: upgrade-regression-test-agent
  baseline:
    branch: main
  target:
    branch_prefix: auto/upgrade-test
    modifications:
      - file: pipeline/config.yaml
        changes:
          - key: spark_version
            from_value: "3.5.4"
            to_value: "4.0.0"
  pr:
    reviewers:
      - alice
      - bob
    auto_create: true
    auto_merge: false

pipeline:
  id: customer-transactions
  entry_script: pipeline/spark_job.py
  spark_config:
    spark.sql.shuffle.partitions: "4"

execution:
  output_base: /tmp/upgrade-agent/output
  baseline_spark_version: "3.5.4"
  target_spark_version: "4.0.0"
  timeouts:
    single_pipeline_seconds: 300

log_analysis:
  known_failure_patterns:
    - pattern: "ArithmeticException.*divide by zero"
      diagnosis: ANSI mode enabled by default in Spark 4.0
      action: config_fix
      auto_fix: true
      fix_config:
        key: spark.sql.ansi.enabled
        value: "false"
  retry:
    max_retries: 2
    backoff_seconds: 5

validation:
  checks:
    - name: row_count_match
      type: exact
      severity: critical
    - name: column_level_diff
      type: tolerance
      severity: warning
      config:
        tolerance: 0.0001

reporting:
  output_dir: reports
  formats:
    - html
    - json

upgrade_strategy:
  type: spark_version_bump
  version_mapping:
    baseline:
      spark: "3.5.4"
      java: "17"
    target:
      spark: "4.0.0"
      java: "17"
  migration_notes:
    - ANSI SQL mode is on by default in Spark 4.0
"""


def test_load_valid_manifest_from_yaml_string():
    raw = yaml.safe_load(VALID_MANIFEST_YAML)
    manifest = ManifestLoader.load_from_dict(raw)

    assert isinstance(manifest, TestManifest)
    assert manifest.test.name == "spark-3.5-to-4.0-upgrade"
    assert manifest.source_control.repo == "upgrade-regression-test-agent"
    assert manifest.pipeline.id == "customer-transactions"
    assert manifest.execution.target_spark_version == "4.0.0"


def test_missing_required_field_raises_clear_error():
    raw = yaml.safe_load(VALID_MANIFEST_YAML)
    del raw["test"]["owner"]

    with pytest.raises(ManifestLoadError) as exc_info:
        ManifestLoader.load_from_dict(raw)

    assert "test.owner" in str(exc_info.value)


def test_missing_top_level_section_raises_clear_error():
    raw = yaml.safe_load(VALID_MANIFEST_YAML)
    del raw["validation"]

    with pytest.raises(ManifestLoadError) as exc_info:
        ManifestLoader.load_from_dict(raw)

    assert "validation" in str(exc_info.value)


def test_known_failure_patterns_parse_correctly():
    raw = yaml.safe_load(VALID_MANIFEST_YAML)
    manifest = ManifestLoader.load_from_dict(raw)

    patterns = manifest.log_analysis.known_failure_patterns
    assert len(patterns) == 1

    pattern = patterns[0]
    assert pattern.pattern == "ArithmeticException.*divide by zero"
    assert pattern.action == "config_fix"
    assert pattern.auto_fix is True
    assert pattern.fix_config is not None
    assert pattern.fix_config.key == "spark.sql.ansi.enabled"
    assert pattern.fix_config.value == "false"


def test_auto_merge_true_is_rejected():
    raw = yaml.safe_load(VALID_MANIFEST_YAML)
    raw["source_control"]["pr"]["auto_merge"] = True

    with pytest.raises(ManifestLoadError):
        ManifestLoader.load_from_dict(raw)


def test_load_from_file_missing_path_raises_clear_error():
    with pytest.raises(ManifestLoadError) as exc_info:
        ManifestLoader.load_from_file("/nonexistent/manifest.yaml")

    assert "not found" in str(exc_info.value).lower()
