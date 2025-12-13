# Multi-Workspace Playbook (5 concurrent devcontainers)

Goal: go from nothing to 5 stable, persistent devcontainers you (or agents) can hop between on any machine, assuming you have a stable cloud storage backend.

## Prereqs

Host:
- `uv`
- Docker
- VS Code + Dev Containers extension

Cloud storage (S3/R2):
- Bucket
- Credentials available *inside the devcontainer runtime*
  - easiest: Codespaces secrets / container env injection (var names below)

## Non-negotiable isolation rules

- Every workspace must have a unique `{org}/{project}/{env}`.
- Never point two devcontainers at the same `persistence.sync[*].remote_path` prefix.
- Keep “agent state” separate from “codebase” to reduce conflicts.

## Create 5 workspaces

Pick:
- `org=acme`
- `project=widget`
- envs: `dev-a`, `dev-b`, `dev-c`, `dev-d`, `dev-e`

```bash
org=acme
project=widget

for env in dev-a dev-b dev-c dev-d dev-e; do
  ./scripts/cs.sh new "./${project}-${env}" \
    --data org="$org" \
    --data project="$project" \
    --data env="$env" \
    --data project_type=python

done
```

Open each in VS Code (`code ./widget-dev-a`) then run “Dev Containers: Reopen in Container”.

## Required storage env vars

The base template expects these to be present inside the container (they are expanded at runtime by `coltec-daemon`):
- `RCLONE_BUCKET`
- `RCLONE_S3_ACCESS_KEY_ID`
- `RCLONE_S3_SECRET_ACCESS_KEY`
- `RCLONE_S3_ENDPOINT`

Defaults baked into the template:
- `provider: Cloudflare`
- `region: auto`

If you’re not using R2/Cloudflare, override the remote options in `.devcontainer/workspace-spec.yaml`.

## Secrets injection (fnox + devcontainer CLI)

This repo supports a host-driven flow where `fnox` decrypts secrets from `fnox.toml` and the Dev Containers CLI injects them into the container.

Prereq: you need an `age` identity key that can decrypt the recipients in `fnox.toml`. Provide it via `fnox --age-key-file ...` or the `FNOX_AGE_KEY_FILE` env var.

Preferred: use the wrapper script (mktemp + export + cleanup):

```bash
./scripts/dc-up.sh --workspace-folder ./widget-dev-a
```

Manual flow (if you want explicit control):

```bash
fnox export -f json -o /tmp/coltec-secrets.json
chmod 600 /tmp/coltec-secrets.json

devcontainer up \
  --workspace-folder ./widget-dev-a \
  --secrets-file /tmp/coltec-secrets.json

rm -f /tmp/coltec-secrets.json
```

Notes:
- This is mainly for local/headless orchestration. If you’re using VS Code “Reopen in Container” or Codespaces, use that platform’s secrets injection mechanism instead.
- If you need non-secret env vars, use `devcontainer up --remote-env NAME=value`.

## Recommended persistence defaults (for 5+ concurrent)

Edit each workspace’s `.devcontainer/workspace-spec.yaml`:
- Prefer fewer, higher-value sync targets.
- Use longer intervals unless you truly need “near realtime”.

Suggested starting point:
- `workspace` sync: `interval: 180`–`300`
- “agent-context” sync: `interval: 30`–`60` (small directory only)
- `transfers: 4`, `checkers: 8`, optional `bwlimit: "10M"`
- aggressive excludes for heavy dirs (`.git/**`, `node_modules/**`, `.venv/**`, `target/**`, caches)

## Optional: networking overlay (Tailscale)

Use the dedicated extension when you want a tailnet:

```bash
./scripts/cs.sh new ./widget-dev-net \
  --template ./extensions/coltec-network \
  --data org=acme \
  --data project=widget \
  --data env=dev-net \
  --data project_type=python
```

Then set:
- `COLTEC_TAILSCALE_AUTH_KEY` (preferred) or `TAILSCALE_AUTH_KEY`
- optional tuning: `COLTEC_NET_HOSTNAME_PREFIX`, `COLTEC_NET_TAGS`, `COLTEC_NET_ACCEPT_DNS`, `COLTEC_NET_ACCEPT_ROUTES`, `COLTEC_NET_SSH`

## Ops / debugging

- Supervisor logs: `.devcontainer/logs/*`
- If you need a safe pause: set `COLTEC_DISABLED=true` in the container env and restart.

## Common pitfalls

- Missing storage creds: daemon will validate spec then fail sync (or run dry-run depending on hook), leaving you with a working container but no persistence.
- Conflicts: two containers writing the same remote path (fix by changing `env`/`remote_path`).
- Too-aggressive intervals across many workspaces: storage throttling and slow editor performance.
