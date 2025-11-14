#!/bin/bash
set -euo pipefail

echo "Testing base-net image extras..."

echo -n "  tailscale: "
tailscale version || exit 1

echo -n "  juicefs: "
juicefs version || exit 1

echo ""
echo "✅ base-net extras look good"
