# syntax=docker/dockerfile:1.7

# =============================================================================
# Stage 1: Build Rust daemon
# =============================================================================
FROM rust:1-bookworm AS rust-builder

WORKDIR /build

# Copy Cargo manifests for dependency caching
COPY coltec-daemon/Cargo.toml coltec-daemon/Cargo.lock* ./

# Create dummy source for dependency build
RUN mkdir -p src/bin && \
    echo 'fn main() {}' > src/main.rs && \
    echo 'fn main() {}' > src/bin/validate.rs && \
    echo '' > src/lib.rs

# Build dependencies only (cached unless Cargo.toml changes)
RUN cargo build --release && rm -rf src

# Copy actual source
COPY coltec-daemon/src ./src
COPY coltec-daemon/schema ./schema

# Rebuild with real source
RUN touch src/main.rs src/lib.rs && \
    cargo build --release --bin coltec-daemon --bin coltec-validate && \
    strip target/release/coltec-daemon target/release/coltec-validate

# =============================================================================
# Stage 2: Base devcontainer image
# =============================================================================
FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
ARG MISE_VERSION=2025.11.4

LABEL org.opencontainers.image.source="https://github.com/PSU3D0/coltec-codespaces"
LABEL org.opencontainers.image.description="Coltec base devcontainer image with core invariants (mise, git, zsh, tmux, sudo, coltec-daemon)"
LABEL org.opencontainers.image.licenses="MIT"

# Copy build scripts
COPY docker/scripts/ /tmp/coltec-scripts/

# Install core packages (includes rclone)
RUN bash /tmp/coltec-scripts/install-core-packages.sh \
    && bash /tmp/coltec-scripts/install-mise.sh "${MISE_VERSION}" \
    && rm -rf /tmp/coltec-scripts

# Copy daemon binaries from Rust builder
COPY --from=rust-builder /build/target/release/coltec-daemon /usr/local/bin/
COPY --from=rust-builder /build/target/release/coltec-validate /usr/local/bin/

# Create user and groups
RUN groupadd -f docker \
    && useradd -m -s /bin/zsh -G docker,sudo vscode \
    && echo "vscode ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/vscode \
    && chmod 0440 /etc/sudoers.d/vscode

# Setup workspace directories
RUN mkdir -p /workspace /home/vscode/.config \
    && chown -R vscode:vscode /workspace /home/vscode

USER vscode
WORKDIR /workspace

# Verify installations
RUN mise --version && \
    coltec-daemon --version && \
    coltec-validate --version && \
    rclone --version | head -1

CMD ["/bin/zsh"]
