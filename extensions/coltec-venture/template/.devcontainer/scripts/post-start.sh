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

COLTEC_TAILSCALE_AUTH_KEY="${COLTEC_TAILSCALE_AUTH_KEY:-${TAILSCALE_AUTH_KEY:-}}"
if [[ -z "${COLTEC_TAILSCALE_AUTH_KEY}" ]]; then
  echo "[post-start] Done!"
  exit 0
fi

if ! command -v tailscale >/dev/null 2>&1; then
  echo "[post-start] tailscale not installed, skipping tailscale up"
  echo "[post-start] Done!"
  exit 0
fi

echo "[post-start] Authenticating Tailscale..."

for _ in {1..10}; do
  if sudo tailscale status >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

HOSTNAME_PREFIX="${COLTEC_NET_HOSTNAME_PREFIX:-${NETWORKING_HOSTNAME_PREFIX:-dev-}}"
HOSTNAME="${COLTEC_NET_HOSTNAME:-${HOSTNAME_PREFIX}${WORKSPACE_NAME:-codespace}}"

TAGS="${COLTEC_NET_TAGS:-}"
ADVERTISE_TAGS_ARGS=()
if [[ -n "${TAGS}" ]]; then
  ADVERTISE_TAGS_ARGS=("--advertise-tags=${TAGS}")
fi

ACCEPT_DNS="${COLTEC_NET_ACCEPT_DNS:-false}"
ACCEPT_ROUTES="${COLTEC_NET_ACCEPT_ROUTES:-false}"
SSH="${COLTEC_NET_SSH:-true}"
EXTRA_ARGS="${COLTEC_NET_EXTRA_ARGS:-}"

echo "[post-start] tailscale up --hostname=${HOSTNAME}"

set +e
sudo tailscale up \
  --authkey="${COLTEC_TAILSCALE_AUTH_KEY}" \
  --hostname="${HOSTNAME}" \
  --accept-dns="${ACCEPT_DNS}" \
  --accept-routes="${ACCEPT_ROUTES}" \
  --ssh="${SSH}" \
  "${ADVERTISE_TAGS_ARGS[@]}" \
  ${EXTRA_ARGS}
set -e

echo "[post-start] Done!"