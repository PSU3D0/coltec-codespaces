# syntax=docker/dockerfile:1.7

ARG BASE_IMAGE=ghcr.io/psu3d0/coltec-codespace:base-v1.0.0
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive

USER root

COPY scripts/install-docker.sh /tmp/install-docker.sh
RUN bash /tmp/install-docker.sh \
    && rm -f /tmp/install-docker.sh

USER vscode
WORKDIR /workspace

CMD ["/bin/zsh"]
