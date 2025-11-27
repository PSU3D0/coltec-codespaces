"""Storage mapping and validation utilities for persistence.

Supports:
- JuiceFS-backed persistence (legacy mounted mode)
- rclone-backed replicated persistence (V2)
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Literal, Tuple

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .spec import (
    WorkspaceSpec,
    RcloneConfig,
    RcloneVolumeConfig,
    StorageConfig,
    MultiScopeVolumeSpec,
)


class MountMapping(BaseModel):
    """Mapping of a logical mount to a bucket subpath."""

    name: str
    target: str
    source: str
    type: str = "symlink"  # symlink or bind
    bucket_path: Optional[str] = None
    juicefs_uuid: Optional[str] = None

    @field_validator("target")
    @classmethod
    def _absolute_target(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("mount target must be an absolute path")
        return value

    @field_validator("type")
    @classmethod
    def _allowed_type(cls, value: str) -> str:
        if value not in {"symlink", "bind"}:
            raise ValueError("mount type must be 'symlink' or 'bind'")
        return value


class WorkspaceStorageEntry(BaseModel):
    """Per-workspace storage configuration."""

    org: str
    project: str
    env: str
    scope: Optional[Literal["project", "environment"]] = None
    mounts: List[MountMapping] = Field(default_factory=list)


class StorageMapping(BaseModel):
    """Top-level storage mapping configuration."""

    version: int = 1
    bucket: str
    filesystem: str
    root_prefix: str = "workspaces"
    metadata_dsn_env: str
    s3_endpoint_env: Optional[str] = None
    default_scope: Literal["project", "environment"] = "project"
    workspaces: Dict[str, WorkspaceStorageEntry] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _populate_bucket_paths(self) -> "StorageMapping":
        """Derive bucket_path for mounts if not provided."""
        for key, entry in self.workspaces.items():
            scope = entry.scope or self.default_scope
            _ = scope  # scope retained for future use; currently derived path is same regardless
            for m in entry.mounts:
                if not m.bucket_path:
                    m.bucket_path = "/".join(
                        [
                            self.root_prefix.strip("/"),
                            entry.org,
                            entry.project,
                            entry.env,
                            m.source,
                        ]
                    )
        return self


def load_storage_mapping(path: Path) -> StorageMapping:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return StorageMapping.model_validate(data)


def validate_mounts_match_spec(
    workspace_path: Path,
    mapping_entry: WorkspaceStorageEntry,
) -> None:
    spec_path = workspace_path / ".devcontainer" / "workspace-spec.yaml"
    if not spec_path.exists():
        raise RuntimeError(f"Spec file not found at {spec_path}")
    spec_data = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    spec = WorkspaceSpec.model_validate(spec_data)
    spec_mounts = {
        (m.name, m.source, m.target, m.type) for m in spec.persistence.mounts
    }
    map_mounts = {(m.name, m.source, m.target, m.type) for m in mapping_entry.mounts}
    if spec_mounts != map_mounts:
        missing = spec_mounts - map_mounts
        extra = map_mounts - spec_mounts
        details = []
        if missing:
            details.append(f"missing in mapping: {missing}")
        if extra:
            details.append(f"extra in mapping: {extra}")
        raise RuntimeError(
            "Mounts mismatch between mapping and spec: " + "; ".join(details)
        )

    # Optional: Validate UUID if present (consistency check)
    # If multiple mounts share the same scope/fs, they should probably have the same UUID?
    # Not strictly required by schema but good for sanity.
    uuids = {m.juicefs_uuid for m in mapping_entry.mounts if m.juicefs_uuid}
    if len(uuids) > 1:
        print(
            f"Warning: Multiple JuiceFS UUIDs found for workspace {mapping_entry.project}/{mapping_entry.env}: {uuids}"
        )


@dataclass
class JuiceFSCommands:
    """Thin wrapper to allow mocking in tests."""

    def run(
        self, args: List[str], env: Optional[Dict[str, str]] = None
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            args, check=False, capture_output=True, text=True, env=env
        )


def ensure_env_vars_present(env: Dict[str, str], required: List[str]) -> None:
    missing = [k for k in required if not env.get(k)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")


def juicefs_status(commands: JuiceFSCommands, dsn: str, env: Dict[str, str]) -> bool:
    result = commands.run(["juicefs", "status", dsn], env=env)
    return result.returncode == 0


def juicefs_get_uuid(
    commands: JuiceFSCommands, dsn: str, env: Dict[str, str]
) -> Optional[str]:
    result = commands.run(["juicefs", "status", dsn], env=env)
    if result.returncode == 0:
        import json

        try:
            data = json.loads(result.stdout)
            return data.get("Setting", {}).get("UUID")
        except Exception:
            pass
    return None


def juicefs_format(
    commands: JuiceFSCommands,
    dsn: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    endpoint: Optional[str],
    filesystem: str,
    env: Dict[str, str],
) -> Optional[str]:
    # If bucket is already a URL, use it. Otherwise prepent endpoint if available.
    if bucket.startswith("http://") or bucket.startswith("https://"):
        bucket_arg = bucket
    elif endpoint:
        # Ensure endpoint doesn't end with slash and bucket doesn't start
        e = endpoint.rstrip("/")
        b = bucket.lstrip("/")
        bucket_arg = f"{e}/{b}"
    else:
        bucket_arg = bucket

    args = [
        "juicefs",
        "format",
        "--storage",
        "s3",
        "--bucket",
        bucket_arg,
        "--access-key",
        access_key,
        "--secret-key",
        secret_key,
        dsn,
        filesystem,
    ]
    result = commands.run(args, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"juicefs format failed: {result.stderr.strip()}")

    # Try to capture UUID
    try:
        status_res = commands.run(["juicefs", "status", dsn], env=env)
        if status_res.returncode == 0:
            import json

            data = json.loads(status_res.stdout)
            return data.get("Setting", {}).get("UUID")
    except Exception:
        pass
    return None


def docker_plugin_is_installed(commands: JuiceFSCommands) -> bool:
    """Check if the juicedata/juicefs plugin is installed and enabled."""
    # docker plugin ls --format '{{.Name}}:{{.Enabled}}'
    res = commands.run(["docker", "plugin", "ls", "--format", "{{.Name}}:{{.Enabled}}"])
    if res.returncode != 0:
        return False

    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        try:
            name, enabled = line.rsplit(":", 1)
        except ValueError:
            continue
        # Check if it matches generic or specific version
        # Use 'in' to catch both 'juicedata/juicefs' and 'juicedata/juicefs:1.2.1'
        # Note: we specifically look for 'juicedata/juicefs' prefix.
        if name.startswith("juicedata/juicefs") and enabled.strip() == "true":
            return True
    return False


def docker_volume_exists(commands: JuiceFSCommands, volume_name: str) -> bool:
    res = commands.run(["docker", "volume", "inspect", volume_name])
    return res.returncode == 0


def docker_volume_create(
    commands: JuiceFSCommands,
    volume_name: str,
    fs_name: str,
    dsn: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    subdir: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    endpoint: Optional[str] = None,
    **kwargs,
) -> None:
    """Create a Docker volume using the JuiceFS driver."""

    # Use specific version tag for driver if that is what is installed
    # But docker volume create -d juicedata/juicefs should work if alias is set,
    # or we must use juicedata/juicefs:1.2.1 explicit?
    # The `docker plugin ls` showed NAME=juicedata/juicefs:1.2.1
    # Usually you refer to it by NAME.

    driver_name = "juicedata/juicefs:1.2.1"

    # Handle endpoint for bucket argument if needed
    bucket_arg = bucket
    if endpoint:
        if not (bucket.startswith("http://") or bucket.startswith("https://")):
            e = endpoint.rstrip("/")
            b = bucket.lstrip("/")
            bucket_arg = f"{e}/{b}"

    cmd = [
        "docker",
        "volume",
        "create",
        "-d",
        driver_name,
        "-o",
        f"name={fs_name}",
        "-o",
        f"metaurl={dsn}",
        "-o",
        f"bucket={bucket_arg}",
        "-o",
        f"access-key={access_key}",
        "-o",
        f"secret-key={secret_key}",
    ]

    if subdir:
        # The plugin uses 'subdir' option to mount a sub-directory
        cmd.extend(["-o", f"subdir={subdir}"])

    # Add any extra options
    for k, v in kwargs.items():
        cmd.extend(["-o", f"{k}={v}"])

    cmd.append(volume_name)

    # We generally don't need special env vars for the docker CLI command itself,
    # but the plugin might read them if passed via -o env=...
    # For now, keys are passed via options.

    res = commands.run(cmd, env=env)
    if res.returncode != 0:
        raise RuntimeError(
            f"Failed to create docker volume {volume_name}: {res.stderr.strip()}"
        )


def generate_storage_mapping(
    repo_root: Path,
    manifest_path: Path,
    bucket: str,
    filesystem: str,
    metadata_dsn_env: str = "JUICEFS_DSN",
    s3_endpoint_env: str = "JUICEFS_S3_ENDPOINT",
    root_prefix: str = "workspaces",
) -> StorageMapping:
    from .manifest import load_manifest

    manifest_data = load_manifest(manifest_path)
    mapping = StorageMapping(
        bucket=bucket,
        filesystem=filesystem,
        root_prefix=root_prefix,
        metadata_dsn_env=metadata_dsn_env,
        s3_endpoint_env=s3_endpoint_env,
    )

    for org_slug, org_data in (manifest_data.get("manifest") or {}).items():
        project_dir = org_data.get("project_dir") or org_slug
        for project_slug, project_data in (org_data.get("projects") or {}).items():
            for env in project_data.get("environments") or []:
                env_name = env.get("name")
                rel_path = (
                    env.get("workspace_path") or f"codespaces/{project_dir}/{env_name}"
                )
                ws_path = (repo_root / rel_path).resolve()

                spec_path = ws_path / ".devcontainer" / "workspace-spec.yaml"
                if not spec_path.exists():
                    print(
                        f"Warning: No spec found for {env_name} at {spec_path}, skipping storage generation."
                    )
                    continue

                try:
                    spec_data_yaml = yaml.safe_load(
                        spec_path.read_text(encoding="utf-8")
                    )
                    spec = WorkspaceSpec.model_validate(spec_data_yaml)
                except Exception as e:
                    print(f"Warning: Failed to load spec for {env_name}: {e}")
                    continue

                if not spec.persistence.enabled:
                    continue

                mounts = []
                for m in spec.persistence.mounts:
                    mounts.append(
                        MountMapping(
                            name=m.name,
                            target=m.target,
                            source=m.source,
                            type=m.type,
                        )
                    )

                # We trust spec validation for scope values
                scope_val = spec.persistence.scope
                # type ignore because Pydantic 2 str vs Literal strictness

                entry = WorkspaceStorageEntry(
                    org=org_slug,
                    project=project_slug,
                    env=env_name,
                    scope=scope_val,  # type: ignore
                    mounts=mounts,
                )

                key = f"{org_slug}/{project_slug}/{env_name}"
                mapping.workspaces[key] = entry

    # Re-validate to trigger path population logic
    mapping = StorageMapping.model_validate(mapping.model_dump())
    return mapping


def resolve_rclone_env(
    config: "RcloneConfig",
    env_source: Dict[str, str],
) -> Dict[str, str]:
    """
    Resolves rclone configuration options into environment variables.
    Handles ${ENV_VAR} placeholders.

    Args:
        config: The RcloneConfig object from the spec.
        env_source: Dictionary containing source environment variables (e.g. os.environ).

    Returns:
        A dictionary of environment variables formatted for rclone (RCLONE_CONFIG_...).
    """
    result = {}
    remote = config.remote_name.upper().replace("-", "_")

    # Set type (backend)
    result[f"RCLONE_CONFIG_{remote}_TYPE"] = config.type

    # Process options
    for key, value in config.options.items():
        resolved_value = value
        # Check for ${VAR} syntax
        if value.startswith("${") and value.endswith("}"):
            var_name = value[2:-1]
            resolved_value = env_source.get(var_name, "")
            if not resolved_value:
                print(f"Warning: Referenced env var {var_name} is missing or empty.")

        # Format key: provider -> PROVIDER
        option_key = key.upper().replace("-", "_")
        result[f"RCLONE_CONFIG_{remote}_{option_key}"] = resolved_value

    return result


# ============================================================================
# rclone-based replicated persistence (V2)
# ============================================================================


@dataclass
class RcloneCommands:
    """Wrapper for rclone operations."""

    def run(
        self, args: List[str], env: Optional[Dict[str, str]] = None
    ) -> subprocess.CompletedProcess:
        """Execute an rclone command."""
        return subprocess.run(
            args, check=False, capture_output=True, text=True, env=env
        )

    def sync(
        self,
        local: Path,
        remote: str,
        strategy: Literal["push", "pull", "bidirectional"],
        dry_run: bool = False,
        exclude: Optional[List[str]] = None,
        transfers: Optional[int] = None,
        bwlimit: Optional[str] = None,
        timeout: Optional[int] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> subprocess.CompletedProcess:
        """Execute rclone sync or bisync based on strategy.

        Args:
            local: Local path
            remote: Remote path (e.g., "r2coltec:bucket/path")
            strategy: "push" (local->remote), "pull" (remote->local), or "bidirectional"
            dry_run: Run in dry-run mode
            exclude: List of exclude patterns
            transfers: Number of parallel transfers
            bwlimit: Bandwidth limit (e.g., "10M")
            timeout: Timeout in seconds
            env: Environment variables

        Returns:
            CompletedProcess with stdout, stderr, returncode
        """
        if strategy == "bidirectional":
            # Use bisync for bidirectional sync
            cmd = ["rclone", "bisync", str(local), remote]
            cmd.extend(["--check-access", "--max-delete", "10"])
        else:
            # Use sync for one-way sync
            direction = "push" if strategy == "push" else "pull"
            src = str(local) if direction == "push" else remote
            dst = remote if direction == "push" else str(local)
            cmd = ["rclone", "sync", src, dst, "--fast-list"]

        # Common options
        if dry_run:
            cmd.append("--dry-run")
        if transfers:
            cmd.extend(["--transfers", str(transfers)])
        if bwlimit:
            cmd.extend(["--bwlimit", bwlimit])
        if timeout:
            cmd.extend(["--timeout", f"{timeout}s"])

        for pattern in exclude or []:
            cmd.extend(["--exclude", pattern])

        return self.run(cmd, env=env)

    def bisync(
        self,
        local: Path,
        remote: str,
        resync: bool = False,
        dry_run: bool = False,
        check_access: bool = True,
        max_delete: int = 10,
        exclude: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> subprocess.CompletedProcess:
        """Execute rclone bisync for bidirectional sync.

        Args:
            local: Local path
            remote: Remote path (e.g., "r2coltec:bucket/path")
            resync: Force resync (first time setup)
            dry_run: Run in dry-run mode
            check_access: Check access to both paths
            max_delete: Maximum number of deletes allowed
            exclude: List of exclude patterns
            env: Environment variables

        Returns:
            CompletedProcess with stdout, stderr, returncode
        """
        cmd = ["rclone", "bisync", str(local), remote]

        if resync:
            cmd.append("--resync")
        if dry_run:
            cmd.append("--dry-run")
        if check_access:
            cmd.append("--check-access")
        if max_delete:
            cmd.extend(["--max-delete", str(max_delete)])

        for pattern in exclude or []:
            cmd.extend(["--exclude", pattern])

        return self.run(cmd, env=env)


def ensure_rclone_configured(env: Dict[str, str]) -> None:
    """Ensure rclone is installed and configured.

    Args:
        env: Environment variables (must contain rclone credentials)

    Raises:
        RuntimeError: If rclone is not installed or configuration fails
    """
    # Check if rclone is installed
    result = subprocess.run(
        ["which", "rclone"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(
            "rclone not found. Please install rclone or ensure it's in the base image."
        )

    # Verify required environment variables
    required = [
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
        "JUICEFS_S3_ENDPOINT",
        "JUICEFS_BUCKET",
    ]
    ensure_env_vars_present(env, required)


def create_replicated_volumes(
    workspace_spec: "WorkspaceSpec",
    bucket: str,
    org: str,
    project: str,
    env_name: str,
    commands: Optional[JuiceFSCommands] = None,
) -> List[str]:
    """Create Docker volumes for replicated persistence mode.

    Args:
        workspace_spec: WorkspaceSpec with persistence.mode="replicated"
        bucket: Bucket name
        org: Organization name
        project: Project name
        env_name: Environment name
        commands: JuiceFSCommands instance (reused for docker operations)

    Returns:
        List of docker run mount arguments

    Raises:
        RuntimeError: If volume creation fails
    """
    if not workspace_spec.persistence.enabled:
        return []

    if workspace_spec.persistence.mode != "replicated":
        return []

    mount_args = []
    commands = commands or JuiceFSCommands()

    for volume in workspace_spec.persistence.volumes:
        # Format remote path with placeholders
        remote_path = volume.remote_path.format(org=org, project=project, env=env_name)

        # Create Docker volume name
        # For Phase 1, we only support environment-scoped volumes
        volume_name = f"e-{workspace_spec.name}-{volume.name}"

        # Check if volume exists
        if not docker_volume_exists(commands, volume_name):
            print(f"[storage] Creating Docker volume: {volume_name}")
            result = commands.run(["docker", "volume", "create", volume_name])
            if result.returncode != 0:
                raise RuntimeError(
                    f"Failed to create Docker volume {volume_name}: {result.stderr.strip()}"
                )

        # Add mount argument
        readonly = ",readonly" if volume.read_only else ""
        mount_args.extend(
            [
                "--mount",
                f"type=volume,source={volume_name},target={volume.mount_path}{readonly}",
            ]
        )

    return mount_args


# ============================================================================
# Global Storage Config (V2 multi-scope)
# ============================================================================


def load_storage_config(path: Path) -> StorageConfig:
    """Load global storage configuration from YAML file.

    Args:
        path: Path to storage-config.yaml

    Returns:
        Validated StorageConfig instance

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValidationError: If config is invalid
    """
    if not path.exists():
        raise FileNotFoundError(f"Storage config not found at {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return StorageConfig.model_validate(data)


def find_storage_config(start_path: Path) -> Optional[Path]:
    """Find storage-config.yaml by walking up from start_path.

    Args:
        start_path: Starting directory to search from

    Returns:
        Path to storage-config.yaml if found, None otherwise
    """
    current = start_path.resolve()
    while current != current.parent:
        config_path = current / "storage-config.yaml"
        if config_path.exists():
            return config_path
        current = current.parent
    return None


def get_xdg_state_dir(workspace_name: str) -> Path:
    """Get XDG-compliant state directory for a workspace.

    Returns path like: ~/.local/state/coltec-persistence/{workspace_name}/

    Args:
        workspace_name: Name of the workspace

    Returns:
        Path to state directory (created if it doesn't exist)
    """
    xdg_state = os.environ.get("XDG_STATE_HOME", "")
    if not xdg_state:
        xdg_state = str(Path.home() / ".local" / "state")

    state_dir = Path(xdg_state) / "coltec-persistence" / workspace_name
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def get_bisync_state_dir(workspace_name: str) -> Path:
    """Get bisync state directory for a workspace.

    Args:
        workspace_name: Name of the workspace

    Returns:
        Path to bisync state directory (created if it doesn't exist)
    """
    state_dir = get_xdg_state_dir(workspace_name) / "bisync-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def get_sync_log_path(workspace_name: str) -> Path:
    """Get sync daemon log file path.

    Args:
        workspace_name: Name of the workspace

    Returns:
        Path to sync log file
    """
    log_dir = get_xdg_state_dir(workspace_name) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / "sync-daemon.log"


def resolve_all_volumes(
    workspace_spec: WorkspaceSpec,
    storage_config: Optional[StorageConfig] = None,
) -> Tuple[List[RcloneVolumeConfig], List[RcloneVolumeConfig], List[RcloneVolumeConfig]]:
    """Resolve all volumes for a workspace from spec and global config.

    Args:
        workspace_spec: The workspace specification
        storage_config: Global storage configuration (optional)

    Returns:
        Tuple of (global_volumes, project_volumes, environment_volumes)

    Raises:
        ValueError: If a referenced volume is not found in storage_config
    """
    if workspace_spec.persistence.mode != "replicated":
        return [], [], []

    vol_refs = workspace_spec.persistence.get_all_volume_refs()
    project = workspace_spec.metadata.project

    global_volumes: List[RcloneVolumeConfig] = []
    project_volumes: List[RcloneVolumeConfig] = []
    environment_volumes: List[RcloneVolumeConfig] = list(vol_refs.environment)

    if storage_config:
        # Resolve global volume references
        for name in vol_refs.global_refs:
            vol = storage_config.resolve_volume(name, "global")
            if not vol:
                raise ValueError(
                    f"Global volume '{name}' not found in storage-config.yaml"
                )
            global_volumes.append(vol)

        # Resolve project volume references
        for name in vol_refs.project_refs:
            vol = storage_config.resolve_volume(name, "project", project)
            if not vol:
                raise ValueError(
                    f"Project volume '{name}' not found in storage-config.yaml "
                    f"for project '{project}'"
                )
            project_volumes.append(vol)

    return global_volumes, project_volumes, environment_volumes


def create_multi_scope_volumes(
    workspace_spec: WorkspaceSpec,
    storage_config: Optional[StorageConfig],
    org: str,
    project: str,
    env_name: str,
    bucket: Optional[str] = None,
    commands: Optional[JuiceFSCommands] = None,
    rclone_env: Optional[Dict[str, str]] = None,
) -> List[str]:
    """Create Docker volumes for all scopes (global, project, environment).

    Global and project volumes are seeded from R2 on first creation.
    Environment volumes are created empty (sync daemon handles them).

    Args:
        workspace_spec: WorkspaceSpec with persistence.mode="replicated"
        storage_config: Global storage configuration
        org: Organization name
        project: Project name
        env_name: Environment name
        bucket: R2 bucket name (defaults from storage_config or env)
        commands: Command runner (for mocking)
        rclone_env: Pre-resolved rclone environment variables (RCLONE_CONFIG_*)

    Returns:
        List of docker run mount arguments

    Raises:
        RuntimeError: If volume creation fails
    """
    if not workspace_spec.persistence.enabled:
        return []

    if workspace_spec.persistence.mode != "replicated":
        return []

    mount_args = []
    commands = commands or JuiceFSCommands()

    global_vols, project_vols, env_vols = resolve_all_volumes(
        workspace_spec, storage_config
    )

    # Get rclone config from workspace spec or storage config
    remote_name = "r2coltec"  # Default
    if workspace_spec.persistence.rclone_config:
        remote_name = workspace_spec.persistence.rclone_config.remote_name
    elif storage_config and storage_config.rclone:
        remote_name = storage_config.rclone.remote_name

    # Get bucket from param or env
    if not bucket:
        bucket = os.environ.get("RCLONE_BUCKET", "coltec-codespaces-data")

    # Global volumes: use ensure_global_volume for initial R2 pull
    for vol in global_vols:
        result = ensure_global_volume(
            volume=vol,
            org=org,
            remote_name=remote_name,
            bucket=bucket,
            commands=commands,
            rclone_env=rclone_env,
        )
        mount_args.extend(["--mount", result["mount_arg"]])

    # Project volumes: use ensure_project_volume for initial bisync
    for vol in project_vols:
        result = ensure_project_volume(
            volume=vol,
            project=project,
            remote_name=remote_name,
            bucket=bucket,
            commands=commands,
            rclone_env=rclone_env,
        )
        mount_args.extend(["--mount", result["mount_arg"]])

    # Environment volumes: simple creation (sync daemon handles ongoing sync)
    for vol in env_vols:
        vol_name = f"e-{workspace_spec.name}-{vol.name}"
        if not docker_volume_exists(commands, vol_name):
            print(f"[storage] Creating environment volume: {vol_name}")
            result = commands.run(["docker", "volume", "create", vol_name])
            if result.returncode != 0:
                raise RuntimeError(
                    f"Failed to create Docker volume {vol_name}: {result.stderr.strip()}"
                )

        readonly = ",readonly" if vol.read_only else ""
        mount_args.extend([
            "--mount",
            f"type=volume,source={vol_name},target={vol.mount_path}{readonly}",
        ])

    return mount_args


def is_volume_initialized(
    commands: JuiceFSCommands,
    volume_name: str,
) -> bool:
    """Check if a Docker volume has been initialized (seeded from R2).

    Uses Docker volume labels to track initialization state.

    Args:
        commands: Command runner
        volume_name: Name of the Docker volume

    Returns:
        True if volume has been initialized, False otherwise
    """
    result = commands.run(
        ["docker", "volume", "inspect", volume_name, "--format", "{{.Labels}}"]
    )
    if result.returncode != 0:
        return False

    # Check for our initialization label
    return "coltec.initialized=true" in result.stdout


def mark_volume_initialized(
    commands: JuiceFSCommands,
    volume_name: str,
) -> None:
    """Mark a Docker volume as initialized.

    Note: Docker doesn't support adding labels to existing volumes,
    so we store the state in a marker file inside the volume instead.

    Args:
        commands: Command runner
        volume_name: Name of the Docker volume
    """
    # Use a temporary container to write the marker file
    result = commands.run([
        "docker", "run", "--rm",
        "-v", f"{volume_name}:/data",
        "alpine:latest",
        "sh", "-c", "echo 'initialized' > /data/.coltec-initialized"
    ])
    if result.returncode != 0:
        print(f"[storage] Warning: Failed to mark volume {volume_name} as initialized")


def _check_volume_marker(
    commands: JuiceFSCommands,
    volume_name: str,
) -> bool:
    """Check if volume has initialization marker file."""
    result = commands.run([
        "docker", "run", "--rm",
        "-v", f"{volume_name}:/data",
        "alpine:latest",
        "test", "-f", "/data/.coltec-initialized"
    ])
    return result.returncode == 0


def ensure_global_volume(
    volume: "RcloneVolumeConfig",
    org: str,
    remote_name: str,
    bucket: str,
    commands: Optional[JuiceFSCommands] = None,
    rclone_commands: Optional[RcloneCommands] = None,
    rclone_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Ensure a global (org-wide) volume exists and is populated.

    Global volumes are:
    - Named: g-{org}-{volume_name}
    - Read-only at runtime
    - Pull-only sync (R2 -> local)
    - Seeded on first creation

    Args:
        volume: Volume configuration
        org: Organization name
        remote_name: rclone remote name (e.g., "r2coltec")
        bucket: R2 bucket name
        commands: Docker command runner
        rclone_commands: rclone command runner
        rclone_env: Pre-resolved rclone environment variables (RCLONE_CONFIG_*)

    Returns:
        Dict with volume_name, mount_path, read_only, mount_arg
    """
    commands = commands or JuiceFSCommands()
    rclone_commands = rclone_commands or RcloneCommands()

    volume_name = f"g-{org}-{volume.name}"
    remote_path = f"{remote_name}:{bucket}/{volume.remote_path}"

    # Create volume if it doesn't exist
    if not docker_volume_exists(commands, volume_name):
        print(f"[storage] Creating global volume: {volume_name}")
        result = commands.run(["docker", "volume", "create", volume_name])
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to create Docker volume {volume_name}: {result.stderr.strip()}"
            )

    # Perform initial pull if not yet initialized
    if not is_volume_initialized(commands, volume_name) and not _check_volume_marker(commands, volume_name):
        print(f"[storage] Seeding global volume {volume_name} from {remote_path}...")

        # Build rclone env vars for docker run
        # If rclone_env provided, use those; otherwise build from os.environ
        if rclone_env:
            env_args = []
            for k, v in rclone_env.items():
                env_args.extend(["-e", f"{k}={v}"])
        else:
            # Fallback: build from standard env vars
            remote_upper = remote_name.upper().replace('-', '_')
            env_args = [
                "-e", f"RCLONE_CONFIG_{remote_upper}_TYPE=s3",
                "-e", f"RCLONE_CONFIG_{remote_upper}_PROVIDER=Cloudflare",
                "-e", f"RCLONE_CONFIG_{remote_upper}_ACCESS_KEY_ID={os.environ.get('S3_ACCESS_KEY_ID', '')}",
                "-e", f"RCLONE_CONFIG_{remote_upper}_SECRET_ACCESS_KEY={os.environ.get('S3_SECRET_ACCESS_KEY', '')}",
                "-e", f"RCLONE_CONFIG_{remote_upper}_ENDPOINT={os.environ.get('JUICEFS_S3_ENDPOINT', '')}",
                "-e", f"RCLONE_CONFIG_{remote_upper}_REGION=auto",
            ]

        sync_cmd = [
            "docker", "run", "--rm",
            "-v", f"{volume_name}:/data",
            *env_args,
            "rclone/rclone:latest",
            "sync", remote_path, "/data", "--fast-list"
        ]

        result = commands.run(sync_cmd)
        if result.returncode != 0:
            print(f"[storage] Warning: Initial sync failed for {volume_name}: {result.stderr}")
        else:
            print(f"[storage] ✓ Global volume {volume_name} seeded successfully")
            mark_volume_initialized(commands, volume_name)

    # Build mount arg
    readonly = ",readonly" if volume.read_only else ""
    mount_arg = f"type=volume,source={volume_name},target={volume.mount_path}{readonly}"

    return {
        "volume_name": volume_name,
        "mount_path": volume.mount_path,
        "read_only": volume.read_only,
        "mount_arg": mount_arg,
    }


def ensure_project_volume(
    volume: "RcloneVolumeConfig",
    project: str,
    remote_name: str,
    bucket: str,
    commands: Optional[JuiceFSCommands] = None,
    rclone_commands: Optional[RcloneCommands] = None,
    rclone_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Ensure a project-wide shared volume exists and is populated.

    Project volumes are:
    - Named: p-{project}-{volume_name}
    - Writable (bidirectional sync)
    - Shared across all environments in the project
    - Seeded with initial bisync --resync on first creation

    Args:
        volume: Volume configuration
        project: Project name
        remote_name: rclone remote name (e.g., "r2coltec")
        bucket: R2 bucket name
        commands: Docker command runner
        rclone_commands: rclone command runner
        rclone_env: Pre-resolved rclone environment variables (RCLONE_CONFIG_*)

    Returns:
        Dict with volume_name, mount_path, read_only, mount_arg
    """
    commands = commands or JuiceFSCommands()
    rclone_commands = rclone_commands or RcloneCommands()

    volume_name = f"p-{project}-{volume.name}"
    remote_path = f"{remote_name}:{bucket}/{volume.remote_path}"

    # Create volume if it doesn't exist
    if not docker_volume_exists(commands, volume_name):
        print(f"[storage] Creating project volume: {volume_name}")
        result = commands.run(["docker", "volume", "create", volume_name])
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to create Docker volume {volume_name}: {result.stderr.strip()}"
            )

    # Perform initial bisync with --resync if not yet initialized
    if not is_volume_initialized(commands, volume_name) and not _check_volume_marker(commands, volume_name):
        print(f"[storage] Initializing project volume {volume_name} with bisync from {remote_path}...")

        # Build rclone env vars for docker run
        if rclone_env:
            env_args = []
            for k, v in rclone_env.items():
                env_args.extend(["-e", f"{k}={v}"])
        else:
            # Fallback: build from standard env vars
            remote_upper = remote_name.upper().replace('-', '_')
            env_args = [
                "-e", f"RCLONE_CONFIG_{remote_upper}_TYPE=s3",
                "-e", f"RCLONE_CONFIG_{remote_upper}_PROVIDER=Cloudflare",
                "-e", f"RCLONE_CONFIG_{remote_upper}_ACCESS_KEY_ID={os.environ.get('S3_ACCESS_KEY_ID', '')}",
                "-e", f"RCLONE_CONFIG_{remote_upper}_SECRET_ACCESS_KEY={os.environ.get('S3_SECRET_ACCESS_KEY', '')}",
                "-e", f"RCLONE_CONFIG_{remote_upper}_ENDPOINT={os.environ.get('JUICEFS_S3_ENDPOINT', '')}",
                "-e", f"RCLONE_CONFIG_{remote_upper}_REGION=auto",
            ]

        bisync_cmd = [
            "docker", "run", "--rm",
            "-v", f"{volume_name}:/data",
            *env_args,
            "rclone/rclone:latest",
            "bisync", "/data", remote_path,
            "--resync",
            "--fast-list",
        ]

        result = commands.run(bisync_cmd)
        if result.returncode != 0:
            print(f"[storage] Warning: Initial bisync failed for {volume_name}: {result.stderr}")
        else:
            print(f"[storage] ✓ Project volume {volume_name} initialized successfully")
            mark_volume_initialized(commands, volume_name)

    # Build mount arg (project volumes are typically writable)
    readonly = ",readonly" if volume.read_only else ""
    mount_arg = f"type=volume,source={volume_name},target={volume.mount_path}{readonly}"

    return {
        "volume_name": volume_name,
        "mount_path": volume.mount_path,
        "read_only": volume.read_only,
        "mount_arg": mount_arg,
    }
