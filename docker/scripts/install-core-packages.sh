#!/usr/bin/env bash
set -euo pipefail

apt-get update
apt-get install -y \
    git \
    curl \
    wget \
    ca-certificates \
    gnupg \
    lsb-release \
    zsh \
    tmux \
    sudo \
    build-essential \
    apt-transport-https \
    software-properties-common \
    unzip \
    supervisor \
    python3-yaml

# Install GitHub CLI
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update \
    && apt-get install -y gh

# Install rclone for replicated persistence
RCLONE_VERSION="1.68.2"
cd /tmp
curl -fsSL -o rclone.zip "https://downloads.rclone.org/v${RCLONE_VERSION}/rclone-v${RCLONE_VERSION}-linux-amd64.zip"
unzip -q rclone.zip
cp "rclone-v${RCLONE_VERSION}-linux-amd64/rclone" /usr/local/bin/
chmod +x /usr/local/bin/rclone
rm -rf rclone.zip "rclone-v${RCLONE_VERSION}-linux-amd64"
echo "[install-core-packages] Installed rclone: $(rclone version | head -1)"

apt-get clean
rm -rf /var/lib/apt/lists/*
