#!/usr/bin/env bash
set -euo pipefail

CONF="/workspace/.devcontainer/supervisord.conf"
LOG_DIR="/workspace/.devcontainer/logs"
PID_FILE="/workspace/.devcontainer/supervisord.pid"

mkdir -p "${LOG_DIR}"

if ! command -v supervisord >/dev/null 2>&1; then
    echo "[post-start] ERROR: supervisord not installed" >&2
    exit 1
fi

echo "[post-start] Launching supervisor..."
# Clean up any stale pid files so repeated post-start runs succeed
sudo rm -f "${PID_FILE}"

sudo supervisord -c "${CONF}"

echo "[post-start] Supervisor started (logs in ${LOG_DIR})."

# Handle Tailscale Auth (optional)
if [[ -n "${TAILSCALE_AUTH_KEY:-}" ]]; then
    echo "[post-start] Authenticating Tailscale..."

    # Wait for tailscaled to accept commands
    for _ in {1..10}; do
        if sudo tailscale status >/dev/null 2>&1; then
            break
        fi
        sleep 1
    done

    sudo tailscale up \
        --authkey="${TAILSCALE_AUTH_KEY}" \
        --hostname="${NETWORKING_HOSTNAME_PREFIX:-dev-}${WORKSPACE_NAME:-codespace}" \
        --ssh \
        --accept-routes
else
    echo "[post-start] TAILSCALE_AUTH_KEY not found. Skipping Tailscale auth (tailscaled is still running)."
fi
