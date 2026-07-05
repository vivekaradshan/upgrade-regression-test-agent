"""Applies auto-fixable corrective actions identified by pattern matching or the LLM."""

from __future__ import annotations

from pathlib import Path

import yaml


class CorrectiveActionApplier:
    @staticmethod
    def apply_config_fix(config_path: str, key: str, value: str) -> bool:
        path = Path(config_path)
        config = yaml.safe_load(path.read_text()) if path.exists() else {}

        spark_config = config.setdefault("spark_config", {})
        spark_config[key] = value

        path.write_text(yaml.safe_dump(config, sort_keys=False))
        return True

    @staticmethod
    def apply_to_spark_config(spark_config: dict[str, str], key: str, value: str) -> dict[str, str]:
        updated = dict(spark_config)
        updated[key] = value
        return updated
