# Workspace Customization Guide

This guide covers how to customize Coltec workspaces at different levels:

- **Developer-level**: Personal dotfiles, shell config, editor preferences
- **Org-level**: Shared defaults, Claude Code settings, security policies

## Overview

Customization follows a layered approach where each level can extend or override the previous:

```
Base Image → Org Defaults → Project Config → Developer Preferences
```

| Level | Scope | Persistence | Example |
|-------|-------|-------------|---------|
| Base Image | All workspaces | Baked in | mise, zsh, tmux |
| Org Defaults | All org workspaces | Pull-only sync | Claude Code settings, org zshrc |
| Project Config | Single project | workspace-spec.yaml | Extensions, sync paths |
| Developer | Single developer | Dotfiles or pull-only sync | Personal zshrc, git config |

## Developer Customization

### Option 1: Native Devcontainer Dotfiles (Recommended)

VS Code and DevPod support automatic dotfiles installation via a git repository.

**Setup:**

1. Create a dotfiles repository (e.g., `github.com/youruser/dotfiles`)

2. Add an install script that symlinks or copies files:
   ```bash
   #!/bin/bash
   # install.sh
   ln -sf ~/dotfiles/.zshrc ~/.zshrc
   ln -sf ~/dotfiles/.gitconfig ~/.gitconfig
   ln -sf ~/dotfiles/.tmux.conf ~/.tmux.conf
   ```

3. Configure VS Code settings (user-level, not workspace):
   ```json
   {
     "dotfiles.repository": "youruser/dotfiles",
     "dotfiles.targetPath": "~/dotfiles",
     "dotfiles.installCommand": "install.sh"
   }
   ```

**Pros:**
- Standard approach, works with any devcontainer
- You control your own repository
- No cloud storage credentials needed

**Cons:**
- Requires maintaining a git repository
- Only runs on container creation (not updated automatically)

### Option 2: Pull-Only Sync Path

Use the coltec-daemon to sync personal config from cloud storage.

**Setup in workspace-spec.yaml:**
```yaml
persistence:
  enabled: true
  default_remote: r2storage
  remotes:
    r2storage:
      type: s3
      bucket: my-bucket
      options:
        provider: Cloudflare
        access_key_id: ${RCLONE_S3_ACCESS_KEY_ID}
        secret_access_key: ${RCLONE_S3_SECRET_ACCESS_KEY}
        endpoint: ${RCLONE_S3_ENDPOINT}
  sync:
    # Personal dotfiles - pulled from cloud, never pushed back
    - name: dotfiles
      path: /home/vscode/.config/personal
      remote_path: users/${USER}/dotfiles
      direction: pull-only
      interval: 3600  # Check hourly for updates
      priority: 0     # Run before other syncs
```

Then source your personal config from standard dotfiles:
```bash
# In ~/.zshrc (persisted in home volume)
[[ -f ~/.config/personal/.zshrc ]] && source ~/.config/personal/.zshrc
```

**Pros:**
- Automatically updates when you change remote config
- Works with any devcontainer tooling
- Can version/rollback via cloud storage

**Cons:**
- Requires cloud storage credentials
- More complex initial setup

### Common Developer Customizations

| File | Purpose | Recommendation |
|------|---------|----------------|
| `~/.zshrc` | Shell config | Dotfiles repo or pull-only |
| `~/.gitconfig` | Git settings | Dotfiles repo |
| `~/.tmux.conf` | Tmux config | Dotfiles repo |
| `~/.config/nvim/` | Neovim config | Dotfiles repo |
| `~/.ssh/config` | SSH hosts | Dotfiles repo (careful with keys) |

## Org-Level Customization

Organizations can define shared defaults that apply to all workspaces.

### Pull-Only Org Defaults

Add an org defaults sync path to your workspace template:

```yaml
persistence:
  enabled: true
  default_remote: orgstore
  remotes:
    orgstore:
      type: s3
      bucket: org-bucket
      options:
        provider: Cloudflare
        access_key_id: ${ORG_S3_ACCESS_KEY_ID}
        secret_access_key: ${ORG_S3_SECRET_ACCESS_KEY}
        endpoint: ${ORG_S3_ENDPOINT}
  sync:
    # Org defaults - read-only for all workspaces
    - name: org-defaults
      path: /home/vscode/.config/org
      remote_path: org/defaults
      direction: pull-only
      interval: 3600
      priority: 0
      exclude:
        - "*.local"  # Allow local overrides
```

### What to Include in Org Defaults

**Claude Code Settings** (`~/.config/org/claude/`):
```
org/defaults/
├── claude/
│   ├── settings.json      # Shared Claude Code settings
│   └── CLAUDE.md          # Org-wide instructions
├── git/
│   └── config             # Org git defaults (signing, hooks)
├── zsh/
│   └── org.zsh            # Org shell functions/aliases
└── security/
    └── allowed-hosts.txt  # Network policy
```

**Example org zsh config** (`org/defaults/zsh/org.zsh`):
```bash
# Org-wide aliases
alias deploy='./scripts/deploy.sh'
alias lint='pre-commit run --all-files'

# Security: warn on dangerous commands
alias rm='rm -i'
alias mv='mv -i'

# Org environment
export ORG_NAME="myorg"
```

**Source in user's zshrc:**
```bash
# ~/.zshrc
[[ -f ~/.config/org/zsh/org.zsh ]] && source ~/.config/org/zsh/org.zsh
```

### Claude Code Configuration

Claude Code stores configuration in `~/.claude/`:

```
~/.claude/
├── settings.json          # User preferences
├── projects/              # Per-project settings
│   └── <project-hash>/
│       └── CLAUDE.md      # Project instructions
└── .credentials.json      # Auth (do NOT sync)
```

**Org-managed Claude settings:**

1. Create org defaults at `org/defaults/claude/settings.json`:
   ```json
   {
     "permissions": {
       "allow_bash": true,
       "allow_file_write": true
     },
     "model": "claude-sonnet-4-20250514"
   }
   ```

2. Sync as pull-only to a staging location
3. Merge or symlink in post-start:
   ```bash
   # post-start.sh addition
   if [[ -f ~/.config/org/claude/settings.json ]]; then
     # Merge org settings with user settings (org as base)
     jq -s '.[0] * .[1]' \
       ~/.config/org/claude/settings.json \
       ~/.claude/settings.json > ~/.claude/settings.json.tmp \
       && mv ~/.claude/settings.json.tmp ~/.claude/settings.json
   fi
   ```

## Layering Example

Here's a complete example showing all layers:

**workspace-spec.yaml:**
```yaml
persistence:
  enabled: true
  default_remote: storage
  remotes:
    storage:
      type: s3
      bucket: workspaces
      options:
        provider: Cloudflare
        access_key_id: ${RCLONE_S3_ACCESS_KEY_ID}
        secret_access_key: ${RCLONE_S3_SECRET_ACCESS_KEY}
        endpoint: ${RCLONE_S3_ENDPOINT}
  sync:
    # Layer 1: Org defaults (read-only)
    - name: org-defaults
      path: /home/vscode/.config/org
      remote_path: org/acme/defaults
      direction: pull-only
      interval: 3600
      priority: 0

    # Layer 2: Developer preferences (read-only from their personal store)
    - name: user-config
      path: /home/vscode/.config/user
      remote_path: users/${USER}/config
      direction: pull-only
      interval: 3600
      priority: 1

    # Layer 3: Project workspace data (read-write)
    - name: workspace
      path: /workspace/data
      remote_path: projects/myproject/data
      direction: bidirectional
      interval: 60
      priority: 10
```

**~/.zshrc (in persisted home volume):**
```bash
# Base config (from image)
source /etc/zsh/zshrc

# Org defaults (pull-only)
[[ -f ~/.config/org/zsh/org.zsh ]] && source ~/.config/org/zsh/org.zsh

# Personal overrides (pull-only or dotfiles)
[[ -f ~/.config/user/zsh/personal.zsh ]] && source ~/.config/user/zsh/personal.zsh

# Project-specific (if any)
[[ -f /workspace/.zshrc.local ]] && source /workspace/.zshrc.local
```

## Best Practices

### Do

- Keep dotfiles simple and portable
- Use environment variables for secrets, never commit them
- Test dotfiles in a fresh container before committing
- Use `pull-only` for shared config to prevent accidental overwrites
- Set low priority (0-1) for config syncs so they run first

### Don't

- Sync `~/.ssh/` private keys (use SSH agent forwarding instead)
- Sync credentials files (`~/.claude/.credentials.json`, `~/.aws/credentials`)
- Use bidirectional sync for config (risks merge conflicts)
- Rely on dotfiles for security-critical settings (bake into image)

### Sensitive Files to Exclude

Always exclude these from sync:
```yaml
exclude:
  - .credentials.json
  - .aws/credentials
  - .ssh/id_*
  - .gnupg/private-keys*
  - .netrc
  - "*.key"
  - "*.pem"
```

## Troubleshooting

### Dotfiles not applied

1. Check VS Code settings include `dotfiles.repository`
2. Verify install script is executable (`chmod +x install.sh`)
3. Check container logs for errors during creation

### Pull-only sync not updating

1. Check daemon is running: `pgrep coltec-daemon`
2. Check health file: `cat ~/.local/share/coltec-daemon/*/health.json`
3. Check credentials are set: `echo $RCLONE_S3_ACCESS_KEY_ID`
4. Run manual sync: `coltec-daemon --once --config /workspace/.devcontainer/workspace-spec.yaml`

### Config conflicts between layers

1. Use distinct paths for each layer (`~/.config/org/`, `~/.config/user/`)
2. Source files in correct order (org before user)
3. Use `pull-only` to prevent local changes from syncing back

## References

- [VS Code Dotfiles Documentation](https://code.visualstudio.com/docs/devcontainers/containers#_personalizing-with-dotfile-repositories)
- [DevPod Dotfiles](https://devpod.sh/docs/developing-in-workspaces/dotfiles-in-a-workspace)
- [Coltec Daemon README](../coltec-daemon/README.md)
