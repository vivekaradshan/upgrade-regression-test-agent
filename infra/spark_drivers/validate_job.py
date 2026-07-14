"""EMR Serverless driver for the validate_data step.

Locally (Step 10), DataValidator runs in-process inside the orchestrator,
given a SparkSession it doesn't own. On AWS, validation is its own EMR
job (Lambda can't run Spark) - this script is the standalone entry point
EMR invokes via spark-submit, creating its own SparkSession and calling
the exact same, unchanged DataValidator class. Only the invocation
context changes; the comparison logic itself doesn't.

Usage:
  spark-submit validate_job.py \
    --baseline-path s3://.../baseline/output/transactions \
    --target-path s3://.../target/output/transactions \
    --checks-json '[{"name": "row_count_match", ...}, ...]' \
    --output-path s3://.../validate/results.json
"""

from __future__ import annotations

import argparse
import json

import boto3
from pyspark.sql import SparkSession

from src.tools.data_validator import DataValidator


def run(spark: SparkSession, baseline_path: str, target_path: str, checks: list[dict]) -> dict:
    report = DataValidator(spark).validate(baseline_path, target_path, checks)
    return {
        "overall_status": report.overall_status,
        "checks": [
            {"name": c.name, "status": c.status, "details": c.details, "severity": c.severity}
            for c in report.checks
        ],
    }


def write_results_to_s3(results: dict, output_path: str) -> None:
    bucket, key = output_path.replace("s3://", "", 1).split("/", 1)
    boto3.client("s3").put_object(
        Bucket=bucket, Key=key, Body=json.dumps(results, indent=2).encode("utf-8"),
        ContentType="application/json",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run data validation as an EMR job")
    parser.add_argument("--baseline-path", required=True)
    parser.add_argument("--target-path", required=True)
    parser.add_argument("--checks-json", required=True)
    parser.add_argument("--output-path", required=True)
    args = parser.parse_args()

    checks = json.loads(args.checks_json)
    spark = SparkSession.builder.appName("upgrade-agent-validate").getOrCreate()

    try:
        results = run(spark, args.baseline_path, args.target_path, checks)
        write_results_to_s3(results, args.output_path)
        print(f"Validation overall_status={results['overall_status']}")
    finally:
        spark.stop()


if __name__ == "__main__":
    main()
