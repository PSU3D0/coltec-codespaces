# syntax=docker/dockerfile:1.7

ARG BASE_IMAGE=ghcr.io/psu3d0/coltec-codespace:1.0-base-dind
FROM ${BASE_IMAGE}

LABEL org.opencontainers.image.source="https://github.com/PSU3D0/coltec-codespaces"
LABEL org.opencontainers.image.description="Coltec Docker + networking devcontainer with Docker dind, Tailscale, and JuiceFS"
LABEL org.opencontainers.image.licenses="MIT"

ARG DEBIAN_FRONTEND=noninteractive
ARG TAILSCALE_VERSION=1.90.6
ARG JUICEFS_VERSION=1.3.0

USER root

COPY scripts/install-tailscale.sh /tmp/install-tailscale.sh
COPY scripts/install-juicefs.sh /tmp/install-juicefs.sh

RUN bash /tmp/install-tailscale.sh "${TAILSCALE_VERSION}" \
    && bash /tmp/install-juicefs.sh "${JUICEFS_VERSION}" \
    && rm -f /tmp/install-tailscale.sh /tmp/install-juicefs.sh

USER vscode
WORKDIR /workspace

CMD ["/bin/zsh"]
