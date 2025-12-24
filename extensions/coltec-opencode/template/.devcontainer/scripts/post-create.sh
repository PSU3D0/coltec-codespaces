#!/usr/bin/env bash
set -euo pipefail

echo "[post-create] Setting up workspace ${WORKSPACE_NAME:-unknown}"

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)

# Verify coltec tooling is available
if command -v coltec-daemon >/dev/null 2>&1; then
  echo "[post-create] Verifying tools..."
  coltec-daemon --version
  coltec-validate --version
fi

# Validate workspace spec if it exists
if [[ -f "${ROOT_DIR}/.devcontainer/workspace-spec.yaml" ]]; then
  echo "[post-create] Validating workspace-spec.yaml..."
  coltec-validate --file "${ROOT_DIR}/.devcontainer/workspace-spec.yaml" || true
fi

# Install mise-managed tools
if command -v mise >/dev/null 2>&1; then
  echo "[post-create] Installing mise tools..."
  mise trust
  mise install
fi

# Install the OpenCode wrapper into ~/.local/bin
OPENCODE_WRAPPER="${ROOT_DIR}/.devcontainer/scripts/opencode-wrapper.sh"
if [[ -f "${OPENCODE_WRAPPER}" ]]; then
  mkdir -p "${HOME}/.local/bin"
  cp "${OPENCODE_WRAPPER}" "${HOME}/.local/bin/opencode"
  chmod +x "${HOME}/.local/bin/opencode"
  echo "[post-create] Installed opencode wrapper to ~/.local/bin/opencode"
fi

echo "[post-create] Done!"
