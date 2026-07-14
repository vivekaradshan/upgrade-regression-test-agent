#!/usr/bin/env bash
# Builds a pure-Python (no compiled C extension) PyYAML package as a
# spark-submit --py-files zip, needed because EMR Serverless's default
# Spark Python environment doesn't include PyYAML - confirmed by an
# actual failed job run during Phase 14.2 verification
# (ModuleNotFoundError: No module named 'yaml'). Must be pure-Python since
# a compiled extension built on macOS/ARM wouldn't run on EMR's Linux
# x86_64 runtime; --no-binary forces a source build, which produces pure
# Python output here as long as libyaml dev headers aren't present at
# build time (verified: no .so files in the output).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTPUT_DIR="$SCRIPT_DIR/../build"
BUILD_TMP="$(mktemp -d)"

mkdir -p "$OUTPUT_DIR"

pip install --no-binary :all: --target "$BUILD_TMP" pyyaml

if find "$BUILD_TMP" -iname "*.so" | grep -q .; then
  echo "ERROR: compiled .so file found - this build is not portable to EMR's Linux runtime" >&2
  find "$BUILD_TMP" -iname "*.so" >&2
  exit 1
fi

rm -rf "$BUILD_TMP"/*.dist-info "$BUILD_TMP"/yaml/__pycache__ "$BUILD_TMP"/_yaml/__pycache__

(cd "$BUILD_TMP" && zip -r "$OUTPUT_DIR/pyyaml.zip" yaml _yaml -x "*.pyc" > /dev/null)
rm -rf "$BUILD_TMP"

echo "Built $OUTPUT_DIR/pyyaml.zip"
