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
    software-properties-common
apt-get clean
rm -rf /var/lib/apt/lists/*
