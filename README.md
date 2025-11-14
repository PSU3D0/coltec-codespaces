# Coltec Codespaces Tooling

This repository owns **all** Coltec devcontainer tooling: Dockerfiles, devcontainer templates, the `coltec-codespaces` rendering CLI, and GitHub Actions that build/publish images to GHCR. Downstream control planes consume tagged releases of this repo when scaffolding workspaces.

## Repository Layout

| Path | Purpose |
| --- | --- |
| `docker/` | Dockerfiles + smoke tests for every base SKU (see below) and shared install helpers under `docker/scripts/`. |
| `devcontainer_templates/` | Jinja templates referenced by workspace specs (`template.path`). |
| `src/coltec_codespaces/` | Pydantic workspace spec models and CLI entry point (`coltec-codespaces`). |
| `scripts/` | Local helper scripts for building/testing images (`build-all.sh`, `test-image.sh`). |
| `specs/examples/` | Example workspace specs used for validation/tests. |
| `.github/workflows/` | CI for building/testing (`build-base.yml`) and releasing (`release.yml`). |

## Base Image SKUs

All workspaces consume one of four GHCR tags: `ghcr.io/psu3d0/coltec-codespace:<version>-<sku>`. Example: `ghcr.io/psu3d0/coltec-codespace:1.0-base-dind-net`. Each SKU trades features for size so most workspaces can stick to the leanest image.

| SKU | Dockerfile | Adds on top of… | Invariants |
| --- | --- | --- | --- |
| `base` | `docker/base/Dockerfile` | `ubuntu:22.04` | mise (v2025.11.4), git, zsh, tmux, sudo, base tooling + `vscode` user |
| `base-dind` | `docker/base-dind/Dockerfile` | `base` | Docker CE CLI/daemon, buildx, compose |
| `base-net` | `docker/base-net/Dockerfile` | `base` | Tailscale (v1.90.6), JuiceFS (v1.3.0) |
| `base-dind-net` | `docker/base-dind-net/Dockerfile` | `base-dind` | Tailscale + JuiceFS |

Guidance:
- **Most workspaces** → `base`
- **Needs Docker builds only** → `base-dind`
- **Needs Tailscale tailnet + JuiceFS** → `base-net`
- **Needs everything** → `base-dind-net`

JuiceFS only ships in the network-aware SKUs (`base-net`, `base-dind-net`) so we can keep the default `base` as small as possible while still supporting host and in-container mounting strategies.

## Building & Testing Locally
```bash
# Build every SKU in dependency order and tag them locally (coltec-codespace:<sku>-local)
./scripts/build-all.sh [version]

# Test any SKU (set COLTEC_TEST_LOCAL=1 to target the freshly built local tag)
COLTEC_TEST_LOCAL=1 ./scripts/test-image.sh base-dind [version]
```

## Workspace Spec CLI
The `coltec-codespaces` CLI renders workspace specs into devcontainer JSON:

```bash
# Render to stdout (JSON) using uv
uv run --project . coltec-codespaces render specs/examples/formualizer-dev.yaml

# Validate a spec
uv run --project . coltec-codespaces validate specs/examples/formualizer-dev.yaml

# List workspaces in a bundle spec
uv run --project . coltec-codespaces list specs/examples/*.yaml
```

Control-plane scaffolding scripts shell out to this CLI to validate and render `.devcontainer/workspace-spec.yaml` for every workspace (see hADR-0007).

## GitHub Actions
- **build-base.yml** – Runs on pushes/PRs to `main` that touch `docker/**` or supporting tooling. Sequentially builds all four SKUs (pushing only on non-PR events), tags them as `sha-<commit>-<sku>`, and runs the matching smoke test script for each.
- **release.yml** – Runs on tags (`v*`). Publishes `<version>-<sku>` tags for every SKU (e.g., `1.0-base`, `1.0-base-net`, `1.0-base-dind-net`) plus SHA aliases, then re-runs the smoke suites before exiting.

Both workflows log in to GHCR using `${{ secrets.GITHUB_TOKEN }}` and mount the SKU-specific `docker/<sku>/test.sh` scripts into `docker run` for validation.

## Release Process
1. Update Dockerfiles/tests/templates/specs as needed.
2. `./scripts/build-all.sh` locally, then `COLTEC_TEST_LOCAL=1 ./scripts/test-image.sh <sku>` for any variants you touched.
3. Commit + push to `main`. `build-base.yml` will build/test and push branch/SHA tags.
4. Tag the repo (`git tag v1.0.0 && git push origin v1.0.0`). `release.yml` produces `<version>-<sku>` tags such as `1.0-base-net` and `1.0-base-dind-net` (plus major/minor aliases) for every SKU.
5. Update workspace specs / control-plane references to the new image tag.

## Maintenance Notes
- Keep `docker/base` minimal (mise, git, shells, tmux, sudo). Add new capabilities by extending one of the higher SKUs.
- Bump tool versions via `ARG` values in the relevant Dockerfile and matching install script under `docker/scripts/`, then update that SKU's `test.sh`.
- Extend `devcontainer_templates/` + `specs/` in lockstep with template changes; add regression tests to CI for new spec scenarios.

## Links
- Registry namespace: `ghcr.io/psu3d0/coltec-codespace`
