#!/usr/bin/env bash
set -euo pipefail

echo "[post-start] Starting workspace ${WORKSPACE_NAME:-unknown}"

CONFIG_PATH="${COLTEC_CONFIG:-/workspace/.devcontainer/workspace-spec.yaml}"

# Skip if explicitly disabled
if [[ "${COLTEC_DISABLED:-false}" == "true" ]]; then
  echo "[post-start] COLTEC_DISABLED=true, skipping daemon"
  exit 0
fi

# Skip if no config
if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "[post-start] No workspace-spec.yaml found, skipping daemon"
  exit 0
fi

# Skip if daemon not installed
if ! command -v coltec-daemon >/dev/null 2>&1; then
  echo "[post-start] coltec-daemon not installed, skipping"
  exit 0
fi

# Validate config
if ! coltec-validate --file "$CONFIG_PATH"; then
  echo "[post-start] ERROR: Invalid workspace-spec.yaml"
  exit 1
fi

# Start Tailscale if auth key provided
if [[ -n "${TAILSCALE_AUTH_KEY:-}" ]] && command -v tailscale >/dev/null 2>&1; then
  echo "[post-start] Starting Tailscale..."
  sudo tailscaled --tun=userspace-networking --socks5-server=localhost:1055 &
  sleep 2
  HOSTNAME="${WORKSPACE_NAME:-dev}"
  tailscale up --hostname "dev-${HOSTNAME}" --authkey "${TAILSCALE_AUTH_KEY}" --accept-dns=false || true
fi

# Run daemon
# If no rclone credentials, run dry-run once for validation
if [[ -z "${RCLONE_S3_ACCESS_KEY_ID:-}" ]] && [[ -z "${AWS_ACCESS_KEY_ID:-}" ]]; then
  echo "[post-start] No storage credentials, running daemon in dry-run mode"
  coltec-daemon --config "$CONFIG_PATH" --once --dry-run || true
else
  echo "[post-start] Starting coltec-daemon..."
  # Initial sync
  coltec-daemon --config "$CONFIG_PATH" --once || true
  # Background daemon for continuous sync
  nohup coltec-daemon --config "$CONFIG_PATH" > /tmp/coltec-daemon.log 2>&1 &
  echo "[post-start] Daemon started (PID: $!)"
fi

echo "[post-start] Done!"
