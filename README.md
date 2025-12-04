# Coltec Codespaces Tooling

This repository owns **all** Coltec devcontainer tooling: Dockerfiles, devcontainer templates, the `coltec-codespaces` rendering CLI, and GitHub Actions that build/publish images to GHCR. Downstream control planes consume tagged releases of this repo when scaffolding workspaces.

## Repository Layout

| Path | Purpose |
| --- | --- |
| `docker/` | Dockerfiles + smoke tests for every base SKU (see below) and shared install helpers under `docker/scripts/`. |
| `devcontainer_templates/` | Jinja templates referenced by workspace specs (`template.path`). |
| `coltec-daemon/` | Rust sync daemon for workspace persistence (`coltec-daemon`). |
| `scripts/` | Local helper scripts for building/testing images (`build-all.sh`, `test-image.sh`). |
| `specs/examples/` | Example workspace specs used for validation/tests. |
| `.github/workflows/` | CI for building/testing (`build-base.yml`) and releasing (`release.yml`). |

## Base Image SKUs

All workspaces consume one of four GHCR tags: `ghcr.io/psu3d0/coltec-codespace:<version>-<sku>`. Example: `ghcr.io/psu3d0/coltec-codespace:1.0-base-dind-net`. Each SKU trades features for size so most workspaces can stick to the leanest image.

| SKU | Dockerfile | Adds on top of… | Invariants |
| --- | --- | --- | --- |
| `base` | `docker/base/Dockerfile` | `ubuntu:22.04` | mise (v2025.11.4), git, zsh, tmux, sudo, base tooling + `vscode` user |
| `base-dind` | `docker/base-dind/Dockerfile` | `base` | Docker CE CLI/daemon, buildx, compose |
| `base-net` | `docker/base-net/Dockerfile` | `base` | Tailscale (v1.90.6), rclone (v1.65.0) |
| `base-dind-net` | `docker/base-dind-net/Dockerfile` | `base-dind` | Tailscale + rclone |

Guidance:
- **Most workspaces** → `base`
- **Needs Docker builds only** → `base-dind`
- **Needs Tailscale tailnet + rclone sync** → `base-net`
- **Needs everything** → `base-dind-net`

The network-aware SKUs (`base-net`, `base-dind-net`) include Tailscale for private networking and rclone for cloud storage sync.

## Building & Testing Locally
```bash
# Build every SKU in dependency order and tag them locally (coltec-codespace:<sku>-local)
./scripts/build-all.sh [version]

# Test any SKU (set COLTEC_TEST_LOCAL=1 to target the freshly built local tag)
COLTEC_TEST_LOCAL=1 ./scripts/test-image.sh base-dind [version]
```

## Sync Daemon
The `coltec-daemon` (in `coltec-daemon/`) syncs workspace data to cloud storage via rclone.

### Quick Start
```bash
# Validate a workspace spec
cargo run --bin coltec-validate -- --file .devcontainer/workspace-spec.yaml

# Run sync daemon (dry-run, single pass)
COLTEC_CONFIG=.devcontainer/workspace-spec.yaml cargo run --bin coltec-daemon -- --dry-run --once

# Run continuous sync
COLTEC_CONFIG=.devcontainer/workspace-spec.yaml cargo run --bin coltec-daemon
```

### Named Remotes Configuration
The daemon uses "named remotes" to define storage backends. Configure in `workspace-spec.yaml`:

```yaml
persistence:
  enabled: true
  mode: replicated
  default_remote: r2coltec  # Default remote for sync paths
  remotes:
    r2coltec:
      type: s3
      bucket: my-bucket
      options:
        provider: Cloudflare  # or AWS, Wasabi, etc.
        access_key_id: ${RCLONE_S3_ACCESS_KEY_ID}
        secret_access_key: ${RCLONE_S3_SECRET_ACCESS_KEY}
        endpoint: ${RCLONE_S3_ENDPOINT}
        region: auto
  sync:
    - name: agent-context
      path: /workspace/agent-context
      remote_path: workspaces/{org}/{project}/{env}/agent-context
      direction: bidirectional
      interval: 60
      priority: 1
```

**Key features:**
- **Environment variable expansion**: Use `${VAR}` syntax in config values (expanded at runtime)
- **Automatic URL quoting**: Endpoints with special characters (like `https://`) are handled correctly
- **Retry with backoff**: Transient failures (network errors, rate limits) are retried up to 3 times
- **Health file**: Status written to `~/.local/share/coltec-daemon/{workspace}/health.json` for supervisor integration

### Environment Variables
| Variable | Default | Description |
|----------|---------|-------------|
| `COLTEC_CONFIG` | `/workspace/.devcontainer/workspace-spec.yaml` | Config path |
| `COLTEC_INTERVAL` | (from config) | Override sync interval (seconds) |
| `COLTEC_LOG_FORMAT` | `text` | `text` or `json` |
| `RUST_LOG` | `info` | Log level (trace/debug/info/warn/error) |

See `coltec-daemon/README.md` for full documentation.

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
