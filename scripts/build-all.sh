#!/bin/bash
# Build all Coltec devcontainer images locally
# Usage: ./scripts/build-all.sh [version]

set -euo pipefail

VERSION=${1:-"1.0.0"}
REGISTRY="ghcr.io/coltec/codespace"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGES_DIR="$(cd "${SCRIPT_DIR}/../images" && pwd)"

echo "Building Coltec devcontainer images v${VERSION}..."
echo "Images directory: ${IMAGES_DIR}"
echo ""

# Build base image first
echo "==> Building base image..."
docker build \
    -t "${REGISTRY}:base-v${VERSION}" \
    -t "coltec-codespace:base-local" \
    "${IMAGES_DIR}/base"

echo "✓ Base image built: ${REGISTRY}:base-v${VERSION}"
echo ""

# Build specialized images (they extend the base)
# For local builds, we'll use the local tag instead of pulling from registry
SPECIALIZED_IMAGES=("rust" "python" "node" "monorepo")

for image in "${SPECIALIZED_IMAGES[@]}"; do
    if [ ! -d "${IMAGES_DIR}/${image}" ]; then
        echo "==> Skipping ${image} (no sources yet)"
        echo ""
        continue
    fi

    echo "==> Building ${image} image..."
    
    # Create a temporary Dockerfile that uses the local base image
    TMP_DOCKERFILE="${IMAGES_DIR}/${image}/Dockerfile.local"
    sed "s|FROM ghcr.io/coltec/codespace:base-v.*|FROM coltec-codespace:base-local|" \
        "${IMAGES_DIR}/${image}/Dockerfile" > "${TMP_DOCKERFILE}"
    
    docker build \
        -t "${REGISTRY}:${image}-v${VERSION}" \
        -t "coltec-codespace:${image}-local" \
        -f "${TMP_DOCKERFILE}" \
        "${IMAGES_DIR}/${image}"
    
    rm "${TMP_DOCKERFILE}"
    echo "✓ ${image} image built: ${REGISTRY}:${image}-v${VERSION}"
    echo ""
done

echo "==> Build Summary"
echo "All images built successfully:"
docker images --filter "reference=${REGISTRY}" --format "table {{.Repository}}:{{.Tag}}\t{{.Size}}\t{{.CreatedAt}}"

echo ""
echo "✅ Build complete! Run './scripts/test-image.sh <variant>' to test an image."
