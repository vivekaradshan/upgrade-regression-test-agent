#!/usr/bin/env bash
# Packages src/tools/data_validator.py (and the minimal package structure
# needed to import it as `src.tools.data_validator`, matching the exact
# import style used everywhere else in this codebase) as a spark-submit
# --py-files zip for the validate EMR job. No PyYAML needed here - unlike
# spark_job.py, data_validator.py has zero dependencies beyond pyspark
# and the stdlib (dataclasses, typing) - confirmed by reading its imports
# before writing this script.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/../build"
BUILD_TMP="$(mktemp -d)"

mkdir -p "$OUTPUT_DIR" "$BUILD_TMP/src/tools"

cp "$PROJECT_ROOT/src/__init__.py" "$BUILD_TMP/src/__init__.py"
cp "$PROJECT_ROOT/src/tools/__init__.py" "$BUILD_TMP/src/tools/__init__.py"
cp "$PROJECT_ROOT/src/tools/data_validator.py" "$BUILD_TMP/src/tools/data_validator.py"

(cd "$BUILD_TMP" && zip -r "$OUTPUT_DIR/validator_deps.zip" src -x "*.pyc" > /dev/null)
rm -rf "$BUILD_TMP"

echo "Built $OUTPUT_DIR/validator_deps.zip"
