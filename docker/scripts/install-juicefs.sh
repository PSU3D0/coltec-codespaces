#!/usr/bin/env bash
set -euo pipefail

JUICEFS_VERSION="${1:?usage: install-juicefs.sh <version>}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

curl -fsSL "https://github.com/juicedata/juicefs/releases/download/v${JUICEFS_VERSION}/juicefs-${JUICEFS_VERSION}-linux-amd64.tar.gz" \
    -o "${TMP_DIR}/juicefs.tar.gz"
tar -xzf "${TMP_DIR}/juicefs.tar.gz" -C "${TMP_DIR}"
install -m 0755 "${TMP_DIR}/juicefs" /usr/local/bin/juicefs
