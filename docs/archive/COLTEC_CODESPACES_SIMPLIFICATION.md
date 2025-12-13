# Coltec Codespaces Simplification

## Design Document & Implementation Plan

**Status:** Draft
**Author:** Claude + Frank
**Created:** 2025-11-27
**Last Updated:** 2025-11-27

---

## Executive Summary

Simplify coltec-codespaces from a ~2000 line Python package to a composition of:
- **copier** for template management
- **Rust daemon** for sync orchestration (efficient at scale)
- **mise** for task orchestration
- **DevPod** (optional) for workspace lifecycle
- **rclone** for persistence
- **Tailscale** for networking
- **Rust-generated JSON Schema + coltec-validate** for validation (ajv optional)

**Goal:** Replace custom Python with purpose-built components that scale to 50+ concurrent devcontainers.

### Core Capabilities Preserved
1. Consistent declaration semantics with validation
2. Configuration resync from nexus root
3. Service registration and discoverability
4. Global org policy/installs
5. Per-user customization

---

## Architecture Overview

### Why This Stack?

| Component | Purpose | Why Not Alternatives |
|-----------|---------|---------------------|
| **Rust daemon** | Sync orchestration | Bash: brittle at scale. Python: 5x memory overhead |
| **copier** | Template management | Cookiecutter: no update support. Custom: reinventing wheel |
| **JSON Schema** | Config validation | Pydantic: requires Python runtime |
| **mise** | Task orchestration | Make: poor UX. Just: less ecosystem |
| **rclone** | Cloud sync | rsync: no native S3/R2 support |

### High-Level Design

```
┌─────────────────────────────────────────────────────────────────┐
│                         NEXUS (Orchestrator)                     │
│  mise.toml: cs:new, cs:update, cs:up, cs:list, cs:validate      │
└─────────────────────────────────────────────────────────────────┘
                                │
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
            ┌───────────┐ ┌─────────┐ ┌──────────┐
            │  copier   │ │ DevPod/ │ │   ajv    │
            │ (template)│ │ devcon  │ │(validate)│
            └───────────┘ └─────────┘ └──────────┘
                    │           │
                    ▼           ▼
            ┌─────────────────────────────────────┐
            │         Generated Workspace          │
            │  .devcontainer/                      │
            │  ├── devcontainer.json               │
            │  ├── workspace-spec.yaml             │
            │  └── scripts/                        │
            │      └── post-start.sh               │
            └─────────────────────────────────────┘
                                │
                                ▼
            ┌─────────────────────────────────────┐
            │     coltec-base-image (Docker)       │
            │  /usr/local/bin/                     │
            │  ├── coltec-daemon   ◄── Rust binary │
            │  ├── rclone                          │
            │  └── tailscale                       │
            └─────────────────────────────────────┘
                                │
                    ┌───────────┴───────────┐
                    ▼                       ▼
            ┌─────────────┐         ┌─────────────┐
            │   rclone    │         │  Tailscale  │
            │ (sync to R2)│         │ (networking)│
            └─────────────┘         └─────────────┘
```

### Repository Structure (Target)

```
coltec-codespaces/                     # Single repo (this repo)
├── copier.yaml                        # Template configuration (root; _subdirectory: template)
├── template/                          # Copier template directory
│   ├── .devcontainer/
│   │   ├── devcontainer.json.jinja    # Single template with project_type conditionals
│   │   ├── workspace-spec.yaml.jinja  # Emits spec consumed by Rust schema/validator
│   │   └── scripts/
│   │       ├── post-create.sh.jinja
│   │       └── post-start.sh.jinja
│   ├── README-coltec-workspace.md.jinja
│   └── {{ _copier_conf.answers_file }}.jinja
├── schema/
│   └── workspace-spec.schema.json     # Generated from Rust types
├── coltec-daemon/                     # Rust crate (in-repo)
├── Cargo.toml
├── src/
│   ├── main.rs                        # Entry point, signal handling
│   ├── config.rs                      # workspace-spec.yaml parsing
│   ├── sync.rs                        # rclone orchestration
│   ├── tailscale.rs                   # Tailscale setup
│   └── cli.rs                         # CLI args (--once, --dry-run)
├── tests/
│   ├── config_test.rs
│   └── integration_test.rs
└── README.md

coltec-base-image/                     # Docker image repo
├── Dockerfile                         # Multi-stage with Rust build
├── scripts/
│   └── entrypoint.sh
└── README.md

nexus/                                 # Orchestrator repo (simplified)
├── mise.toml                          # cs:* tasks
├── storage-config.yaml                # Secrets reference
├── codespaces/                        # Generated workspaces
│   ├── coltec/coltec-codespaces-dev/
│   ├── formualizer/formualizer-dev/
│   └── ...
└── COLTEC_CODESPACES_SIMPLIFICATION.md  # This document
```

---

## Component Deep Dives

### Rust Daemon (`coltec-daemon`)

#### Why Rust?

For **50+ parallel devcontainers**, efficiency is non-negotiable:

| Metric | Bash | Python | Rust |
|--------|------|--------|------|
| Memory per daemon | ~15MB | ~25MB | <5MB |
| 50 containers overhead | ~750MB | ~1.25GB | ~250MB |
| Concurrent intervals | Multiple sleep loops | Threading/asyncio | tokio (single thread) |
| Config parsing | yq + string ops | PyYAML | serde (zero-copy) |
| Binary size | N/A | N/A | ~3-5MB |
| Startup time | ~100ms | ~500ms | ~10ms |

#### Cargo.toml

```toml
[package]
name = "coltec-daemon"
version = "0.1.0"
edition = "2021"
description = "Sync daemon for Coltec devcontainer workspaces"
license = "MIT"

[dependencies]
tokio = { version = "1", features = ["full", "signal"] }
serde = { version = "1", features = ["derive"] }
serde_yaml = "0.9"
anyhow = "1"
thiserror = "1"
tracing = "0.1"
tracing-subscriber = { version = "0.3", features = ["env-filter", "json"] }
clap = { version = "4", features = ["derive"] }
notify = "6"                    # File system watching (optional)
directories = "5"               # XDG directories

[dev-dependencies]
tempfile = "3"
assert_cmd = "2"
predicates = "3"

[profile.release]
lto = true
codegen-units = 1
strip = true
```

#### Source: `src/main.rs`

```rust
use anyhow::Result;
use clap::Parser;
use tokio::signal;
use tokio::sync::broadcast;
use tracing::{info, error, warn};

mod cli;
mod config;
mod sync;
mod tailscale;

use cli::Args;
use config::WorkspaceSpec;

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();

    // Initialize tracing
    tracing_subscriber::fmt()
        .with_env_filter(
            std::env::var("RUST_LOG")
                .unwrap_or_else(|_| "coltec_daemon=info".into())
        )
        .json()
        .init();

    // Load configuration
    let spec = config::load(&args.config)?;
    info!(
        workspace = %spec.name,
        org = %spec.metadata.org,
        project = %spec.metadata.project,
        "Starting coltec-daemon"
    );

    // Handle --validate-only
    if args.validate_only {
        info!("Configuration valid");
        return Ok(());
    }

    // Setup shutdown signal
    let (shutdown_tx, _) = broadcast::channel::<()>(1);

    // Start Tailscale if enabled
    if spec.networking.enabled && !args.dry_run {
        tailscale::setup(&spec).await?;
    }

    // Check persistence mode
    if !spec.persistence.enabled {
        info!("Persistence disabled, nothing to sync");
        return Ok(());
    }

    if spec.persistence.mode != "replicated" {
        warn!(mode = %spec.persistence.mode, "Only 'replicated' mode supported");
        return Ok(());
    }

    // Spawn sync tasks for each path
    let mut handles = vec![];

    for sync_path in spec.persistence.sync.clone() {
        let shutdown_rx = shutdown_tx.subscribe();
        let dry_run = args.dry_run;
        let once = args.once;

        let handle = tokio::spawn(async move {
            sync::run_sync_loop(sync_path, shutdown_rx, dry_run, once).await
        });
        handles.push(handle);
    }

    // If --once, wait for all syncs then exit
    if args.once {
        for handle in handles {
            if let Err(e) = handle.await {
                error!(error = %e, "Sync task failed");
            }
        }
        info!("Single sync pass complete");
        return Ok(());
    }

    // Wait for shutdown signal
    tokio::select! {
        _ = signal::ctrl_c() => {
            info!("Received SIGINT, shutting down...");
        }
        _ = async {
            #[cfg(unix)]
            {
                let mut sigterm = signal::unix::signal(signal::unix::SignalKind::terminate())?;
                sigterm.recv().await;
                Ok::<_, std::io::Error>(())
            }
            #[cfg(not(unix))]
            futures::future::pending::<Result<(), std::io::Error>>()
        } => {
            info!("Received SIGTERM, shutting down...");
        }
    }

    // Signal all tasks to stop
    let _ = shutdown_tx.send(());

    // Wait for tasks to complete
    for handle in handles {
        let _ = handle.await;
    }

    info!("Shutdown complete");
    Ok(())
}
```

#### Source: `src/cli.rs`

```rust
use clap::Parser;
use std::path::PathBuf;

#[derive(Parser, Debug)]
#[command(name = "coltec-daemon")]
#[command(about = "Sync daemon for Coltec devcontainer workspaces")]
#[command(version)]
pub struct Args {
    /// Path to workspace-spec.yaml
    #[arg(short, long, default_value = "/workspace/.devcontainer/workspace-spec.yaml")]
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

    /// Sync a specific path by name (can be repeated)
    #[arg(long)]
    pub only: Vec<String>,
}
```

#### Source: `src/config.rs`

```rust
use anyhow::{Context, Result};
use serde::Deserialize;
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Deserialize)]
pub struct WorkspaceSpec {
    pub name: String,
    #[serde(default)]
    pub version: String,
    pub metadata: Metadata,
    #[serde(default)]
    pub persistence: Persistence,
    #[serde(default)]
    pub networking: Networking,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Metadata {
    pub org: String,
    pub project: String,
    #[serde(default)]
    pub environment: Option<String>,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default)]
    pub tags: Vec<String>,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct Persistence {
    #[serde(default)]
    pub enabled: bool,
    #[serde(default = "default_mode")]
    pub mode: String,
    #[serde(default)]
    pub sync: Vec<SyncPath>,
    #[serde(default)]
    pub rclone_config: Option<RcloneConfig>,
}

fn default_mode() -> String {
    "replicated".into()
}

#[derive(Debug, Clone, Deserialize)]
pub struct SyncPath {
    pub name: String,
    pub path: PathBuf,
    pub remote_path: String,
    #[serde(default = "default_direction")]
    pub direction: Direction,
    #[serde(default = "default_interval")]
    pub interval: u64,
    #[serde(default = "default_priority")]
    pub priority: u8,
    #[serde(default)]
    pub exclude: Vec<String>,
}

#[derive(Debug, Clone, Default, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum Direction {
    #[default]
    Bidirectional,
    PullOnly,
    PushOnly,
}

fn default_direction() -> Direction {
    Direction::Bidirectional
}

fn default_interval() -> u64 {
    300
}

fn default_priority() -> u8 {
    2
}

#[derive(Debug, Clone, Deserialize)]
pub struct RcloneConfig {
    pub remote_name: String,
    #[serde(rename = "type")]
    pub remote_type: String,
    #[serde(default)]
    pub options: std::collections::HashMap<String, String>,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct Networking {
    #[serde(default)]
    pub enabled: bool,
    #[serde(default = "default_hostname_prefix")]
    pub hostname_prefix: String,
    #[serde(default)]
    pub tags: Vec<String>,
}

fn default_hostname_prefix() -> String {
    "dev-".into()
}

impl SyncPath {
    /// Resolve {org}, {project}, {env} placeholders in remote_path
    pub fn resolve_remote_path(&self) -> Result<String> {
        let org = std::env::var("WORKSPACE_ORG")
            .context("WORKSPACE_ORG not set")?;
        let project = std::env::var("WORKSPACE_PROJECT")
            .context("WORKSPACE_PROJECT not set")?;
        let env = std::env::var("WORKSPACE_ENV")
            .unwrap_or_else(|_| "dev".into());

        Ok(self.remote_path
            .replace("{org}", &org)
            .replace("{project}", &project)
            .replace("{env}", &env))
    }
}

pub fn load(path: &Path) -> Result<WorkspaceSpec> {
    let content = std::fs::read_to_string(path)
        .with_context(|| format!("Failed to read {}", path.display()))?;

    let spec: WorkspaceSpec = serde_yaml::from_str(&content)
        .with_context(|| format!("Failed to parse {}", path.display()))?;

    // Validate
    if spec.persistence.enabled && spec.persistence.sync.is_empty() {
        anyhow::bail!("Persistence enabled but no sync paths defined");
    }

    Ok(spec)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_minimal_spec() {
        let yaml = r#"
name: test-workspace
metadata:
  org: testorg
  project: testproject
"#;
        let spec: WorkspaceSpec = serde_yaml::from_str(yaml).unwrap();
        assert_eq!(spec.name, "test-workspace");
        assert!(!spec.persistence.enabled);
    }

    #[test]
    fn test_parse_full_spec() {
        let yaml = r#"
name: test-workspace
metadata:
  org: testorg
  project: testproject
  environment: dev
persistence:
  enabled: true
  mode: replicated
  sync:
    - name: workspace
      path: /workspace
      remote_path: workspaces/{org}/{project}/{env}/workspace
      direction: bidirectional
      interval: 120
      exclude:
        - .git/**
        - node_modules/**
"#;
        let spec: WorkspaceSpec = serde_yaml::from_str(yaml).unwrap();
        assert_eq!(spec.persistence.sync.len(), 1);
        assert_eq!(spec.persistence.sync[0].interval, 120);
    }
}
```

#### Source: `src/sync.rs`

```rust
use anyhow::{Context, Result};
use tokio::process::Command;
use tokio::sync::broadcast;
use tokio::time::{interval, Duration, Instant};
use tracing::{info, error, warn, debug, instrument};

use crate::config::{SyncPath, Direction};

/// State directory for bisync
fn state_dir() -> std::path::PathBuf {
    let xdg = directories::BaseDirs::new()
        .map(|d| d.data_local_dir().to_path_buf())
        .unwrap_or_else(|| std::path::PathBuf::from("/tmp"));

    let workspace = std::env::var("WORKSPACE_NAME")
        .unwrap_or_else(|_| "unknown".into());

    xdg.join("coltec-daemon").join(&workspace)
}

/// Run the sync loop for a single path
#[instrument(skip_all, fields(name = %sync_path.name, interval = sync_path.interval))]
pub async fn run_sync_loop(
    sync_path: SyncPath,
    mut shutdown: broadcast::Receiver<()>,
    dry_run: bool,
    once: bool,
) -> Result<()> {
    let mut ticker = interval(Duration::from_secs(sync_path.interval));
    let mut first_run = true;
    let mut last_sync: Option<Instant> = None;

    // Ensure state directory exists
    let state = state_dir();
    tokio::fs::create_dir_all(&state).await?;

    loop {
        tokio::select! {
            _ = ticker.tick() => {
                // Skip first immediate tick if not first run
                if !first_run && last_sync.map(|t| t.elapsed().as_secs() < 5).unwrap_or(false) {
                    continue;
                }

                let resync = first_run && !state.join(format!("{}.bisync", sync_path.name)).exists();

                match execute_sync(&sync_path, dry_run, resync).await {
                    Ok(_) => {
                        last_sync = Some(Instant::now());

                        // Mark as initialized
                        if first_run {
                            let marker = state.join(format!("{}.bisync", sync_path.name));
                            let _ = tokio::fs::write(&marker, "").await;
                        }
                    }
                    Err(e) => {
                        error!(error = %e, "Sync failed");
                    }
                }

                first_run = false;

                if once {
                    return Ok(());
                }
            }
            _ = shutdown.recv() => {
                info!("Received shutdown signal");
                return Ok(());
            }
        }
    }
}

#[instrument(skip_all, fields(
    name = %sync_path.name,
    local = %sync_path.path.display(),
    dry_run = dry_run,
    resync = resync
))]
async fn execute_sync(sync_path: &SyncPath, dry_run: bool, resync: bool) -> Result<()> {
    // Check if local path exists
    if !sync_path.path.exists() {
        warn!(path = %sync_path.path.display(), "Local path does not exist, skipping");
        return Ok(());
    }

    let remote_name = std::env::var("RCLONE_REMOTE_NAME")
        .context("RCLONE_REMOTE_NAME not set")?;
    let bucket = std::env::var("RCLONE_BUCKET")
        .context("RCLONE_BUCKET not set")?;

    // Extract bucket name from URL if needed
    let bucket_name = if bucket.starts_with("https://") {
        bucket
            .trim_start_matches("https://")
            .split('.')
            .next()
            .unwrap_or(&bucket)
            .to_string()
    } else {
        bucket
    };

    let remote_path = sync_path.resolve_remote_path()?;
    let remote = format!("{}:{}/{}", remote_name, bucket_name, remote_path);
    let local = sync_path.path.to_string_lossy();

    debug!(remote = %remote, "Resolved remote path");

    let mut cmd = Command::new("rclone");

    // Choose sync mode
    match sync_path.direction {
        Direction::Bidirectional => {
            cmd.args(["bisync", &local, &remote]);
            cmd.args(["--resilient", "--recover", "--max-lock", "2m"]);

            if resync {
                info!("First-time sync, running with --resync");
                cmd.arg("--resync");
            }
        }
        Direction::PushOnly => {
            cmd.args(["sync", &local, &remote]);
        }
        Direction::PullOnly => {
            cmd.args(["sync", &remote, &local]);
        }
    }

    // Add excludes
    for pattern in &sync_path.exclude {
        cmd.args(["--exclude", pattern]);
    }

    // Common flags
    cmd.args([
        "--fast-list",
        "--transfers", "8",
        "--checkers", "16",
    ]);

    if dry_run {
        cmd.arg("--dry-run");
        info!("DRY RUN: would sync {} <-> {}", local, remote);
    }

    debug!(cmd = ?cmd, "Executing rclone");

    let output = cmd.output().await
        .context("Failed to execute rclone")?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);

        // Check for known recoverable errors
        if stderr.contains("directory not found") {
            warn!("Remote directory not found, will be created on next sync");
            return Ok(());
        }

        anyhow::bail!("rclone failed: {}", stderr);
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    if !stdout.is_empty() {
        debug!(output = %stdout, "rclone output");
    }

    info!("Sync complete");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    #[test]
    fn test_state_dir() {
        std::env::set_var("WORKSPACE_NAME", "test-workspace");
        let dir = state_dir();
        assert!(dir.to_string_lossy().contains("coltec-daemon"));
        assert!(dir.to_string_lossy().contains("test-workspace"));
    }
}
```

#### Source: `src/tailscale.rs`

```rust
use anyhow::{Context, Result};
use tokio::process::Command;
use tracing::{info, debug};

use crate::config::WorkspaceSpec;

pub async fn setup(spec: &WorkspaceSpec) -> Result<()> {
    let auth_key = std::env::var("TAILSCALE_AUTH_KEY")
        .context("TAILSCALE_AUTH_KEY not set but networking.enabled=true")?;

    let hostname = format!(
        "{}{}",
        spec.networking.hostname_prefix,
        spec.name
    );

    info!(hostname = %hostname, "Setting up Tailscale");

    // Check if tailscaled is running
    let status = Command::new("tailscale")
        .args(["status", "--json"])
        .output()
        .await;

    let needs_start = match status {
        Ok(output) => !output.status.success(),
        Err(_) => true,
    };

    if needs_start {
        info!("Starting tailscaled...");
        Command::new("tailscaled")
            .args(["--tun=userspace-networking", "--socks5-server=localhost:1055"])
            .spawn()
            .context("Failed to start tailscaled")?;

        // Wait for tailscaled to be ready
        tokio::time::sleep(tokio::time::Duration::from_secs(2)).await;
    }

    // Bring up Tailscale
    let mut cmd = Command::new("tailscale");
    cmd.args(["up", "--hostname", &hostname, "--authkey", &auth_key]);

    // Add tags
    if !spec.networking.tags.is_empty() {
        let tags = spec.networking.tags.join(",");
        cmd.args(["--advertise-tags", &tags]);
    }

    cmd.arg("--accept-dns=false");

    debug!(cmd = ?cmd, "Running tailscale up");

    let output = cmd.output().await
        .context("Failed to run tailscale up")?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        anyhow::bail!("tailscale up failed: {}", stderr);
    }

    // Get assigned IP
    let ip_output = Command::new("tailscale")
        .args(["ip", "-4"])
        .output()
        .await?;

    let ip = String::from_utf8_lossy(&ip_output.stdout);
    info!(ip = %ip.trim(), hostname = %hostname, "Tailscale connected");

    Ok(())
}
```

### Docker Image

```dockerfile
# coltec-base-image/Dockerfile

# =============================================================================
# Stage 1: Build Rust daemon
# =============================================================================
FROM rust:1.82-slim as rust-builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y \
    pkg-config \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy and build daemon
COPY coltec-daemon/Cargo.toml coltec-daemon/Cargo.lock ./
COPY coltec-daemon/src ./src

RUN cargo build --release && \
    strip target/release/coltec-daemon

# =============================================================================
# Stage 2: Runtime image
# =============================================================================
FROM mcr.microsoft.com/devcontainers/base:ubuntu-22.04

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    curl \
    ca-certificates \
    unzip \
    yq \
    && rm -rf /var/lib/apt/lists/*

# Install rclone
RUN curl -O https://downloads.rclone.org/rclone-current-linux-amd64.zip && \
    unzip rclone-current-linux-amd64.zip && \
    cp rclone-*-linux-amd64/rclone /usr/local/bin/ && \
    chmod +x /usr/local/bin/rclone && \
    rm -rf rclone-*

# Install Tailscale
RUN curl -fsSL https://tailscale.com/install.sh | sh

# Install mise
RUN curl https://mise.run | sh && \
    mv ~/.local/bin/mise /usr/local/bin/

# Copy Rust daemon from builder
COPY --from=rust-builder /build/target/release/coltec-daemon /usr/local/bin/

# Verify installations
RUN coltec-daemon --version && \
    rclone --version && \
    tailscale --version && \
    mise --version

# Default user
USER vscode
WORKDIR /workspace
```

---

## Implementation Phases

---

## Phase 0: Test Infrastructure

### Why
Establish test-driven development before making changes.

### How
1. Create test fixtures from current working state
2. Set up validation tests for workspace-spec.yaml
3. Create integration test for full workspace lifecycle

### Where
```
nexus/
└── tests/
    ├── fixtures/
    │   ├── workspace-spec-valid.yaml
    │   ├── workspace-spec-invalid.yaml
    │   └── expected-devcontainer.json
    ├── test_workspace_spec.py
    └── test_workspace_lifecycle.sh
```

### Definition of Done
- [ ] Test fixtures captured from current coltec-codespaces-dev
- [ ] Validation tests passing against current implementation
- [ ] E2E lifecycle test script working

### Test-Driven Completion Criteria
```bash
# All must pass
./tests/test_workspace_lifecycle.sh
# Creates workspace, starts container, verifies services, cleans up
```

---

## Phase 1: JSON Schema Validation

### Why
Replace Pydantic with Rust types + schemars, generating the authoritative JSON Schema from Rust and validating with a Rust CLI (ajv optional/secondary).

### How
1. Define workspace-spec types in `coltec-daemon` using `serde` + `schemars`.
2. Generate `coltec-daemon/schema/workspace-spec.schema.json` from those types (authoritative schema).
3. Provide a Rust CLI validator (`coltec-validate`) that pretty-prints errors against the generated schema.
4. Optional: keep `ajv-cli` as a secondary validator pointing at the generated schema.

### Where
```
coltec-daemon/
├── src/lib.rs                 # types + schemars
├── src/bin/validate.rs        # validator CLI
├── schema/workspace-spec.schema.json  # generated
└── tests/data/*.yaml          # fixtures
```

### Definition of Done
- [x] Rust types cover all workspace-spec.yaml fields
- [x] Generated schema produced from Rust types
- [x] `coltec-validate --file <spec>` passes on real workspaces and fails with helpful errors on bad specs
- [ ] (Optional) `ajv validate -s coltec-daemon/schema/workspace-spec.schema.json -d <spec>` works

### Test-Driven Completion Criteria
```bash
# Rust validator (authoritative)
cargo test --manifest-path coltec-daemon/Cargo.toml
coltec-validate --file coltec-daemon/tests/data/valid_workspace.yaml   # Exit 0
coltec-validate --file coltec-daemon/tests/data/invalid_workspace.yaml # Exit 1, pretty error

# Optional ajv check (if installed)
ajv validate -s coltec-daemon/schema/workspace-spec.schema.json -d coltec-daemon/tests/data/valid_workspace.yaml
ajv validate -s coltec-daemon/schema/workspace-spec.schema.json -d coltec-daemon/tests/data/invalid_workspace.yaml
```

---

## Phase 2: Copier Template

### Why
Replace custom Python template rendering with copier; template lives in-repo under `template/` (no separate template repo for now).

### How
1. Keep `copier.yaml` at repo root with `_subdirectory: template`.
2. Convert devcontainer + workspace-spec Jinja2 to a single copier-friendly template with `project_type` conditionals.
3. Render devcontainer.json and workspace-spec.yaml during `copier copy`.
4. Ensure template is git-tracked so `copier update` works (copier needs a VCS reference for diffs).
5. Test `copier copy` and `copier update` against a temporary workspace.

### Where
```
copier.yaml          # root
template/.devcontainer/devcontainer.json.jinja
template/.devcontainer/workspace-spec.yaml.jinja
template/.devcontainer/scripts/{post-create,post-start}.sh.jinja
template/README-coltec-workspace.md.jinja
template/{{ _copier_conf.answers_file }}.jinja
```

### copier.yaml (Key Sections)

```yaml
_min_copier_version: "9.0.0"
_subdirectory: template
_answers_file: .copier-answers.yml

_tasks:
  - "chmod +x .devcontainer/scripts/*.sh"

org:
  type: str
  help: Organization slug
  validator: "{% if not org | regex_search('^[a-z0-9-]+$') %}Invalid{% endif %}"

project:
  type: str
  help: Project slug

project_type:
  type: str
  choices:
    python: Python project
    rust: Rust project
    node: Node.js project
  default: python

persistence_enabled:
  type: bool
  default: true

sync_interval:
  type: int
  default: 120
  when: "{{ persistence_enabled }}"
```

### Definition of Done
- [x] `copier copy` generates working workspace from in-repo template
- [ ] `copier update` applies template changes (requires template path to be git-tracked)
- [x] Generated files pass Rust schema validation (`coltec-validate`)
- [ ] At least one real workspace migrated

### Test-Driven Completion Criteria
```bash
# Generate workspace from local template
uv tool run copier copy . /tmp/test-workspace --trust

# Validate spec via Rust
coltec-validate --file /tmp/test-workspace/.devcontainer/workspace-spec.yaml

# Update flow (template must be git-tracked)
uv tool run copier update /tmp/test-workspace --trust
```

---

## Phase 3: Rust Daemon

### Why
Bash scripts don't scale to 50+ containers. Rust provides:
- 5x lower memory footprint
- Proper async concurrency
- Type-safe config parsing
- Reliable signal handling

### Current State
Phase 1 (types/schema) is complete. Phase 3 implementation is broken into 7 milestones in `RUST_DAEMON_PLAN.md`.

### Where
```
coltec-daemon/
├── Cargo.toml
├── src/
│   ├── lib.rs          # ✅ Complete: types + schema
│   ├── bin/validate.rs # ✅ Complete: validator CLI
│   ├── main.rs         # TODO: daemon entry point
│   ├── cli.rs          # TODO: daemon CLI args
│   ├── config.rs       # TODO: semantic validation
│   ├── plan.rs         # TODO: sync planner
│   ├── sync.rs         # TODO: rclone executor
│   └── tailscale.rs    # TODO: optional networking
├── schema/
│   └── workspace-spec.schema.json  # ✅ Generated
└── tests/data/
    └── valid_workspace.yaml        # ✅ Test fixture
```

### Milestones (see RUST_DAEMON_PLAN.md for details)
1. **Config & Types Hardening** - Semantic validation beyond JSON Schema
2. **CLI Surface & Wiring** - --config, --once, --dry-run, --validate-only, etc.
3. **Sync Planning Layer** - Pure planner: config → SyncPlan
4. **Execution Engine** - rclone integration with retries/backoff
5. **Loop & Signals** - Interval loop, graceful shutdown
6. **Observability & UX** - Structured logging, exit codes
7. **Integration Hooks** - post-start.sh integration

### Definition of Done
- [ ] `cargo build --release` produces <5MB binary
- [ ] Daemon parses workspace-spec.yaml correctly
- [ ] Semantic validation catches invariant violations
- [ ] `--dry-run` shows what would sync without syncing
- [ ] `--once` completes single sync pass and exits
- [ ] Graceful shutdown on SIGTERM/SIGINT
- [ ] Integration test with real R2

### Test-Driven Completion Criteria
```bash
# Unit tests
cargo test

# Config parsing + semantic validation
coltec-daemon --config tests/data/valid_workspace.yaml --validate-only
# Exit 0

coltec-daemon --config tests/data/invalid_replicated_no_sync.yaml --validate-only
# Exit 1, error about replicated requiring sync paths

# Dry run
coltec-daemon --config tests/data/valid_workspace.yaml --once --dry-run
# Logs what would sync, exit 0

# Integration (requires R2 credentials)
RCLONE_BUCKET=coltec-workspaces coltec-daemon --config tests/data/valid_workspace.yaml --once
# Actually syncs, exit 0

# Memory check
/usr/bin/time -v coltec-daemon --once 2>&1 | grep "Maximum resident"
# Should be < 10MB
```

---

## Phase 4: Docker Image & Integration

### Why
Bundle daemon into base image for zero-setup in workspaces.

### How
1. Create multi-stage Dockerfile
2. Build and push to GHCR
3. Update post-start.sh to call daemon
4. Update nexus mise tasks

### Where
```
coltec-base-image/
├── Dockerfile
└── scripts/

nexus/
└── mise.toml  # Simplified tasks
```

### Nexus mise.toml (Target)

```toml
[tools]
"pipx:copier" = "latest"
"npm:ajv-cli" = "latest"
"npm:@devcontainers/cli" = "latest"

[env]
COLTEC_TEMPLATE = "gh:psu3d0/coltec-devcontainer-template"

[tasks."cs:new"]
run = "copier copy $COLTEC_TEMPLATE codespaces/$1 --trust"

[tasks."cs:update"]
run = "copier update codespaces/$1 --trust"

[tasks."cs:validate"]
run = "ajv validate -s schema/workspace-spec.schema.json -d codespaces/$1/.devcontainer/workspace-spec.yaml"

[tasks."cs:up"]
run = """
eval "$(fnox eval)"
devcontainer up --workspace-folder codespaces/$1
"""

[tasks."cs:up-rebuild"]
run = """
eval "$(fnox eval)"
devcontainer up --workspace-folder codespaces/$1 --remove-existing-container --build-no-cache
"""
```

### post-start.sh (Target)

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "[post-start] Starting coltec-daemon..."

# Daemon reads workspace-spec.yaml and handles everything
exec coltec-daemon
```

### Definition of Done
- [ ] Docker image builds successfully
- [ ] Image pushed to ghcr.io/psu3d0/coltec-codespace
- [ ] `cs:up` starts container with working daemon
- [ ] No Python dependencies for workspace management
- [ ] Python package archived

### Test-Driven Completion Criteria
```bash
# Full lifecycle
mise run cs:new testorg/testproject-dev
mise run cs:validate testorg/testproject-dev
mise run cs:up testorg/testproject-dev

# Verify daemon running
docker exec $(docker ps -q) pgrep coltec-daemon
# Exit 0

# Verify Tailscale
docker exec $(docker ps -q) tailscale status
# Shows connected

# Verify sync working
docker exec $(docker ps -q) coltec-daemon --once --dry-run
# Shows sync paths

mise run cs:down testorg/testproject-dev
```

---

## Phase 5: Advanced Features

### 5a: Per-User Customization

```yaml
# ~/.config/coltec/user.yaml
user_id: "frank"
dotfiles_repo: "gh:psu3d0/dotfiles"
extra_sync_paths:
  - name: user-config
    path: /home/vscode/.config
    remote_path: users/{user_id}/config
```

Daemon reads and merges this at startup.

### 5b: Encryption

```rust
// In config.rs
#[derive(Debug, Clone, Deserialize)]
pub struct RcloneConfig {
    pub remote_name: String,
    pub encryption: Option<EncryptionConfig>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct EncryptionConfig {
    pub enabled: bool,
    pub password_env: String,  // e.g., "RCLONE_CRYPT_PASSWORD"
}
```

### 5c: Service Discovery

```bash
# New task in mise.toml
[tasks."cs:discover"]
run = "tailscale status --json | jq '.Peer[] | select(.Tags | contains([\"tag:devcontainer\"]))'"
```

### Definition of Done
- [ ] User config file supported
- [ ] Encryption toggle works
- [ ] `cs:discover` lists all devcontainer workspaces

---

## Migration Checklist

### Per-Workspace Migration
- [ ] Generate `.copier-answers.yml` from existing config
- [ ] Run `copier update` to regenerate
- [ ] Verify workspace-spec.yaml unchanged
- [ ] Test `cs:up` and `cs:down`
- [ ] Verify daemon runs
- [ ] Remove Python artifacts

### Global Migration
- [ ] Phase 0: Tests
- [x] Phase 1: JSON Schema (Rust + schemars + coltec-validate)
  - [x] Rust types in lib.rs
  - [x] Schema generation via schemars
  - [x] coltec-validate CLI binary
  - [x] Test fixture (valid_workspace.yaml)
- [ ] Phase 2: Copier template (copy done, update pending git-tracked template ref)
- [ ] Phase 3: Rust daemon (see RUST_DAEMON_PLAN.md for 7 milestones)
  - [ ] Milestone 1: Config & Types Hardening
  - [ ] Milestone 2: CLI Surface & Wiring
  - [ ] Milestone 3: Sync Planning Layer
  - [ ] Milestone 4: Execution Engine
  - [ ] Milestone 5: Loop & Signals
  - [ ] Milestone 6: Observability & UX
  - [ ] Milestone 7: Integration Hooks
- [ ] Phase 4: Docker + Integration
- [ ] Phase 5: Advanced (optional)
- [ ] Archive Python package
- [ ] Update documentation

---

## Success Metrics

| Metric | Before | After | Target |
|--------|--------|-------|--------|
| Custom Python | ~2000 lines | 0 | 0 |
| Custom Rust | 0 | ~500 lines | <600 |
| Memory per container | ~40MB overhead | ~5MB overhead | <10MB |
| Binary size | N/A | ~3-5MB | <10MB |
| Startup time | ~500ms | ~10ms | <100ms |
| Dependencies | Python ecosystem | Single binary | Minimal |

---

## Rollback Plan

1. **Daemon issues**: Fall back to starting rclone directly
   ```bash
   # In post-start.sh
   if ! coltec-daemon --validate-only; then
       echo "Falling back to direct rclone"
       rclone bisync /workspace remote:path &
   fi
   ```

2. **Template issues**: Pin copier to known-good version
   ```bash
   copier copy gh:psu3d0/coltec-devcontainer-template@v1.0.0 .
   ```

3. **Full rollback**: Python package remains until Phase 4 complete

---

## Appendix: File Mapping

| Current | Target | Notes |
|---------|--------|-------|
| spec.py | workspace-spec.schema.json + config.rs | Pydantic → JSON Schema + serde |
| provision.py | copier.yaml | Custom → copier |
| up.py | mise.toml cs:up task | Python → shell |
| storage.py | sync.rs | Python → Rust |
| __main__.py | Deleted | CLI → mise tasks |
| sync-daemon.sh | coltec-daemon binary | Bash → Rust |
| templates/*.jinja2 | template/*.jinja | Move to copier |
