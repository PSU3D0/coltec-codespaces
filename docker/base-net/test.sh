#!/bin/bash
set -euo pipefail

echo "Testing base-net image extras..."

echo -n "  tailscale: "
tailscale version || exit 1

echo -n "  rclone: "
rclone version | head -1 || exit 1

echo ""
echo "✅ base-net extras look good"
