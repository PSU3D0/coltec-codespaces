//! Sync execution engine - executes sync actions via rclone.
//!
//! This module handles:
//! - Tracking bisync state (first-time requires --resync)
//! - Executing rclone commands with proper flags
//! - Building dynamic rclone config for named remotes
//! - Handling errors and recoverable conditions

use crate::plan::{OperationSettings, ResolvedRemote, SyncAction, SyncPlan};
use crate::SyncDirection;
use anyhow::{Context, Result};
use std::path::Path;
use std::process::Stdio;
use tokio::process::Command;
use tracing::{debug, error, info, instrument, warn};

/// Get the state directory for a workspace.
///
/// State is stored in `~/.local/share/coltec-daemon/{workspace}/`
/// or `/tmp/coltec-daemon/{workspace}/` if XDG dirs unavailable.
fn state_dir(workspace: &str) -> std::path::PathBuf {
    directories::BaseDirs::new()
        .map(|d| d.data_local_dir().join("coltec-daemon").join(workspace))
        .unwrap_or_else(|| std::path::PathBuf::from("/tmp/coltec-daemon").join(workspace))
}

/// Check if this is a first-time sync (needs --resync for bisync).
pub async fn needs_resync(workspace: &str, action_name: &str) -> bool {
    let marker = state_dir(workspace).join(format!("{}.bisync", action_name));
    !marker.exists()
}

/// Mark a sync action as initialized (bisync state established).
pub async fn mark_initialized(workspace: &str, action_name: &str) -> Result<()> {
    let state = state_dir(workspace);
    tokio::fs::create_dir_all(&state)
        .await
        .with_context(|| format!("failed to create state dir: {}", state.display()))?;

    let marker = state.join(format!("{}.bisync", action_name));
    tokio::fs::write(&marker, "")
        .await
        .with_context(|| format!("failed to write marker: {}", marker.display()))?;

    debug!(marker = %marker.display(), "marked sync as initialized");
    Ok(())
}

/// Build an rclone backend connection string for a named remote.
///
/// For S3-type remotes: `:s3,bucket=mybucket,provider=Cloudflare:path`
/// For crypt remotes: `:crypt,remote=:s3,...:path,password=...:path`
///
/// This uses rclone's on-the-fly backend syntax to avoid needing config files.
fn build_rclone_backend(remote: &ResolvedRemote, remote_path: &str) -> String {
    if remote.remote_type == "crypt" {
        build_crypt_backend(remote, remote_path)
    } else {
        build_storage_backend(remote, remote_path)
    }
}

/// Build a storage backend connection string (s3, gcs, etc.)
fn build_storage_backend(remote: &ResolvedRemote, remote_path: &str) -> String {
    let mut opts = Vec::new();

    // Add bucket if present
    if let Some(ref bucket) = remote.bucket {
        opts.push(format!("bucket={}", bucket));
    }

    // Add all backend-specific options
    for (key, value) in &remote.options {
        opts.push(format!("{}={}", key, value));
    }

    let opts_str = if opts.is_empty() {
        String::new()
    } else {
        format!(",{}", opts.join(","))
    };

    format!(":{}{}:{}", remote.remote_type, opts_str, remote_path)
}

/// Build a crypt backend connection string.
///
/// Crypt wraps another remote, so we recursively build the inner remote.
/// Password is read from environment variables.
fn build_crypt_backend(remote: &ResolvedRemote, remote_path: &str) -> String {
    // Build the inner remote that crypt wraps
    let inner_remote = if let Some(ref wrap) = remote.wrap_remote {
        let wrap_path = remote.wrap_path.as_deref().unwrap_or("");
        build_rclone_backend(wrap, wrap_path)
    } else {
        // Fallback if wrap_remote missing (shouldn't happen with validation)
        "".to_string()
    };

    // Remove the trailing path from inner_remote since crypt adds its own
    // The inner_remote already includes wrap_path, so we build crypt to reference it
    let inner_base = if let Some(idx) = inner_remote.rfind(':') {
        &inner_remote[..=idx]
    } else {
        &inner_remote
    };

    let mut opts = vec![format!(
        "remote={}{}",
        inner_base,
        remote.wrap_path.as_deref().unwrap_or("")
    )];

    // Filename encryption setting
    if let Some(ref fe) = remote.filename_encryption {
        opts.push(format!("filename_encryption={}", fe));
    }

    // Directory name encryption
    if let Some(dne) = remote.directory_name_encryption {
        opts.push(format!("directory_name_encryption={}", dne));
    }

    // Note: passwords are passed via environment variables
    // RCLONE_CRYPT_PASSWORD and RCLONE_CRYPT_PASSWORD2

    format!(":crypt,{}:{}", opts.join(","), remote_path)
}

/// Apply operation settings to an rclone command.
fn apply_operation_settings(cmd: &mut Command, settings: &OperationSettings) {
    if let Some(transfers) = settings.transfers {
        cmd.args(["--transfers", &transfers.to_string()]);
    } else {
        cmd.args(["--transfers", "8"]); // default
    }

    if let Some(checkers) = settings.checkers {
        cmd.args(["--checkers", &checkers.to_string()]);
    } else {
        cmd.args(["--checkers", "16"]); // default
    }

    if let Some(ref bwlimit) = settings.bwlimit {
        cmd.args(["--bwlimit", bwlimit]);
    }
}

/// Result of a sync execution.
#[derive(Debug)]
pub struct SyncResult {
    /// Name of the sync action
    pub name: String,
    /// Whether the sync succeeded
    pub success: bool,
    /// Error message if failed
    pub error: Option<String>,
    /// Whether this was a first-time resync
    pub was_resync: bool,
}

/// Execute a single sync action.
///
/// The remote target is built from the action's resolved remote configuration.
#[instrument(skip_all, fields(name = %action.name, direction = ?action.direction))]
pub async fn execute_sync(action: &SyncAction, dry_run: bool, resync: bool) -> Result<SyncResult> {
    let result_name = action.name.clone();

    // Check local path exists
    if !Path::new(&action.local_path).exists() {
        warn!(path = %action.local_path, "local path does not exist, skipping");
        return Ok(SyncResult {
            name: result_name,
            success: true, // Not an error, just skip
            error: None,
            was_resync: false,
        });
    }

    // Build the remote target string from resolved remote
    let remote_target = match &action.remote {
        Some(resolved) => build_rclone_backend(resolved, &action.remote_path),
        None => {
            error!("action {} has no remote configured", action.name);
            return Ok(SyncResult {
                name: result_name,
                success: false,
                error: Some("no remote configured for sync action".into()),
                was_resync: false,
            });
        }
    };
    let local = &action.local_path;

    let mut cmd = Command::new("rclone");

    // Set up environment variables for crypt passwords if needed
    if let Some(ref resolved) = action.remote {
        if resolved.remote_type == "crypt" {
            if let Some(ref pw_env) = resolved.password_env {
                // Check if the env var is set, pass through to rclone
                if let Ok(pw) = std::env::var(pw_env) {
                    cmd.env("RCLONE_CRYPT_PASSWORD", pw);
                }
            }
            if let Some(ref pw2_env) = resolved.password2_env {
                if let Ok(pw2) = std::env::var(pw2_env) {
                    cmd.env("RCLONE_CRYPT_PASSWORD2", pw2);
                }
            }
        }
    }

    // Choose sync mode based on direction
    match action.direction {
        SyncDirection::Bidirectional => {
            cmd.args(["bisync", local, &remote_target]);
            cmd.args(["--resilient", "--recover", "--max-lock", "2m"]);
            if resync {
                info!("first-time sync, using --resync");
                cmd.arg("--resync");
            }
        }
        SyncDirection::PushOnly => {
            cmd.args(["sync", local, &remote_target]);
        }
        SyncDirection::PullOnly => {
            cmd.args(["sync", &remote_target, local]);
        }
    }

    // Add excludes
    for pattern in &action.excludes {
        cmd.args(["--exclude", pattern]);
    }

    // Apply operation settings (transfers, checkers, bwlimit)
    apply_operation_settings(&mut cmd, &action.operation);

    // Common flags for performance
    cmd.arg("--fast-list");

    // Add verbosity for debugging
    cmd.arg("-v");

    if dry_run {
        cmd.arg("--dry-run");
        info!(local = %local, remote = %remote_target, "DRY RUN: executing rclone");
    }

    debug!(cmd = ?cmd, "executing rclone command");

    let output = match cmd
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .output()
        .await
    {
        Ok(output) => output,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            error!("rclone not found - please install rclone");
            return Ok(SyncResult {
                name: result_name,
                success: false,
                error: Some("rclone not found - please install rclone".into()),
                was_resync: resync,
            });
        }
        Err(e) => {
            return Err(e).context("failed to execute rclone");
        }
    };

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);

    if !stdout.is_empty() {
        debug!(stdout = %stdout, "rclone stdout");
    }

    if !output.status.success() {
        // Check for known recoverable errors
        if stderr.contains("directory not found") || stderr.contains("does not exist") {
            warn!("remote directory not found, will be created on next sync");
            return Ok(SyncResult {
                name: result_name,
                success: true,
                error: None,
                was_resync: resync,
            });
        }

        // Check for empty source (not an error for bisync)
        if stderr.contains("empty") && action.direction == SyncDirection::Bidirectional {
            warn!("empty source directory, skipping");
            return Ok(SyncResult {
                name: result_name,
                success: true,
                error: None,
                was_resync: resync,
            });
        }

        error!(
            stderr = %stderr,
            exit_code = ?output.status.code(),
            "rclone failed"
        );

        return Ok(SyncResult {
            name: result_name,
            success: false,
            error: Some(stderr.to_string()),
            was_resync: resync,
        });
    }

    info!("sync complete");

    Ok(SyncResult {
        name: result_name,
        success: true,
        error: None,
        was_resync: resync,
    })
}

/// Result of executing an entire plan.
#[derive(Debug)]
pub struct PlanResult {
    /// Results for each action
    pub results: Vec<SyncResult>,
    /// Number of successful syncs
    pub success_count: usize,
    /// Number of failed syncs
    pub failure_count: usize,
}

impl PlanResult {
    /// Returns true if all syncs succeeded.
    pub fn all_success(&self) -> bool {
        self.failure_count == 0
    }
}

/// Execute all actions in a sync plan.
#[instrument(skip_all, fields(workspace = %plan.workspace_name, actions = plan.actions.len()))]
pub async fn execute_plan(plan: &SyncPlan, dry_run: bool) -> Result<PlanResult> {
    let mut results = Vec::with_capacity(plan.actions.len());
    let mut success_count = 0;
    let mut failure_count = 0;

    for action in &plan.actions {
        // Check if we need resync (first-time bisync)
        let resync = if action.direction == SyncDirection::Bidirectional {
            needs_resync(&plan.workspace_name, &action.name).await
        } else {
            false
        };

        let result = execute_sync(action, dry_run, resync).await?;

        // Mark as initialized if successful and was a resync
        if result.success && result.was_resync && !dry_run {
            if let Err(e) = mark_initialized(&plan.workspace_name, &action.name).await {
                warn!(error = %e, action = %action.name, "failed to mark sync as initialized");
            }
        }

        if result.success {
            success_count += 1;
        } else {
            failure_count += 1;
        }

        results.push(result);
    }

    info!(
        success = success_count,
        failed = failure_count,
        "plan execution complete"
    );

    Ok(PlanResult {
        results,
        success_count,
        failure_count,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    #[test]
    fn test_state_dir() {
        let dir = state_dir("test-workspace");
        assert!(dir.to_string_lossy().contains("coltec-daemon"));
        assert!(dir.to_string_lossy().contains("test-workspace"));
    }

    #[tokio::test]
    async fn test_needs_resync_new_workspace() {
        // Use a unique workspace name that won't have state
        let needs = needs_resync("nonexistent-workspace-12345", "test-action").await;
        assert!(needs, "new workspace should need resync");
    }

    #[tokio::test]
    async fn test_mark_initialized() {
        let workspace = format!("test-workspace-{}", std::process::id());
        let action = "test-action";

        // Should need resync initially
        assert!(needs_resync(&workspace, action).await);

        // Mark as initialized
        mark_initialized(&workspace, action).await.unwrap();

        // Should not need resync now
        assert!(!needs_resync(&workspace, action).await);

        // Cleanup
        let state = state_dir(&workspace);
        let _ = tokio::fs::remove_dir_all(&state).await;
    }

    #[test]
    fn test_build_storage_backend_s3() {
        let mut options = BTreeMap::new();
        options.insert("provider".to_string(), "Cloudflare".to_string());
        options.insert(
            "endpoint".to_string(),
            "https://xxx.r2.cloudflarestorage.com".to_string(),
        );

        let remote = ResolvedRemote {
            name: "r2".to_string(),
            remote_type: "s3".to_string(),
            bucket: Some("my-bucket".to_string()),
            path_prefix: None,
            options,
            wrap_remote: None,
            wrap_path: None,
            password_env: None,
            password2_env: None,
            filename_encryption: None,
            directory_name_encryption: None,
        };

        let backend = build_storage_backend(&remote, "workspaces/org/project/data");
        assert!(backend.starts_with(":s3,"));
        assert!(backend.contains("bucket=my-bucket"));
        assert!(backend.contains("provider=Cloudflare"));
        assert!(backend.ends_with(":workspaces/org/project/data"));
    }

    #[test]
    fn test_build_storage_backend_minimal() {
        let remote = ResolvedRemote {
            name: "simple".to_string(),
            remote_type: "s3".to_string(),
            bucket: Some("bucket".to_string()),
            path_prefix: None,
            options: BTreeMap::new(),
            wrap_remote: None,
            wrap_path: None,
            password_env: None,
            password2_env: None,
            filename_encryption: None,
            directory_name_encryption: None,
        };

        let backend = build_storage_backend(&remote, "path");
        assert_eq!(backend, ":s3,bucket=bucket:path");
    }

    #[test]
    fn test_build_crypt_backend() {
        let base_remote = ResolvedRemote {
            name: "r2-base".to_string(),
            remote_type: "s3".to_string(),
            bucket: Some("my-bucket".to_string()),
            path_prefix: None,
            options: BTreeMap::new(),
            wrap_remote: None,
            wrap_path: None,
            password_env: None,
            password2_env: None,
            filename_encryption: None,
            directory_name_encryption: None,
        };

        let crypt_remote = ResolvedRemote {
            name: "encrypted".to_string(),
            remote_type: "crypt".to_string(),
            bucket: None,
            path_prefix: None,
            options: BTreeMap::new(),
            wrap_remote: Some(Box::new(base_remote)),
            wrap_path: Some("encrypted-data".to_string()),
            password_env: Some("RCLONE_CRYPT_PASSWORD".to_string()),
            password2_env: Some("RCLONE_CRYPT_PASSWORD2".to_string()),
            filename_encryption: Some("standard".to_string()),
            directory_name_encryption: Some(true),
        };

        let backend = build_crypt_backend(&crypt_remote, "secrets");
        assert!(backend.starts_with(":crypt,"));
        assert!(backend.contains("filename_encryption=standard"));
        assert!(backend.contains("directory_name_encryption=true"));
        assert!(backend.ends_with(":secrets"));
    }

    #[test]
    fn test_apply_operation_settings_all() {
        let settings = OperationSettings {
            transfers: Some(16),
            checkers: Some(32),
            bwlimit: Some("100M".to_string()),
        };

        let mut cmd = Command::new("echo");
        apply_operation_settings(&mut cmd, &settings);

        // Convert to string for assertion
        let args: Vec<_> = cmd.as_std().get_args().collect();
        assert!(args.contains(&std::ffi::OsStr::new("--transfers")));
        assert!(args.contains(&std::ffi::OsStr::new("16")));
        assert!(args.contains(&std::ffi::OsStr::new("--checkers")));
        assert!(args.contains(&std::ffi::OsStr::new("32")));
        assert!(args.contains(&std::ffi::OsStr::new("--bwlimit")));
        assert!(args.contains(&std::ffi::OsStr::new("100M")));
    }

    #[test]
    fn test_apply_operation_settings_defaults() {
        let settings = OperationSettings::default();

        let mut cmd = Command::new("echo");
        apply_operation_settings(&mut cmd, &settings);

        let args: Vec<_> = cmd.as_std().get_args().collect();
        // Should have default transfers and checkers
        assert!(args.contains(&std::ffi::OsStr::new("--transfers")));
        assert!(args.contains(&std::ffi::OsStr::new("8")));
        assert!(args.contains(&std::ffi::OsStr::new("--checkers")));
        assert!(args.contains(&std::ffi::OsStr::new("16")));
        // Should NOT have bwlimit
        assert!(!args.contains(&std::ffi::OsStr::new("--bwlimit")));
    }
}
