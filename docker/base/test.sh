#!/bin/bash
set -euo pipefail

echo "Testing base image invariants..."

echo -n "  mise: "
mise --version || exit 1

echo -n "  git: "
git --version || exit 1

echo -n "  zsh: "
zsh --version || exit 1

echo -n "  tmux: "
tmux -V || exit 1

echo ""
echo "Testing user setup..."

# Verify user is vscode
if [ "$(whoami)" = "vscode" ]; then
    echo "  ✓ User is vscode"
else
    echo "  ✗ User is not vscode (current: $(whoami))"
    exit 1
fi

# Verify workspace directory exists
if [ -d "/workspace" ]; then
    echo "  ✓ /workspace directory exists"
else
    echo "  ✗ /workspace directory missing"
    exit 1
fi

# Test write access to workspace
if touch /workspace/.test-write 2>/dev/null; then
    rm /workspace/.test-write
    echo "  ✓ /workspace is writable"
else
    echo "  ✗ /workspace is not writable"
    exit 1
fi

# Test sudo access
if sudo -n true 2>/dev/null; then
    echo "  ✓ Passwordless sudo works"
else
    echo "  ✗ Passwordless sudo failed"
    exit 1
fi

echo ""
echo "✅ All base image tests passed"
