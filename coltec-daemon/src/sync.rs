//! Sync execution engine - executes sync actions via rclone.
//!
//! This module handles:
//! - Tracking bisync state (first-time requires --resync)
//! - Executing rclone commands with proper flags
//! - Building dynamic rclone config for named remotes
//! - Handling errors and recoverable conditions
//! - Retry with exponential backoff for transient failures
//! - Health file updates for supervisor integration

use crate::plan::{OperationSettings, ResolvedRemote, SyncAction, SyncPlan};
use crate::SyncDirection;
use anyhow::{Context, Result};
use std::path::Path;
use std::process::Stdio;
use std::time::Duration;
use tokio::process::Command;
use tokio::time::sleep;
use tracing::{debug, error, info, instrument, warn};

/// Default number of retry attempts for transient failures.
const DEFAULT_MAX_RETRIES: u32 = 3;

/// Base delay for exponential backoff (doubles each retry).
const RETRY_BASE_DELAY_MS: u64 = 1000;

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

/// Health status for supervisor integration.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct HealthStatus {
    /// Whether the last sync pass succeeded
    pub healthy: bool,
    /// Timestamp of the last successful sync (Unix epoch seconds)
    pub last_success: Option<u64>,
    /// Timestamp of the last sync attempt
    pub last_attempt: u64,
    /// Number of consecutive failures
    pub consecutive_failures: u32,
    /// Last error message if unhealthy
    pub last_error: Option<String>,
}

/// Get the health file path for a workspace.
pub fn health_file_path(workspace: &str) -> std::path::PathBuf {
    state_dir(workspace).join("health.json")
}

/// Update the health file after a sync pass.
pub async fn update_health(workspace: &str, result: &PlanResult) -> Result<()> {
    let health_path = health_file_path(workspace);
    let state = state_dir(workspace);

    tokio::fs::create_dir_all(&state)
        .await
        .with_context(|| format!("failed to create state dir: {}", state.display()))?;

    // Read existing health status
    let mut status = if health_path.exists() {
        let content = tokio::fs::read_to_string(&health_path).await.ok();
        content
            .and_then(|c| serde_json::from_str(&c).ok())
            .unwrap_or(HealthStatus {
                healthy: true,
                last_success: None,
                last_attempt: 0,
                consecutive_failures: 0,
                last_error: None,
            })
    } else {
        HealthStatus {
            healthy: true,
            last_success: None,
            last_attempt: 0,
            consecutive_failures: 0,
            last_error: None,
        }
    };

    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);

    status.last_attempt = now;

    if result.all_success() {
        status.healthy = true;
        status.last_success = Some(now);
        status.consecutive_failures = 0;
        status.last_error = None;
    } else {
        status.consecutive_failures += 1;
        // Mark unhealthy after 3 consecutive failures
        if status.consecutive_failures >= 3 {
            status.healthy = false;
        }
        // Capture the first error
        status.last_error = result.results.iter().find_map(|r| r.error.clone());
    }

    let json =
        serde_json::to_string_pretty(&status).context("failed to serialize health status")?;
    tokio::fs::write(&health_path, json)
        .await
        .with_context(|| format!("failed to write health file: {}", health_path.display()))?;

    debug!(path = %health_path.display(), healthy = status.healthy, "updated health file");
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

/// Expand environment variable references in a string.
///
/// Supports `${VAR}` and `$VAR` syntax. Unset variables are left unchanged.
fn expand_env_vars(value: &str) -> String {
    let mut result = value.to_string();

    // Handle ${VAR} syntax - use index-based iteration to avoid infinite loops
    let mut search_start = 0;
    while let Some(rel_start) = result[search_start..].find("${") {
        let start = search_start + rel_start;
        if let Some(rel_end) = result[start..].find('}') {
            let end = start + rel_end;
            let var_name = &result[start + 2..end];
            match std::env::var(var_name) {
                Ok(replacement) => {
                    result = format!("{}{}{}", &result[..start], replacement, &result[end + 1..]);
                    search_start = start + replacement.len();
                }
                Err(_) => {
                    // Skip past this ${VAR} to avoid infinite loop
                    search_start = end + 1;
                }
            }
        } else {
            break;
        }
    }

    // Handle $VAR syntax (only alphanumeric and underscore)
    let mut i = 0;
    while i < result.len() {
        if result[i..].starts_with('$') && !result[i..].starts_with("${") {
            let rest = &result[i + 1..];
            let var_end = rest
                .find(|c: char| !c.is_alphanumeric() && c != '_')
                .unwrap_or(rest.len());
            if var_end > 0 {
                let var_name = &rest[..var_end];
                match std::env::var(var_name) {
                    Ok(replacement) => {
                        result = format!("{}{}{}", &result[..i], replacement, &rest[var_end..]);
                        i += replacement.len();
                    }
                    Err(_) => {
                        // Skip past $VAR
                        i += 1 + var_end;
                    }
                }
                continue;
            }
        }
        i += 1;
    }

    result
}

/// Quote a value for rclone connection string if it contains special characters.
///
/// Values with colons or commas need to be quoted. We use single quotes and
/// double any internal single quotes per rclone's escaping rules.
fn quote_rclone_value(value: &str) -> String {
    if value.contains(':') || value.contains(',') || value.contains('\'') || value.contains('"') {
        // Escape internal single quotes by doubling them
        let escaped = value.replace('\'', "''");
        format!("'{}'", escaped)
    } else {
        value.to_string()
    }
}

/// Build a storage backend connection string (s3, gcs, etc.)
fn build_storage_backend(remote: &ResolvedRemote, remote_path: &str) -> String {
    let mut opts = Vec::new();

    // Add bucket if present
    if let Some(ref bucket) = remote.bucket {
        let expanded = expand_env_vars(bucket);
        opts.push(format!("bucket={}", quote_rclone_value(&expanded)));
    }

    // Add all backend-specific options, expanding env vars and quoting as needed
    for (key, value) in &remote.options {
        let expanded = expand_env_vars(value);
        opts.push(format!("{}={}", key, quote_rclone_value(&expanded)));
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
    /// Number of retry attempts made
    pub attempts: u32,
}

/// Check if an rclone error is likely transient and worth retrying.
fn is_retryable_error(stderr: &str) -> bool {
    // Network/connection errors
    stderr.contains("connection reset")
        || stderr.contains("connection refused")
        || stderr.contains("timeout")
        || stderr.contains("temporary failure")
        || stderr.contains("network is unreachable")
        || stderr.contains("no such host")
        || stderr.contains("TLS handshake")
        || stderr.contains("EOF")
        // S3/cloud provider transient errors
        || stderr.contains("503")
        || stderr.contains("500")
        || stderr.contains("429") // rate limit
        || stderr.contains("SlowDown")
        || stderr.contains("ServiceUnavailable")
        || stderr.contains("InternalError")
        // Bisync lock contention
        || stderr.contains("lock file")
        || stderr.contains("locked")
}

/// Execute a single sync action with retry logic.
///
/// The remote target is built from the action's resolved remote configuration.
/// Transient failures are retried with exponential backoff.
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
            attempts: 0,
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
                attempts: 0,
            });
        }
    };

    // Retry loop with exponential backoff
    let mut last_error: Option<String> = None;
    for attempt in 1..=DEFAULT_MAX_RETRIES {
        match execute_sync_attempt(action, &remote_target, dry_run, resync).await {
            Ok(()) => {
                if attempt > 1 {
                    info!(attempt, "sync succeeded after retry");
                } else {
                    info!("sync complete");
                }
                return Ok(SyncResult {
                    name: result_name,
                    success: true,
                    error: None,
                    was_resync: resync,
                    attempts: attempt,
                });
            }
            Err(SyncAttemptError::NotRetryable(msg)) => {
                // Fatal error, don't retry
                return Ok(SyncResult {
                    name: result_name,
                    success: false,
                    error: Some(msg),
                    was_resync: resync,
                    attempts: attempt,
                });
            }
            Err(SyncAttemptError::Skipped) => {
                // Not an error, just skip (e.g., empty source)
                return Ok(SyncResult {
                    name: result_name,
                    success: true,
                    error: None,
                    was_resync: resync,
                    attempts: attempt,
                });
            }
            Err(SyncAttemptError::Retryable(msg)) => {
                last_error = Some(msg.clone());
                if attempt < DEFAULT_MAX_RETRIES {
                    let delay_ms = RETRY_BASE_DELAY_MS * 2u64.pow(attempt - 1);
                    warn!(
                        attempt,
                        max_attempts = DEFAULT_MAX_RETRIES,
                        delay_ms,
                        error = %msg,
                        "sync failed, retrying after backoff"
                    );
                    sleep(Duration::from_millis(delay_ms)).await;
                }
            }
        }
    }

    // All retries exhausted
    error!(
        attempts = DEFAULT_MAX_RETRIES,
        error = ?last_error,
        "sync failed after all retry attempts"
    );
    Ok(SyncResult {
        name: result_name,
        success: false,
        error: last_error,
        was_resync: resync,
        attempts: DEFAULT_MAX_RETRIES,
    })
}

/// Error type for a single sync attempt.
enum SyncAttemptError {
    /// Error that should be retried (transient)
    Retryable(String),
    /// Error that should not be retried (permanent)
    NotRetryable(String),
    /// Not an error, action was skipped
    Skipped,
}

/// Execute a single attempt of an rclone sync.
async fn execute_sync_attempt(
    action: &SyncAction,
    remote_target: &str,
    dry_run: bool,
    resync: bool,
) -> std::result::Result<(), SyncAttemptError> {
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
            cmd.args(["bisync", local, remote_target]);
            cmd.args(["--resilient", "--recover", "--max-lock", "2m"]);
            if resync {
                info!("first-time sync, using --resync");
                cmd.arg("--resync");
            }
        }
        SyncDirection::PushOnly => {
            cmd.args(["sync", local, remote_target]);
        }
        SyncDirection::PullOnly => {
            cmd.args(["sync", remote_target, local]);
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
            return Err(SyncAttemptError::NotRetryable(
                "rclone not found - please install rclone".into(),
            ));
        }
        Err(e) => {
            // IO errors when spawning are potentially transient
            return Err(SyncAttemptError::Retryable(format!(
                "failed to execute rclone: {}",
                e
            )));
        }
    };

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);

    if !stdout.is_empty() {
        debug!(stdout = %stdout, "rclone stdout");
    }

    if !output.status.success() {
        let stderr_str = stderr.to_string();

        // Check for known recoverable errors (not retryable, just skip)
        if stderr.contains("directory not found") || stderr.contains("does not exist") {
            warn!("remote directory not found, will be created on next sync");
            return Err(SyncAttemptError::Skipped);
        }

        // Check for empty source (not an error for bisync)
        if stderr.contains("empty") && action.direction == SyncDirection::Bidirectional {
            warn!("empty source directory, skipping");
            return Err(SyncAttemptError::Skipped);
        }

        error!(
            stderr = %stderr,
            exit_code = ?output.status.code(),
            "rclone failed"
        );

        // Check if this error is retryable
        if is_retryable_error(&stderr_str) {
            return Err(SyncAttemptError::Retryable(stderr_str));
        }

        return Err(SyncAttemptError::NotRetryable(stderr_str));
    }

    Ok(())
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

    #[test]
    fn test_expand_env_vars_braced() {
        std::env::set_var("TEST_VAR_BRACED", "expanded_value");
        assert_eq!(expand_env_vars("${TEST_VAR_BRACED}"), "expanded_value");
        assert_eq!(
            expand_env_vars("prefix_${TEST_VAR_BRACED}_suffix"),
            "prefix_expanded_value_suffix"
        );
        std::env::remove_var("TEST_VAR_BRACED");
    }

    #[test]
    fn test_expand_env_vars_unbraced() {
        std::env::set_var("TEST_VAR_UNBRACED", "value123");
        assert_eq!(expand_env_vars("$TEST_VAR_UNBRACED"), "value123");
        assert_eq!(expand_env_vars("$TEST_VAR_UNBRACED/path"), "value123/path");
        std::env::remove_var("TEST_VAR_UNBRACED");
    }

    #[test]
    fn test_expand_env_vars_missing() {
        // Missing env vars should keep original syntax
        assert_eq!(
            expand_env_vars("${NONEXISTENT_VAR_12345}"),
            "${NONEXISTENT_VAR_12345}"
        );
    }

    #[test]
    fn test_expand_env_vars_no_expansion() {
        assert_eq!(expand_env_vars("plain_string"), "plain_string");
        assert_eq!(
            expand_env_vars("https://example.com"),
            "https://example.com"
        );
    }

    #[test]
    fn test_quote_rclone_value_no_special_chars() {
        assert_eq!(quote_rclone_value("simple"), "simple");
        assert_eq!(quote_rclone_value("Cloudflare"), "Cloudflare");
        assert_eq!(quote_rclone_value("auto"), "auto");
    }

    #[test]
    fn test_quote_rclone_value_with_colon() {
        // URLs with colons need quoting
        assert_eq!(
            quote_rclone_value("https://example.com"),
            "'https://example.com'"
        );
        assert_eq!(
            quote_rclone_value("https://xxx.r2.cloudflarestorage.com"),
            "'https://xxx.r2.cloudflarestorage.com'"
        );
    }

    #[test]
    fn test_quote_rclone_value_with_comma() {
        assert_eq!(quote_rclone_value("a,b,c"), "'a,b,c'");
    }

    #[test]
    fn test_quote_rclone_value_with_quotes() {
        // Internal single quotes get doubled
        assert_eq!(quote_rclone_value("it's"), "'it''s'");
        // Double quotes also trigger quoting
        assert_eq!(quote_rclone_value("say \"hi\""), "'say \"hi\"'");
    }

    #[test]
    fn test_build_storage_backend_s3_with_url_endpoint() {
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
        // Endpoint URL should be quoted because of colons
        assert!(backend.contains("endpoint='https://xxx.r2.cloudflarestorage.com'"));
        assert!(backend.ends_with(":workspaces/org/project/data"));
    }
}
