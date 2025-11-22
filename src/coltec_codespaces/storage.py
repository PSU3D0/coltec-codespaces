"""Storage mapping and validation utilities for JuiceFS-backed persistence."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from .spec import WorkspaceSpec


class MountMapping(BaseModel):
    """Mapping of a logical mount to a bucket subpath."""

    name: str
    target: str
    source: str
    type: str = "symlink"  # symlink or bind
    bucket_path: Optional[str] = None

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
    spec_mounts = {(m.name, m.source, m.target, m.type) for m in spec.persistence.mounts}
    map_mounts = {(m.name, m.source, m.target, m.type) for m in mapping_entry.mounts}
    if spec_mounts != map_mounts:
        missing = spec_mounts - map_mounts
        extra = map_mounts - spec_mounts
        details = []
        if missing:
            details.append(f"missing in mapping: {missing}")
        if extra:
            details.append(f"extra in mapping: {extra}")
        raise RuntimeError("Mounts mismatch between mapping and spec: " + "; ".join(details))


@dataclass
class JuiceFSCommands:
    """Thin wrapper to allow mocking in tests."""

    def run(self, args: List[str], env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
        return subprocess.run(args, check=False, capture_output=True, text=True, env=env)


def ensure_env_vars_present(env: Dict[str, str], required: List[str]) -> None:
    missing = [k for k in required if not env.get(k)]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")


def juicefs_status(commands: JuiceFSCommands, dsn: str, env: Dict[str, str]) -> bool:
    result = commands.run(["juicefs", "status", dsn], env=env)
    return result.returncode == 0


def juicefs_format(
    commands: JuiceFSCommands,
    dsn: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    endpoint: Optional[str],
    filesystem: str,
    env: Dict[str, str],
) -> None:
    args = [
        "juicefs",
        "format",
        "--storage",
        "s3",
        "--bucket",
        bucket,
        "--access-key",
        access_key,
        "--secret-key",
        secret_key,
    ]
    if endpoint:
        args.extend(["--endpoint", endpoint])
    args.extend([dsn, filesystem])
    result = commands.run(args, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"juicefs format failed: {result.stderr.strip()}")


def juicefs_mount(
    commands: JuiceFSCommands,
    dsn: str,
    mountpoint: Path,
    bucket: str,
    access_key: str,
    secret_key: str,
    endpoint: Optional[str],
    cache_size_mb: int,
    env: Dict[str, str],
) -> None:
    args = [
        "juicefs",
        "mount",
        "--background",
        "--writeback",
        "--cache-size",
        str(cache_size_mb),
        "--no-usage-report",
        "--storage",
        "s3",
        "--bucket",
        bucket,
        "--access-key",
        access_key,
        "--secret-key",
        secret_key,
    ]
    if endpoint:
        args.extend(["--endpoint", endpoint])
    args.extend([dsn, str(mountpoint)])
    result = commands.run(args, env=env)
    if result.returncode != 0:
        raise RuntimeError(f"juicefs mount failed: {result.stderr.strip()}")


def juicefs_umount(commands: JuiceFSCommands, mountpoint: Path, env: Dict[str, str]) -> None:
    result = commands.run(["juicefs", "umount", str(mountpoint)], env=env)
    if result.returncode != 0:
        raise RuntimeError(f"juicefs umount failed: {result.stderr.strip()}")
