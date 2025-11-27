# Agent Instructions

This is a Coltec-managed workspace. Your primary operating environment is this wrapper repository, but the actual application code lives in the `codebase/` submodule.

## Core Mandates
- **Code Location**: All application source code is in `codebase/`. Do not create source files in the root.
- **Tooling**: Use `mise` to run tasks. Check `mise tasks` to see available commands.
- **Python**: Always use `uv` to invoke python: `uv run python ...` or `uv run pytest`.
- **State**: Store persistent context/memory in `agent-context/`. Use `scratch/` for temporary files.

## Common Workflows

### 1. Setup & Verification
Before starting work, verify the environment:
```bash
mise run info
# If this is a fresh container, ensure dependencies are synced
cd codebase && uv sync  # or npm install, cargo build, etc.
```

### 2. Running Code
Execute commands within the submodule context:
```bash
# Good
cd codebase && uv run python src/main.py
mise run start  # if defined

# Bad
python codebase/src/main.py  # Missing environment context
```

### 3. Version Control
This wrapper repo tracks the *configuration* of the workspace. The `codebase/` submodule tracks the *application*.
- To commit app changes: `cd codebase && git commit ...`
- To checkpoint workspace state: `git commit ...` (in root)
