//! Semantic validation for workspace-spec beyond JSON Schema.
//!
//! JSON Schema validates structure; this module validates invariants like:
//! - Replicated persistence mode requires sync paths
//! - Sync intervals must be >= 10 seconds
//! - Networking hostname prefix required when enabled
//! - Remote references must exist in the remotes map
//! - Crypt remotes require proper configuration

use crate::{PersistenceMode, WorkspaceSpec};
use anyhow::Context;
use std::collections::HashSet;
use std::path::Path;
use thiserror::Error;

/// Errors from semantic validation (beyond JSON Schema).
#[derive(Error, Debug)]
pub enum ConfigError {
    #[error("persistence.mode='replicated' requires at least one sync path or multi_scope_volumes.environment entry")]
    ReplicatedRequiresSyncPaths,

    #[error("sync path '{name}' has interval < 10s (got {interval}s), minimum is 10s")]
    SyncIntervalTooLow { name: String, interval: u64 },

    #[error("multi_scope_volumes.environment['{name}'] has interval < 10s (got {interval}s), minimum is 10s")]
    VolumeIntervalTooLow { name: String, interval: u64 },

    #[error("networking.enabled=true but networking.hostname_prefix is empty")]
    NetworkingMissingHostname,

    #[error("duplicate sync path name: '{0}'")]
    DuplicateSyncPathName(String),

    #[error("duplicate volume name: '{0}'")]
    DuplicateVolumeName(String),

    // --- Remote validation errors ---
    #[error("default_remote '{0}' does not exist in remotes")]
    DefaultRemoteNotFound(String),

    #[error(
        "sync path '{path_name}' references remote '{remote_name}' which does not exist in remotes"
    )]
    SyncPathRemoteNotFound {
        path_name: String,
        remote_name: String,
    },

    #[error("sync path '{0}' has no remote specified and no default_remote is set")]
    SyncPathNoRemote(String),

    #[error("remote '{0}' has type='s3' but no bucket specified")]
    S3RemoteMissingBucket(String),

    #[error("remote '{0}' has type='crypt' but no wrap_remote specified")]
    CryptRemoteMissingWrapRemote(String),

    #[error("remote '{0}' has type='crypt' but no crypt config specified")]
    CryptRemoteMissingConfig(String),

    #[error("remote '{name}' wraps remote '{wrap_remote}' which does not exist")]
    WrapRemoteNotFound { name: String, wrap_remote: String },

    #[error("remote '{0}' has circular wrap_remote reference")]
    CircularWrapRemote(String),

    #[error("persistence has sync paths but no remotes configured")]
    NoRemotesConfigured,
}

impl WorkspaceSpec {
    /// Validate semantic invariants beyond JSON Schema.
    ///
    /// Call this after deserializing to catch configuration errors
    /// that JSON Schema cannot express.
    pub fn validate_semantics(&self) -> Result<(), ConfigError> {
        // Invariant 1: replicated mode requires sync paths
        if self.persistence.enabled && matches!(self.persistence.mode, PersistenceMode::Replicated)
        {
            let has_sync_paths = !self.persistence.sync.is_empty();
            let has_env_volumes = self
                .persistence
                .multi_scope_volumes
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

        // Also check multi_scope_volumes.environment intervals
        if let Some(msv) = &self.persistence.multi_scope_volumes {
            for vol in &msv.environment {
                if vol.interval < 10 {
                    return Err(ConfigError::VolumeIntervalTooLow {
                        name: vol.name.clone(),
                        interval: vol.interval,
                    });
                }
            }
        }

        // Invariant 3: networking hostname prefix required if enabled
        if self.networking.enabled && self.networking.hostname_prefix.is_empty() {
            return Err(ConfigError::NetworkingMissingHostname);
        }

        // Invariant 4: no duplicate sync path names
        let mut seen_sync = HashSet::new();
        for sp in &self.persistence.sync {
            if !seen_sync.insert(&sp.name) {
                return Err(ConfigError::DuplicateSyncPathName(sp.name.clone()));
            }
        }

        // Invariant 5: no duplicate volume names
        if let Some(msv) = &self.persistence.multi_scope_volumes {
            let mut seen_vol = HashSet::new();
            for vol in &msv.environment {
                if !seen_vol.insert(&vol.name) {
                    return Err(ConfigError::DuplicateVolumeName(vol.name.clone()));
                }
            }
        }

        // --- Remote validation (Option 2 - Named remotes) ---
        self.validate_remotes()?;

        Ok(())
    }

    /// Validate remote configuration.
    fn validate_remotes(&self) -> Result<(), ConfigError> {
        let has_sync_paths = !self.persistence.sync.is_empty();
        let has_env_volumes = self
            .persistence
            .multi_scope_volumes
            .as_ref()
            .map(|v| !v.environment.is_empty())
            .unwrap_or(false);

        // If no sync paths, skip remote validation
        if !has_sync_paths && !has_env_volumes {
            return Ok(());
        }

        // Must have named remotes if there are sync paths
        if self.persistence.remotes.is_empty() {
            return Err(ConfigError::NoRemotesConfigured);
        }

        // Validate default_remote exists
        if let Some(default) = &self.persistence.default_remote {
            if !self.persistence.remotes.contains_key(default) {
                return Err(ConfigError::DefaultRemoteNotFound(default.clone()));
            }
        }

        // Validate each remote's configuration
        for (name, remote) in &self.persistence.remotes {
            // S3-type remotes need a bucket
            if remote.r#type == "s3" && remote.bucket.is_none() {
                return Err(ConfigError::S3RemoteMissingBucket(name.clone()));
            }

            // Crypt-type remotes need wrap_remote and crypt config
            if remote.r#type == "crypt" {
                if remote.wrap_remote.is_none() {
                    return Err(ConfigError::CryptRemoteMissingWrapRemote(name.clone()));
                }
                if remote.crypt.is_none() {
                    return Err(ConfigError::CryptRemoteMissingConfig(name.clone()));
                }

                // Validate wrap_remote exists
                let wrap = remote.wrap_remote.as_ref().unwrap();
                if !self.persistence.remotes.contains_key(wrap) {
                    return Err(ConfigError::WrapRemoteNotFound {
                        name: name.clone(),
                        wrap_remote: wrap.clone(),
                    });
                }

                // Check for circular references (simple 1-level check)
                if wrap == name {
                    return Err(ConfigError::CircularWrapRemote(name.clone()));
                }
            }
        }

        // Validate sync path remote references
        for sp in &self.persistence.sync {
            let remote_name = sp
                .remote
                .as_ref()
                .or(self.persistence.default_remote.as_ref());

            match remote_name {
                Some(r) if !self.persistence.remotes.contains_key(r) => {
                    return Err(ConfigError::SyncPathRemoteNotFound {
                        path_name: sp.name.clone(),
                        remote_name: r.clone(),
                    });
                }
                None => {
                    return Err(ConfigError::SyncPathNoRemote(sp.name.clone()));
                }
                _ => {}
            }
        }

        Ok(())
    }
}

/// Load a workspace-spec YAML file and validate both schema and semantics.
///
/// This is the primary entry point for loading configuration.
pub fn load_and_validate(path: &Path) -> anyhow::Result<WorkspaceSpec> {
    let content = std::fs::read_to_string(path)
        .with_context(|| format!("failed to read {}", path.display()))?;

    let spec: WorkspaceSpec = serde_yaml::from_str(&content)
        .with_context(|| format!("failed to parse YAML in {}", path.display()))?;

    spec.validate_semantics()
        .with_context(|| format!("semantic validation failed for {}", path.display()))?;

    Ok(spec)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn minimal_valid_spec() -> &'static str {
        r#"
name: test-workspace
metadata:
  org: testorg
  project: testproject
devcontainer:
  template:
    name: python
    path: templates/python.json
  image:
    name: ghcr.io/test/image
"#
    }

    fn replicated_with_sync() -> &'static str {
        r#"
name: test-workspace
metadata:
  org: testorg
  project: testproject
devcontainer:
  template:
    name: python
    path: templates/python.json
  image:
    name: ghcr.io/test/image
persistence:
  enabled: true
  mode: replicated
  default_remote: primary
  remotes:
    primary:
      type: s3
      bucket: test-bucket
  sync:
    - name: workspace
      path: /workspace
      remote_path: workspaces/{org}/{project}/data
      interval: 60
"#
    }

    fn replicated_no_sync() -> &'static str {
        r#"
name: test-workspace
metadata:
  org: testorg
  project: testproject
devcontainer:
  template:
    name: python
    path: templates/python.json
  image:
    name: ghcr.io/test/image
persistence:
  enabled: true
  mode: replicated
"#
    }

    fn interval_too_low() -> &'static str {
        r#"
name: test-workspace
metadata:
  org: testorg
  project: testproject
devcontainer:
  template:
    name: python
    path: templates/python.json
  image:
    name: ghcr.io/test/image
persistence:
  enabled: true
  mode: replicated
  default_remote: primary
  remotes:
    primary:
      type: s3
      bucket: test-bucket
  sync:
    - name: fast
      path: /workspace
      remote_path: foo/bar
      interval: 5
"#
    }

    fn duplicate_sync_name() -> &'static str {
        r#"
name: test-workspace
metadata:
  org: testorg
  project: testproject
devcontainer:
  template:
    name: python
    path: templates/python.json
  image:
    name: ghcr.io/test/image
persistence:
  enabled: true
  mode: replicated
  default_remote: primary
  remotes:
    primary:
      type: s3
      bucket: test-bucket
  sync:
    - name: data
      path: /workspace/data
      remote_path: foo/data
      interval: 60
    - name: data
      path: /workspace/other
      remote_path: foo/other
      interval: 60
"#
    }

    #[test]
    fn test_minimal_valid_spec_passes() {
        let spec: WorkspaceSpec = serde_yaml::from_str(minimal_valid_spec()).unwrap();
        assert!(spec.validate_semantics().is_ok());
    }

    // --- Named remotes validation tests ---

    fn named_remotes_valid() -> &'static str {
        r#"
name: test-workspace
metadata:
  org: testorg
  project: testproject
devcontainer:
  image:
    name: ghcr.io/test/image
persistence:
  enabled: true
  mode: replicated
  default_remote: primary
  remotes:
    primary:
      type: s3
      bucket: my-bucket
  sync:
    - name: workspace
      path: /workspace
      remote_path: data
      interval: 60
"#
    }

    fn named_remotes_default_not_found() -> &'static str {
        r#"
name: test-workspace
metadata:
  org: testorg
  project: testproject
devcontainer:
  image:
    name: ghcr.io/test/image
persistence:
  enabled: true
  mode: replicated
  default_remote: nonexistent
  remotes:
    primary:
      type: s3
      bucket: my-bucket
  sync:
    - name: workspace
      path: /workspace
      remote_path: data
      interval: 60
"#
    }

    fn named_remotes_s3_missing_bucket() -> &'static str {
        r#"
name: test-workspace
metadata:
  org: testorg
  project: testproject
devcontainer:
  image:
    name: ghcr.io/test/image
persistence:
  enabled: true
  mode: replicated
  default_remote: primary
  remotes:
    primary:
      type: s3
  sync:
    - name: workspace
      path: /workspace
      remote_path: data
      interval: 60
"#
    }

    fn named_remotes_crypt_valid() -> &'static str {
        r#"
name: test-workspace
metadata:
  org: testorg
  project: testproject
devcontainer:
  image:
    name: ghcr.io/test/image
persistence:
  enabled: true
  mode: replicated
  default_remote: encrypted
  remotes:
    base:
      type: s3
      bucket: my-bucket
    encrypted:
      type: crypt
      wrap_remote: base
      crypt:
        password_env: RCLONE_CRYPT_PASSWORD
  sync:
    - name: workspace
      path: /workspace
      remote_path: data
      interval: 60
"#
    }

    fn named_remotes_crypt_missing_wrap() -> &'static str {
        r#"
name: test-workspace
metadata:
  org: testorg
  project: testproject
devcontainer:
  image:
    name: ghcr.io/test/image
persistence:
  enabled: true
  mode: replicated
  default_remote: encrypted
  remotes:
    encrypted:
      type: crypt
      crypt:
        password_env: RCLONE_CRYPT_PASSWORD
  sync:
    - name: workspace
      path: /workspace
      remote_path: data
      interval: 60
"#
    }

    fn named_remotes_sync_path_override() -> &'static str {
        r#"
name: test-workspace
metadata:
  org: testorg
  project: testproject
devcontainer:
  image:
    name: ghcr.io/test/image
persistence:
  enabled: true
  mode: replicated
  default_remote: primary
  remotes:
    primary:
      type: s3
      bucket: bucket1
    secondary:
      type: s3
      bucket: bucket2
  sync:
    - name: workspace
      path: /workspace
      remote_path: data
      interval: 60
    - name: logs
      path: /logs
      remote_path: logs
      remote: secondary
      interval: 300
"#
    }

    fn named_remotes_sync_path_invalid_remote() -> &'static str {
        r#"
name: test-workspace
metadata:
  org: testorg
  project: testproject
devcontainer:
  image:
    name: ghcr.io/test/image
persistence:
  enabled: true
  mode: replicated
  default_remote: primary
  remotes:
    primary:
      type: s3
      bucket: bucket1
  sync:
    - name: workspace
      path: /workspace
      remote_path: data
      remote: nonexistent
      interval: 60
"#
    }

    #[test]
    fn test_named_remotes_valid_passes() {
        let spec: WorkspaceSpec = serde_yaml::from_str(named_remotes_valid()).unwrap();
        assert!(spec.validate_semantics().is_ok());
    }

    #[test]
    fn test_named_remotes_default_not_found_fails() {
        let spec: WorkspaceSpec = serde_yaml::from_str(named_remotes_default_not_found()).unwrap();
        let err = spec.validate_semantics().unwrap_err();
        match err {
            ConfigError::DefaultRemoteNotFound(name) => {
                assert_eq!(name, "nonexistent");
            }
            _ => panic!("expected DefaultRemoteNotFound, got {:?}", err),
        }
    }

    #[test]
    fn test_named_remotes_s3_missing_bucket_fails() {
        let spec: WorkspaceSpec = serde_yaml::from_str(named_remotes_s3_missing_bucket()).unwrap();
        let err = spec.validate_semantics().unwrap_err();
        match err {
            ConfigError::S3RemoteMissingBucket(name) => {
                assert_eq!(name, "primary");
            }
            _ => panic!("expected S3RemoteMissingBucket, got {:?}", err),
        }
    }

    #[test]
    fn test_named_remotes_crypt_valid_passes() {
        let spec: WorkspaceSpec = serde_yaml::from_str(named_remotes_crypt_valid()).unwrap();
        assert!(spec.validate_semantics().is_ok());
    }

    #[test]
    fn test_named_remotes_crypt_missing_wrap_fails() {
        let spec: WorkspaceSpec = serde_yaml::from_str(named_remotes_crypt_missing_wrap()).unwrap();
        let err = spec.validate_semantics().unwrap_err();
        match err {
            ConfigError::CryptRemoteMissingWrapRemote(name) => {
                assert_eq!(name, "encrypted");
            }
            _ => panic!("expected CryptRemoteMissingWrapRemote, got {:?}", err),
        }
    }

    #[test]
    fn test_named_remotes_sync_path_override_passes() {
        let spec: WorkspaceSpec = serde_yaml::from_str(named_remotes_sync_path_override()).unwrap();
        assert!(spec.validate_semantics().is_ok());
    }

    #[test]
    fn test_named_remotes_sync_path_invalid_remote_fails() {
        let spec: WorkspaceSpec =
            serde_yaml::from_str(named_remotes_sync_path_invalid_remote()).unwrap();
        let err = spec.validate_semantics().unwrap_err();
        match err {
            ConfigError::SyncPathRemoteNotFound {
                path_name,
                remote_name,
            } => {
                assert_eq!(path_name, "workspace");
                assert_eq!(remote_name, "nonexistent");
            }
            _ => panic!("expected SyncPathRemoteNotFound, got {:?}", err),
        }
    }

    #[test]
    fn test_replicated_with_sync_passes() {
        let spec: WorkspaceSpec = serde_yaml::from_str(replicated_with_sync()).unwrap();
        assert!(spec.validate_semantics().is_ok());
    }

    #[test]
    fn test_replicated_no_sync_fails() {
        let spec: WorkspaceSpec = serde_yaml::from_str(replicated_no_sync()).unwrap();
        let err = spec.validate_semantics().unwrap_err();
        assert!(matches!(err, ConfigError::ReplicatedRequiresSyncPaths));
    }

    #[test]
    fn test_interval_too_low_fails() {
        let spec: WorkspaceSpec = serde_yaml::from_str(interval_too_low()).unwrap();
        let err = spec.validate_semantics().unwrap_err();
        match err {
            ConfigError::SyncIntervalTooLow { name, interval } => {
                assert_eq!(name, "fast");
                assert_eq!(interval, 5);
            }
            _ => panic!("expected SyncIntervalTooLow, got {:?}", err),
        }
    }

    #[test]
    fn test_duplicate_sync_name_fails() {
        let spec: WorkspaceSpec = serde_yaml::from_str(duplicate_sync_name()).unwrap();
        let err = spec.validate_semantics().unwrap_err();
        match err {
            ConfigError::DuplicateSyncPathName(name) => {
                assert_eq!(name, "data");
            }
            _ => panic!("expected DuplicateSyncPathName, got {:?}", err),
        }
    }
}
