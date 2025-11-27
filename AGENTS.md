# Agent Instructions

This repository contains the `coltec-codespaces` Python package and associated Docker images/templates for provisioning development environments.

## Build & Test
- **Package Manager**: `uv` is used for dependency management.
- **Run All Tests**: `uv run pytest`
- **Run Single Test**: `uv run pytest tests/test_provision.py::test_name`
- **Build Docker Images**: `./scripts/build-all.sh [version]`
- **Verify Docker Image**: `./scripts/test-image.sh <variant>`

## Repository Structure
- `src/coltec_codespaces/`: Main Python package.
- `docker/`: Dockerfile definitions for base images.
- `devcontainer_templates/`: Jinja2 templates for `devcontainer.json`.
- `scripts/`: Utility scripts for building and testing.
