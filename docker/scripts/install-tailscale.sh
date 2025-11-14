#!/usr/bin/env bash
set -euo pipefail

TAILSCALE_VERSION="${1:?usage: install-tailscale.sh <version|latest>}"

curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/jammy.noarmor.gpg \
    -o /usr/share/keyrings/tailscale-archive-keyring.gpg
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/jammy.tailscale-keyring.list \
    -o /etc/apt/sources.list.d/tailscale.list
apt-get update
if [[ "${TAILSCALE_VERSION}" == "latest" ]]; then
    apt-get install -y tailscale
else
    if ! apt-get install -y "tailscale=${TAILSCALE_VERSION}"; then
        echo "Requested Tailscale version ${TAILSCALE_VERSION} not found, installing latest" >&2
        apt-get install -y tailscale
    fi
fi
apt-get clean
rm -rf /var/lib/apt/lists/*
