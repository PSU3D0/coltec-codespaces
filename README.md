# Coltec Devcontainer Image Sources

Canonical Dockerfile sources for Coltec's pre-built devcontainer images live here. The `coltec-codespace-images` publishing repo consumes this tree and pushes images to GHCR.

## Scope (v1.0.0)
- Ship the **base** image only; specialized variants (rust, python, node, monorepo) stay in backlog until the pipeline proves out.
- Hold all source code, tests, and build tooling under `templates/devcontainer/images/`.
- Use GitHub Actions (in this subrepo) plus the publishing repo to build, test, and release.

## Base Image
Location: `base/`

Includes mise v2024.11.14, git, Docker CE (dind), Tailscale v1.74.1, JuiceFS v1.2.1, zsh, tmux, and the `vscode` user with passwordless sudo. These replace the slow devcontainer features:

```json
"features": {
  "ghcr.io/devcontainers-extra/features/mise:1": {},
  "ghcr.io/devcontainers/features/git:1": {},
  "ghcr.io/devcontainers/features/docker-in-docker:2.12.4": {},
  "ghcr.io/tailscale/codespace/tailscale": {}
}
```

## Working Locally
1. Build: `./scripts/build-all.sh [version]` (defaults to `1.0.0`).
2. Test: `./scripts/test-image.sh base [version]` which mounts `base/test.sh` into the built image.
3. Manual build/test:
   ```bash
   cd base
   docker build -t coltec-codespace:base-test .
   docker run --rm -v "$PWD/test.sh:/test.sh:ro" coltec-codespace:base-test /bin/bash /test.sh
   ```

## Publishing Path
1. Update Dockerfile/test sources and validate locally.
2. Commit and push to the `coltec-codespace-images` repo.
3. `build-base.yml` runs on every push/PR to main, building + testing and (when not a PR) pushing `sha`/branch tags to `ghcr.io/coltec/codespace`.
4. Tag the repo (`vX.Y.Z`) to trigger `release.yml`, which publishes `base-vX.Y.Z` plus semver aliases.
5. Update devcontainer templates to point at the new tag.

## Devcontainer Usage
Before:
```json
{
  "image": "mcr.microsoft.com/devcontainers/base:ubuntu",
  "features": { ... }
}
```
After:
```json
{
  "image": "ghcr.io/coltec/codespace:base-v1.0.0"
}
```

## Maintenance Notes
- Keep the base image minimal—only invariants required by all workspaces. Add new tooling only with hADR approval.
- Update tool versions by editing the `ARG` values at the top of `base/Dockerfile`, then rebuild/test locally before pushing.
- Mirror any validation logic updates in `base/test.sh` to keep CI smoke tests aligned.

## Links
- [hADR-0006: Devcontainer Base Images](/docs/hadr/hADR-0006-devcontainer-base-images.md)
- Publishing repo: `coltec-codespace-images`
- Registry namespace: `ghcr.io/coltec/codespace`
