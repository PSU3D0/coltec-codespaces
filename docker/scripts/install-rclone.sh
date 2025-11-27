#!/usr/bin/env bash
set -euo pipefail

REQUESTED_RCLONE_VERSION="${1:?usage: install-rclone.sh <version>}"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

curl -fsSL "https://downloads.rclone.org/v${REQUESTED_RCLONE_VERSION}/rclone-v${REQUESTED_RCLONE_VERSION}-linux-amd64.zip" \
    -o "${TMP_DIR}/rclone.zip"
unzip -q "${TMP_DIR}/rclone.zip" -d "${TMP_DIR}"
install -m 0755 "${TMP_DIR}/rclone-v${REQUESTED_RCLONE_VERSION}-linux-amd64/rclone" /usr/local/bin/rclone

# Verify installation (unset the build ARG env var so rclone doesn't misinterpret it)
env -u RCLONE_VERSION rclone version
