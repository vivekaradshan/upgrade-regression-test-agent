from src.config.manifest import ManifestLoader, TestManifest

MANIFEST_PATH = "manifests/spark-3.5-to-4.0.yaml"


def test_spark_manifest_loads_and_validates():
    manifest = ManifestLoader.load_from_file(MANIFEST_PATH)

    assert isinstance(manifest, TestManifest)
    assert manifest.execution.baseline_spark_version == "3.5.4"
    assert manifest.execution.target_spark_version == "4.0.0"


def test_spark_manifest_known_failure_patterns():
    manifest = ManifestLoader.load_from_file(MANIFEST_PATH)
    patterns = manifest.log_analysis.known_failure_patterns

    assert len(patterns) == 4

    auto_fixable = [p for p in patterns if p.auto_fix]
    escalations = [p for p in patterns if not p.auto_fix]
    assert len(auto_fixable) == 3
    assert len(escalations) == 1
    assert escalations[0].action == "escalate"
    assert "javax.servlet" in escalations[0].pattern


def test_spark_manifest_validation_checks():
    manifest = ManifestLoader.load_from_file(MANIFEST_PATH)
    check_names = {c.name for c in manifest.validation.checks}

    assert check_names == {"row_count_match", "schema_match", "column_level_diff"}


def test_spark_manifest_pr_never_auto_merges():
    manifest = ManifestLoader.load_from_file(MANIFEST_PATH)

    assert manifest.source_control.pr.auto_merge is False
