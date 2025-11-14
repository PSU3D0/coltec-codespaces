# syntax=docker/dockerfile:1.7

ARG BASE_IMAGE=ghcr.io/psu3d0/coltec-codespace:1.0-base
FROM ${BASE_IMAGE}

LABEL org.opencontainers.image.source="https://github.com/PSU3D0/coltec-codespaces"
LABEL org.opencontainers.image.description="Coltec base devcontainer with Docker CE CLI/daemon, buildx, and compose"
LABEL org.opencontainers.image.licenses="MIT"

ARG DEBIAN_FRONTEND=noninteractive

USER root

COPY scripts/install-docker.sh /tmp/install-docker.sh
RUN bash /tmp/install-docker.sh \
    && rm -f /tmp/install-docker.sh

USER vscode
WORKDIR /workspace

CMD ["/bin/zsh"]
