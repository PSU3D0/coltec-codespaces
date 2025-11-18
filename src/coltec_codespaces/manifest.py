"""Manifest handling logic for Coltec workspaces."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Any, Optional, Tuple

import yaml


def load_manifest(path: Path) -> Dict[str, Any]:
    """Load and normalize a workspace manifest file."""
    if not path.exists():
        return {"version": 1, "manifest": {}}

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Failed to parse manifest at {path}: {exc}") from exc

    version = data.get("version")
    if version not in (None, 1):
        raise RuntimeError(
            f"Unsupported manifest version ({version}). Expected version 1 at {path}."
        )

    data.setdefault("version", 1)
    data.setdefault("manifest", {})
    return data


def save_manifest(path: Path, data: Dict[str, Any]) -> None:
    """Save the workspace manifest file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def find_manifest_entry(
    manifest_data: Dict[str, Any], workspace_path: Path, repo_root: Path
) -> Optional[Tuple[str, str, Dict[str, Any]]]:
    """
    Locate a workspace within the manifest structure.

    Returns (org_slug, project_slug, environment_entry) or None.
    """
    try:
        relative = workspace_path.relative_to(repo_root).as_posix()
    except ValueError:
        relative = str(workspace_path)

    manifest_root = manifest_data.get("manifest", {})
    for org_slug, org_data in manifest_root.items():
        project_dir = org_data.get("project_dir") or org_slug
        projects = org_data.get("projects", {}) or {}

        for project_slug, project_data in projects.items():
            environments = project_data.get("environments", []) or []
            for env in environments:
                env_name = env.get("name")
                env_path = env.get("workspace_path")

                # Match logic: exact path match OR predictable default path
                default_path = (
                    f"codespaces/{project_dir}/{env_name}"
                    if env_name and project_dir
                    else None
                )

                if relative == env_path or (default_path and relative == default_path):
                    return org_slug, project_slug, env

    return None
