#!/usr/bin/env bash
set -euo pipefail

echo "[post-create] Setting up workspace ${WORKSPACE_NAME:-unknown}"

# Verify coltec tooling is available
if command -v coltec-daemon >/dev/null 2>&1; then
  echo "[post-create] Verifying tools..."
  coltec-daemon --version
  coltec-validate --version
fi

# Validate workspace spec if it exists
if [[ -f /workspace/.devcontainer/workspace-spec.yaml ]]; then
  echo "[post-create] Validating workspace-spec.yaml..."
  coltec-validate --file /workspace/.devcontainer/workspace-spec.yaml || true
fi

echo "[post-create] Done!"
