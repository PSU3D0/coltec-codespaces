# Coltec Workspace: {{ env }}

This workspace wraps the asset repo at `codebase/` and layers on Coltec tooling for {{ org }}/{{ project }} ({{ project_type }}).

## Generated files
- `.devcontainer/workspace-spec.yaml` — source of truth for devcontainer and persistence.
- `.devcontainer/devcontainer.json` — render from the spec (use `coltec-validate` / future renderer).
- `.devcontainer/scripts/` — lifecycle hooks for post-create/post-start.

## Next steps
- Install required secrets in your host environment (e.g., `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `JUICEFS_S3_ENDPOINT`).
- Bring up the devcontainer with your preferred tooling once rendering is wired (Phase 3+).
