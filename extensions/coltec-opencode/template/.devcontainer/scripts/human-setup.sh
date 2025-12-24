#!/usr/bin/env bash
set -euo pipefail

# Optional human shell bootstrap
# - Creates .human/ for personal, git-ignored config
# - Installs powerlevel10k, zsh-autosuggestions, zsh-completions if git/network available
# - Writes a zshrc that stays in .human and symlinks ~/.zshrc if empty
# - Adds eval "$(mise activate zsh)" when mise is present

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
HUMAN_DIR="$ROOT_DIR/.human"
THEMES_DIR="$HUMAN_DIR/themes"
PLUGINS_DIR="$HUMAN_DIR/plugins"

mkdir -p "$THEMES_DIR" "$PLUGINS_DIR"

clone_if_missing() {
  local repo=$1 dest=$2
  if [ -d "$dest/.git" ]; then
    echo "[human-setup] already present: $dest"
    return
  fi
  echo "[human-setup] cloning $repo -> $dest"
  git clone --depth 1 "$repo" "$dest"
}

# Best-effort clones; if offline, user can re-run later
if command -v git >/dev/null 2>&1; then
  clone_if_missing https://github.com/romkatv/powerlevel10k.git "$THEMES_DIR/powerlevel10k" || true
  clone_if_missing https://github.com/zsh-users/zsh-autosuggestions.git "$PLUGINS_DIR/zsh-autosuggestions" || true
  clone_if_missing https://github.com/zsh-users/zsh-completions.git "$PLUGINS_DIR/zsh-completions" || true
else
  echo "[human-setup] git not available; skipping theme/plugin clones"
fi

ZSHRC_CONTENT="${HUMAN_DIR}/zshrc"
if [ ! -f "$ZSHRC_CONTENT" ]; then
  cat >"$ZSHRC_CONTENT" <<'EOF'
# Coltec human zsh profile (optional) — stored in .human/
export HUMAN_ROOT="${HUMAN_ROOT:-$HOME/.human}"
autoload -Uz compinit promptinit
compinit
promptinit

# Powerlevel10k if available
if [ -f "$HUMAN_ROOT/themes/powerlevel10k/powerlevel10k.zsh-theme" ]; then
  source "$HUMAN_ROOT/themes/powerlevel10k/powerlevel10k.zsh-theme"
fi

# Plugins
if [ -f "$HUMAN_ROOT/plugins/zsh-autosuggestions/zsh-autosuggestions.zsh" ]; then
  source "$HUMAN_ROOT/plugins/zsh-autosuggestions/zsh-autosuggestions.zsh"
fi
fpath=($HUMAN_ROOT/plugins/zsh-completions/src $fpath)

# History/navigation
setopt hist_ignore_space
setopt share_history

# Useful aliases
alias ll='ls -alF'
alias gs='git status'

# Prefer workspace root
cd /workspace 2>/dev/null || true

# Activate mise if present
if command -v mise >/dev/null 2>&1; then
  eval "$(mise activate zsh)"
fi
EOF
fi

# Symlink ~/.zshrc if it doesn't exist
if [ ! -e "$HOME/.zshrc" ]; then
  ln -s "$ZSHRC_CONTENT" "$HOME/.zshrc"
  echo "[human-setup] linked $HOME/.zshrc -> $ZSHRC_CONTENT"
else
  echo "[human-setup] ~/.zshrc already exists; not touching"
fi

echo "[human-setup] complete. Customize files under $HUMAN_DIR"
