# syntax=docker/dockerfile:1.7

FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
ARG MISE_VERSION=2025.11.4

LABEL org.opencontainers.image.source="https://github.com/PSU3D0/coltec-codespaces"
LABEL org.opencontainers.image.description="Coltec base devcontainer image with core invariants (mise, git, zsh, tmux, sudo)"
LABEL org.opencontainers.image.licenses="MIT"

COPY scripts/ /tmp/coltec-scripts/

RUN bash /tmp/coltec-scripts/install-core-packages.sh \
    && bash /tmp/coltec-scripts/install-mise.sh "${MISE_VERSION}" \
    && rm -rf /tmp/coltec-scripts

RUN groupadd -f docker \
    && useradd -m -s /bin/zsh -G docker,sudo vscode \
    && echo "vscode ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/vscode \
    && chmod 0440 /etc/sudoers.d/vscode

RUN mkdir -p /workspace /home/vscode/.config \
    && chown -R vscode:vscode /workspace /home/vscode

USER vscode
WORKDIR /workspace

RUN mise --version

CMD ["/bin/zsh"]
