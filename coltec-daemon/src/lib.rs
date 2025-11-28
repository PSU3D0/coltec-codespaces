use chrono::{DateTime, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub mod config;
pub mod plan;
pub mod sync;

pub use config::{load_and_validate, ConfigError};
pub use plan::{build_plan, OperationSettings, PlanContext, ResolvedRemote, SyncAction, SyncPlan};
pub use sync::{execute_plan, execute_sync, PlanResult, SyncResult};
pub use schemars::schema_for;

pub fn workspace_schema() -> schemars::schema::RootSchema {
    schema_for!(WorkspaceSpec)
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct NetworkingSpec {
    #[serde(default)]
    pub enabled: bool,
    #[serde(default = "default_hostname_prefix")]
    pub hostname_prefix: String,
    #[serde(default = "default_network_tags")]
    pub tags: Vec<String>,
}

fn default_hostname_prefix() -> String {
    "dev-".to_string()
}

fn default_network_tags() -> Vec<String> {
    vec!["tag:devcontainer".to_string()]
}

impl Default for NetworkingSpec {
    fn default() -> Self {
        Self {
            enabled: false,
            hostname_prefix: default_hostname_prefix(),
            tags: default_network_tags(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct PersistenceMount {
    pub name: String,
    pub target: String,
    pub source: String,
    #[serde(default = "default_persistence_mount_type")]
    pub r#type: String,
}

fn default_persistence_mount_type() -> String {
    "symlink".to_string()
}

fn default_remote_type() -> String {
    "s3".to_string()
}

// =============================================================================
// Named Remote Configuration
// =============================================================================

/// Filename encryption mode for rclone crypt remotes.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum FilenameEncryption {
    /// Full filename encryption (max ~143 characters)
    Standard,
    /// Lightweight rotation-based obfuscation (longer names allowed)
    Obfuscate,
    /// Names remain visible; only ".bin" extensions are added
    Off,
}

impl Default for FilenameEncryption {
    fn default() -> Self {
        Self::Standard
    }
}

/// Encryption configuration for rclone crypt remotes.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct CryptConfig {
    /// Environment variable containing the encryption password
    pub password_env: String,
    /// Environment variable containing the salt (optional but recommended)
    #[serde(default)]
    pub password2_env: Option<String>,
    /// Filename encryption mode
    #[serde(default)]
    pub filename_encryption: FilenameEncryption,
    /// Whether to encrypt directory names
    #[serde(default = "default_true")]
    pub directory_name_encryption: bool,
}

fn default_true() -> bool {
    true
}

/// Named remote configuration for rclone backends.
///
/// Supports both direct backends (s3, gcs, etc.) and wrapper backends (crypt).
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RemoteConfig {
    /// Backend type: "s3", "gcs", "crypt", etc.
    #[serde(default = "default_remote_type")]
    pub r#type: String,

    /// Bucket name (required for s3/gcs, not for crypt)
    #[serde(default)]
    pub bucket: Option<String>,

    /// Path prefix prepended to all remote_paths using this remote
    #[serde(default)]
    pub path_prefix: Option<String>,

    /// Backend-specific options (provider, endpoint, region, etc.)
    /// These map directly to rclone config options.
    #[serde(default)]
    pub options: std::collections::BTreeMap<String, String>,

    // --- Crypt-specific fields (only when type = "crypt") ---
    /// Base remote to wrap (required for crypt)
    #[serde(default)]
    pub wrap_remote: Option<String>,

    /// Path within the wrapped remote
    #[serde(default)]
    pub wrap_path: Option<String>,

    /// Encryption configuration (required for crypt)
    #[serde(default)]
    pub crypt: Option<CryptConfig>,
}

/// Default operation settings for sync paths.
#[derive(Debug, Clone, Default, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct SyncDefaults {
    /// Number of parallel file transfers (rclone --transfers)
    #[serde(default)]
    pub transfers: Option<u32>,

    /// Number of parallel checkers (rclone --checkers)
    #[serde(default)]
    pub checkers: Option<u32>,

    /// Bandwidth limit (e.g., "10M", "1G") (rclone --bwlimit)
    #[serde(default)]
    pub bwlimit: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "kebab-case")]
pub enum SyncDirection {
    #[serde(alias = "bidirectional")]
    Bidirectional,
    #[serde(alias = "pull-only")]
    PullOnly,
    #[serde(alias = "push-only")]
    PushOnly,
}

impl Default for SyncDirection {
    fn default() -> Self {
        Self::Bidirectional
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct SyncPath {
    /// Unique name for this sync path
    pub name: String,
    /// Local filesystem path to sync
    pub path: String,
    /// Remote path (supports {org}, {project}, {env} placeholders)
    pub remote_path: String,
    /// Override the default remote for this sync path
    #[serde(default)]
    pub remote: Option<String>,
    #[serde(default)]
    pub direction: SyncDirection,
    #[serde(default = "default_sync_interval")]
    pub interval: u64,
    #[serde(default = "default_sync_priority")]
    pub priority: u8,
    #[serde(default)]
    pub exclude: Vec<String>,

    // --- Operation overrides (optional, inherit from defaults) ---
    /// Override parallel file transfers for this path
    #[serde(default)]
    pub transfers: Option<u32>,
    /// Override parallel checkers for this path
    #[serde(default)]
    pub checkers: Option<u32>,
    /// Override bandwidth limit for this path (e.g., "5M")
    #[serde(default)]
    pub bwlimit: Option<String>,
}

fn default_sync_interval() -> u64 {
    300
}

fn default_sync_priority() -> u8 {
    2
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "kebab-case")]
pub enum PersistenceMode {
    Mounted,
    Replicated,
}

impl Default for PersistenceMode {
    fn default() -> Self {
        Self::Mounted
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum PersistenceScope {
    Project,
    Environment,
}

impl Default for PersistenceScope {
    fn default() -> Self {
        Self::Project
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct RcloneVolumeConfig {
    pub name: String,
    pub remote_path: String,
    pub mount_path: String,
    #[serde(default)]
    pub sync: SyncDirection,
    #[serde(default = "default_sync_interval")]
    pub interval: u64,
    #[serde(default = "default_sync_priority")]
    pub priority: u8,
    #[serde(default)]
    pub exclude: Vec<String>,
    #[serde(default)]
    pub read_only: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct MultiScopeVolumes {
    #[serde(default)]
    pub global_refs: Vec<String>,
    #[serde(default)]
    pub project_refs: Vec<String>,
    #[serde(default)]
    pub environment: Vec<RcloneVolumeConfig>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct PersistenceSpec {
    #[serde(default)]
    pub enabled: bool,
    #[serde(default)]
    pub mode: PersistenceMode,
    #[serde(default)]
    pub scope: PersistenceScope,
    #[serde(default)]
    pub mounts: Vec<PersistenceMount>,

    /// Named remote configurations. Keys are remote names referenced by sync paths.
    #[serde(default)]
    pub remotes: std::collections::BTreeMap<String, RemoteConfig>,

    /// Default remote name used when sync paths don't specify one.
    /// Must reference a key in `remotes`.
    #[serde(default)]
    pub default_remote: Option<String>,

    /// Default operation settings inherited by sync paths.
    #[serde(default)]
    pub defaults: Option<SyncDefaults>,

    #[serde(default)]
    pub sync: Vec<SyncPath>,
    #[serde(default)]
    pub multi_scope_volumes: Option<MultiScopeVolumes>,
}

impl Default for PersistenceSpec {
    fn default() -> Self {
        Self {
            enabled: false,
            mode: PersistenceMode::default(),
            scope: PersistenceScope::default(),
            mounts: Vec::new(),
            remotes: std::collections::BTreeMap::new(),
            default_remote: None,
            defaults: None,
            sync: Vec::new(),
            multi_scope_volumes: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct TemplateRef {
    pub name: String,
    pub path: String,
    #[serde(default)]
    pub overlays: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ImageRef {
    pub name: String,
    #[serde(default)]
    pub digest: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct FeatureRef {
    pub id: String,
    #[serde(default)]
    pub options: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum MountType {
    Bind,
    Volume,
    Tmpfs,
    Symlink,
}

impl Default for MountType {
    fn default() -> Self {
        Self::Volume
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct MountSpec {
    pub source: String,
    pub target: String,
    #[serde(default)]
    pub r#type: MountType,
    #[serde(default)]
    pub extra: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct SecretMount {
    pub provider: String,
    pub key: String,
    pub mount_path: String,
    #[serde(default = "default_read_only")]
    pub read_only: bool,
}

fn default_read_only() -> bool {
    true
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct LifecycleHooks {
    #[serde(default)]
    pub post_create: Vec<String>,
    #[serde(default)]
    pub post_start: Vec<String>,
}

impl Default for LifecycleHooks {
    fn default() -> Self {
        Self {
            post_create: Vec::new(),
            post_start: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct VSCodeExtensions {
    #[serde(default)]
    pub recommended: Vec<String>,
    #[serde(default)]
    pub optional: Vec<String>,
}

impl Default for VSCodeExtensions {
    fn default() -> Self {
        Self {
            recommended: Vec::new(),
            optional: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct VSCodeSettings {
    #[serde(default)]
    pub values: std::collections::BTreeMap<String, Value>,
}

impl Default for VSCodeSettings {
    fn default() -> Self {
        Self {
            values: std::collections::BTreeMap::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct VSCodeCustomization {
    #[serde(default)]
    pub extensions: VSCodeExtensions,
    #[serde(default)]
    pub settings: VSCodeSettings,
}

impl Default for VSCodeCustomization {
    fn default() -> Self {
        Self {
            extensions: VSCodeExtensions::default(),
            settings: VSCodeSettings::default(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct DevcontainerSpec {
    /// Template reference (optional, vestigial from old system)
    #[serde(default)]
    pub template: Option<TemplateRef>,
    pub image: ImageRef,
    #[serde(default)]
    pub features: Vec<FeatureRef>,
    #[serde(default = "default_user")]
    pub user: String,
    #[serde(default = "default_workspace_folder")]
    pub workspace_folder: String,
    #[serde(default)]
    pub workspace_mount: Option<String>,
    #[serde(default)]
    pub mounts: Vec<MountSpec>,
    #[serde(default)]
    pub run_args: Vec<String>,
    #[serde(default)]
    pub env: std::collections::BTreeMap<String, String>,
    #[serde(default)]
    pub lifecycle: LifecycleHooks,
    #[serde(default)]
    pub customizations: VSCodeCustomization,
}

fn default_user() -> String {
    "vscode".to_string()
}

fn default_workspace_folder() -> String {
    "/workspace".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceMetadata {
    pub org: String,
    pub project: String,
    #[serde(default = "default_environment")]
    pub environment: String,
    #[serde(default)]
    pub description: Option<String>,
    #[serde(default)]
    pub tags: Vec<String>,
}

fn default_environment() -> String {
    "dev".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct WorkspaceSpec {
    pub name: String,
    #[serde(default = "default_version")]
    pub version: String,
    pub metadata: WorkspaceMetadata,
    pub devcontainer: DevcontainerSpec,
    #[serde(default)]
    pub mounts: Vec<MountSpec>,
    #[serde(default)]
    pub secrets: Vec<SecretMount>,
    #[serde(default)]
    pub networking: NetworkingSpec,
    #[serde(default)]
    pub persistence: PersistenceSpec,
    #[serde(default)]
    pub generated_at: Option<DateTime<Utc>>,
}

fn default_version() -> String {
    "1.0.0".to_string()
}
