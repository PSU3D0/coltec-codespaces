#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/cs.sh new <dest> [--data key=val ...]   # copier copy ./template
  ./scripts/cs.sh update <dest>                     # copier update

Notes:
- Runs copier via `uv tool run copier`.
- Provide --data flags for new (e.g., --data org=acme --data project=proj --data env=dev --data project_type=python).
EOF
}

if [[ $# -lt 2 ]]; then
  usage
  exit 1
fi

cmd="$1"
dest="$2"
shift 2

case "$cmd" in
  new)
    uv tool run copier copy ./template "$dest" --trust "$@"
    ;;
  update)
    uv tool run copier update "$dest" --trust "$@"
    ;;
  *)
    usage
    exit 1
    ;;
esac
