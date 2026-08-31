#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$(mktemp -d /tmp/toyoko-lambda-package.XXXXXX)"
UV_CACHE_DIR="$(mktemp -d /tmp/toyoko-uv-cache.XXXXXX)"
OUTPUT_FILE="${PROJECT_ROOT}/aws/toyoko-lambda.zip"

cleanup() {
  rm -rf "${BUILD_DIR}" "${UV_CACHE_DIR}"
}
trap cleanup EXIT

UV_CACHE_DIR="${UV_CACHE_DIR}" \
UV_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple" \
  "${HOME}/.local/bin/uv" pip install \
    --target "${BUILD_DIR}" \
    --python-version 3.12 \
    --python-platform aarch64-manylinux2014 \
    --only-binary :all: \
    'requests>=2.32.0,<3.0.0'

cp "${PROJECT_ROOT}/lambda_handler.py" "${PROJECT_ROOT}/toyoko_monitor.py" "${BUILD_DIR}/"
rm -f "${BUILD_DIR}/.lock"

rm -f "${OUTPUT_FILE}"
(
  cd "${BUILD_DIR}"
  zip -qr "${OUTPUT_FILE}" .
)

echo "Built ${OUTPUT_FILE}"
shasum -a 256 "${OUTPUT_FILE}"
