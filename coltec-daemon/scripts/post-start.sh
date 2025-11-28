#!/usr/bin/env bash
#
# post-start.sh - Devcontainer lifecycle hook for coltec-daemon
#
# This script is executed by the devcontainer after it starts.
# It launches the sync daemon to keep workspace files synchronized
# with cloud storage.
#
# Environment variables:
#   COLTEC_CONFIG       - Path to workspace-spec.yaml (default: /workspace/.devcontainer/workspace-spec.yaml)
#   COLTEC_LOG_FORMAT   - Log format: text or json (default: text)
#   COLTEC_LOG_LEVEL    - Log level: trace, debug, info, warn, error (default: info)
#   COLTEC_INTERVAL     - Override sync interval in seconds (optional)
#   RCLONE_BUCKET       - S3/R2 bucket name (required for sync)
#   COLTEC_DAEMON_ARGS  - Additional arguments to pass to daemon (optional)
#   COLTEC_DISABLED     - Set to "true" to skip daemon startup
#
# Exit codes:
#   0   - Success (daemon started or disabled)
#   1   - Configuration error
#   2   - Daemon startup failed
#

set -euo pipefail

# Configuration
CONFIG_PATH="${COLTEC_CONFIG:-/workspace/.devcontainer/workspace-spec.yaml}"
LOG_PREFIX="[coltec-daemon]"

log_info() {
    echo "${LOG_PREFIX} $*"
}

log_error() {
    echo "${LOG_PREFIX} ERROR: $*" >&2
}

# Check if daemon is disabled
if [[ "${COLTEC_DISABLED:-false}" == "true" ]]; then
    log_info "Daemon disabled via COLTEC_DISABLED=true"
    exit 0
fi

# Check if config exists
if [[ ! -f "$CONFIG_PATH" ]]; then
    log_error "Config not found: $CONFIG_PATH"
    log_info "Set COLTEC_CONFIG to override or create workspace-spec.yaml"
    exit 1
fi

# Validate config before starting daemon
log_info "Validating configuration..."
if ! coltec-validate --file "$CONFIG_PATH"; then
    log_error "Configuration validation failed"
    exit 1
fi

# Check if rclone bucket is configured
if [[ -z "${RCLONE_BUCKET:-}" ]]; then
    log_info "RCLONE_BUCKET not set - sync will use default bucket"
fi

# Build daemon arguments
DAEMON_ARGS=(
    "--config" "$CONFIG_PATH"
)

# Add any extra arguments from environment
if [[ -n "${COLTEC_DAEMON_ARGS:-}" ]]; then
    # shellcheck disable=SC2206
    DAEMON_ARGS+=($COLTEC_DAEMON_ARGS)
fi

# Start the daemon
log_info "Starting sync daemon..."
log_info "Config: $CONFIG_PATH"
log_info "Log level: ${COLTEC_LOG_LEVEL:-info}"

# Use exec to replace this shell with the daemon process
# This makes the daemon the main process for signal handling
exec coltec-daemon "${DAEMON_ARGS[@]}"
