"""Typed meta-spec for Coltec devcontainer workspaces.

The goal is to describe every workspace in one structured document and then
render devcontainer.json files, lifecycle scripts, and documentation from the
same source of truth.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

ALLOWED_MOUNT_TYPES = {"bind", "volume", "tmpfs"}


class NetworkingSpec(BaseModel):
    """Networking feature toggle and metadata."""

    enabled: bool = False
    hostname_prefix: str = "dev-"
    tags: List[str] = Field(default_factory=lambda: ["tag:devcontainer"])


class PersistenceMount(BaseModel):
    """Logical mapping of a JuiceFS subpath into the workspace."""

    name: str
    target: str
    source: str
    type: str = "symlink"  # symlink or bind

    @field_validator("target")
    @classmethod
    def _absolute_target(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("persistence mount target must be an absolute path")
        return value

    @field_validator("type")
    @classmethod
    def _allowed_type(cls, value: str) -> str:
        if value not in {"symlink", "bind"}:
            raise ValueError("persistence mount type must be 'symlink' or 'bind'")
        return value


class PersistenceSpec(BaseModel):
    """Persistence feature toggle and mount definitions."""

    enabled: bool = False
    scope: str = Field("project", description="project or environment")
    mounts: List[PersistenceMount] = Field(default_factory=list)

    @field_validator("scope")
    @classmethod
    def _scope_allowed(cls, value: str) -> str:
        if value not in {"project", "environment"}:
            raise ValueError("persistence.scope must be 'project' or 'environment'")
        return value


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
        merged = self.devcontainer.copy(deep=True)
        merged.mounts.extend(self.mounts)
        payload = merged.to_devcontainer_dict()
        payload["name"] = self.name
        # Inject feature flags for downstream templates/scripts
        payload.setdefault("features", {})["coltec:networking"] = {
            "enabled": self.networking.enabled,
            "hostname_prefix": self.networking.hostname_prefix,
            "tags": self.networking.tags,
        }
        payload.setdefault("features", {})["coltec:persistence"] = {
            "enabled": self.persistence.enabled,
            "scope": self.persistence.scope,
            "mounts": [mount.model_dump() for mount in self.persistence.mounts],
        }
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
            image=ImageRef(name="ghcr.io/psu3d0/coltec-codespace:1.0-base-dind-net"),
            workspace_mount="source=${localWorkspaceFolder},target=/workspace,type=bind",
            run_args=["--cap-add=SYS_PTRACE", "--security-opt=seccomp=unconfined"],
            mounts=[
                MountSpec(
                    source="formualizer-dev-home",
                    target="/home/vscode",
                    type="volume",
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
