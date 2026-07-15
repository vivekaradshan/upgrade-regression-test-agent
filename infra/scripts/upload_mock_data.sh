#!/usr/bin/env bash
# Generates the shared mock transaction dataset once (deterministic, fixed
# seed - identical for every run/branch, no per-run generation needed) and
# uploads it to a fixed S3 location every job run references directly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PIPELINE_DIR="$PROJECT_ROOT/workspace/customer-transactions-pipeline"
ARTIFACTS_BUCKET="${ARTIFACTS_BUCKET:?Set ARTIFACTS_BUCKET, e.g. upgrade-agent-artifacts-<account-id>}"

if [ ! -d "$PIPELINE_DIR" ]; then
  echo "ERROR: $PIPELINE_DIR not found - clone customer-transactions-pipeline there first" >&2
  exit 1
fi

TMP_CSV="$(mktemp -d)/transactions.csv"
python3 "$PIPELINE_DIR/pipeline/generate_mock_data.py" --output "$TMP_CSV"

aws s3 cp "$TMP_CSV" "s3://$ARTIFACTS_BUCKET/shared/data/transactions.csv"

echo "Uploaded to s3://$ARTIFACTS_BUCKET/shared/data/transactions.csv"
