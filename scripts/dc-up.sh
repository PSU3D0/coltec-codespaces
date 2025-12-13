#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/dc-up.sh --workspace-folder <path> [--] [devcontainer up args...]

Description:
  Exports secrets from fnox.toml to a temp JSON file and starts the devcontainer
  with `devcontainer up --secrets-file <json>`.

Notes:
  - Requires: `fnox`, `devcontainer`.
  - Provide your age identity via `FNOX_AGE_KEY_FILE` or `fnox --age-key-file ...`.
  - The secrets file is created with 0600 perms and deleted on exit.

Examples:
  ./scripts/dc-up.sh --workspace-folder ./widget-dev-a
  ./scripts/dc-up.sh --workspace-folder ./widget-dev-a -- --log-level trace
EOF
}

if [[ $# -lt 2 ]]; then
  usage
  exit 1
fi

workspace_folder=""
devcontainer_args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace-folder)
      workspace_folder="${2:?missing value for --workspace-folder}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      devcontainer_args+=("$@")
      break
      ;;
    *)
      devcontainer_args+=("$1")
      shift
      ;;
  esac
done

if [[ -z "$workspace_folder" ]]; then
  echo "[dc-up] ERROR: --workspace-folder is required" >&2
  exit 1
fi

if ! command -v fnox >/dev/null 2>&1; then
  echo "[dc-up] ERROR: fnox not found" >&2
  exit 1
fi

if ! command -v devcontainer >/dev/null 2>&1; then
  echo "[dc-up] ERROR: devcontainer CLI not found" >&2
  exit 1
fi

secrets_file="$(mktemp -t coltec-secrets.XXXXXX.json)"
cleanup() {
  rm -f "$secrets_file"
}
trap cleanup EXIT

chmod 600 "$secrets_file"

fnox export -f json -o "$secrets_file"

devcontainer up \
  --workspace-folder "$workspace_folder" \
  --secrets-file "$secrets_file" \
  "${devcontainer_args[@]}"
