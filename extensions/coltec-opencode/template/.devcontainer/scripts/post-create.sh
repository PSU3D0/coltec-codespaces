#!/usr/bin/env bash
set -euo pipefail

echo "[post-create] Setting up workspace ${WORKSPACE_NAME:-unknown}"

# Fix volume mount permissions (Docker creates volumes as root)
if [[ -d "${HOME}/.local" ]]; then
  sudo chown -R "$(id -u):$(id -g)" "${HOME}/.local"
fi

# Mark workspace as safe for git (handles UID mismatch from bind mounts)
git config --global --add safe.directory /workspace
git config --global --add safe.directory /workspace/codebase

# Fix bind mount ownership for git operations (submodules, worktrees)
if [[ -d "/workspace/.git" ]]; then
  sudo chown -R "$(id -u):$(id -g)" /workspace/.git
fi
if [[ -d "/workspace/codebase" ]]; then
  sudo chown "$(id -u):$(id -g)" /workspace/codebase
  # Create .sessions dir for worktrees with correct ownership
  mkdir -p /workspace/codebase/.sessions
  sudo chown "$(id -u):$(id -g)" /workspace/codebase/.sessions
fi

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

# Setup GitHub authentication if token provided (use HTTPS for containers)
if [[ -n "${GH_TOKEN:-}" ]]; then
  echo "[post-create] Configuring GitHub authentication..."
  gh config set git_protocol https --host github.com
  gh auth setup-git
fi

# Run user dotfiles script if configured
if [[ -n "${DOTFILES_REPO:-}" ]]; then
  echo "[post-create] Cloning dotfiles from ${DOTFILES_REPO}..."
  git clone --depth 1 "${DOTFILES_REPO}" "${HOME}/dotfiles"
  SCRIPT="${DOTFILES_SCRIPT_PATH:-install.sh}"
  if [[ -x "${HOME}/dotfiles/${SCRIPT}" ]]; then
    echo "[post-create] Running ${SCRIPT}..."
    "${HOME}/dotfiles/${SCRIPT}" -y
  fi
  # Re-apply safe.directory after dotfiles (devcontainer.sh skips .gitconfig but zsh/etc may touch it)
  git config --global --add safe.directory /workspace
  git config --global --add safe.directory /workspace/codebase
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
