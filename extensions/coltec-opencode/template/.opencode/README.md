# OpenCode Supervisor Config

This directory holds the default OpenCode configuration for the workspace supervisor agent.
Nexus overlays per-session configs into `.sessions/<id>/.opencode/config.json`.

## Files
- `opencode.json` - Base configuration (theme, keybinds, defaults).
- `.gitignore` - Ignores runtime artifacts (logs/sessions).

## Usage
- Start or attach: `opencode`
- Bypass tmux wrapper: `OPENCODE_TMUX_DISABLE=true opencode start`

## Customization
Keep workspace-wide defaults here. Project/session-specific overrides should be applied via Nexus overlays.
