#!/usr/bin/env bash
set -euo pipefail

echo "[post-start] Coltec workspace {{ env }} for {{ org }}/{{ project }}"

# Placeholder: start sync daemon or other services here.
if command -v coltec-daemon >/dev/null 2>&1; then
  echo "[post-start] Starting coltec-daemon"
  coltec-daemon --config /workspace/.devcontainer/workspace-spec.yaml --once --dry-run || true
else
  echo "[post-start] coltec-daemon not installed; skipping"
fi
