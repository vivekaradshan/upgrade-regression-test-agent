import os

from dotenv import load_dotenv

load_dotenv()

# src/aws_lambda/*.py handlers read these at module import time (correct
# for Lambda's execution model - avoids re-reading on every invocation
# within a warm container), which means they must exist before pytest
# imports those modules. conftest.py loads before test collection, so
# setting dummy values here (never used for real - actual values come
# from Terraform-configured Lambda environment variables) satisfies the
# import-time read without needing real AWS resource identifiers in tests.
for _var, _dummy in {
    "GITHUB_TOKEN_SECRET_ID": "test/github-token",
    "OPENAI_API_KEY_SECRET_ID": "test/openai-api-key",
    "BASELINE_APPLICATION_ID": "test-baseline-app-id",
    "TARGET_APPLICATION_ID": "test-target-app-id",
    "EMR_EXECUTION_ROLE_ARN": "arn:aws:iam::123456789012:role/test-emr-execution",
    "ARTIFACTS_BUCKET": "test-artifacts-bucket",
    "REPORTS_BUCKET": "test-reports-bucket",
    "PYYAML_PYFILES_S3_URI": "s3://test-artifacts-bucket/dependencies/pyyaml.zip",
    "VALIDATOR_PYFILES_S3_URI": "s3://test-artifacts-bucket/dependencies/validator_deps.zip",
}.items():
    os.environ.setdefault(_var, _dummy)
