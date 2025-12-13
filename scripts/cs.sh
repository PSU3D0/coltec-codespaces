#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/cs.sh new <dest> [--template <path>] [--data key=val ...]   # copier copy
  ./scripts/cs.sh update <dest>                                         # copier update

Notes:
- Runs copier via `uv tool run copier`.
- Default template is this repo root (`.`), which contains `copier.yaml`.
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
    template_path="."
    copier_args=()

    while [[ $# -gt 0 ]]; do
      case "$1" in
        --template)
          template_path="${2:?missing value for --template}"
          shift 2
          ;;
        *)
          copier_args+=("$1")
          shift
          ;;
      esac
    done

    vcs_ref_args=()
    if [[ "$template_path" == "." ]]; then
      vcs_ref_args=(--vcs-ref=HEAD)
    fi

    uv tool run copier copy "$template_path" "$dest" --trust --defaults "${vcs_ref_args[@]}" "${copier_args[@]}"
    ;;
  update)
    uv tool run copier update "$dest" --trust "$@"
    ;;
  *)
    usage
    exit 1
    ;;
esac
