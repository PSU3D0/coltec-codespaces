//! End-to-end sync tests for coltec-daemon.
//!
//! These tests require rclone to be installed and use local filesystem
//! remotes to test actual sync operations without cloud credentials.
//!
//! Run with: `cargo test --test e2e_sync -- --ignored`
//! Or: `cargo test e2e -- --ignored`

use assert_cmd::cargo::cargo_bin_cmd;
use predicates::prelude::*;
use std::fs;
use std::path::Path;
use tempfile::TempDir;

/// Generate a workspace-spec YAML for testing with local filesystem remote.
fn make_config(remote_name: &str, sync_entries: &str) -> String {
    format!(
        r#"
name: e2e-test-workspace
metadata:
  org: testorg
  project: testproject
  environment: test
devcontainer:
  template:
    name: test
    path: test.json
  image:
    name: ghcr.io/test/image
persistence:
  enabled: true
  mode: replicated
  default_remote: {remote_name}
  remotes:
    {remote_name}:
      type: local
  sync:
{sync_entries}
"#,
        remote_name = remote_name,
        sync_entries = sync_entries,
    )
}

/// Generate a sync entry for the config.
/// For local backend testing, remote_path should be the full absolute path.
fn sync_entry(name: &str, local_path: &Path, remote_path: &Path, direction: &str) -> String {
    format!(
        r#"    - name: {name}
      path: {local_path}
      remote_path: {remote_path}
      direction: {direction}
      interval: 60
      priority: 1
"#,
        name = name,
        local_path = local_path.to_str().unwrap(),
        remote_path = remote_path.to_str().unwrap(),
        direction = direction,
    )
}

/// Generate a sync entry with excludes.
/// For local backend testing, remote_path should be the full absolute path.
fn sync_entry_with_excludes(
    name: &str,
    local_path: &Path,
    remote_path: &Path,
    direction: &str,
    excludes: &[&str],
) -> String {
    let exclude_yaml: String = excludes
        .iter()
        .map(|e| format!("        - \"{}\"\n", e))
        .collect();

    format!(
        r#"    - name: {name}
      path: {local_path}
      remote_path: {remote_path}
      direction: {direction}
      interval: 60
      priority: 1
      exclude:
{excludes}"#,
        name = name,
        local_path = local_path.to_str().unwrap(),
        remote_path = remote_path.to_str().unwrap(),
        direction = direction,
        excludes = exclude_yaml,
    )
}

// ============================================================================
// PUSH-ONLY TESTS
// ============================================================================

#[test]
#[ignore]
fn test_push_only_creates_remote_files() {
    let temp = TempDir::new().unwrap();
    let local_dir = temp.path().join("local");
    let remote_dir = temp.path().join("remote");
    let state_dir = temp.path().join("state");
    let config_path = temp.path().join("config.yaml");

    fs::create_dir_all(&local_dir).unwrap();
    fs::create_dir_all(&remote_dir).unwrap();
    fs::create_dir_all(&state_dir).unwrap();

    // Create local files
    fs::write(local_dir.join("file1.txt"), "hello from local").unwrap();
    fs::create_dir_all(local_dir.join("subdir")).unwrap();
    fs::write(local_dir.join("subdir/file2.txt"), "nested file").unwrap();

    let remote_data = remote_dir.join("data");
    let sync_entries = sync_entry("data", &local_dir, &remote_data, "push-only");
    let config = make_config("testremote", &sync_entries);
    fs::write(&config_path, &config).unwrap();

    let mut cmd = cargo_bin_cmd!("coltec-daemon");
    cmd.env("XDG_DATA_HOME", state_dir.to_str().unwrap())
        .arg("--config")
        .arg(&config_path)
        .arg("--once")
        .arg("--log-level")
        .arg("debug");

    cmd.assert().success();

    // Verify files were pushed to remote
    assert!(
        remote_data.join("file1.txt").exists(),
        "file1.txt should exist in remote"
    );
    assert!(
        remote_data.join("subdir/file2.txt").exists(),
        "subdir/file2.txt should exist in remote"
    );
    assert_eq!(
        fs::read_to_string(remote_data.join("file1.txt")).unwrap(),
        "hello from local"
    );
}

#[test]
#[ignore]
fn test_push_only_does_not_pull_remote_files() {
    let temp = TempDir::new().unwrap();
    let local_dir = temp.path().join("local");
    let remote_dir = temp.path().join("remote");
    let state_dir = temp.path().join("state");
    let config_path = temp.path().join("config.yaml");

    fs::create_dir_all(&local_dir).unwrap();
    fs::create_dir_all(&remote_dir).unwrap();
    fs::create_dir_all(&state_dir).unwrap();

    // Create remote-only file (should NOT be pulled)
    let remote_data = remote_dir.join("data");
    fs::create_dir_all(&remote_data).unwrap();
    fs::write(remote_data.join("remote-only.txt"), "from remote").unwrap();

    // Create local file (should be pushed)
    fs::write(local_dir.join("local-only.txt"), "from local").unwrap();

    let sync_entries = sync_entry("data", &local_dir, &remote_data, "push-only");
    let config = make_config("testremote", &sync_entries);
    fs::write(&config_path, &config).unwrap();

    let mut cmd = cargo_bin_cmd!("coltec-daemon");
    cmd.env("XDG_DATA_HOME", state_dir.to_str().unwrap())
        .arg("--config")
        .arg(&config_path)
        .arg("--once")
        .arg("--log-level")
        .arg("debug");

    cmd.assert().success();

    // Local file should be pushed
    assert!(remote_data.join("local-only.txt").exists());

    // Remote-only file should NOT be pulled to local
    assert!(
        !local_dir.join("remote-only.txt").exists(),
        "push-only should not pull remote files"
    );
}

// ============================================================================
// PULL-ONLY TESTS
// ============================================================================

#[test]
#[ignore]
fn test_pull_only_downloads_remote_files() {
    let temp = TempDir::new().unwrap();
    let local_dir = temp.path().join("local");
    let remote_dir = temp.path().join("remote");
    let state_dir = temp.path().join("state");
    let config_path = temp.path().join("config.yaml");

    fs::create_dir_all(&local_dir).unwrap();
    fs::create_dir_all(&remote_dir).unwrap();
    fs::create_dir_all(&state_dir).unwrap();

    // Create remote file
    let remote_data = remote_dir.join("data");
    fs::create_dir_all(&remote_data).unwrap();
    fs::write(remote_data.join("remote-file.txt"), "from remote").unwrap();

    let sync_entries = sync_entry("data", &local_dir, &remote_data, "pull-only");
    let config = make_config("testremote", &sync_entries);
    fs::write(&config_path, &config).unwrap();

    let mut cmd = cargo_bin_cmd!("coltec-daemon");
    cmd.env("XDG_DATA_HOME", state_dir.to_str().unwrap())
        .arg("--config")
        .arg(&config_path)
        .arg("--once")
        .arg("--log-level")
        .arg("debug");

    cmd.assert().success();

    // Remote file should be pulled to local
    let pulled_file = local_dir.join("remote-file.txt");
    assert!(pulled_file.exists(), "remote file should be pulled");
    assert_eq!(fs::read_to_string(&pulled_file).unwrap(), "from remote");
}

#[test]
#[ignore]
fn test_pull_only_does_not_push_local_files() {
    let temp = TempDir::new().unwrap();
    let local_dir = temp.path().join("local");
    let remote_dir = temp.path().join("remote");
    let state_dir = temp.path().join("state");
    let config_path = temp.path().join("config.yaml");

    fs::create_dir_all(&local_dir).unwrap();
    fs::create_dir_all(&remote_dir).unwrap();
    fs::create_dir_all(&state_dir).unwrap();

    // Create local file (should NOT be pushed)
    fs::write(local_dir.join("local-only.txt"), "from local").unwrap();

    // Create remote directory structure
    let remote_data = remote_dir.join("data");
    fs::create_dir_all(&remote_data).unwrap();

    let sync_entries = sync_entry("data", &local_dir, &remote_data, "pull-only");
    let config = make_config("testremote", &sync_entries);
    fs::write(&config_path, &config).unwrap();

    let mut cmd = cargo_bin_cmd!("coltec-daemon");
    cmd.env("XDG_DATA_HOME", state_dir.to_str().unwrap())
        .arg("--config")
        .arg(&config_path)
        .arg("--once")
        .arg("--log-level")
        .arg("debug");

    cmd.assert().success();

    // Local file should NOT be pushed to remote
    assert!(
        !remote_data.join("local-only.txt").exists(),
        "pull-only should not push local files"
    );
}

// ============================================================================
// BIDIRECTIONAL (BISYNC) TESTS
// ============================================================================

#[test]
#[ignore]
fn test_bidirectional_syncs_both_ways() {
    let temp = TempDir::new().unwrap();
    let local_dir = temp.path().join("local");
    let remote_dir = temp.path().join("remote");
    let state_dir = temp.path().join("state");
    let config_path = temp.path().join("config.yaml");

    fs::create_dir_all(&local_dir).unwrap();
    fs::create_dir_all(&remote_dir).unwrap();
    fs::create_dir_all(&state_dir).unwrap();

    // Create local file
    fs::write(local_dir.join("local-file.txt"), "from local").unwrap();

    // Create remote file
    let remote_data = remote_dir.join("data");
    fs::create_dir_all(&remote_data).unwrap();
    fs::write(remote_data.join("remote-file.txt"), "from remote").unwrap();

    let sync_entries = sync_entry("data", &local_dir, &remote_data, "bidirectional");
    let config = make_config("testremote", &sync_entries);
    fs::write(&config_path, &config).unwrap();

    let mut cmd = cargo_bin_cmd!("coltec-daemon");
    cmd.env("XDG_DATA_HOME", state_dir.to_str().unwrap())
        .arg("--config")
        .arg(&config_path)
        .arg("--once")
        .arg("--log-level")
        .arg("debug");

    let output = cmd.assert().success();

    // First run should use --resync (note: tracing logs go to stdout)
    let stdout = String::from_utf8_lossy(&output.get_output().stdout);
    assert!(
        stdout.contains("first-time sync") || stdout.contains("resync"),
        "first bisync should use --resync, got: {}",
        stdout
    );

    // Local file should be pushed to remote
    assert!(
        remote_data.join("local-file.txt").exists(),
        "local file should be pushed to remote"
    );

    // Remote file should be pulled to local
    assert!(
        local_dir.join("remote-file.txt").exists(),
        "remote file should be pulled to local"
    );
}

#[test]
#[ignore]
fn test_bidirectional_second_run_no_resync() {
    let temp = TempDir::new().unwrap();
    let local_dir = temp.path().join("local");
    let remote_dir = temp.path().join("remote");
    let state_dir = temp.path().join("state");
    let config_path = temp.path().join("config.yaml");

    fs::create_dir_all(&local_dir).unwrap();
    fs::create_dir_all(&remote_dir).unwrap();
    fs::create_dir_all(&state_dir).unwrap();

    // Create matching files on both sides (simulating already synced state)
    fs::write(local_dir.join("file.txt"), "content").unwrap();
    let remote_data = remote_dir.join("data");
    fs::create_dir_all(&remote_data).unwrap();
    fs::write(remote_data.join("file.txt"), "content").unwrap();

    let sync_entries = sync_entry("data", &local_dir, &remote_data, "bidirectional");
    let config = make_config("testremote", &sync_entries);
    fs::write(&config_path, &config).unwrap();

    // First run - should use resync
    let mut cmd = cargo_bin_cmd!("coltec-daemon");
    cmd.env("XDG_DATA_HOME", state_dir.to_str().unwrap())
        .arg("--config")
        .arg(&config_path)
        .arg("--once")
        .arg("--log-level")
        .arg("debug");
    cmd.assert().success();

    // Second run - should NOT use resync
    let mut cmd2 = cargo_bin_cmd!("coltec-daemon");
    cmd2.env("XDG_DATA_HOME", state_dir.to_str().unwrap())
        .arg("--config")
        .arg(&config_path)
        .arg("--once")
        .arg("--log-level")
        .arg("debug");

    let output = cmd2.assert().success();
    // Note: tracing logs go to stdout
    let stdout = String::from_utf8_lossy(&output.get_output().stdout);

    assert!(
        !stdout.contains("first-time sync"),
        "second bisync should NOT use --resync"
    );
}

// ============================================================================
// MULTIPLE TARGETS TESTS
// ============================================================================

#[test]
#[ignore]
fn test_multiple_sync_targets() {
    let temp = TempDir::new().unwrap();
    let local_code = temp.path().join("local/code");
    let local_config = temp.path().join("local/config");
    let remote_dir = temp.path().join("remote");
    let state_dir = temp.path().join("state");
    let config_path = temp.path().join("config.yaml");

    fs::create_dir_all(&local_code).unwrap();
    fs::create_dir_all(&local_config).unwrap();
    fs::create_dir_all(&remote_dir).unwrap();
    fs::create_dir_all(&state_dir).unwrap();

    // Create files in both local directories
    fs::write(local_code.join("main.rs"), "fn main() {}").unwrap();
    fs::write(local_config.join("settings.json"), "{}").unwrap();

    let remote_code = remote_dir.join("code");
    let remote_config = remote_dir.join("config");
    let sync_entries = format!(
        "{}{}",
        sync_entry("code", &local_code, &remote_code, "push-only"),
        sync_entry("config", &local_config, &remote_config, "push-only"),
    );
    let config = make_config("testremote", &sync_entries);
    fs::write(&config_path, &config).unwrap();

    let mut cmd = cargo_bin_cmd!("coltec-daemon");
    cmd.env("XDG_DATA_HOME", state_dir.to_str().unwrap())
        .arg("--config")
        .arg(&config_path)
        .arg("--once")
        .arg("--log-level")
        .arg("debug");

    cmd.assert().success();

    // Both targets should be synced
    assert!(remote_code.join("main.rs").exists());
    assert!(remote_dir.join("config/settings.json").exists());
}

// ============================================================================
// EXCLUDE PATTERN TESTS
// ============================================================================

#[test]
#[ignore]
fn test_exclude_patterns() {
    let temp = TempDir::new().unwrap();
    let local_dir = temp.path().join("local");
    let remote_dir = temp.path().join("remote");
    let state_dir = temp.path().join("state");
    let config_path = temp.path().join("config.yaml");

    fs::create_dir_all(&local_dir).unwrap();
    fs::create_dir_all(&remote_dir).unwrap();
    fs::create_dir_all(&state_dir).unwrap();

    // Create files - some should be excluded
    fs::write(local_dir.join("main.rs"), "fn main() {}").unwrap();
    fs::create_dir_all(local_dir.join(".git")).unwrap();
    fs::write(local_dir.join(".git/config"), "git config").unwrap();
    fs::create_dir_all(local_dir.join("node_modules/lodash")).unwrap();
    fs::write(
        local_dir.join("node_modules/lodash/index.js"),
        "module.exports = {}",
    )
    .unwrap();
    fs::write(local_dir.join("data.tmp"), "temporary").unwrap();

    let remote_code = remote_dir.join("code");
    let sync_entries = sync_entry_with_excludes(
        "code",
        &local_dir,
        &remote_code,
        "push-only",
        &[".git/**", "node_modules/**", "*.tmp"],
    );
    let config = make_config("testremote", &sync_entries);
    fs::write(&config_path, &config).unwrap();

    let mut cmd = cargo_bin_cmd!("coltec-daemon");
    cmd.env("XDG_DATA_HOME", state_dir.to_str().unwrap())
        .arg("--config")
        .arg(&config_path)
        .arg("--once")
        .arg("--log-level")
        .arg("debug");

    cmd.assert().success();

    // main.rs should be synced
    assert!(remote_code.join("main.rs").exists(), "main.rs should sync");

    // Excluded patterns should NOT be synced
    assert!(
        !remote_code.join(".git").exists(),
        ".git should be excluded"
    );
    assert!(
        !remote_code.join("node_modules").exists(),
        "node_modules should be excluded"
    );
    assert!(
        !remote_code.join("data.tmp").exists(),
        "*.tmp should be excluded"
    );
}

// ============================================================================
// DRY RUN TESTS
// ============================================================================

#[test]
#[ignore]
fn test_dry_run_does_not_modify_files() {
    let temp = TempDir::new().unwrap();
    let local_dir = temp.path().join("local");
    let remote_dir = temp.path().join("remote");
    let state_dir = temp.path().join("state");
    let config_path = temp.path().join("config.yaml");

    fs::create_dir_all(&local_dir).unwrap();
    fs::create_dir_all(&remote_dir).unwrap();
    fs::create_dir_all(&state_dir).unwrap();

    // Create local file
    fs::write(local_dir.join("file.txt"), "content").unwrap();

    let remote_data = remote_dir.join("data");
    let sync_entries = sync_entry("data", &local_dir, &remote_data, "push-only");
    let config = make_config("testremote", &sync_entries);
    fs::write(&config_path, &config).unwrap();

    let mut cmd = cargo_bin_cmd!("coltec-daemon");
    cmd.env("XDG_DATA_HOME", state_dir.to_str().unwrap())
        .arg("--config")
        .arg(&config_path)
        .arg("--once")
        .arg("--dry-run")
        .arg("--log-level")
        .arg("debug");

    cmd.assert().success();

    // Remote should NOT have the file (dry run)
    assert!(
        !remote_data.join("file.txt").exists(),
        "dry-run should not create remote files"
    );
}

// ============================================================================
// ERROR HANDLING TESTS
// ============================================================================

#[test]
#[ignore]
fn test_missing_rclone_gives_clear_error() {
    let temp = TempDir::new().unwrap();
    let local_dir = temp.path().join("local");
    let state_dir = temp.path().join("state");
    let config_path = temp.path().join("config.yaml");

    fs::create_dir_all(&local_dir).unwrap();
    fs::create_dir_all(&state_dir).unwrap();

    fs::write(local_dir.join("file.txt"), "content").unwrap();

    let sync_entries = sync_entry(
        "data",
        &local_dir,
        Path::new("/nonexistent/data"),
        "push-only",
    );
    let config = make_config("testremote", &sync_entries);
    fs::write(&config_path, &config).unwrap();

    let mut cmd = cargo_bin_cmd!("coltec-daemon");
    // Set PATH to empty to simulate missing rclone
    cmd.env("PATH", "")
        .env("XDG_DATA_HOME", state_dir.to_str().unwrap())
        .arg("--config")
        .arg(&config_path)
        .arg("--once");

    // Note: tracing logs go to stdout by default
    cmd.assert()
        .stdout(predicate::str::contains("rclone not found"));
}

// ============================================================================
// PRIORITY ORDERING TESTS
// ============================================================================

#[test]
#[ignore]
fn test_sync_order_respects_priority() {
    let temp = TempDir::new().unwrap();
    let local_high = temp.path().join("local/high");
    let local_low = temp.path().join("local/low");
    let remote_dir = temp.path().join("remote");
    let state_dir = temp.path().join("state");
    let config_path = temp.path().join("config.yaml");

    fs::create_dir_all(&local_high).unwrap();
    fs::create_dir_all(&local_low).unwrap();
    fs::create_dir_all(&remote_dir).unwrap();
    fs::create_dir_all(&state_dir).unwrap();

    fs::write(local_high.join("high.txt"), "high priority").unwrap();
    fs::write(local_low.join("low.txt"), "low priority").unwrap();

    let remote_low = remote_dir.join("low");
    let remote_high = remote_dir.join("high");

    // Define low priority first in YAML, high priority second
    // But high priority (1) should sync before low priority (5)
    let config = format!(
        r#"
name: e2e-test-workspace
metadata:
  org: testorg
  project: testproject
  environment: test
devcontainer:
  template:
    name: test
    path: test.json
  image:
    name: ghcr.io/test/image
persistence:
  enabled: true
  mode: replicated
  default_remote: testremote
  remotes:
    testremote:
      type: local
  sync:
    - name: low-priority
      path: {local_low}
      remote_path: {remote_low}
      direction: push-only
      interval: 60
      priority: 5
    - name: high-priority
      path: {local_high}
      remote_path: {remote_high}
      direction: push-only
      interval: 60
      priority: 1
"#,
        local_low = local_low.to_str().unwrap(),
        local_high = local_high.to_str().unwrap(),
        remote_low = remote_low.to_str().unwrap(),
        remote_high = remote_high.to_str().unwrap(),
    );
    fs::write(&config_path, &config).unwrap();

    let mut cmd = cargo_bin_cmd!("coltec-daemon");
    cmd.env("XDG_DATA_HOME", state_dir.to_str().unwrap())
        .arg("--config")
        .arg(&config_path)
        .arg("--once")
        .arg("--log-level")
        .arg("debug");

    let output = cmd.assert().success();
    // Note: tracing logs go to stdout
    let stdout = String::from_utf8_lossy(&output.get_output().stdout);

    // High priority should appear before low priority in logs
    let high_pos = stdout.find("high-priority");
    let low_pos = stdout.find("low-priority");

    assert!(
        high_pos.is_some(),
        "high-priority should be logged, got: {}",
        stdout
    );
    assert!(low_pos.is_some(), "low-priority should be logged");
    assert!(
        high_pos.unwrap() < low_pos.unwrap(),
        "high-priority (1) should sync before low-priority (5)"
    );
}
