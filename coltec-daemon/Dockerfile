# syntax=docker/dockerfile:1
#
# Multi-stage build for coltec-daemon
# - Builder: Rust toolchain compiles release binary
# - Runtime: Minimal debian-slim with just the binary + rclone
#
# Build: docker build -t coltec-daemon .
# Size: ~50MB (vs ~2GB with build deps)

# =============================================================================
# BUILDER STAGE
# =============================================================================
FROM rust:1-bookworm AS builder

WORKDIR /build

# Install build dependencies (none needed beyond rust toolchain for this project)
# If we needed openssl: RUN apt-get update && apt-get install -y pkg-config libssl-dev

# Copy manifests first for dependency caching
COPY Cargo.toml Cargo.lock* ./

# Create dummy main.rs to build dependencies
RUN mkdir -p src/bin && \
    echo 'fn main() {}' > src/main.rs && \
    echo 'fn main() {}' > src/bin/validate.rs && \
    echo '' > src/lib.rs

# Build dependencies only (this layer is cached unless Cargo.toml changes)
RUN cargo build --release && rm -rf src

# Copy actual source code (tests excluded via .dockerignore - not needed for build)
COPY src ./src
COPY schema ./schema

# Touch source files to invalidate cache and rebuild with actual code
RUN touch src/main.rs src/lib.rs

# Build release binary
RUN cargo build --release --bin coltec-daemon --bin coltec-validate

# Strip debug symbols for smaller binary
RUN strip /build/target/release/coltec-daemon /build/target/release/coltec-validate

# =============================================================================
# RUNTIME STAGE
# =============================================================================
FROM debian:bookworm-slim AS runtime

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install rclone
RUN curl -fsSL https://rclone.org/install.sh | bash

# Copy binaries from builder
COPY --from=builder /build/target/release/coltec-daemon /usr/local/bin/
COPY --from=builder /build/target/release/coltec-validate /usr/local/bin/

# Create non-root user for running daemon
RUN useradd -m -s /bin/bash coltec

# Default environment variables
ENV COLTEC_CONFIG=/workspace/.devcontainer/workspace-spec.yaml \
    COLTEC_LOG_FORMAT=text \
    COLTEC_LOG_LEVEL=info \
    RCLONE_BUCKET=coltec-workspaces

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD coltec-validate --file $COLTEC_CONFIG || exit 1

# Default command
ENTRYPOINT ["coltec-daemon"]
CMD ["--config", "/workspace/.devcontainer/workspace-spec.yaml"]
