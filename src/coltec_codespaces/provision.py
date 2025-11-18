"""Provisioning logic for creating new Coltec workspaces."""

from __future__ import annotations

import copy
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from jinja2 import Environment, FileSystemLoader

from .manifest import load_manifest, save_manifest, find_manifest_entry
from .spec import (
    WorkspaceSpec,
    WorkspaceMetadata,
    DevcontainerSpec,
    TemplateRef,
    ImageRef,
    LifecycleHooks,
    VSCodeCustomization,
    VSCodeExtensions,
    VSCodeSettings,
)

# Configuration Defaults
DEFAULT_IMAGE = "ghcr.io/psu3d0/coltec-codespace:1.0-base-dind-net"
WORKSPACE_FOLDER = "/workspace"
DEFAULT_RUN_ARGS = ["--cap-add=SYS_PTRACE", "--security-opt=seccomp=unconfined"]

DEVCONTAINER_TEMPLATE_MAP = {
    "python": "python.json.jinja2",
    "node": "node.json.jinja2",
    "rust": "rust.json.jinja2",
    "monorepo": "monorepo.json.jinja2",
    "other": "other.json.jinja2",
}

BASE_EXTENSIONS = [
    "ms-azuretools.vscode-docker",
    "ms-vscode.makefile-tools",
    "ms-vscode.remote-explorer",
    "fill-labs.dependi",
]

PYTHON_EXTENSIONS = ["ms-python.python"]
NODE_EXTENSIONS = ["dbaeumer.vscode-eslint", "esbenp.prettier-vscode"]
RUST_EXTENSIONS = ["rust-lang.rust-analyzer", "tamasfe.even-better-toml"]

PROJECT_EXTENSION_MAP = {
    "python": PYTHON_EXTENSIONS,
    "node": NODE_EXTENSIONS,
    "rust": RUST_EXTENSIONS,
    "monorepo": PYTHON_EXTENSIONS + NODE_EXTENSIONS + RUST_EXTENSIONS,
    "other": [],
}

BASE_SETTINGS = {
    "terminal.integrated.defaultProfile.linux": "tmux",
    "terminal.integrated.profiles.linux": {
        "zsh": {"path": "/usr/bin/zsh"},
        "resumable-tmux": {
            "path": "/usr/bin/tmux",
            "args": ["new-session", "-A", "-s", "devcontainer"],
        },
    },
    "terminal.integrated.localEchoEnabled": "on",
    "files.trimTrailingWhitespace": True,
    "editor.formatOnSave": True,
    "python.analysis.typeCheckingMode": "strict",
    "rust-analyzer.cargo.allFeatures": True,
    "rust-analyzer.check.command": "clippy",
    "git.openRepositoryInParentFolders": "always",
}


def _dedupe(seq: List[str]) -> List[str]:
    seen = set()
    ordered = []
    for item in seq:
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def _slugify(name: str) -> str:
    s = name.strip().lower().replace(" ", "-")
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789-_"
    return "".join(ch for ch in s if ch in allowed) or "project"


def _run(
    cmd: List[str],
    cwd: Optional[str] = None,
    check: bool = True,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    print(f"[provision] $ {' '.join(cmd)} (cwd={cwd or os.getcwd()})")
    return subprocess.run(cmd, cwd=cwd, check=check, env=env)


def _is_git_url(s: str) -> bool:
    return s.startswith("git@") or s.startswith("https://") or s.startswith("http://")


def get_asset_repo_url(asset_input: str) -> str:
    if _is_git_url(asset_input):
        return asset_input

    path = Path(asset_input).expanduser().resolve()
    if not path.exists():
        raise RuntimeError(f"Local path {path} does not exist")

    git_dir = path / ".git"
    if not git_dir.exists():
        raise RuntimeError(f"{path} is not a git repo (no .git directory)")

    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(path),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True,
        )
        url = result.stdout.strip()
        if url:
            return url
    except subprocess.CalledProcessError:
        pass

    return f"file://{path}"


def build_workspace_spec_data(
    workspace_name: str,
    project_type: str,
    org_slug: str,
    project_slug: str,
) -> Dict[str, Any]:
    template_file = DEVCONTAINER_TEMPLATE_MAP.get(
        project_type, DEVCONTAINER_TEMPLATE_MAP["other"]
    )
    template_path = f"devcontainer_templates/{template_file}"
    extensions = _dedupe(BASE_EXTENSIONS + PROJECT_EXTENSION_MAP.get(project_type, []))
    settings = copy.deepcopy(BASE_SETTINGS)

    mounts = [
        {
            "source": f"{workspace_name}-home",
            "target": "/home/vscode",
            "type": "volume",
        },
        {
            "source": f"{workspace_name}-tool-cache",
            "target": "/home/vscode/.cache",
            "type": "volume",
        },
    ]

    # We use isoformat with Z to indicate UTC
    now_str = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

    spec = {
        "name": workspace_name,
        "version": "1.0.0",
        "metadata": {
            "org": org_slug,
            "project": project_slug,
            "environment": workspace_name,
            "description": f"Coltec workspace for {project_slug}",
            "tags": _dedupe([project_type, org_slug, project_slug]),
        },
        "devcontainer": {
            "template": {"name": project_type, "path": template_path},
            "image": {"name": DEFAULT_IMAGE},
            "user": "vscode",
            "workspace_folder": WORKSPACE_FOLDER,
            "workspace_mount": "source=${localWorkspaceFolder},target=/workspace,type=bind",
            "run_args": DEFAULT_RUN_ARGS,
            "mounts": mounts,
            "lifecycle": {
                "post_create": ["./.devcontainer/scripts/post-create.sh"],
                "post_start": ["./.devcontainer/scripts/post-start.sh"],
            },
            "customizations": {
                "extensions": {"recommended": extensions},
                "settings": {"values": settings},
            },
        },
        "generated_at": now_str,
    }
    return spec


def render_agent_project_yaml(
    project_id: str,
    org_slug: str,
    project_slug: str,
    environment: str,
    asset_repo_url: str,
) -> str:
    content = {
        "project_id": project_id,
        "org": org_slug,
        "project": project_slug,
        "environment": environment,
        "repos": [
            {
                "name": "app",
                "path": "codebase",
                "url": asset_repo_url,
            },
        ],
        "policies": {
            "write_paths": [
                "codebase/**",
                "agent-context/**",
                "scratch/**",
            ],
            "read_only_paths": [],
        },
    }
    return yaml.safe_dump(content, sort_keys=False)


def provision_workspace(
    repo_root: Path,
    asset_input: str,
    org_slug: str,
    project_slug: str,
    environment_name: str,
    project_type: str,
    asset_branch: str = "main",
    create_remote: bool = False,
    gh_org: Optional[str] = None,
    gh_name: Optional[str] = None,
    templates_dir: Optional[Path] = None,
    manifest_path: Optional[Path] = None,
) -> None:
    """
    Core logic to provision a new Coltec workspace.
    """

    if not templates_dir:
        templates_dir = repo_root / "templates"

    workspace_scaffold_dir = templates_dir / "workspace_scaffold"
    if not workspace_scaffold_dir.exists():
        raise RuntimeError(
            f"Workspace scaffold templates not found: {workspace_scaffold_dir}"
        )

    if not manifest_path:
        manifest_path = repo_root / "codespaces/manifest.yaml"

    # 1. Load Manifest & Calculate Paths
    manifest_data = load_manifest(manifest_path)
    orgs = manifest_data.get("manifest", {})

    # Ensure org structure exists
    org_entry = orgs.setdefault(org_slug, {})
    if not org_entry.get("projects"):
        org_entry["projects"] = {}
    if not org_entry.get("project_dir"):
        org_entry["project_dir"] = _slugify(org_slug)

    projects = org_entry["projects"]
    project_entry = projects.setdefault(project_slug, {})
    if not project_entry.get("environments"):
        project_entry["environments"] = []

    environments = project_entry["environments"]
    if any(env.get("name") == environment_name for env in environments):
        raise RuntimeError(
            f"Environment '{environment_name}' already exists for project '{project_slug}'."
        )

    # Path resolution
    codespaces_root = repo_root / "codespaces"
    project_dir_name = org_entry["project_dir"]
    workspace_path = (codespaces_root / project_dir_name / environment_name).resolve()

    if workspace_path.exists():
        raise RuntimeError(f"Workspace path already exists: {workspace_path}")

    asset_repo_url = get_asset_repo_url(asset_input)

    print("\n[provision] Configuration:")
    print(f"  Asset repo:      {asset_repo_url}")
    print(f"  Org slug:        {org_slug}")
    print(f"  Project slug:    {project_slug}")
    print(f"  Environment dir: {environment_name}")
    print(f"  Project type:    {project_type}")
    print(f"  Asset branch:    {asset_branch}")
    print(f"  Workspace path:  {workspace_path}\n")

    # 2. Create Directory Structure
    workspace_path.mkdir(parents=True, exist_ok=True)
    try:
        _run(["git", "init"], cwd=str(workspace_path))

        for subdir in ("agent-context", "scratch"):
            d = workspace_path / subdir
            d.mkdir(parents=True, exist_ok=True)
            (d / ".gitkeep").write_text("", encoding="utf-8")

        devcontainer_dir = workspace_path / ".devcontainer"
        devcontainer_dir.mkdir(parents=True, exist_ok=True)

        # 3. Generate Spec
        spec_data = build_workspace_spec_data(
            workspace_name=environment_name,
            project_type=project_type,
            org_slug=org_slug,
            project_slug=project_slug,
        )

        # Use the Pydantic model to validate/dump
        # We can import the Spec classes to do this programmatically without CLI calls
        try:
            spec_model = WorkspaceSpec.model_validate(spec_data)
            # Write spec file
            spec_path = devcontainer_dir / "workspace-spec.yaml"
            # We dump raw dict to YAML to preserve comments if we had them, but here we just dump model
            # Actually, let's just dump the dict we built to control formatting if needed,
            # but model_dump is safer for validation.
            # Let's use the dict we built to avoid extra fields from model defaults if desired,
            # but standardizing on model_dump is better.
            # Wait, build_workspace_spec_data returns a dict matching the schema.
            # Let's validate it via the model, then dump it.

            # Dump to yaml
            spec_path.write_text(
                yaml.safe_dump(spec_data, sort_keys=False), encoding="utf-8"
            )

            # Render devcontainer.json using the model method
            devcontainer_json = spec_model.devcontainer_json()
            (devcontainer_dir / "devcontainer.json").write_text(
                devcontainer_json, encoding="utf-8"
            )
            print(f"[provision] Wrote devcontainer configuration to {devcontainer_dir}")

        except Exception as e:
            raise RuntimeError(f"Failed to generate/validate spec: {e}") from e

        # 4. Render Scaffold Templates (README, scripts)
        # Setup Jinja environment
        template_env = Environment(
            loader=FileSystemLoader(templates_dir),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        template_env.filters["dedupe"] = _dedupe

        scaffold_context = {
            "workspace_name": environment_name,
            "org_slug": org_slug,
            "project_slug": project_slug,
            "project_type": project_type,
            "asset_repo_url": asset_repo_url,
        }

        for template_file in workspace_scaffold_dir.rglob("*"):
            if template_file.is_dir():
                continue

            # Compute relative path from templates dir for Jinja loading
            # template_file is absolute, templates_dir is absolute
            rel_path = template_file.relative_to(templates_dir)
            template = template_env.get_template(str(rel_path).replace(os.sep, "/"))
            rendered = template.render(**scaffold_context)

            # Compute output path relative to scaffold root
            rel_output = template_file.relative_to(workspace_scaffold_dir)
            if rel_output.suffix == ".jinja2":
                rel_output = rel_output.with_suffix("")

            target_path = workspace_path / rel_output
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(rendered, encoding="utf-8")

            if target_path.suffix in {".sh", ".bash"}:
                target_path.chmod(target_path.stat().st_mode | 0o111)

        # 5. Agent Project YAML
        project_id = f"{org_slug}-{project_slug}-{environment_name}"
        agent_project_yaml = render_agent_project_yaml(
            project_id=project_id,
            org_slug=org_slug,
            project_slug=project_slug,
            environment=environment_name,
            asset_repo_url=asset_repo_url,
        )
        (workspace_path / "agent-project.yaml").write_text(
            agent_project_yaml, encoding="utf-8"
        )

        # 6. Git Submodule & Commit
        # We must explicitly allow file protocol for local submodules due to git security restrictions
        _run(
            [
                "git",
                "-c",
                "protocol.file.allow=always",
                "submodule",
                "add",
                "-b",
                asset_branch,
                asset_repo_url,
                "codebase",
            ],
            cwd=str(workspace_path),
        )
        # Configure submodule branch pinning
        _run(
            [
                "git",
                "config",
                "-f",
                ".gitmodules",
                "submodule.codebase.branch",
                asset_branch,
            ],
            cwd=str(workspace_path),
        )
        _run(
            ["git", "config", "submodule.codebase.branch", asset_branch],
            cwd=str(workspace_path),
        )

        _run(["git", "add", "."], cwd=str(workspace_path))
        _run(
            ["git", "commit", "-m", f"Init workspace {environment_name}"],
            cwd=str(workspace_path),
        )
        print(f"[provision] Initialized workspace repo at {workspace_path}")

        # 7. Remote Provisioning (Optional)
        if create_remote:
            target_name = gh_name or environment_name
            if "/" in target_name:
                repo_name = target_name
            elif gh_org:
                repo_name = f"{gh_org}/{target_name}"
            else:
                repo_name = target_name

            print(f"[provision] Creating remote GitHub repo {repo_name}...")
            try:
                _run(["gh", "--version"], check=True)
                _run(
                    [
                        "gh",
                        "repo",
                        "create",
                        repo_name,
                        "--private",
                        "--source=.",
                        "--remote=origin",
                        "--push",
                    ],
                    cwd=str(workspace_path),
                )
                print(f"[provision] Pushed to https://github.com/{repo_name}")
            except (subprocess.CalledProcessError, FileNotFoundError):
                print(
                    "[provision] Warning: Failed to create/push remote repo. "
                    "Ensure 'gh' is installed and authenticated.",
                    file=sys.stderr,
                )

        # 8. Update Manifest
        try:
            rel_workspace_path = workspace_path.relative_to(repo_root).as_posix()
        except ValueError:
            rel_workspace_path = str(workspace_path)

        env_entry = {
            "name": environment_name,
            "workspace_path": rel_workspace_path,
            "asset_repo_url": asset_repo_url,
            "asset_branch": asset_branch,
            "project_type": project_type,
            "created_at": spec_data["generated_at"],
        }
        environments.append(env_entry)
        save_manifest(manifest_path, manifest_data)
        print(f"[provision] Updated manifest: {manifest_path}")

    except Exception:
        if workspace_path.exists():
            shutil.rmtree(workspace_path, ignore_errors=True)
            print(
                f"[provision] Rolled back workspace at {workspace_path}",
                file=sys.stderr,
            )
        raise
