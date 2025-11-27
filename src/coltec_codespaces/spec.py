"""Typed meta-spec for Coltec devcontainer workspaces.

The goal is to describe every workspace in one structured document and then
render devcontainer.json files, lifecycle scripts, and documentation from the
same source of truth.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

ALLOWED_MOUNT_TYPES = {"bind", "volume", "tmpfs"}


class NetworkingSpec(BaseModel):
    """Networking feature toggle and metadata."""

    enabled: bool = False
    hostname_prefix: str = "dev-"
    tags: List[str] = Field(default_factory=lambda: ["tag:devcontainer"])


class PersistenceMount(BaseModel):
    """Logical mapping of a JuiceFS subpath into the workspace (mounted mode)."""

    name: str
    target: str
    source: str
    type: str = "symlink"  # symlink or bind

    @field_validator("target")
    @classmethod
    def _absolute_target(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("persistence mount target must be an absolute path")
        # Note: we allow targets outside of /workspace (e.g. /home/vscode/.claude)
        return value

    @field_validator("type")
    @classmethod
    def _allowed_type(cls, value: str) -> str:
        if value not in {"symlink", "bind"}:
            raise ValueError("persistence mount type must be 'symlink' or 'bind'")
        return value


class RcloneConfig(BaseModel):
    """Configuration for rclone remote backend (replicated mode)."""

    remote_name: str = Field("r2coltec", description="Name of the rclone remote")
    type: str = Field("s3", description="rclone backend type")
    options: Dict[str, str] = Field(
        default_factory=dict,
        description="Key-value pairs for rclone config. Values can be ${ENV_VAR}.",
    )

    @field_validator("remote_name")
    @classmethod
    def _valid_remote_name(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("rclone remote_name cannot be empty")
        return value.strip()


class SyncPath(BaseModel):
    """Path to sync with rclone in replicated mode.

    This is a sync-only configuration - it does NOT create Docker volumes.
    The path must already exist in the container (via bind mount, volume, or
    being part of another mounted path like /home/vscode).
    """

    name: str = Field(..., description="Identifier for state tracking")
    path: str = Field(..., description="Local path to sync (must exist in container)")
    remote_path: str = Field(
        ...,
        description="Path in R2 bucket (supports {org}/{project}/{env} placeholders)",
    )
    direction: Literal["bidirectional", "pull-only", "push-only"] = Field(
        "bidirectional", description="Sync direction"
    )
    interval: int = Field(300, description="Sync interval in seconds")
    priority: int = Field(
        2, description="Priority level (1=critical, 2=important, 3=nice-to-have)"
    )
    exclude: List[str] = Field(
        default_factory=list, description="Exclude patterns for sync"
    )

    @field_validator("path")
    @classmethod
    def _absolute_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("path must be an absolute path")
        return value

    @field_validator("priority")
    @classmethod
    def _valid_priority(cls, value: int) -> int:
        if value not in {1, 2, 3}:
            raise ValueError(
                "priority must be 1 (critical), 2 (important), or 3 (nice-to-have)"
            )
        return value

    @field_validator("interval")
    @classmethod
    def _positive_interval(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("interval must be positive")
        return value


class RcloneVolumeConfig(BaseModel):
    """Volume configuration for replicated persistence mode.

    DEPRECATED: Use SyncPath for sync configuration and devcontainer.mounts
    for volume configuration separately. This model conflates the two concerns.
    """

    name: str = Field(..., description="Volume name (e.g., agent-context)")
    remote_path: str = Field(
        ...,
        description="Path in R2 bucket (supports {org}/{project}/{env} placeholders)",
    )
    mount_path: str = Field(..., description="Container mount path")
    sync: Literal["bidirectional", "pull-only", "push-only"] = Field(
        "bidirectional", description="Sync strategy"
    )
    interval: int = Field(300, description="Sync interval in seconds")
    priority: int = Field(
        2, description="Priority level (1=critical, 2=important, 3=nice-to-have)"
    )
    exclude: List[str] = Field(
        default_factory=list, description="Exclude patterns for sync"
    )
    read_only: bool = Field(False, description="Mount as read-only")

    @field_validator("mount_path")
    @classmethod
    def _absolute_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("mount_path must be an absolute path")
        return value

    @field_validator("priority")
    @classmethod
    def _valid_priority(cls, value: int) -> int:
        if value not in {1, 2, 3}:
            raise ValueError(
                "priority must be 1 (critical), 2 (important), or 3 (nice-to-have)"
            )
        return value

    @field_validator("interval")
    @classmethod
    def _positive_interval(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("interval must be positive")
        return value

    def to_sync_path(self) -> SyncPath:
        """Convert to SyncPath for compatibility."""
        return SyncPath(
            name=self.name,
            path=self.mount_path,
            remote_path=self.remote_path,
            direction=self.sync,
            interval=self.interval,
            priority=self.priority,
            exclude=self.exclude,
        )


class StorageConfig(BaseModel):
    """Global storage configuration (nexus/storage-config.yaml).

    Defines rclone remote configuration and shared volumes (global/project scope).
    Environment-scoped volumes are defined inline in workspace-spec.yaml.
    """

    version: int = Field(2, description="Schema version")
    rclone: RcloneConfig = Field(
        default_factory=RcloneConfig,
        description="rclone remote configuration",
    )
    global_volumes: List[RcloneVolumeConfig] = Field(
        default_factory=list,
        alias="global",
        description="Org-wide volumes (read-only, pull-only)",
    )
    projects: Dict[str, List[RcloneVolumeConfig]] = Field(
        default_factory=dict,
        description="Project-specific volumes, keyed by project slug",
    )
    exclude: List[str] = Field(
        default_factory=list,
        description="Default exclude patterns for all syncs",
    )

    model_config = {
        "populate_by_name": True,
    }

    @model_validator(mode="after")
    def _validate_global_volumes(self) -> "StorageConfig":
        """Ensure global volumes are pull-only and read-only."""
        for vol in self.global_volumes:
            if vol.sync != "pull-only":
                raise ValueError(
                    f"Global volume '{vol.name}' must have sync='pull-only' "
                    f"(got '{vol.sync}')"
                )
            if not vol.read_only:
                raise ValueError(
                    f"Global volume '{vol.name}' must have read_only=true"
                )
        return self

    def get_project_volumes(self, project: str) -> List[RcloneVolumeConfig]:
        """Get volumes for a specific project."""
        return self.projects.get(project, [])

    def resolve_volume(
        self, name: str, scope: str, project: Optional[str] = None
    ) -> Optional[RcloneVolumeConfig]:
        """Resolve a volume reference by name and scope."""
        if scope == "global":
            for vol in self.global_volumes:
                if vol.name == name:
                    return vol
        elif scope == "project" and project:
            for vol in self.get_project_volumes(project):
                if vol.name == name:
                    return vol
        return None


class MultiScopeVolumeSpec(BaseModel):
    """Volume references for multi-scope persistence.

    Global and project volumes are referenced by name (defined in storage-config.yaml).
    Environment volumes are defined inline.
    """

    global_refs: List[str] = Field(
        default_factory=list,
        alias="global",
        description="Global volume names to mount (must exist in storage-config.yaml)",
    )
    project_refs: List[str] = Field(
        default_factory=list,
        alias="project",
        description="Project volume names to mount (must exist in storage-config.yaml)",
    )
    environment: List[RcloneVolumeConfig] = Field(
        default_factory=list,
        description="Environment-scoped volumes (defined inline)",
    )

    model_config = {
        "populate_by_name": True,
    }


class PersistenceSpec(BaseModel):
    """Persistence feature toggle and mount definitions.

    Supports two modes:
    - mounted: JuiceFS Docker plugin (legacy)
    - replicated: rclone local volumes with background sync (V2)

    For replicated mode, volumes can be specified as:
    - Multi-scope dict with global/project/environment keys (V2)
    - Flat list of RcloneVolumeConfig (legacy V1, treated as environment scope)
    """

    enabled: bool = False
    mode: Literal["mounted", "replicated"] = Field(
        "mounted",
        description="Persistence mode: mounted (JuiceFS) or replicated (rclone)",
    )

    # Legacy fields (mounted mode)
    scope: str = Field("project", description="project or environment")
    mounts: List[PersistenceMount] = Field(default_factory=list)

    # New fields (replicated mode)
    rclone_config: Optional[RcloneConfig] = Field(
        None, description="rclone remote configuration (for replicated mode)"
    )

    # V3 sync paths (preferred) - pure sync config, no volume creation
    sync: List[SyncPath] = Field(
        default_factory=list,
        description="Paths to sync with rclone (does not create volumes)",
    )

    # V2 multi-scope volumes (deprecated - conflates sync and volume concerns)
    multi_scope_volumes: Optional[MultiScopeVolumeSpec] = Field(
        None,
        alias="volumes",
        description="DEPRECATED: Use 'sync' for sync config, devcontainer.mounts for volumes",
    )

    # V1 flat list (deprecated, auto-converted to environment scope)
    _legacy_volumes: List[RcloneVolumeConfig] = []

    model_config = {
        "populate_by_name": True,
    }

    @field_validator("scope")
    @classmethod
    def _scope_allowed(cls, value: str) -> str:
        if value not in {"project", "environment"}:
            raise ValueError("persistence.scope must be 'project' or 'environment'")
        return value

    @field_validator("mode")
    @classmethod
    def _valid_mode(cls, value: str) -> str:
        if value not in {"mounted", "replicated"}:
            raise ValueError("persistence.mode must be 'mounted' or 'replicated'")
        return value

    @field_validator("multi_scope_volumes", mode="before")
    @classmethod
    def _handle_volumes_format(cls, value: Any) -> Optional[MultiScopeVolumeSpec]:
        """Handle both V1 (list) and V2 (dict) volume formats."""
        if value is None:
            return None

        # V2 format: dict with global/project/environment keys
        if isinstance(value, dict):
            return MultiScopeVolumeSpec.model_validate(value)

        # V1 format: flat list of volume configs -> treat as environment scope
        if isinstance(value, list):
            return MultiScopeVolumeSpec(
                environment=[
                    RcloneVolumeConfig.model_validate(v) if isinstance(v, dict) else v
                    for v in value
                ]
            )

        # Already a MultiScopeVolumeSpec
        if isinstance(value, MultiScopeVolumeSpec):
            return value

        raise ValueError(
            "volumes must be either a dict with global/project/environment keys "
            "or a list of volume configs"
        )

    @model_validator(mode="after")
    def _validate_mode_fields(self) -> "PersistenceSpec":
        """Validate that appropriate fields are set for each mode."""
        if not self.enabled:
            return self

        if self.mode == "mounted":
            # Mounted mode requires mounts
            if not self.mounts:
                raise ValueError(
                    "mounted mode requires at least one mount in 'mounts' field"
                )
        elif self.mode == "replicated":
            # Replicated mode requires either sync paths OR multi_scope_volumes
            has_sync = bool(self.sync)
            has_volumes = False
            if self.multi_scope_volumes:
                vol_spec = self.multi_scope_volumes
                has_volumes = bool(
                    vol_spec.global_refs
                    or vol_spec.project_refs
                    or vol_spec.environment
                )

            if not has_sync and not has_volumes:
                raise ValueError(
                    "replicated mode requires at least one entry in 'sync' "
                    "or 'volumes' field"
                )

        return self

    @property
    def volumes(self) -> List[RcloneVolumeConfig]:
        """Return all environment-scoped volumes (for backward compatibility)."""
        if self.multi_scope_volumes:
            return self.multi_scope_volumes.environment
        return []

    def get_all_volume_refs(self) -> MultiScopeVolumeSpec:
        """Get the full multi-scope volume specification."""
        return self.multi_scope_volumes or MultiScopeVolumeSpec()

    def get_sync_paths(self) -> List[SyncPath]:
        """Get sync path configuration for replicated mode.

        Prefers the new 'sync' field. Falls back to converting
        multi_scope_volumes.environment for backward compatibility.
        """
        # Prefer new sync field
        if self.sync:
            return self.sync

        # Fall back to converting multi_scope_volumes
        if self.multi_scope_volumes and self.multi_scope_volumes.environment:
            return [vol.to_sync_path() for vol in self.multi_scope_volumes.environment]

        return []


class ImageRef(BaseModel):
    """Reference to a pre-built devcontainer image."""

    name: str = Field(..., description="OCI reference incl. registry and tag")
    digest: Optional[str] = Field(
        None, description="Optional OCI digest pin for reproducible pulls"
    )

    @field_validator("name")
    @classmethod
    def _name_has_tag(cls, value: str) -> str:
        if ":" not in value:
            raise ValueError(
                "image name must include a tag, e.g. ghcr.io/acme/app:1.0-base-net"
            )
        return value


class FeatureRef(BaseModel):
    """Optional devcontainer feature override."""

    id: str = Field(..., description="Feature identifier")
    options: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("feature id cannot be empty")
        return value


class MountSpec(BaseModel):
    """Volume or bind mount to add to the container."""

    source: str
    target: str
    type: str = "volume"
    extra: Optional[str] = Field(
        None, description="Additional docker mount options (e.g., cache-from)"
    )

    @field_validator("type")
    @classmethod
    def _allowed_type(cls, value: str) -> str:
        if value not in ALLOWED_MOUNT_TYPES:
            raise ValueError(f"mount type must be one of {sorted(ALLOWED_MOUNT_TYPES)}")
        return value

    @field_validator("target")
    @classmethod
    def _absolute_target(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError(
                "mount target must be an absolute path inside the container"
            )
        return value

    def as_devcontainer_string(self) -> str:
        parts = [f"source={self.source}", f"target={self.target}", f"type={self.type}"]
        if self.extra:
            parts.append(self.extra)
        return ",".join(parts)


class SecretMount(BaseModel):
    """Declarative secret reference for post-create hooks."""

    provider: str = Field(
        ..., description="Secret backend identifier (vault, github, etc.)"
    )
    key: str = Field(..., description="Logical secret name (e.g., tailscale/authkey)")
    mount_path: str = Field(
        ..., description="Path where the secret will be materialized"
    )
    read_only: bool = True

    @field_validator("mount_path")
    @classmethod
    def _absolute(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("secret mount_path must be absolute")
        return value


class VSCodeExtensions(BaseModel):
    recommended: List[str] = Field(default_factory=list)
    optional: List[str] = Field(default_factory=list)


class VSCodeSettings(BaseModel):
    values: Dict[str, Any] = Field(default_factory=dict)


class VSCodeCustomization(BaseModel):
    extensions: VSCodeExtensions = Field(default_factory=VSCodeExtensions)
    settings: VSCodeSettings = Field(default_factory=VSCodeSettings)


class LifecycleHooks(BaseModel):
    post_create: List[str] = Field(default_factory=list)
    post_start: List[str] = Field(default_factory=list)

    @field_validator("post_create", "post_start", mode="before")
    @classmethod
    def _strip_empty(cls, value: List[str]) -> List[str]:
        return [cmd for cmd in value or [] if cmd.strip()]


class TemplateRef(BaseModel):
    """Pointer to a devcontainer template file within the repo."""

    name: str = Field(..., description="Logical template name")
    path: Path = Field(..., description="Relative path to the template Jinja file")
    overlays: List[Path] = Field(
        default_factory=list,
        description="Optional overlay templates applied on top of the base template",
    )

    @field_validator("path")
    @classmethod
    def _relative(cls, value: Path | str) -> Path:
        path = Path(value)
        if path.is_absolute():
            raise ValueError("template path must be relative to the repo root")
        return path

    @field_validator("overlays", mode="before")
    @classmethod
    def _relative_overlays(cls, values: List[Path | str]) -> List[Path]:
        normalized: List[Path] = []
        for entry in values or []:
            path = Path(entry)
            if path.is_absolute():
                raise ValueError("overlay paths must be relative")
            normalized.append(path)
        return normalized


class DevcontainerSpec(BaseModel):
    """Full description of a devcontainer that can be rendered from templates."""

    template: TemplateRef
    image: ImageRef
    features: List[FeatureRef] = Field(default_factory=list)
    user: str = Field("vscode", description="Container user")
    workspace_folder: str = Field("/workspace")
    workspace_mount: Optional[str] = Field(
        default=None, description="Override workspace mount target"
    )
    mounts: List[MountSpec] = Field(default_factory=list)
    run_args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    lifecycle: LifecycleHooks = Field(default_factory=LifecycleHooks)
    customizations: VSCodeCustomization = Field(default_factory=VSCodeCustomization)

    @field_validator("workspace_folder")
    @classmethod
    def _workspace_absolute(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("workspace_folder must be an absolute path")
        return value

    def to_devcontainer_dict(self) -> Dict[str, Any]:
        mounts = [mount.as_devcontainer_string() for mount in self.mounts]
        features = {feature.id: feature.options for feature in self.features}

        payload: Dict[str, Any] = {
            "name": self.template.name,
            "image": self.image.name,
            "remoteUser": self.user,
            "workspaceFolder": self.workspace_folder,
            "mounts": mounts or None,
            "runArgs": self.run_args or None,
            "features": features or None,
            "postCreateCommand": " && ".join(self.lifecycle.post_create) or None,
            "postStartCommand": " && ".join(self.lifecycle.post_start) or None,
            "env": self.env or None,
        }

        vscode_custom = {}
        if (
            self.customizations.extensions.recommended
            or self.customizations.extensions.optional
        ):
            vscode_custom["extensions"] = self.customizations.extensions.recommended
        if self.customizations.settings.values:
            vscode_custom["settings"] = self.customizations.settings.values
        if vscode_custom:
            payload["customizations"] = {"vscode": vscode_custom}

        if self.workspace_mount:
            payload["workspaceMount"] = self.workspace_mount

        # Pydantic model field is 'env', but devcontainer.json expects 'containerEnv' for consistency across lifecycle?
        # Actually 'env' is not standard in devcontainer.json schema (VS Code uses 'remoteEnv' or 'containerEnv').
        # The official spec says 'containerEnv'. 'remoteEnv' is for VS Code server.
        # Let's change 'env' to 'containerEnv' in the payload.
        if self.env:
            payload["containerEnv"] = self.env

        return {k: v for k, v in payload.items() if v not in (None, {}, [])}


class WorkspaceMetadata(BaseModel):
    org: str
    project: str
    environment: str = "dev"
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class WorkspaceSpec(BaseModel):
    """Top-level description of a workspace definition."""

    name: str
    version: str = "1.0.0"
    metadata: WorkspaceMetadata
    devcontainer: DevcontainerSpec
    mounts: List[MountSpec] = Field(
        default_factory=list,
        description="Host mounts that scripts/provisioners should honor",
    )
    secrets: List[SecretMount] = Field(default_factory=list)
    networking: NetworkingSpec = Field(default_factory=NetworkingSpec)
    persistence: PersistenceSpec = Field(default_factory=PersistenceSpec)
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )

    model_config = {
        "populate_by_name": True,
    }

    @field_validator("name")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not value:
            raise ValueError("workspace name cannot be empty")
        if any(char.isspace() for char in value):
            raise ValueError("workspace name must be slug-like (no whitespace)")
        return value

    @model_validator(mode="after")
    def _no_duplicate_mount_targets(self) -> "WorkspaceSpec":
        targets = {mount.target for mount in self.devcontainer.mounts}
        for mount in self.mounts:
            if mount.target in targets:
                raise ValueError(
                    f"duplicate mount target {mount.target} between devcontainer and workspace mounts"
                )
            targets.add(mount.target)
        return self

    def render_devcontainer(self) -> Dict[str, Any]:
        merged = self.devcontainer.model_copy(deep=True)
        merged.mounts.extend(self.mounts)

        # NOTE: We no longer auto-create volumes from sync paths.
        # Sync configuration (persistence.sync or persistence.multi_scope_volumes)
        # only configures what the sync daemon backs up - it does NOT create
        # Docker volumes. Users define volumes explicitly in devcontainer.mounts.

        payload = merged.to_devcontainer_dict()
        payload["name"] = self.name

        # Inject standard environment variables
        # Use containerEnv instead of env to ensure they are available to lifecycle hooks
        env = payload.setdefault("containerEnv", {})
        env["WORKSPACE_NAME"] = self.name
        env["WORKSPACE_ORG"] = self.metadata.org
        env["WORKSPACE_PROJECT"] = self.metadata.project
        env["WORKSPACE_ENV"] = self.metadata.environment
        env["NETWORKING_ENABLED"] = str(self.networking.enabled).lower()
        env["PERSISTENCE_ENABLED"] = str(self.persistence.enabled).lower()
        env["PERSISTENCE_MODE"] = self.persistence.mode
        env["PERSISTENCE_SCOPE"] = self.persistence.scope

        if self.persistence.mode == "replicated" and self.persistence.rclone_config:
            env["RCLONE_REMOTE_NAME"] = self.persistence.rclone_config.remote_name

        # Inject secret placeholders for runtime injection via ${localEnv:VAR}
        # This ensures secrets from the host (fnox) are passed to the container
        # and available to lifecycle hooks (post-start.sh).
        secret_vars = [
            "TAILSCALE_AUTH_KEY",
            "JUICEFS_DSN",
            "JUICEFS_BUCKET",
            "S3_ACCESS_KEY_ID",
            "S3_SECRET_ACCESS_KEY",
            "JUICEFS_S3_ENDPOINT",
        ]

        if self.persistence.mode == "replicated":
            secret_vars.append("RCLONE_BUCKET")

        if self.persistence.rclone_config:
            for value in self.persistence.rclone_config.options.values():
                if value.startswith("${") and value.endswith("}"):
                    var_name = value[2:-1]
                    if var_name not in secret_vars:
                        secret_vars.append(var_name)
        for var in secret_vars:
            env[var] = f"${{localEnv:{var}}}"

        # Remove the 'env' key if it was added by to_devcontainer_dict (which maps self.env to 'env')
        if "env" in payload:
            env.update(payload.pop("env"))

        if self.devcontainer.image.digest:
            payload.setdefault("build", {})["args"] = {
                "BASE_DIGEST": self.devcontainer.image.digest
            }
        return payload

    def devcontainer_json(self) -> str:
        import json

        return json.dumps(self.render_devcontainer(), indent=2, sort_keys=True)


class SpecBundle(BaseModel):
    """Collection of workspace specs that can be versioned together."""

    schema_version: str = "2025-11-14"
    workspaces: List[WorkspaceSpec]

    def to_json(self) -> Dict[str, Any]:
        return self.model_dump()


def example_spec() -> WorkspaceSpec:
    """Handy sample used for tests and docs."""

    return WorkspaceSpec(
        name="formualizer-dev",
        metadata=WorkspaceMetadata(
            org="coltec",
            project="formualizer",
            tags=["rust", "open-source"],
            description="Main developer workspace for the Formualizer engine",
        ),
        devcontainer=DevcontainerSpec(
            template=TemplateRef(
                name="rust",
                path=Path("devcontainer_templates/rust.json.jinja2"),
            ),
            image=ImageRef(
                name="ghcr.io/psu3d0/coltec-codespace:1.0-base-dind-net", digest=None
            ),
            user="vscode",
            workspace_folder="/workspace",
            workspace_mount="source=${localWorkspaceFolder},target=/workspace,type=bind",
            run_args=["--cap-add=SYS_PTRACE", "--security-opt=seccomp=unconfined"],
            mounts=[
                MountSpec(
                    source="formualizer-dev-home",
                    target="/home/vscode",
                    type="volume",
                    extra=None,
                ),
            ],
            lifecycle=LifecycleHooks(
                post_create=["./.devcontainer/scripts/post-create.sh"],
                post_start=["./.devcontainer/scripts/post-start.sh"],
            ),
            customizations=VSCodeCustomization(
                extensions=VSCodeExtensions(
                    recommended=[
                        "ms-azuretools.vscode-docker",
                        "rust-lang.rust-analyzer",
                        "tamasfe.even-better-toml",
                    ]
                ),
                settings=VSCodeSettings(
                    values={
                        "terminal.integrated.defaultProfile.linux": "tmux",
                        "files.trimTrailingWhitespace": True,
                    }
                ),
            ),
        ),
        persistence=PersistenceSpec(
            enabled=True,
            scope="project",
            mounts=[
                PersistenceMount(
                    name="agent-context",
                    target="/workspace/agent-context",
                    source="agent-context",
                ),
                PersistenceMount(
                    name="scratch",
                    target="/workspace/scratch",
                    source="scratch",
                ),
            ],
        ),
        networking=NetworkingSpec(enabled=True, hostname_prefix="dev-"),
    )


if __name__ == "__main__":
    spec = example_spec()
    print(spec.devcontainer_json())
