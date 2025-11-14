#!/usr/bin/env bash
set -euo pipefail

MISE_VERSION="${1:?usage: install-mise.sh <version>}"

curl -fsSL "https://github.com/jdx/mise/releases/download/v${MISE_VERSION}/mise-v${MISE_VERSION}-linux-x64" \
    -o /usr/local/bin/mise
chmod +x /usr/local/bin/mise

mkdir -p /etc/profile.d /etc/zsh/zshrc.d
cat <<'EOF' >/etc/profile.d/mise.sh
if command -v mise >/dev/null 2>&1; then
  eval "$(mise activate bash)"
fi
EOF

cat <<'EOF' >/etc/zsh/zshrc.d/mise.zsh
if command -v mise >/dev/null 2>&1; then
  eval "$(mise activate zsh)"
fi
EOF
