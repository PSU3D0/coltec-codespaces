//! Sync planning layer - converts workspace-spec into executable sync actions.
//!
//! This module is pure (no IO) and deterministic. It transforms a `WorkspaceSpec`
//! into a `SyncPlan` containing ordered `SyncAction`s ready for execution.

use crate::{RcloneVolumeConfig, RemoteConfig, SyncDirection, SyncPath, WorkspaceSpec};
use std::collections::BTreeMap;

/// Resolved operation settings for a sync action.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct OperationSettings {
    /// Number of parallel file transfers (rclone --transfers)
    pub transfers: Option<u32>,
    /// Number of parallel checkers (rclone --checkers)
    pub checkers: Option<u32>,
    /// Bandwidth limit (e.g., "10M", "1G") (rclone --bwlimit)
    pub bwlimit: Option<String>,
}

/// Resolved remote configuration for a sync action.
#[derive(Debug, Clone, PartialEq)]
pub struct ResolvedRemote {
    /// Name of the remote (for state tracking)
    pub name: String,
    /// Backend type (s3, gcs, crypt, etc.)
    pub remote_type: String,
    /// Bucket name (for s3/gcs backends)
    pub bucket: Option<String>,
    /// Path prefix prepended to remote_path
    pub path_prefix: Option<String>,
    /// Backend-specific options
    pub options: BTreeMap<String, String>,
    /// For crypt remotes: the resolved base remote
    pub wrap_remote: Option<Box<ResolvedRemote>>,
    /// For crypt remotes: path within the wrapped remote
    pub wrap_path: Option<String>,
    /// For crypt remotes: password env var
    pub password_env: Option<String>,
    /// For crypt remotes: salt env var
    pub password2_env: Option<String>,
    /// For crypt remotes: filename encryption mode
    pub filename_encryption: Option<String>,
    /// For crypt remotes: directory name encryption
    pub directory_name_encryption: Option<bool>,
}

/// A planned sync action (pure data, no IO).
///
/// This represents a single sync operation to be executed by the sync engine.
#[derive(Debug, Clone, PartialEq)]
pub struct SyncAction {
    /// Unique name for this sync path
    pub name: String,
    /// Local filesystem path to sync
    pub local_path: String,
    /// Remote path (after placeholder resolution)
    pub remote_path: String,
    /// Sync direction (bidirectional, push-only, pull-only)
    pub direction: SyncDirection,
    /// Sync interval in seconds
    pub interval_secs: u64,
    /// Priority (lower = higher priority, synced first)
    pub priority: u8,
    /// Glob patterns to exclude from sync
    pub excludes: Vec<String>,
    /// Resolved remote configuration (named remotes) or None (legacy)
    pub remote: Option<ResolvedRemote>,
    /// Operation settings (transfers, checkers, bwlimit)
    pub operation: OperationSettings,
}

/// The full sync plan containing all actions.
#[derive(Debug, Clone)]
pub struct SyncPlan {
    /// Workspace name (for state directory)
    pub workspace_name: String,
    /// Ordered list of sync actions (sorted by priority)
    pub actions: Vec<SyncAction>,
}

impl SyncPlan {
    /// Returns true if the plan has no actions.
    pub fn is_empty(&self) -> bool {
        self.actions.is_empty()
    }

    /// Returns the minimum interval across all actions.
    pub fn min_interval(&self) -> u64 {
        self.actions
            .iter()
            .map(|a| a.interval_secs)
            .min()
            .unwrap_or(300)
    }

    /// Filter actions to only include those with matching names.
    pub fn filter_by_names(&self, names: &[String]) -> SyncPlan {
        if names.is_empty() {
            return self.clone();
        }

        SyncPlan {
            workspace_name: self.workspace_name.clone(),
            actions: self
                .actions
                .iter()
                .filter(|a| names.contains(&a.name))
                .cloned()
                .collect(),
        }
    }
}

/// Context for resolving placeholders in remote paths.
#[derive(Debug, Clone)]
pub struct PlanContext<'a> {
    /// Organization slug
    pub org: String,
    /// Project slug
    pub project: String,
    /// Environment (e.g., "dev", "staging")
    pub env: String,
    /// Named remotes map (reference to spec)
    pub remotes: &'a BTreeMap<String, RemoteConfig>,
    /// Default remote name
    pub default_remote: Option<&'a String>,
    /// Default operation settings
    pub defaults: Option<&'a crate::SyncDefaults>,
}

impl<'a> PlanContext<'a> {
    /// Create context from a workspace spec.
    pub fn from_spec(spec: &'a WorkspaceSpec) -> Self {
        Self {
            org: spec.metadata.org.clone(),
            project: spec.metadata.project.clone(),
            env: spec.metadata.environment.clone(),
            remotes: &spec.persistence.remotes,
            default_remote: spec.persistence.default_remote.as_ref(),
            defaults: spec.persistence.defaults.as_ref(),
        }
    }

    /// Resolve `{org}`, `{project}`, `{env}` placeholders in a template string.
    pub fn resolve(&self, template: &str) -> String {
        template
            .replace("{org}", &self.org)
            .replace("{project}", &self.project)
            .replace("{env}", &self.env)
    }

    /// Resolve a remote by name (recursively for crypt remotes).
    pub fn resolve_remote(&self, name: &str) -> Option<ResolvedRemote> {
        let config = self.remotes.get(name)?;
        self.resolve_remote_config(name, config)
    }

    /// Resolve a RemoteConfig into a ResolvedRemote.
    fn resolve_remote_config(&self, name: &str, config: &RemoteConfig) -> Option<ResolvedRemote> {
        // For crypt remotes, recursively resolve the wrapped remote
        let wrap_remote = if config.r#type == "crypt" {
            config.wrap_remote.as_ref().and_then(|wrap_name| {
                self.resolve_remote(wrap_name).map(Box::new)
            })
        } else {
            None
        };

        // Extract crypt-specific settings
        let (password_env, password2_env, filename_encryption, directory_name_encryption) =
            if let Some(crypt) = &config.crypt {
                (
                    Some(crypt.password_env.clone()),
                    crypt.password2_env.clone(),
                    Some(format!("{:?}", crypt.filename_encryption).to_lowercase()),
                    Some(crypt.directory_name_encryption),
                )
            } else {
                (None, None, None, None)
            };

        Some(ResolvedRemote {
            name: name.to_string(),
            remote_type: config.r#type.clone(),
            bucket: config.bucket.clone(),
            path_prefix: config.path_prefix.clone(),
            options: config.options.clone(),
            wrap_remote,
            wrap_path: config.wrap_path.clone(),
            password_env,
            password2_env,
            filename_encryption,
            directory_name_encryption,
        })
    }

    /// Resolve operation settings with cascading priority:
    /// sync_path overrides > defaults > None
    pub fn resolve_operation(
        &self,
        sp_transfers: Option<u32>,
        sp_checkers: Option<u32>,
        sp_bwlimit: Option<&String>,
    ) -> OperationSettings {
        OperationSettings {
            transfers: sp_transfers.or_else(|| self.defaults.and_then(|d| d.transfers)),
            checkers: sp_checkers.or_else(|| self.defaults.and_then(|d| d.checkers)),
            bwlimit: sp_bwlimit
                .cloned()
                .or_else(|| self.defaults.and_then(|d| d.bwlimit.clone())),
        }
    }

    /// Get the effective remote name for a sync path
    pub fn effective_remote_name<'b>(&self, sp_remote: Option<&'b String>) -> Option<&'b String>
    where
        'a: 'b,
    {
        sp_remote.or(self.default_remote)
    }
}

/// Build a sync plan from a workspace spec.
///
/// This function:
/// 1. Extracts sync paths from `persistence.sync`
/// 2. Extracts volumes from `persistence.multi_scope_volumes.environment`
/// 3. Resolves placeholders in remote paths
/// 4. Resolves named remotes per sync path
/// 5. Applies operation settings (transfers, checkers, bwlimit)
/// 6. Applies interval override if provided
/// 7. Sorts actions by priority (lower = higher priority)
pub fn build_plan(spec: &WorkspaceSpec, interval_override: Option<u64>) -> SyncPlan {
    let ctx = PlanContext::from_spec(spec);
    let mut actions = Vec::new();

    // Add persistence.sync paths
    for sp in &spec.persistence.sync {
        actions.push(sync_path_to_action(sp, &ctx, interval_override));
    }

    // Add multi_scope_volumes.environment
    if let Some(msv) = &spec.persistence.multi_scope_volumes {
        for vol in &msv.environment {
            actions.push(volume_to_action(vol, &ctx, interval_override));
        }
    }

    // Sort by priority (lower = higher priority)
    actions.sort_by_key(|a| a.priority);

    SyncPlan {
        workspace_name: spec.name.clone(),
        actions,
    }
}

/// Convert a `SyncPath` to a `SyncAction`.
fn sync_path_to_action(
    sp: &SyncPath,
    ctx: &PlanContext,
    interval_override: Option<u64>,
) -> SyncAction {
    // Resolve remote (named remotes if available)
    let remote = ctx
        .effective_remote_name(sp.remote.as_ref())
        .and_then(|name| ctx.resolve_remote(name));

    // Resolve operation settings
    let operation = ctx.resolve_operation(sp.transfers, sp.checkers, sp.bwlimit.as_ref());

    // Apply path_prefix if remote has one
    let remote_path = if let Some(ref r) = remote {
        if let Some(ref prefix) = r.path_prefix {
            format!("{}/{}", prefix.trim_end_matches('/'), ctx.resolve(&sp.remote_path))
        } else {
            ctx.resolve(&sp.remote_path)
        }
    } else {
        ctx.resolve(&sp.remote_path)
    };

    SyncAction {
        name: sp.name.clone(),
        local_path: sp.path.clone(),
        remote_path,
        direction: sp.direction.clone(),
        interval_secs: interval_override.unwrap_or(sp.interval),
        priority: sp.priority,
        excludes: sp.exclude.clone(),
        remote,
        operation,
    }
}

/// Convert an `RcloneVolumeConfig` to a `SyncAction`.
fn volume_to_action(
    vol: &RcloneVolumeConfig,
    ctx: &PlanContext,
    interval_override: Option<u64>,
) -> SyncAction {
    // Volumes use the default remote (no per-volume override currently)
    let remote = ctx
        .default_remote
        .and_then(|name| ctx.resolve_remote(name));

    // Volumes don't have per-path operation overrides, use defaults
    let operation = ctx.resolve_operation(None, None, None);

    // Apply path_prefix if remote has one
    let remote_path = if let Some(ref r) = remote {
        if let Some(ref prefix) = r.path_prefix {
            format!("{}/{}", prefix.trim_end_matches('/'), ctx.resolve(&vol.remote_path))
        } else {
            ctx.resolve(&vol.remote_path)
        }
    } else {
        ctx.resolve(&vol.remote_path)
    };

    SyncAction {
        name: vol.name.clone(),
        local_path: vol.mount_path.clone(),
        remote_path,
        direction: vol.sync.clone(),
        interval_secs: interval_override.unwrap_or(vol.interval),
        priority: vol.priority,
        excludes: vol.exclude.clone(),
        remote,
        operation,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_placeholder_resolution() {
        let empty_remotes = BTreeMap::new();
        let ctx = PlanContext {
            org: "myorg".into(),
            project: "myproj".into(),
            env: "dev".into(),
            remotes: &empty_remotes,
            default_remote: None,
            defaults: None,
        };

        assert_eq!(
            ctx.resolve("workspaces/{org}/{project}/{env}/data"),
            "workspaces/myorg/myproj/dev/data"
        );

        assert_eq!(
            ctx.resolve("{org}-{project}-{env}"),
            "myorg-myproj-dev"
        );

        // No placeholders
        assert_eq!(ctx.resolve("static/path"), "static/path");
    }

    #[test]
    fn test_build_plan_from_valid_spec() {
        let yaml = r#"
name: test-workspace
metadata:
  org: testorg
  project: testproject
  environment: staging
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
      remote_path: workspaces/{org}/{project}/{env}/code
      direction: bidirectional
      interval: 120
      priority: 1
      exclude:
        - .git/**
        - node_modules/**
    - name: config
      path: /home/vscode/.config
      remote_path: workspaces/{org}/{project}/{env}/config
      direction: push-only
      interval: 300
      priority: 2
"#;

        let spec: WorkspaceSpec = serde_yaml::from_str(yaml).unwrap();
        let plan = build_plan(&spec, None);

        assert_eq!(plan.workspace_name, "test-workspace");
        assert_eq!(plan.actions.len(), 2);

        // Check sorting by priority
        assert_eq!(plan.actions[0].name, "workspace");
        assert_eq!(plan.actions[0].priority, 1);
        assert_eq!(plan.actions[1].name, "config");
        assert_eq!(plan.actions[1].priority, 2);

        // Check placeholder resolution
        assert_eq!(
            plan.actions[0].remote_path,
            "workspaces/testorg/testproject/staging/code"
        );

        // Check remote is resolved
        assert!(plan.actions[0].remote.is_some());
        let remote = plan.actions[0].remote.as_ref().unwrap();
        assert_eq!(remote.remote_type, "s3");
        assert_eq!(remote.bucket.as_deref(), Some("test-bucket"));
    }

    #[test]
    fn test_build_plan_with_interval_override() {
        let yaml = r#"
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
  sync:
    - name: fast
      path: /workspace
      remote_path: foo/bar
      interval: 60
    - name: slow
      path: /data
      remote_path: foo/data
      interval: 600
"#;

        let spec: WorkspaceSpec = serde_yaml::from_str(yaml).unwrap();

        // Without override
        let plan = build_plan(&spec, None);
        assert_eq!(plan.actions[0].interval_secs, 60);
        assert_eq!(plan.actions[1].interval_secs, 600);

        // With override
        let plan = build_plan(&spec, Some(30));
        assert_eq!(plan.actions[0].interval_secs, 30);
        assert_eq!(plan.actions[1].interval_secs, 30);
    }

    #[test]
    fn test_build_plan_with_multi_scope_volumes() {
        let yaml = r#"
name: test-workspace
metadata:
  org: testorg
  project: testproject
  environment: dev
devcontainer:
  template:
    name: python
    path: templates/python.json
  image:
    name: ghcr.io/test/image
persistence:
  enabled: true
  mode: replicated
  multi_scope_volumes:
    environment:
      - name: agent-context
        remote_path: workspaces/{org}/{project}/{env}/agent
        mount_path: /workspace/agent
        sync: bidirectional
        interval: 60
        priority: 1
      - name: scratch
        remote_path: workspaces/{org}/{project}/{env}/scratch
        mount_path: /workspace/scratch
        sync: push-only
        interval: 300
        priority: 3
"#;

        let spec: WorkspaceSpec = serde_yaml::from_str(yaml).unwrap();
        let plan = build_plan(&spec, None);

        assert_eq!(plan.actions.len(), 2);
        assert_eq!(plan.actions[0].name, "agent-context");
        assert_eq!(plan.actions[0].local_path, "/workspace/agent");
        assert_eq!(
            plan.actions[0].remote_path,
            "workspaces/testorg/testproject/dev/agent"
        );
    }

    #[test]
    fn test_plan_filter_by_names() {
        let yaml = r#"
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
  sync:
    - name: a
      path: /a
      remote_path: a
      interval: 60
    - name: b
      path: /b
      remote_path: b
      interval: 60
    - name: c
      path: /c
      remote_path: c
      interval: 60
"#;

        let spec: WorkspaceSpec = serde_yaml::from_str(yaml).unwrap();
        let plan = build_plan(&spec, None);

        // Filter to just "a" and "c"
        let filtered = plan.filter_by_names(&["a".into(), "c".into()]);
        assert_eq!(filtered.actions.len(), 2);
        assert_eq!(filtered.actions[0].name, "a");
        assert_eq!(filtered.actions[1].name, "c");

        // Empty filter returns all
        let all = plan.filter_by_names(&[]);
        assert_eq!(all.actions.len(), 3);
    }

    #[test]
    fn test_plan_min_interval() {
        let yaml = r#"
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
  sync:
    - name: fast
      path: /fast
      remote_path: fast
      interval: 30
    - name: slow
      path: /slow
      remote_path: slow
      interval: 600
"#;

        let spec: WorkspaceSpec = serde_yaml::from_str(yaml).unwrap();
        let plan = build_plan(&spec, None);

        assert_eq!(plan.min_interval(), 30);
    }

    #[test]
    fn test_empty_plan() {
        let yaml = r#"
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
  enabled: false
"#;

        let spec: WorkspaceSpec = serde_yaml::from_str(yaml).unwrap();
        let plan = build_plan(&spec, None);

        assert!(plan.is_empty());
        assert_eq!(plan.min_interval(), 300); // Default when empty
    }

    #[test]
    fn test_named_remotes_resolution() {
        let yaml = r#"
name: test-workspace
metadata:
  org: testorg
  project: testproject
  environment: dev
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
      path_prefix: workspaces
      options:
        provider: Cloudflare
        endpoint: https://xxx.r2.cloudflarestorage.com
    secondary:
      type: s3
      bucket: secondary-bucket
  sync:
    - name: workspace
      path: /workspace
      remote_path: "{org}/{project}/{env}/code"
      interval: 60
    - name: logs
      path: /var/log
      remote_path: "{org}/{project}/{env}/logs"
      remote: secondary
      interval: 300
"#;

        let spec: WorkspaceSpec = serde_yaml::from_str(yaml).unwrap();
        let plan = build_plan(&spec, None);

        assert_eq!(plan.actions.len(), 2);

        // First action uses default remote (primary)
        let workspace_action = &plan.actions[0];
        assert_eq!(workspace_action.name, "workspace");
        assert!(workspace_action.remote.is_some());
        let remote = workspace_action.remote.as_ref().unwrap();
        assert_eq!(remote.name, "primary");
        assert_eq!(remote.remote_type, "s3");
        assert_eq!(remote.bucket, Some("my-bucket".to_string()));
        // Check path_prefix is applied
        assert_eq!(
            workspace_action.remote_path,
            "workspaces/testorg/testproject/dev/code"
        );

        // Second action overrides to secondary remote
        let logs_action = &plan.actions[1];
        assert_eq!(logs_action.name, "logs");
        assert!(logs_action.remote.is_some());
        let remote = logs_action.remote.as_ref().unwrap();
        assert_eq!(remote.name, "secondary");
        assert_eq!(remote.bucket, Some("secondary-bucket".to_string()));
        // No path_prefix on secondary
        assert_eq!(
            logs_action.remote_path,
            "testorg/testproject/dev/logs"
        );
    }

    #[test]
    fn test_operation_settings_resolution() {
        let yaml = r#"
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
  defaults:
    transfers: 8
    checkers: 16
    bwlimit: "10M"
  sync:
    - name: fast
      path: /fast
      remote_path: fast
      interval: 60
      transfers: 16
    - name: slow
      path: /slow
      remote_path: slow
      interval: 300
      bwlimit: "1M"
    - name: default
      path: /default
      remote_path: default
      interval: 300
"#;

        let spec: WorkspaceSpec = serde_yaml::from_str(yaml).unwrap();
        let plan = build_plan(&spec, None);

        assert_eq!(plan.actions.len(), 3);

        // "fast" overrides transfers but inherits checkers and bwlimit
        let fast = &plan.actions[0];
        assert_eq!(fast.operation.transfers, Some(16)); // overridden
        assert_eq!(fast.operation.checkers, Some(16)); // from defaults
        assert_eq!(fast.operation.bwlimit, Some("10M".to_string())); // from defaults

        // "slow" overrides bwlimit but inherits transfers and checkers
        let slow = &plan.actions[1];
        assert_eq!(slow.operation.transfers, Some(8)); // from defaults
        assert_eq!(slow.operation.checkers, Some(16)); // from defaults
        assert_eq!(slow.operation.bwlimit, Some("1M".to_string())); // overridden

        // "default" uses all defaults
        let default = &plan.actions[2];
        assert_eq!(default.operation.transfers, Some(8));
        assert_eq!(default.operation.checkers, Some(16));
        assert_eq!(default.operation.bwlimit, Some("10M".to_string()));
    }

    #[test]
    fn test_crypt_remote_resolution() {
        let yaml = r#"
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
    r2-base:
      type: s3
      bucket: my-bucket
      options:
        provider: Cloudflare
    encrypted:
      type: crypt
      wrap_remote: r2-base
      wrap_path: encrypted-data
      crypt:
        password_env: RCLONE_CRYPT_PASSWORD
        password2_env: RCLONE_CRYPT_PASSWORD2
        filename_encryption: standard
        directory_name_encryption: true
  sync:
    - name: secrets
      path: /secrets
      remote_path: "{org}/secrets"
      interval: 300
"#;

        let spec: WorkspaceSpec = serde_yaml::from_str(yaml).unwrap();
        let plan = build_plan(&spec, None);

        assert_eq!(plan.actions.len(), 1);

        let action = &plan.actions[0];
        assert!(action.remote.is_some());
        let remote = action.remote.as_ref().unwrap();
        assert_eq!(remote.name, "encrypted");
        assert_eq!(remote.remote_type, "crypt");
        assert_eq!(remote.wrap_path, Some("encrypted-data".to_string()));
        assert_eq!(
            remote.password_env,
            Some("RCLONE_CRYPT_PASSWORD".to_string())
        );
        assert_eq!(
            remote.password2_env,
            Some("RCLONE_CRYPT_PASSWORD2".to_string())
        );

        // Check the wrapped remote is resolved
        assert!(remote.wrap_remote.is_some());
        let base = remote.wrap_remote.as_ref().unwrap();
        assert_eq!(base.name, "r2-base");
        assert_eq!(base.remote_type, "s3");
        assert_eq!(base.bucket, Some("my-bucket".to_string()));
    }
}
