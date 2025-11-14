#!/bin/bash
# Build all Coltec devcontainer base images locally
# Usage: ./scripts/build-all.sh [version]

set -euo pipefail

VERSION=${1:-"1.0.0"}
REGISTRY="ghcr.io/psu3d0/coltec-codespace"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DOCKER_DIR="$(cd "${SCRIPT_DIR}/../docker" && pwd)"

VARIANTS=("base" "base-dind" "base-net" "base-dind-net")
declare -A PARENTS=(
    ["base-dind"]="base"
    ["base-net"]="base"
    ["base-dind-net"]="base-dind"
)

echo "Building Coltec base images v${VERSION}..."
echo "Docker directory: ${DOCKER_DIR}"
echo ""

build_variant() {
    local variant="$1"
    local dockerfile="${DOCKER_DIR}/${variant}/Dockerfile"
    if [[ ! -f "${dockerfile}" ]]; then
        echo "Skipping ${variant}: no Dockerfile at ${dockerfile}"
        return
    fi

    local local_tag="coltec-codespace:${variant}-local"
    local registry_tag="${REGISTRY}:${variant}-v${VERSION}"
    local build_args=()

    if [[ -n "${PARENTS[${variant}]:-}" ]]; then
        local parent_variant="${PARENTS[${variant}]}"
        local parent_tag="coltec-codespace:${parent_variant}-local"
        build_args+=(--build-arg "BASE_IMAGE=${parent_tag}")
    fi

    echo "==> Building ${variant}..."
    docker build \
        -f "${dockerfile}" \
        -t "${registry_tag}" \
        -t "${local_tag}" \
        "${build_args[@]}" \
        "${DOCKER_DIR}"

    echo "✓ ${variant} built: ${registry_tag}"
    echo ""
}

for variant in "${VARIANTS[@]}"; do
    build_variant "${variant}"
done

echo "==> Build Summary"
docker images --filter "reference=${REGISTRY}" --format "table {{.Repository}}:{{.Tag}}\t{{.Size}}\t{{.CreatedAt}}"

echo ""
echo "✅ Build complete! Run './scripts/test-image.sh <variant>' to smoke test an image."
