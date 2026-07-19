"""Applying a {key, value} config fix to a target branch's config file.

Extracted from analyze_node.py so both the ordinary auto-fix path
(pattern-matcher fixes, applied immediately) and the human-approved LLM
fix path (src/aws_lambda/approve_run_handler.py, applied only after a
human clicks Approve) can commit a fix the exact same way - one code
path for "a structured {key, value} fix lands on the target branch",
regardless of whether a regex or a human approving an LLM's suggestion
authorized it.
"""

from __future__ import annotations

import yaml

from src.config.manifest import FileModification
from src.tools.github_client import GitHubClient


def find_spark_config_file(
    github_client: GitHubClient, branch: str, modifications: list[FileModification]
) -> str:
    """Locates the manifest-declared file that actually holds a spark_config
    block, instead of assuming it's always modifications[0] - a manifest can
    list multiple files to modify (e.g. a version bump in one file, an
    unrelated change in another), and the fix needs to land in the one a
    Spark job actually reads its config from."""
    for modification in modifications:
        if not modification.file.endswith((".yaml", ".yml")):
            continue
        content, _ = github_client.get_file_content(modification.file, branch)
        try:
            parsed = yaml.safe_load(content)
        except yaml.YAMLError:
            continue
        if isinstance(parsed, dict) and "spark_config" in parsed:
            return modification.file

    # Fall back to the first modification rather than raising, so a manifest
    # that hasn't adopted the spark_config convention yet still behaves as
    # it did before this fix.
    return modifications[0].file


def apply_fix_to_target_branch(
    github_client: GitHubClient, branch: str, config_file_path: str, key: str, value: str, commit_message: str
) -> None:
    content, sha = github_client.get_file_content(config_file_path, branch)
    config = yaml.safe_load(content)
    config.setdefault("spark_config", {})[key] = value

    github_client.update_file(
        path=config_file_path,
        branch=branch,
        content=yaml.safe_dump(config, sort_keys=False),
        sha=sha,
        message=commit_message,
    )
