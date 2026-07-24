#!/usr/bin/env bash
# Builds the shared Lambda Layer: this project's src/ package plus its
# runtime dependencies (infra/lambda-requirements.txt - deliberately not
# the full dev requirements.txt, which includes pytest/moto/pyspark that
# have no business in a Lambda function).
#
# Dependencies are downloaded as prebuilt manylinux (Linux) wheels via
# pip's --platform flag, not compiled locally - this works from macOS
# without Docker as long as PyPI has a matching prebuilt wheel for each
# package (verified: pydantic's Rust extension, pyyaml's C extension, and
# markupsafe's C extension all have manylinux x86_64 wheels available).
# Confirmed via `find -iname "*.so"` that every compiled extension in the
# build output is tagged x86_64-linux-gnu, not macOS.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/../build"
LAYER_DIR="$OUTPUT_DIR/lambda-layer"
PYTHON_DIR="$LAYER_DIR/python"

rm -rf "$LAYER_DIR"
mkdir -p "$PYTHON_DIR"

python3 -m pip install \
  --platform manylinux2014_x86_64 \
  --python-version 3.12 \
  --implementation cp \
  --only-binary=:all: \
  --target "$PYTHON_DIR" \
  -r "$SCRIPT_DIR/../lambda-requirements.txt"

if find "$PYTHON_DIR" -iname "*.so" | grep -v "linux" > /dev/null 2>&1; then
  echo "ERROR: found a non-Linux compiled extension - layer would not run on Lambda" >&2
  find "$PYTHON_DIR" -iname "*.so" | grep -v "linux" >&2
  exit 1
fi

cp -R "$PROJECT_ROOT/src" "$PYTHON_DIR/src"
find "$PYTHON_DIR/src" -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

(cd "$LAYER_DIR" && zip -r "$OUTPUT_DIR/lambda_layer.zip" python -x "*.pyc" > /dev/null)

echo "Built $OUTPUT_DIR/lambda_layer.zip"
du -h "$OUTPUT_DIR/lambda_layer.zip"
