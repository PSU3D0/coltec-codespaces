//! CLI argument parsing for coltec-daemon.

use clap::{Parser, ValueEnum};
use std::path::PathBuf;

/// Log output format.
#[derive(Debug, Clone, Copy, Default, ValueEnum)]
pub enum LogFormat {
    /// Human-readable text output
    #[default]
    Text,
    /// JSON lines output (for structured logging)
    Json,
}

/// Sync daemon for Coltec devcontainer workspaces.
///
/// Reads workspace-spec.yaml and orchestrates file sync via rclone.
/// Supports bidirectional sync, push-only, and pull-only modes.
#[derive(Parser, Debug)]
#[command(name = "coltec-daemon")]
#[command(version)]
#[command(about = "Sync daemon for Coltec devcontainer workspaces")]
pub struct Args {
    /// Path to workspace-spec.yaml
    #[arg(
        short,
        long,
        default_value = "/workspace/.devcontainer/workspace-spec.yaml"
    )]
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
    #[arg(long = "only", value_name = "NAME")]
    pub only_paths: Vec<String>,

    /// Log format
    #[arg(long, default_value = "text", env = "COLTEC_LOG_FORMAT")]
    pub log_format: LogFormat,

    /// Log level (trace, debug, info, warn, error)
    #[arg(long, default_value = "info", env = "COLTEC_LOG_LEVEL")]
    pub log_level: String,
}

impl Args {
    /// Returns true if daemon should run in continuous mode (not --once, not --validate-only).
    #[allow(dead_code)]
    pub fn is_continuous(&self) -> bool {
        !self.once && !self.validate_only
    }

    /// Returns true if any filtering by path name is requested.
    #[allow(dead_code)]
    pub fn has_path_filter(&self) -> bool {
        !self.only_paths.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_config_path() {
        let args = Args::parse_from(["coltec-daemon"]);
        assert_eq!(
            args.config,
            PathBuf::from("/workspace/.devcontainer/workspace-spec.yaml")
        );
    }

    #[test]
    fn test_once_mode() {
        let args = Args::parse_from(["coltec-daemon", "--once"]);
        assert!(args.once);
        assert!(!args.is_continuous());
    }

    #[test]
    fn test_validate_only() {
        let args = Args::parse_from(["coltec-daemon", "--validate-only"]);
        assert!(args.validate_only);
        assert!(!args.is_continuous());
    }

    #[test]
    fn test_dry_run() {
        let args = Args::parse_from(["coltec-daemon", "--dry-run"]);
        assert!(args.dry_run);
    }

    #[test]
    fn test_interval_override() {
        let args = Args::parse_from(["coltec-daemon", "--interval", "60"]);
        assert_eq!(args.interval, Some(60));
    }

    #[test]
    fn test_only_paths() {
        let args = Args::parse_from(["coltec-daemon", "--only", "workspace", "--only", "config"]);
        assert_eq!(args.only_paths, vec!["workspace", "config"]);
        assert!(args.has_path_filter());
    }

    #[test]
    fn test_log_format_json() {
        let args = Args::parse_from(["coltec-daemon", "--log-format", "json"]);
        assert!(matches!(args.log_format, LogFormat::Json));
    }

    #[test]
    fn test_custom_config_path() {
        let args = Args::parse_from(["coltec-daemon", "--config", "/tmp/spec.yaml"]);
        assert_eq!(args.config, PathBuf::from("/tmp/spec.yaml"));
    }
}
