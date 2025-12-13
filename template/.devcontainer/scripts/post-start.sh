#!/usr/bin/env bash
set -euo pipefail

echo "[post-start] Starting workspace ${WORKSPACE_NAME:-unknown}"

CONF="/workspace/.devcontainer/supervisord.conf"
LOG_DIR="/workspace/.devcontainer/logs"
PID_FILE="/workspace/.devcontainer/supervisord.pid"
CONFIG_PATH="${COLTEC_CONFIG:-/workspace/.devcontainer/workspace-spec.yaml}"

if [[ "${COLTEC_DISABLED:-false}" == "true" ]]; then
  echo "[post-start] COLTEC_DISABLED=true, skipping"
  exit 0
fi

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "[post-start] No workspace-spec.yaml found at $CONFIG_PATH, skipping"
  exit 0
fi

if ! command -v coltec-daemon >/dev/null 2>&1; then
  echo "[post-start] coltec-daemon not installed, skipping"
  exit 0
fi

if ! command -v coltec-validate >/dev/null 2>&1; then
  echo "[post-start] coltec-validate not installed, skipping"
  exit 0
fi

echo "[post-start] Validating workspace-spec.yaml..."
coltec-validate --file "$CONFIG_PATH"

if [[ -z "${RCLONE_S3_ACCESS_KEY_ID:-}" ]] && [[ -z "${AWS_ACCESS_KEY_ID:-}" ]]; then
  echo "[post-start] No storage credentials, running daemon once in dry-run mode"
  coltec-daemon --config "$CONFIG_PATH" --once --dry-run || true
  exit 0
fi

mkdir -p "${LOG_DIR}"

if ! command -v supervisord >/dev/null 2>&1; then
  echo "[post-start] ERROR: supervisord not installed" >&2
  exit 1
fi

export COLTEC_CONFIG="$CONFIG_PATH"

echo "[post-start] Launching supervisor..."
sudo rm -f "${PID_FILE}"
sudo supervisord -c "${CONF}"

echo "[post-start] Supervisor started (logs in ${LOG_DIR})."

echo "[post-start] Done!"