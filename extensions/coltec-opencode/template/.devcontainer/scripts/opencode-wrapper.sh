#!/usr/bin/env bash
set -euo pipefail

resolve_opencode() {
  local self target resolved
  self=$(readlink -f "$0" 2>/dev/null || echo "$0")
  IFS=: read -r -a dirs <<<"$PATH"
  for dir in "${dirs[@]}"; do
    target="$dir/opencode"
    if [[ -x "$target" ]]; then
      resolved=$(readlink -f "$target" 2>/dev/null || echo "$target")
      if [[ "$resolved" != "$self" ]]; then
        echo "$target"
        return 0
      fi
    fi
  done
  return 1
}

REAL_OPENCODE=$(resolve_opencode || true)
if [[ -z "${REAL_OPENCODE}" ]]; then
  echo "[opencode] Unable to locate the real opencode binary" >&2
  exit 1
fi

if [[ "${OPENCODE_TMUX_DISABLE:-false}" == "true" ]]; then
  exec "${REAL_OPENCODE}" "$@"
fi

if [[ $# -eq 0 ]]; then
  set -- start
fi

if ! command -v tmux >/dev/null 2>&1; then
  exec "${REAL_OPENCODE}" "$@"
fi

SESSION="${OPENCODE_TMUX_SESSION:-opencode}"
if tmux has-session -t "${SESSION}" 2>/dev/null; then
  if [[ $# -gt 0 ]]; then
    echo "[opencode] Session already running; attaching (args ignored)" >&2
  fi
  exec tmux attach -t "${SESSION}"
fi

exec tmux new-session -A -s "${SESSION}" "${REAL_OPENCODE}" "$@"
