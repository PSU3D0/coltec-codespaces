"""Validation logic for checking workspace integrity."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, List, Optional, Tuple

import yaml

from .manifest import load_manifest, find_manifest_entry


def _record(results: List[bool], condition: bool, success: str, failure: str) -> None:
    symbol = "✓" if condition else "✗"
    message = success if condition else failure
    print(f"{symbol} {message}")
    results.append(condition)


def _git_check(path: Path) -> bool:
    try:
        subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def _load_yaml_safely(path: Path) -> Tuple[bool, Any, str]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return True, data, ""
    except yaml.YAMLError as exc:
        return False, None, str(exc)


def validate_workspace_layout(
    workspace_path: Path, repo_root: Path, manifest_path: Optional[Path] = None
) -> bool:
    """
    Perform a structural validation of a Coltec workspace.

    Checks:
    - Directory structure (agent-context, scratch, codebase)
    - Git configuration (submodules, remote tracking)
    - Configuration files (agent-project.yaml, .devcontainer)
    - Manifest registration
    """
    if not workspace_path.exists():
        print(f"Workspace path does not exist: {workspace_path}", file=sys.stderr)
        return False

    # Locate manifest
    if not manifest_path:
        manifest_path = repo_root / "codespaces/manifest.yaml"

    manifest_exists = manifest_path.exists()
    manifest_data = load_manifest(manifest_path) if manifest_exists else {}

    results: List[bool] = []

    _record(
        results,
        manifest_exists,
        f"Manifest found at {manifest_path}",
        f"Manifest missing at {manifest_path}",
    )

    # 1. agent-project.yaml checks
    agent_project = workspace_path / "agent-project.yaml"
    _record(
        results,
        agent_project.is_file(),
        "agent-project.yaml present",
        "agent-project.yaml missing",
    )

    agent_manifest = None
    if agent_project.is_file():
        ok, data, error = _load_yaml_safely(agent_project)
        _record(
            results,
            ok,
            "agent-project.yaml parsed",
            f"agent-project.yaml invalid YAML: {error}",
        )
        if ok and isinstance(data, dict):
            agent_manifest = data
            repos = data.get("repos", []) or []
            has_codebase = any(
                repo.get("path") == "codebase"
                for repo in repos
                if isinstance(repo, dict)
            )
            _record(
                results,
                has_codebase,
                "agent-project.yaml declares codebase repo",
                "agent-project.yaml missing codebase repo entry",
            )

            policies = (
                data.get("policies", {})
                if isinstance(data.get("policies"), dict)
                else {}
            )
            write_paths = (
                policies.get("write_paths", []) if isinstance(policies, dict) else []
            )
            has_policy = "codebase/**" in write_paths
            _record(
                results,
                has_policy,
                "Policies include codebase/** write path",
                "Policies missing codebase/** write path",
            )

    # 2. Git & Submodule checks
    codebase_dir = workspace_path / "codebase"
    _record(
        results,
        codebase_dir.is_dir(),
        "codebase/ directory present",
        "codebase/ directory missing",
    )
    if codebase_dir.is_dir():
        _record(
            results,
            _git_check(codebase_dir),
            "codebase/ is a git repository",
            "codebase/ is not a git repository",
        )

    gitmodules = workspace_path / ".gitmodules"
    _record(
        results,
        gitmodules.is_file(),
        ".gitmodules present",
        ".gitmodules missing",
    )
    if gitmodules.is_file():
        has_entry = "path = codebase" in gitmodules.read_text(encoding="utf-8")
        _record(
            results,
            has_entry,
            ".gitmodules references codebase submodule",
            ".gitmodules missing codebase path entry",
        )

    # 3. Devcontainer checks
    devcontainer = workspace_path / ".devcontainer" / "devcontainer.json"
    _record(
        results,
        devcontainer.is_file(),
        ".devcontainer/devcontainer.json present",
        ".devcontainer/devcontainer.json missing",
    )

    post_create = workspace_path / ".devcontainer" / "scripts" / "post-create.sh"
    post_start = workspace_path / ".devcontainer" / "scripts" / "post-start.sh"
    _record(
        results,
        post_create.is_file(),
        "post-create script present",
        "post-create script missing",
    )
    _record(
        results,
        post_start.is_file(),
        "post-start script present",
        "post-start script missing",
    )

    # 4. Agent folders
    for dirname in ("agent-context", "scratch"):
        path = workspace_path / dirname
        _record(
            results,
            path.is_dir(),
            f"{dirname}/ directory present",
            f"{dirname}/ directory missing",
        )

    # 5. README
    readme = workspace_path / "README-coltec-workspace.md"
    _record(
        results,
        readme.is_file(),
        "Workspace README present",
        "Workspace README missing",
    )

    # 6. Root Git check
    _record(
        results,
        _git_check(workspace_path),
        "Workspace root is a git repository",
        "Workspace root is not a git repository",
    )

    # 7. Manifest consistency
    manifest_entry = (
        find_manifest_entry(manifest_data, workspace_path, repo_root)
        if manifest_data
        else None
    )

    if manifest_entry:
        org_slug, project_slug, env = manifest_entry
        env_name = env.get("name") or workspace_path.name
        manifest_success = (
            f"Workspace listed in manifest ({org_slug}/{project_slug}/{env_name})"
        )
    else:
        manifest_success = "Workspace listed in manifest"

    _record(
        results,
        manifest_entry is not None,
        manifest_success,
        "Workspace missing from manifest",
    )

    # 8. URL consistency check
    if manifest_entry and agent_manifest:
        manifest_repo_url = manifest_entry[2].get("asset_repo_url")
        agent_repo_url = None
        for repo in agent_manifest.get("repos", []) or []:
            if isinstance(repo, dict) and repo.get("path") == "codebase":
                agent_repo_url = repo.get("url")
                break

        if manifest_repo_url:
            failure_text = (
                "Manifest repo URL mismatch "
                f"(manifest={manifest_repo_url}, agent={agent_repo_url})"
            )
            _record(
                results,
                manifest_repo_url == agent_repo_url,
                "Manifest repo URL matches agent manifest",
                failure_text,
            )

    if all(results):
        print("\nWorkspace validation passed.")
        return True

    print("\nWorkspace validation failed.")
    return False
