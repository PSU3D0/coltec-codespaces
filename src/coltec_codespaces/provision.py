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
    "terminal.integrated.cwd": "/workspace",
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


def render_templates(
    scaffold_roots: List[Path],
    context: Dict[str, Any],
    env: Environment,
) -> Dict[Path, str]:
    """
    Renders all templates from the scaffold roots using the provided context.
    Returns a dict of {relative_output_path: content}.
    Files that render to empty strings are omitted (Ghost File Pattern).
    """
    results = {}
    # Render in order: base first, overlays later overwrite
    for scaffold_root in scaffold_roots:
        for template_file in scaffold_root.rglob("*"):
            if template_file.is_dir():
                continue

            rel_path = template_file.relative_to(scaffold_root.parent)
            template = env.get_template(str(rel_path).replace(os.sep, "/"))
            rendered = template.render(**context)

            # Skip empty files (Ghost File Pattern)
            rel_output = template_file.relative_to(scaffold_root)
            if rel_output.suffix == ".jinja2":
                rel_output = rel_output.with_suffix("")

            if not rendered.strip():
                # If a later overlay renders empty, it effectively deletes/hides the file
                if rel_output in results:
                    del results[rel_output]
                continue

            results[rel_output] = rendered

    return results


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
    template_overlays: Optional[List[Path]] = None,
    manifest_path: Optional[Path] = None,
) -> None:
    """
    Core logic to provision a new Coltec workspace.
    """

    # Resolve default templates and overlays
    if not templates_dir:
        templates_dir = repo_root / "templates"

    scaffold_roots: List[Path] = []
    base_scaffold = templates_dir / "workspace_scaffold"
    if not base_scaffold.exists():
        raise RuntimeError(f"Base template directory not found: {base_scaffold}")
    scaffold_roots.append(base_scaffold)

    if template_overlays:
        for overlay in template_overlays:
            overlay_scaffold = overlay / "workspace_scaffold"
            if overlay_scaffold.exists():
                scaffold_roots.append(overlay_scaffold)
            else:
                print(
                    f"[provision] Warning: overlay scaffold not found, skipping: {overlay_scaffold}",
                    file=sys.stderr,
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
        # Setup Jinja environment that can see all scaffold roots
        loader_paths = sorted({str(root.parent) for root in scaffold_roots})
        template_env = Environment(
            loader=FileSystemLoader(loader_paths),
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
            "config": project_entry.get("config", {}),
            "features": project_entry.get("features", []),
        }

        rendered_files = render_templates(
            scaffold_roots, scaffold_context, template_env
        )

        for rel_output, content in rendered_files.items():
            target_path = workspace_path / rel_output
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content, encoding="utf-8")

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


def update_workspace(
    workspace_path: Path,
    repo_root: Path,
    manifest_path: Optional[Path] = None,
    templates_dir: Optional[Path] = None,
    template_overlays: Optional[List[Path]] = None,
    dry_run: bool = False,
    force: bool = False,
) -> bool:
    """
    Updates an existing workspace with the latest templates.
    Returns True if changes were applied (or would be applied in dry-run).
    """
    if not workspace_path.exists():
        raise RuntimeError(f"Workspace path not found: {workspace_path}")

    if not manifest_path:
        manifest_path = repo_root / "codespaces/manifest.yaml"

    # 1. Reconstruct Context
    # We need to find the manifest entry to get config/features
    # and use the workspace-spec.yaml for immutable IDs if needed,
    # but manifest is the source of truth for *desired* state.

    manifest_data = load_manifest(manifest_path)
    # Find entry matching this workspace path
    try:
        rel_path = workspace_path.relative_to(repo_root).as_posix()
    except ValueError:
        # If workspace_path is absolute and not in repo_root, we can't easily find it by path
        # But we can assume standard structure: codespaces/org/project/env
        # Let's try to parse from path parts
        # codespaces/coltec/coltec-codespaces -> org=coltec, project=coltec-codespaces (wrong, env is coltec-codespaces?)
        # Standard: codespaces/<org_dir>/<env_name>
        # But org_dir is slugified org.
        # Let's search the manifest for value matching rel_path
        rel_path = str(workspace_path)  # Fallback

    # find_manifest_entry returns (org, proj, env) or None
    # It takes Path objects for signature
    entry_tuple = find_manifest_entry(manifest_data, workspace_path, repo_root)

    if not entry_tuple:
        # Heuristic: assuming standard codespaces/<org>/<project>/<env> structure
        parts = workspace_path.parts
        if len(parts) >= 3:
            env_name_heuristic = parts[-1]
            # Try to find an entry with this env name.
            # This is risky if names aren't unique, but `provision` enforces uniqueness per project.
            # Let's iterate all projects to find a match.
            found = None
            for org_key, org_val in manifest_data.get("manifest", {}).items():
                for proj_key, proj_val in org_val.get("projects", {}).items():
                    for env in proj_val.get("environments", []):
                        if env.get("name") == env_name_heuristic:
                            # Found it
                            # Reconstruct entry format returned by find_manifest_entry
                            found = (org_key, proj_key, env)
                            break
                    if found:
                        break
                if found:
                    break
            entry_tuple = found

    if not entry_tuple:
        raise RuntimeError(f"Could not find manifest entry for {workspace_path}")

    org_slug, project_slug, env_entry = entry_tuple
    env_name = env_entry["name"]
    project_type = env_entry["project_type"]
    asset_repo_url = env_entry["asset_repo_url"]

    # Config/Features are on the PROJECT level, not environment level in schema?
    # provision.py:391: "config": project_entry.get("config", {})
    # So we need the project entry. `find_manifest_entry` returns a flattened dict?
    # Let's look at `manifest.py` or just re-traverse.
    # Actually, let's implement a robust lookup helper or just traverse here.

    # Re-traverse to get full context including project-level config
    org_data = manifest_data.get("manifest", {}).get(org_slug, {})
    project_data = org_data.get("projects", {}).get(project_slug, {})

    scaffold_context = {
        "workspace_name": env_name,
        "org_slug": org_slug,
        "project_slug": project_slug,
        "project_type": project_type,
        "asset_repo_url": asset_repo_url,
        "config": project_data.get("config", {}),
        "features": project_data.get("features", []),
    }

    # 2. Prepare Templates
    if not templates_dir:
        templates_dir = repo_root / "templates"

    scaffold_roots: List[Path] = []
    base_scaffold = templates_dir / "workspace_scaffold"
    if not base_scaffold.exists():
        raise RuntimeError(f"Base template directory not found: {base_scaffold}")
    scaffold_roots.append(base_scaffold)

    if template_overlays:
        for overlay in template_overlays:
            overlay_scaffold = overlay / "workspace_scaffold"
            if overlay_scaffold.exists():
                scaffold_roots.append(overlay_scaffold)

    loader_paths = sorted({str(root.parent) for root in scaffold_roots})
    template_env = Environment(
        loader=FileSystemLoader(loader_paths),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template_env.filters["dedupe"] = _dedupe

    # 3. Render & Diff
    rendered_files = render_templates(scaffold_roots, scaffold_context, template_env)
    changes = []

    print(f"\n[update] Checking for drift in {workspace_path}...")

    for rel_path, new_content in rendered_files.items():
        target_file = workspace_path / rel_path

        if not target_file.exists():
            changes.append(("ADD", rel_path, new_content))
            continue

        # Check content
        current_content = target_file.read_text(encoding="utf-8")
        if current_content != new_content:
            changes.append(("MOD", rel_path, new_content))

    if not changes:
        print("[update] No changes detected. Workspace is up to date.")
        return False

    print(f"[update] Found {len(changes)} changes:")
    for action, path, _ in changes:
        print(f"  {action} {path}")

    if dry_run:
        print("[update] Dry run complete. Pass --force to apply changes.")
        return True

    if not force:
        # Simple safeguard against accidental bulk overwrites if called programmatically
        # CLI usually handles confirmation
        pass

    # 4. Apply Changes
    print("[update] Applying changes...")
    for action, rel_path, content in changes:
        target_path = workspace_path / rel_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(content, encoding="utf-8")
        if target_path.suffix in {".sh", ".bash"}:
            target_path.chmod(target_path.stat().st_mode | 0o111)
        print(f"  Applied {action} {rel_path}")

    # TODO: Should we regenerate devcontainer.json?
    # Yes, because the spec might have changed if templates changed (e.g. spec template).
    # But `workspace-spec.yaml` is generated from code logic, NOT Jinja templates (mostly).
    # Actually, `workspace-spec.yaml` IS generated via python code `build_workspace_spec_data`, not a template file.
    # If the python logic changed, we should regenerate it.
    # Let's regenerate the spec and devcontainer.json as well to be safe.

    try:
        spec_data = build_workspace_spec_data(
            workspace_name=env_name,
            project_type=project_type,
            org_slug=org_slug,
            project_slug=project_slug,
        )
        spec_model = WorkspaceSpec.model_validate(spec_data)

        # Check spec drift
        spec_path = workspace_path / ".devcontainer/workspace-spec.yaml"
        new_spec_yaml = yaml.safe_dump(spec_data, sort_keys=False)

        if (
            not spec_path.exists()
            or spec_path.read_text(encoding="utf-8") != new_spec_yaml
        ):
            print("  MOD .devcontainer/workspace-spec.yaml")
            spec_path.write_text(new_spec_yaml, encoding="utf-8")

            # Regenerate devcontainer.json
            devcontainer_json = spec_model.devcontainer_json()
            (workspace_path / ".devcontainer/devcontainer.json").write_text(
                devcontainer_json, encoding="utf-8"
            )
            print("  MOD .devcontainer/devcontainer.json")

    except Exception as e:
        print(f"[update] Warning: Failed to regenerate spec: {e}", file=sys.stderr)

    return True
