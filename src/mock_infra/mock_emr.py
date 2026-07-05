"""Runs the pipeline-under-test locally as a subprocess, standing in for EMR."""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import yaml


class LocalSparkRunner:
    def __init__(self, python_executable: str, spark4_libs_path: str | None = None):
        self.python_executable = python_executable
        self.spark4_libs_path = spark4_libs_path

    def run_spark_job(
        self,
        entry_script: str,
        input_path: str,
        output_path: str,
        spark_config: dict[str, str],
        spark_version: str,
        log_dir: str,
        cwd: str,
        timeout_seconds: int = 300,
    ) -> dict:
        effective_config = dict(spark_config)
        if spark_version.startswith("4.") and "spark.sql.ansi.enabled" not in effective_config:
            effective_config["spark.sql.ansi.enabled"] = "true"

        config_path = self._write_temp_config(spark_version, effective_config, log_dir)
        env = self._build_env(spark_version)

        Path(log_dir).mkdir(parents=True, exist_ok=True)
        log_path = str(Path(log_dir) / f"{Path(entry_script).stem}-{uuid.uuid4().hex[:8]}.log")

        cmd = [
            self.python_executable,
            entry_script,
            "--input",
            input_path,
            "--output",
            output_path,
            "--config",
            config_path,
        ]

        with open(log_path, "w") as log_file:
            try:
                result = subprocess.run(
                    cmd,
                    cwd=cwd,
                    env=env,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    timeout=timeout_seconds,
                )
                exit_code = result.returncode
            except subprocess.TimeoutExpired:
                log_file.write("\n[LocalSparkRunner] job timed out\n")
                exit_code = -1

        status = "SUCCEEDED" if exit_code == 0 else "FAILED"
        return {"status": status, "log_path": log_path, "exit_code": exit_code}

    def _write_temp_config(
        self, spark_version: str, spark_config: dict[str, str], log_dir: str
    ) -> str:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        config_path = str(Path(log_dir) / f"spark-config-{uuid.uuid4().hex[:8]}.yaml")
        with open(config_path, "w") as f:
            yaml.safe_dump({"spark_version": spark_version, "spark_config": spark_config}, f)
        return config_path

    def _build_env(self, spark_version: str) -> dict[str, str]:
        env = os.environ.copy()
        # Spark 4.0.0 is installed in an isolated --target directory (not the
        # venv's site-packages, which holds 3.5.4) since only one pyspark
        # version can live in a normal site-packages at a time. Prepending it
        # to PYTHONPATH makes the subprocess's `import pyspark` resolve to
        # 4.0.0 instead of the venv's 3.5.4, without touching the venv itself.
        if spark_version.startswith("4.") and self.spark4_libs_path:
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = (
                f"{self.spark4_libs_path}{os.pathsep}{existing}" if existing else self.spark4_libs_path
            )
        return env
