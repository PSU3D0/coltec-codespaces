# Rust Daemon (Phase 3) Plan

This breaks Phase 3 into milestones with definitions of done, test expectations, and **prescriptive implementation details**.

---

## Current State (as of 2025-11-27)

### What Exists
```
coltec-daemon/
├── Cargo.toml              # Basic deps (serde, schemars, jsonschema, clap, anyhow)
├── src/
│   ├── lib.rs              # ✅ Complete: WorkspaceSpec types with serde + schemars
│   └── bin/validate.rs     # ✅ Complete: JSON Schema validator CLI
├── schema/
│   └── workspace-spec.schema.json  # ✅ Generated schema
└── tests/data/
    └── valid_workspace.yaml        # ✅ Test fixture
```

### What's Missing for Daemon
```
coltec-daemon/src/
├── main.rs       # Entry point, tokio runtime, signal handling
├── cli.rs        # clap Args struct with all flags
├── config.rs     # Semantic validation beyond JSON Schema
├── plan.rs       # Pure sync planner (config → SyncPlan)
├── sync.rs       # rclone execution engine
└── tailscale.rs  # Tailscale setup (optional)
```

### Cargo.toml Changes Needed
```toml
# ADD to [dependencies]
tokio = { version = "1", features = ["rt-multi-thread", "macros", "signal", "time", "process", "fs"] }
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter", "json"] }
directories = "5"
thiserror = "2"

# ADD to [dev-dependencies]
assert_cmd = "2"
predicates = "3"
tempfile = "3"
```

---

## Milestone 1: Config & Types Hardening

### Goal
Rust config types match the workspace-spec contract, including **semantic invariants** beyond JSON Schema.

### Implementation

#### 1.1 Add `config.rs` with semantic validation

**File:** `src/config.rs`

```rust
use crate::{WorkspaceSpec, PersistenceMode};
use thiserror::Error;

#[derive(Error, Debug)]
pub enum ConfigError {
    #[error("persistence.mode='replicated' requires at least one sync path or multi_scope_volumes.environment entry")]
    ReplicatedRequiresSyncPaths,

    #[error("sync path '{name}' has interval < 10s (got {interval}s), minimum is 10s")]
    SyncIntervalTooLow { name: String, interval: u64 },

    #[error("networking.enabled=true but networking.hostname_prefix is empty")]
    NetworkingMissingHostname,

    #[error("duplicate sync path name: '{0}'")]
    DuplicateSyncPathName(String),
}

impl WorkspaceSpec {
    /// Validate semantic invariants beyond JSON Schema.
    pub fn validate_semantics(&self) -> Result<(), ConfigError> {
        // Invariant 1: replicated mode requires sync paths
        if self.persistence.enabled
           && matches!(self.persistence.mode, PersistenceMode::Replicated)
        {
            let has_sync_paths = !self.persistence.sync.is_empty();
            let has_env_volumes = self.persistence.multi_scope_volumes
                .as_ref()
                .map(|v| !v.environment.is_empty())
                .unwrap_or(false);

            if !has_sync_paths && !has_env_volumes {
                return Err(ConfigError::ReplicatedRequiresSyncPaths);
            }
        }

        // Invariant 2: sync intervals must be >= 10s
        for sp in &self.persistence.sync {
            if sp.interval < 10 {
                return Err(ConfigError::SyncIntervalTooLow {
                    name: sp.name.clone(),
                    interval: sp.interval,
                });
            }
        }

        // Invariant 3: networking hostname prefix required if enabled
        if self.networking.enabled && self.networking.hostname_prefix.is_empty() {
            return Err(ConfigError::NetworkingMissingHostname);
        }

        // Invariant 4: no duplicate sync path names
        let mut seen = std::collections::HashSet::new();
        for sp in &self.persistence.sync {
            if !seen.insert(&sp.name) {
                return Err(ConfigError::DuplicateSyncPathName(sp.name.clone()));
            }
        }

        Ok(())
    }
}
```

#### 1.2 Add `load_and_validate()` helper

```rust
// In config.rs
use std::path::Path;

pub fn load_and_validate(path: &Path) -> anyhow::Result<WorkspaceSpec> {
    let content = std::fs::read_to_string(path)
        .with_context(|| format!("failed to read {}", path.display()))?;

    let spec: WorkspaceSpec = serde_yaml::from_str(&content)
        .with_context(|| format!("failed to parse YAML in {}", path.display()))?;

    spec.validate_semantics()
        .with_context(|| format!("semantic validation failed for {}", path.display()))?;

    Ok(spec)
}
```

#### 1.3 Add test fixtures

**File:** `tests/data/invalid_replicated_no_sync.yaml`
```yaml
name: bad-workspace
metadata:
  org: test
  project: test
devcontainer:
  template: { name: python, path: foo }
  image: { name: ghcr.io/foo/bar }
persistence:
  enabled: true
  mode: replicated
  # ERROR: no sync paths defined
```

**File:** `tests/data/invalid_interval_too_low.yaml`
```yaml
name: bad-workspace
metadata:
  org: test
  project: test
devcontainer:
  template: { name: python, path: foo }
  image: { name: ghcr.io/foo/bar }
persistence:
  enabled: true
  mode: replicated
  sync:
    - name: fast
      path: /workspace
      remote_path: foo/bar
      interval: 5  # ERROR: < 10s
```

### Definition of Done
- [ ] `config.rs` exists with `validate_semantics()` method
- [ ] `load_and_validate()` helper loads YAML + validates
- [ ] At least 4 semantic invariants enforced
- [ ] Invalid fixture files created
- [ ] Unit tests pass for valid/invalid fixtures

### Test Commands
```bash
# Unit tests (add to lib.rs or config.rs)
cargo test --lib

# Verify invalid fixtures fail
cargo run --bin coltec-validate -- --file tests/data/invalid_replicated_no_sync.yaml
# Expected: exit 1, error message about replicated requiring sync paths

cargo run --bin coltec-validate -- --file tests/data/invalid_interval_too_low.yaml
# Expected: exit 1, error message about interval < 10s
```

---

## Milestone 2: CLI Surface & Wiring

### Goal
CLI supports `--config`, `--once`, `--dry-run`, `--interval`, `--validate-only`, `--log-format`, `--log-level`.

### Implementation

#### 2.1 Create `src/cli.rs`

```rust
use clap::{Parser, ValueEnum};
use std::path::PathBuf;

#[derive(Debug, Clone, ValueEnum)]
pub enum LogFormat {
    Text,
    Json,
}

#[derive(Parser, Debug)]
#[command(name = "coltec-daemon")]
#[command(about = "Sync daemon for Coltec devcontainer workspaces")]
#[command(version)]
pub struct Args {
    /// Path to workspace-spec.yaml
    #[arg(short, long, default_value = "/workspace/.devcontainer/workspace-spec.yaml")]
    #[arg(env = "COLTEC_CONFIG")]
    pub config: PathBuf,

    /// Run a single sync pass and exit
    #[arg(long)]
    pub once: bool,

    /// Don't actually sync, just log what would happen
    #[arg(long)]
    pub dry_run: bool,

    /// Only validate configuration, don't start daemon
    #[arg(long)]
    pub validate_only: bool,

    /// Override sync interval for all paths (seconds)
    #[arg(long, env = "COLTEC_INTERVAL")]
    pub interval: Option<u64>,

    /// Sync only specific paths by name (can be repeated)
    #[arg(long = "only")]
    pub only_paths: Vec<String>,

    /// Log format
    #[arg(long, default_value = "text", env = "COLTEC_LOG_FORMAT")]
    pub log_format: LogFormat,

    /// Log level (trace, debug, info, warn, error)
    #[arg(long, default_value = "info", env = "RUST_LOG")]
    pub log_level: String,
}

impl Args {
    /// Returns true if daemon should run in continuous mode
    pub fn is_continuous(&self) -> bool {
        !self.once && !self.validate_only
    }
}
```

#### 2.2 Create `src/main.rs` skeleton

```rust
use anyhow::Result;
use clap::Parser;
use tracing::info;

mod cli;
mod config;

use cli::Args;

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();

    // Initialize tracing
    init_tracing(&args);

    info!(config = %args.config.display(), "coltec-daemon starting");

    // Load and validate config
    let spec = config::load_and_validate(&args.config)?;
    info!(workspace = %spec.name, "configuration loaded");

    if args.validate_only {
        println!("✓ configuration valid: {}", args.config.display());
        return Ok(());
    }

    // TODO: Milestone 3+ implementation
    todo!("sync loop not yet implemented");
}

fn init_tracing(args: &Args) {
    use tracing_subscriber::{fmt, EnvFilter};

    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new(&args.log_level));

    match args.log_format {
        cli::LogFormat::Json => {
            fmt().json().with_env_filter(filter).init();
        }
        cli::LogFormat::Text => {
            fmt().with_env_filter(filter).init();
        }
    }
}
```

#### 2.3 Update Cargo.toml

```toml
[[bin]]
name = "coltec-daemon"
path = "src/main.rs"
```

### Definition of Done
- [ ] `src/cli.rs` exists with all flags
- [ ] `src/main.rs` compiles and parses args
- [ ] `--help` shows all flags with descriptions
- [ ] `--validate-only` exits 0 for valid config, 1 for invalid
- [ ] Environment variable overrides work (`COLTEC_CONFIG`, `COLTEC_INTERVAL`, etc.)

### Test Commands
```bash
# Build
cargo build

# Help text
cargo run --bin coltec-daemon -- --help

# Validate-only mode
cargo run --bin coltec-daemon -- --validate-only --config tests/data/valid_workspace.yaml
# Expected: exit 0, "✓ configuration valid"

cargo run --bin coltec-daemon -- --validate-only --config tests/data/invalid_replicated_no_sync.yaml
# Expected: exit 1, error message

# Env override
COLTEC_CONFIG=tests/data/valid_workspace.yaml cargo run --bin coltec-daemon -- --validate-only
# Expected: exit 0
```

---

## Milestone 3: Sync Planning Layer

### Goal
Pure planner converts config into planned sync actions (no IO).

### Implementation

#### 3.1 Create `src/plan.rs`

```rust
use crate::{WorkspaceSpec, SyncPath, SyncDirection, RcloneVolumeConfig};

/// A planned sync action (pure data, no IO)
#[derive(Debug, Clone, PartialEq)]
pub struct SyncAction {
    pub name: String,
    pub local_path: String,
    pub remote_path: String,      // After placeholder resolution
    pub direction: SyncDirection,
    pub interval_secs: u64,
    pub priority: u8,
    pub excludes: Vec<String>,
    pub needs_resync: bool,       // First-time bisync
}

/// The full sync plan
#[derive(Debug, Clone)]
pub struct SyncPlan {
    pub workspace_name: String,
    pub remote_name: String,
    pub actions: Vec<SyncAction>,
}

/// Environment context for resolving placeholders
pub struct PlanContext {
    pub org: String,
    pub project: String,
    pub env: String,
    pub remote_name: String,
}

impl PlanContext {
    pub fn from_spec(spec: &WorkspaceSpec) -> Self {
        Self {
            org: spec.metadata.org.clone(),
            project: spec.metadata.project.clone(),
            env: spec.metadata.environment.clone(),
            remote_name: spec.persistence.rclone_config
                .as_ref()
                .map(|c| c.remote_name.clone())
                .unwrap_or_else(|| "r2coltec".to_string()),
        }
    }

    /// Resolve {org}, {project}, {env} placeholders
    pub fn resolve(&self, template: &str) -> String {
        template
            .replace("{org}", &self.org)
            .replace("{project}", &self.project)
            .replace("{env}", &self.env)
    }
}

/// Build a sync plan from workspace spec
pub fn build_plan(spec: &WorkspaceSpec, interval_override: Option<u64>) -> SyncPlan {
    let ctx = PlanContext::from_spec(spec);
    let mut actions = Vec::new();

    // Add persistence.sync paths
    for sp in &spec.persistence.sync {
        actions.push(SyncAction {
            name: sp.name.clone(),
            local_path: sp.path.clone(),
            remote_path: ctx.resolve(&sp.remote_path),
            direction: sp.direction.clone(),
            interval_secs: interval_override.unwrap_or(sp.interval),
            priority: sp.priority,
            excludes: sp.exclude.clone(),
            needs_resync: false, // Determined at runtime
        });
    }

    // Add multi_scope_volumes.environment
    if let Some(msv) = &spec.persistence.multi_scope_volumes {
        for vol in &msv.environment {
            actions.push(SyncAction {
                name: vol.name.clone(),
                local_path: vol.mount_path.clone(),
                remote_path: ctx.resolve(&vol.remote_path),
                direction: vol.sync.clone(),
                interval_secs: interval_override.unwrap_or(vol.interval),
                priority: vol.priority,
                excludes: vol.exclude.clone(),
                needs_resync: false,
            });
        }
    }

    // Sort by priority (lower = higher priority)
    actions.sort_by_key(|a| a.priority);

    SyncPlan {
        workspace_name: spec.name.clone(),
        remote_name: ctx.remote_name,
        actions,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_placeholder_resolution() {
        let ctx = PlanContext {
            org: "myorg".into(),
            project: "myproj".into(),
            env: "dev".into(),
            remote_name: "r2coltec".into(),
        };

        let resolved = ctx.resolve("workspaces/{org}/{project}/{env}/data");
        assert_eq!(resolved, "workspaces/myorg/myproj/dev/data");
    }

    #[test]
    fn test_build_plan_sorts_by_priority() {
        // Test that actions are sorted by priority
        // ...
    }
}
```

### Definition of Done
- [ ] `src/plan.rs` exists with `SyncAction`, `SyncPlan`, `PlanContext`
- [ ] `build_plan()` function handles both `sync` and `multi_scope_volumes.environment`
- [ ] Placeholder resolution works for `{org}`, `{project}`, `{env}`
- [ ] Actions sorted by priority
- [ ] Unit tests for placeholder resolution and plan building

### Test Commands
```bash
cargo test plan::

# Verify plan output (add a debug print in main.rs temporarily)
cargo run --bin coltec-daemon -- --dry-run --config tests/data/valid_workspace.yaml
```

---

## Milestone 4: Execution Engine

### Goal
Execute planned actions via rclone (or stub), with retries/backoff and clear errors.

### Implementation

#### 4.1 Create `src/sync.rs`

```rust
use crate::plan::{SyncAction, SyncPlan};
use anyhow::{Context, Result};
use std::path::Path;
use std::process::Stdio;
use tokio::process::Command;
use tracing::{info, warn, error, debug, instrument};

/// State directory for bisync tracking
fn state_dir(workspace: &str) -> std::path::PathBuf {
    directories::BaseDirs::new()
        .map(|d| d.data_local_dir().join("coltec-daemon").join(workspace))
        .unwrap_or_else(|| std::path::PathBuf::from("/tmp/coltec-daemon").join(workspace))
}

/// Check if this is a first-time sync (needs --resync)
pub async fn needs_resync(workspace: &str, action_name: &str) -> bool {
    let marker = state_dir(workspace).join(format!("{}.bisync", action_name));
    !marker.exists()
}

/// Mark sync as initialized
pub async fn mark_initialized(workspace: &str, action_name: &str) -> Result<()> {
    let state = state_dir(workspace);
    tokio::fs::create_dir_all(&state).await?;
    let marker = state.join(format!("{}.bisync", action_name));
    tokio::fs::write(&marker, "").await?;
    Ok(())
}

/// Execute a single sync action
#[instrument(skip_all, fields(name = %action.name, direction = ?action.direction))]
pub async fn execute_sync(
    action: &SyncAction,
    remote_name: &str,
    bucket: &str,
    dry_run: bool,
    resync: bool,
) -> Result<()> {
    // Check local path exists
    if !Path::new(&action.local_path).exists() {
        warn!(path = %action.local_path, "local path does not exist, skipping");
        return Ok(());
    }

    let remote = format!("{}:{}/{}", remote_name, bucket, action.remote_path);
    let local = &action.local_path;

    let mut cmd = Command::new("rclone");

    // Choose sync mode based on direction
    use crate::SyncDirection;
    match action.direction {
        SyncDirection::Bidirectional => {
            cmd.args(["bisync", local, &remote]);
            cmd.args(["--resilient", "--recover", "--max-lock", "2m"]);
            if resync {
                info!("first-time sync, using --resync");
                cmd.arg("--resync");
            }
        }
        SyncDirection::PushOnly => {
            cmd.args(["sync", local, &remote]);
        }
        SyncDirection::PullOnly => {
            cmd.args(["sync", &remote, local]);
        }
    }

    // Add excludes
    for pattern in &action.excludes {
        cmd.args(["--exclude", pattern]);
    }

    // Common flags
    cmd.args(["--fast-list", "--transfers", "8", "--checkers", "16"]);

    if dry_run {
        cmd.arg("--dry-run");
        info!(local = %local, remote = %remote, "DRY RUN: would sync");
    }

    debug!(cmd = ?cmd, "executing rclone");

    let output = cmd
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .await
        .context("failed to execute rclone")?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);

        // Known recoverable errors
        if stderr.contains("directory not found") {
            warn!("remote directory not found, will be created on next sync");
            return Ok(());
        }

        error!(stderr = %stderr, "rclone failed");
        anyhow::bail!("rclone failed: {}", stderr);
    }

    info!("sync complete");
    Ok(())
}

/// Execute all actions in a plan (single pass)
pub async fn execute_plan(
    plan: &SyncPlan,
    bucket: &str,
    dry_run: bool,
) -> Result<()> {
    for action in &plan.actions {
        let resync = needs_resync(&plan.workspace_name, &action.name).await;

        execute_sync(action, &plan.remote_name, bucket, dry_run, resync).await?;

        if !dry_run && resync {
            mark_initialized(&plan.workspace_name, &action.name).await?;
        }
    }
    Ok(())
}
```

### Definition of Done
- [ ] `src/sync.rs` exists with `execute_sync()` and `execute_plan()`
- [ ] Supports all three directions: bidirectional, push-only, pull-only
- [ ] Tracks bisync state in `~/.local/share/coltec-daemon/{workspace}/`
- [ ] `--dry-run` adds `--dry-run` to rclone and logs without executing
- [ ] Handles "directory not found" gracefully
- [ ] Clear error messages on rclone failure

### Test Commands
```bash
# Dry run (no actual sync)
RCLONE_BUCKET=test-bucket cargo run --bin coltec-daemon -- \
  --config tests/data/valid_workspace.yaml \
  --once \
  --dry-run

# Integration test with real rclone (requires credentials)
# See Milestone 7 for full integration
```

---

## Milestone 5: Loop & Signals

### Goal
Interval loop runs planner+executor; supports `--once`; graceful shutdown on SIGINT/SIGTERM.

### Implementation

#### 5.1 Update `src/main.rs` with full runtime

```rust
use anyhow::Result;
use clap::Parser;
use tokio::signal;
use tokio::sync::broadcast;
use tokio::time::{interval, Duration};
use tracing::{info, error, warn};

mod cli;
mod config;
mod plan;
mod sync;

use cli::Args;
use plan::build_plan;

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();
    init_tracing(&args);

    let spec = config::load_and_validate(&args.config)?;
    info!(workspace = %spec.name, "configuration loaded");

    if args.validate_only {
        println!("✓ configuration valid: {}", args.config.display());
        return Ok(());
    }

    // Check persistence enabled
    if !spec.persistence.enabled {
        info!("persistence disabled, nothing to sync");
        return Ok(());
    }

    // Build sync plan
    let plan = build_plan(&spec, args.interval);
    info!(actions = plan.actions.len(), "sync plan built");

    if plan.actions.is_empty() {
        warn!("no sync actions in plan");
        return Ok(());
    }

    // Get bucket from env
    let bucket = std::env::var("RCLONE_BUCKET")
        .unwrap_or_else(|_| "coltec-workspaces".into());

    // Single pass mode
    if args.once {
        sync::execute_plan(&plan, &bucket, args.dry_run).await?;
        info!("single sync pass complete");
        return Ok(());
    }

    // Continuous mode with signal handling
    let (shutdown_tx, _) = broadcast::channel::<()>(1);

    // Spawn sync loop
    let shutdown_rx = shutdown_tx.subscribe();
    let plan_clone = plan.clone();
    let bucket_clone = bucket.clone();
    let dry_run = args.dry_run;
    let min_interval = plan.actions.iter().map(|a| a.interval_secs).min().unwrap_or(300);

    let sync_handle = tokio::spawn(async move {
        run_sync_loop(plan_clone, bucket_clone, dry_run, min_interval, shutdown_rx).await
    });

    // Wait for shutdown signal
    tokio::select! {
        _ = signal::ctrl_c() => {
            info!("received SIGINT, shutting down...");
        }
        _ = async {
            #[cfg(unix)]
            {
                let mut sigterm = signal::unix::signal(signal::unix::SignalKind::terminate())?;
                sigterm.recv().await;
                Ok::<_, std::io::Error>(())
            }
            #[cfg(not(unix))]
            std::future::pending::<Result<(), std::io::Error>>()
        } => {
            info!("received SIGTERM, shutting down...");
        }
    }

    // Signal shutdown
    let _ = shutdown_tx.send(());
    let _ = sync_handle.await;

    info!("shutdown complete");
    Ok(())
}

async fn run_sync_loop(
    plan: plan::SyncPlan,
    bucket: String,
    dry_run: bool,
    interval_secs: u64,
    mut shutdown: broadcast::Receiver<()>,
) -> Result<()> {
    let mut ticker = interval(Duration::from_secs(interval_secs));

    loop {
        tokio::select! {
            _ = ticker.tick() => {
                if let Err(e) = sync::execute_plan(&plan, &bucket, dry_run).await {
                    error!(error = %e, "sync pass failed");
                }
            }
            _ = shutdown.recv() => {
                info!("sync loop received shutdown signal");
                return Ok(());
            }
        }
    }
}
```

### Definition of Done
- [ ] `--once` runs single pass and exits
- [ ] Continuous mode runs on interval
- [ ] SIGINT (Ctrl+C) triggers graceful shutdown
- [ ] SIGTERM triggers graceful shutdown
- [ ] In-progress sync completes before exit

### Test Commands
```bash
# Once mode
cargo run --bin coltec-daemon -- --once --dry-run --config tests/data/valid_workspace.yaml

# Continuous mode (Ctrl+C to stop)
cargo run --bin coltec-daemon -- --dry-run --config tests/data/valid_workspace.yaml &
sleep 5
kill -SIGTERM $!
# Expected: graceful shutdown message

# Interval override
cargo run --bin coltec-daemon -- --interval 10 --dry-run --config tests/data/valid_workspace.yaml
```

---

## Milestone 6: Observability & UX

### Goal
Structured logging, pretty validation errors, clear exit codes.

### Implementation

#### 6.1 Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Configuration error (invalid YAML, schema, or semantics) |
| 2 | Runtime error (rclone failure, network error) |
| 130 | Interrupted (SIGINT) |
| 143 | Terminated (SIGTERM) |

#### 6.2 Pretty validation errors

Update `validate.rs` to use colors:
```rust
use yansi::Paint;

// In error output:
eprintln!("{} invalid: {}", Paint::red("✗"), args.file.display());
eprintln!("  {} at {}: {}", Paint::yellow("→"), path, message);
```

#### 6.3 JSON log format

Already implemented in `init_tracing()`. Verify output:
```bash
cargo run --bin coltec-daemon -- --log-format json --validate-only --config tests/data/valid_workspace.yaml
```

### Definition of Done
- [ ] Exit codes documented and implemented
- [ ] `--log-format json` outputs valid JSON lines
- [ ] `--log-format text` is human-readable with colors
- [ ] Validation errors show path to problematic field
- [ ] `--dry-run` clearly indicates no changes made

### Test Commands
```bash
# JSON logs
cargo run --bin coltec-daemon -- --log-format json --once --dry-run --config tests/data/valid_workspace.yaml 2>&1 | jq .

# Exit codes
cargo run --bin coltec-daemon -- --validate-only --config tests/data/valid_workspace.yaml; echo "Exit: $?"
# Expected: Exit: 0

cargo run --bin coltec-daemon -- --validate-only --config tests/data/invalid_replicated_no_sync.yaml; echo "Exit: $?"
# Expected: Exit: 1
```

---

## Milestone 7: Integration Hooks

### Goal
Docker image with daemon binary; post-start script for devcontainer integration; env overrides honored.

### Implementation

#### 7.1 Multi-stage Dockerfile

**File:** `coltec-daemon/Dockerfile`

```dockerfile
# Builder stage - Rust toolchain
FROM rust:1-bookworm AS builder
WORKDIR /build
COPY Cargo.toml Cargo.lock* ./
# ... dependency caching trick ...
COPY src ./src
RUN cargo build --release --bin coltec-daemon --bin coltec-validate
RUN strip target/release/coltec-daemon target/release/coltec-validate

# Runtime stage - minimal debian with rclone
FROM debian:bookworm-slim AS runtime
RUN apt-get update && apt-get install -y ca-certificates curl && \
    curl -fsSL https://rclone.org/install.sh | bash
COPY --from=builder /build/target/release/coltec-daemon /usr/local/bin/
COPY --from=builder /build/target/release/coltec-validate /usr/local/bin/
ENTRYPOINT ["coltec-daemon"]
```

**Build:** `docker build -t coltec-daemon .`
**Size:** ~50MB (vs ~2GB with build deps)

#### 7.2 post-start.sh integration script

**File:** `template/.devcontainer/scripts/post-start.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail

CONFIG_PATH="${COLTEC_CONFIG:-/workspace/.devcontainer/workspace-spec.yaml}"

# Skip if disabled
[[ "${COLTEC_DISABLED:-false}" == "true" ]] && exit 0

# Validate before starting
coltec-validate --file "$CONFIG_PATH" || exit 1

# Start daemon (exec replaces shell for proper signal handling)
exec coltec-daemon --config "$CONFIG_PATH" ${COLTEC_DAEMON_ARGS:-}
```

#### 7.3 Environment variables reference

| Variable | Default | Description |
|----------|---------|-------------|
| `COLTEC_CONFIG` | `/workspace/.devcontainer/workspace-spec.yaml` | Config path |
| `COLTEC_INTERVAL` | (from config) | Override sync interval |
| `COLTEC_LOG_FORMAT` | `text` | `text` or `json` |
| `COLTEC_LOG_LEVEL` | `info` | Log level (trace/debug/info/warn/error) |
| `RCLONE_BUCKET` | `coltec-workspaces` | S3/R2 bucket name |
| `COLTEC_DAEMON_ARGS` | (none) | Extra args for daemon |
| `COLTEC_DISABLED` | `false` | Set to `true` to skip daemon startup |

#### 7.4 rclone configuration

rclone credentials are passed via environment variables:

```bash
# For Cloudflare R2
RCLONE_CONFIG_R2COLTEC_TYPE=s3
RCLONE_CONFIG_R2COLTEC_PROVIDER=Cloudflare
RCLONE_CONFIG_R2COLTEC_ACCESS_KEY_ID=<key>
RCLONE_CONFIG_R2COLTEC_SECRET_ACCESS_KEY=<secret>
RCLONE_CONFIG_R2COLTEC_ENDPOINT=https://<account>.r2.cloudflarestorage.com
```

### Definition of Done
- [x] Multi-stage Dockerfile created
- [x] post-start.sh script with validation
- [x] All env overrides documented
- [x] .dockerignore for faster builds
- [ ] Docker image builds successfully
- [ ] End-to-end test in container

### Test Commands
```bash
# Build Docker image
docker build -t coltec-daemon .

# Check image size
docker images coltec-daemon

# Test validate in container
docker run --rm coltec-daemon coltec-validate --help

# Test daemon help
docker run --rm coltec-daemon --help

# Full end-to-end (in generated workspace)
coltec-validate --file .devcontainer/workspace-spec.yaml && \
  coltec-daemon --once --dry-run --config .devcontainer/workspace-spec.yaml
```

---

## Implementation Order

```
Milestone 1 (Config)  ─┐
                       ├─► Milestone 2 (CLI) ─► Milestone 3 (Plan) ─┐
                       │                                            │
                       │   ┌────────────────────────────────────────┘
                       │   │
                       │   ▼
                       │   Milestone 4 (Sync) ─► Milestone 5 (Loop) ─► Milestone 6 (UX)
                       │                                                      │
                       │                                                      ▼
                       └──────────────────────────────────────────► Milestone 7 (Hooks)
```

**Estimated file changes:**
- `Cargo.toml`: Add dependencies
- `src/lib.rs`: Export config module
- `src/config.rs`: NEW - semantic validation
- `src/cli.rs`: NEW - CLI args
- `src/main.rs`: NEW - daemon entry point
- `src/plan.rs`: NEW - sync planner
- `src/sync.rs`: NEW - rclone executor
- `tests/data/invalid_*.yaml`: NEW - test fixtures

---

## Quick Reference: Cargo Commands

```bash
# Build all binaries
cargo build --release

# Run validator
cargo run --bin coltec-validate -- --file <spec.yaml>

# Run daemon
cargo run --bin coltec-daemon -- --help
cargo run --bin coltec-daemon -- --validate-only --config <spec.yaml>
cargo run --bin coltec-daemon -- --once --dry-run --config <spec.yaml>

# Run tests
cargo test

# Check binary size
ls -lh target/release/coltec-daemon
# Target: < 5MB
```
