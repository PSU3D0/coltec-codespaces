# Agent Instructions

This repository contains Coltec devcontainer base images, Copier templates, and the Rust `coltec-daemon` sync daemon.

## Build & Test
- **Scaffold workspace (Copier)**: `./scripts/cs.sh new <dest> --data org=... --data project=... --data env=... --data project_type=python`
  - Requires `uv` (Copier is run via `uv tool run copier`).
- **Update workspace (Copier)**: `./scripts/cs.sh update <dest>`
- **Rust daemon tests**: `cargo test --manifest-path coltec-daemon/Cargo.toml`
- **Build Docker images**: `./scripts/build-all.sh [version]`
- **Verify Docker image**: `./scripts/test-image.sh <variant> [version]`

## Repository Structure
- `template/`: Base Copier template (emits `.devcontainer/` + `workspace-spec.yaml`)
- `extensions/`: Opinionated Copier extensions (e.g. `extensions/coltec-venture/`)
- `coltec-daemon/`: Rust daemon + schema + validator (`coltec-daemon`, `coltec-validate`)
- `docker/`: Dockerfile definitions and shared install scripts
- `scripts/`: Local helper scripts
