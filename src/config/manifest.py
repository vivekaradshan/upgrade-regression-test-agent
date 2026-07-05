"""Pydantic models for the upgrade test manifest YAML schema."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError


# Identifying metadata for the test run: what it's called and who owns it.
class TestInfo(BaseModel):
    name: str
    description: str
    owner: str


# Names the git branch a piece of source_control config points at.
class BranchConfig(BaseModel):
    branch: str


# A single key's value change to apply to a file (e.g. spark_version 3.5.4 -> 4.0.0).
class FileChange(BaseModel):
    key: str
    from_value: str
    to_value: str


# One file and all the key changes to apply to it on the target branch.
class FileModification(BaseModel):
    file: str
    changes: list[FileChange]


# Describes the branch the agent creates for the upgraded pipeline and the
# code edits (modifications) it applies there before running the target job.
class TargetBranchConfig(BaseModel):
    branch_prefix: str
    modifications: list[FileModification] = Field(default_factory=list)


# Controls how the agent raises its results PR. auto_merge is pinned to False
# so the agent can never merge its own upgrade test PR unattended.
class PullRequestConfig(BaseModel):
    reviewers: list[str] = Field(default_factory=list)
    auto_create: bool = True
    auto_merge: Literal[False] = False


# Everything the agent needs to talk to the repo: which provider/repo, which
# branch is the baseline, how to build the target branch, and PR behavior.
class SourceControlConfig(BaseModel):
    provider: Literal["github"] = "github"
    owner: str
    repo: str
    baseline: BranchConfig
    target: TargetBranchConfig
    pr: PullRequestConfig


# Identifies the pipeline under test: its id, entry script to run, and any
# Spark configs to pass in (separate from the baseline/target version itself).
class PipelineConfig(BaseModel):
    id: str
    entry_script: str
    spark_config: dict[str, str] = Field(default_factory=dict)


# How long a single pipeline run is allowed to take before it's considered hung.
class TimeoutConfig(BaseModel):
    single_pipeline_seconds: int


# Where to run and which Spark versions to compare: baseline (old) vs. target (new).
class ExecutionConfig(BaseModel):
    output_base: str
    baseline_spark_version: str
    target_spark_version: str
    timeouts: TimeoutConfig


# The specific config key/value to set when auto-applying a corrective fix.
class FixConfig(BaseModel):
    key: str
    value: str


# One pre-seeded Spark 4.0 breaking-change signature: a regex to match against
# logs, a human-readable diagnosis, what kind of fix it needs, and whether the
# agent may apply that fix automatically without escalating to a human.
class KnownFailurePattern(BaseModel):
    pattern: str
    diagnosis: str
    action: Literal["config_fix", "code_change", "escalate"]
    auto_fix: bool
    fix_config: FixConfig | None = None


# How many times to retry after an auto-fix, and how long to wait between tries.
class RetryConfig(BaseModel):
    max_retries: int
    backoff_seconds: int


# All the failure-detection knowledge the agent checks before ever calling an LLM.
class LogAnalysisConfig(BaseModel):
    known_failure_patterns: list[KnownFailurePattern] = Field(default_factory=list)
    retry: RetryConfig


# One data-parity check to run against baseline vs. target output (e.g. row
# counts must match exactly, or numeric columns must match within a tolerance).
class ValidationCheck(BaseModel):
    name: str
    type: Literal["exact", "tolerance"]
    severity: Literal["critical", "warning", "info"]
    config: dict[str, Any] = Field(default_factory=dict)


# The full set of data validation checks to run after both jobs succeed.
class ValidationConfig(BaseModel):
    checks: list[ValidationCheck] = Field(default_factory=list)


# Where and in what formats (html/json) to write the final test report.
class ReportingConfig(BaseModel):
    output_dir: str
    formats: list[str] = Field(default_factory=list)


# The full baseline vs. target dependency versions (Spark, Java, Python, ...)
# used for display in the report and as context passed to the LLM analyzer.
class VersionMapping(BaseModel):
    baseline: dict[str, str]
    target: dict[str, str]


# High-level description of the upgrade being tested, plus human-authored
# migration notes that get fed to the LLM as background when it diagnoses an
# unrecognized failure.
class UpgradeStrategyConfig(BaseModel):
    type: str
    version_mapping: VersionMapping
    migration_notes: list[str] = Field(default_factory=list)


# The root manifest model: one field per top-level YAML section. Loading this
# validates the entire test definition in one pass.
class TestManifest(BaseModel):
    test: TestInfo
    source_control: SourceControlConfig
    pipeline: PipelineConfig
    execution: ExecutionConfig
    log_analysis: LogAnalysisConfig
    validation: ValidationConfig
    reporting: ReportingConfig
    upgrade_strategy: UpgradeStrategyConfig


class ManifestLoadError(Exception):
    """Raised when a manifest file fails to load or validate."""


class ManifestLoader:
    @staticmethod
    def load_from_file(path: str) -> TestManifest:
        file_path = Path(path)
        if not file_path.exists():
            raise ManifestLoadError(f"Manifest file not found: {path}")

        with file_path.open("r") as f:
            raw = yaml.safe_load(f)

        return ManifestLoader.load_from_dict(raw)

    @staticmethod
    def load_from_dict(raw: dict[str, Any]) -> TestManifest:
        try:
            return TestManifest.model_validate(raw)
        except ValidationError as e:
            errors = "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                for err in e.errors()
            )
            raise ManifestLoadError(f"Invalid manifest: {errors}") from e
