#!/bin/bash
# Test a Coltec devcontainer image
# Usage: ./scripts/test-image.sh <variant> [version]

set -euo pipefail

VARIANT=${1:-"base"}
VERSION=${2:-"1.0.0"}
REGISTRY="ghcr.io/coltec/codespace"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGES_DIR="$(cd "${SCRIPT_DIR}/../images" && pwd)"

IMAGE_TAG="${REGISTRY}:${VARIANT}-v${VERSION}"
TEST_SCRIPT="${IMAGES_DIR}/${VARIANT}/test.sh"

if [ ! -f "${TEST_SCRIPT}" ]; then
    echo "Error: Test script not found for variant '${VARIANT}'"
    echo "Expected: ${TEST_SCRIPT}"
    exit 1
fi

echo "Testing image: ${IMAGE_TAG}"
echo "Running test script: ${TEST_SCRIPT}"
echo ""

# Run the test script inside the container
docker run --rm \
    -v "${TEST_SCRIPT}:/test.sh:ro" \
    "${IMAGE_TAG}" \
    /bin/bash /test.sh

echo ""
echo "✅ Tests passed for ${VARIANT} image!"
