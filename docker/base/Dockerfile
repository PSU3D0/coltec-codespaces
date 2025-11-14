# Coltec Codespace Base Image
# Includes all invariants used by ALL workspaces
# See: hADR-0006

FROM ubuntu:22.04

# Version arguments for easy updates
ARG MISE_VERSION=2024.11.14
ARG TAILSCALE_VERSION=1.74.1
ARG JUICEFS_VERSION=1.2.1
ARG DEBIAN_FRONTEND=noninteractive

LABEL org.opencontainers.image.source=https://github.com/coltec/nexus
LABEL org.opencontainers.image.description="Coltec base devcontainer image with mise, git, docker, tailscale, juicefs"
LABEL org.opencontainers.image.licenses=MIT

# Install system dependencies and Docker CE in a single layer
RUN apt-get update && apt-get install -y \
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
    && rm -rf /var/lib/apt/lists/*

# Install Docker CE (docker-in-docker capability)
RUN install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc \
    && chmod a+r /etc/apt/keyrings/docker.asc \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y \
        docker-ce \
        docker-ce-cli \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin \
    && rm -rf /var/lib/apt/lists/*

# Install mise (tool version manager)
RUN curl -fsSL https://github.com/jdx/mise/releases/download/v${MISE_VERSION}/mise-v${MISE_VERSION}-linux-x64 -o /usr/local/bin/mise \
    && chmod +x /usr/local/bin/mise \
    && mkdir -p /etc/profile.d \
    && echo 'eval "$(/usr/local/bin/mise activate bash)"' > /etc/profile.d/mise.sh \
    && echo 'eval "$(/usr/local/bin/mise activate zsh)"' > /etc/zsh/zshrc.d/mise.zsh || true

# Install Tailscale
RUN curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/jammy.noarmor.gpg -o /usr/share/keyrings/tailscale-archive-keyring.gpg \
    && curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/jammy.tailscale-keyring.list -o /etc/apt/sources.list.d/tailscale.list \
    && apt-get update \
    && apt-get install -y tailscale \
    && rm -rf /var/lib/apt/lists/*

# Install JuiceFS
RUN curl -fsSL https://github.com/juicedata/juicefs/releases/download/v${JUICEFS_VERSION}/juicefs-${JUICEFS_VERSION}-linux-amd64.tar.gz -o /tmp/juicefs.tar.gz \
    && tar -xzf /tmp/juicefs.tar.gz -C /usr/local/bin \
    && chmod +x /usr/local/bin/juicefs \
    && rm /tmp/juicefs.tar.gz

# Create vscode user with sudo access
RUN useradd -m -s /bin/zsh -G docker,sudo vscode \
    && echo "vscode ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/vscode \
    && chmod 0440 /etc/sudoers.d/vscode

# Create workspace directory
RUN mkdir -p /workspace \
    && chown -R vscode:vscode /workspace

# Set up zsh for vscode user
RUN mkdir -p /home/vscode/.config \
    && chown -R vscode:vscode /home/vscode

# Switch to vscode user
USER vscode
WORKDIR /workspace

# Configure mise for vscode user
RUN mise --version

# Default command
CMD ["/bin/zsh"]
