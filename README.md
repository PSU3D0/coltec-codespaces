# Coltec Codespaces Tooling

This repository owns **all** Coltec devcontainer tooling: Dockerfiles, devcontainer templates, the `coltec-codespaces` rendering CLI, and GitHub Actions that build/publish images to GHCR. Downstream control planes consume tagged releases of this repo when scaffolding workspaces (see hADR-0007).

## Repository Layout

| Path | Purpose |
| --- | --- |
| `docker/base/` | Dockerfile + smoke tests for the base image (`ghcr.io/coltec/codespace:base-v1.x.x`). Future variants will live alongside `base/`. |
| `devcontainer_templates/` | Jinja templates referenced by workspace specs (`template.path`). |
| `src/coltec_codespaces/` | Pydantic workspace spec models and CLI entry point (`coltec-codespaces`). |
| `scripts/` | Local helper scripts for building/testing images (`build-all.sh`, `test-image.sh`). |
| `specs/examples/` | Example workspace specs used for validation/tests. |
| `.github/workflows/` | CI for building/testing (`build-base.yml`) and releasing (`release.yml`). |

## Base Image (v1.0.0)
Includes mise v2024.11.14, git, Docker CE (dind), Tailscale v1.74.1, JuiceFS v1.2.1, zsh, tmux, and the `vscode` user with passwordless sudo. These replace the devcontainer features:

```json
{
  "features": {
    "ghcr.io/devcontainers-extra/features/mise:1": {},
    "ghcr.io/devcontainers/features/git:1": {},
    "ghcr.io/devcontainers/features/docker-in-docker:2.12.4": {},
    "ghcr.io/tailscale/codespace/tailscale": {}
  }
}
```

## Building & Testing Locally
```bash
# Build base image (tags ghcr.io/coltec/codespace:base-v1.0.0 and local dev tags)
./scripts/build-all.sh [version]

# Smoke test the base image using docker run
./scripts/test-image.sh base [version]
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
- **build-base.yml** – Runs on pushes/PRs to `main` that touch `docker/base/` or related scripts. Builds `ghcr.io/coltec/codespace:<branch>-base` and smoke tests it. Pushes only on non-PR events.
- **release.yml** – Runs on tags (`v*`). Builds from `docker/base/`, pushes semver tags (`base-v1.0.0`, `base-v1.0`, `base-v1`), and smoke tests the published image.

Both workflows log in to GHCR using `${{ secrets.GITHUB_TOKEN }}` and run tests via `docker/base/test.sh`.

## Release Process
1. Update Dockerfile/tests/templates/specs as needed.
2. `./scripts/build-all.sh` + `./scripts/test-image.sh base` locally.
3. Commit + push to `main`. `build-base.yml` will build/test and push branch/SHA tags.
4. Tag the repo (`git tag v1.0.0 && git push origin v1.0.0`). `release.yml` produces `base-v1.0.0` and semver aliases.
5. Update workspace specs / control-plane references to the new image tag.

## Maintenance Notes
- Keep the base image minimal—only invariants required by all workspaces (mise, git, docker, tailscale, juicefs, shells).
- Bump tool versions via `ARG` values in `docker/base/Dockerfile`, then update `docker/base/test.sh` accordingly.
- Extend `devcontainer_templates/` + `specs/` in lockstep with template changes; add regression tests to CI for new spec scenarios.

## Links
- hADR-0006 (Devcontainer Base Images)
- hADR-0007 (Workspace Spec-Driven Provisioning)
- Registry namespace: `ghcr.io/coltec/codespace`
