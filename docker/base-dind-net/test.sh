#!/bin/bash
set -euo pipefail

echo "Testing base-dind-net image extras..."

echo -n "  docker cli: "
docker --version || exit 1

echo -n "  dockerd: "
dockerd --version >/dev/null || exit 1

echo -n "  docker compose plugin: "
docker compose version >/dev/null || exit 1

echo -n "  tailscale: "
tailscale version || exit 1

echo -n "  juicefs: "
juicefs version || exit 1

echo ""
echo "Validating docker group membership..."

if id -nG | tr ' ' '\n' | grep -qx "docker"; then
    echo "  ✓ vscode user belongs to docker group"
else
    echo "  ✗ vscode user is missing docker group membership"
    exit 1
fi

echo ""
echo "✅ base-dind-net extras look good"
