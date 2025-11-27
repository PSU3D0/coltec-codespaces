"""CLI for rendering Coltec workspace specs into devcontainer artifacts."""

from __future__ import annotations

import argparse
import json
import sys
import shutil
import subprocess
from pathlib import Path
from typing import Tuple, Optional, List

import yaml

from .spec import SpecBundle, WorkspaceSpec
from .validate import validate_workspace_layout
from .provision import provision_workspace, _slugify
from .manifest import load_manifest
from .storage import (
    load_storage_mapping,
    load_storage_config,
    StorageMapping,
    validate_mounts_match_spec,
    ensure_env_vars_present,
    JuiceFSCommands,
    juicefs_status,
    juicefs_get_uuid,
    juicefs_format,
)
from .spec import StorageConfig


def _load_spec_object(path: Path) -> Tuple[WorkspaceSpec | SpecBundle, bool]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not data:
        raise SystemExit(f"Spec file {path} is empty")
    if "workspaces" in data:
        return SpecBundle.model_validate(data), True
    return WorkspaceSpec.model_validate(data), False


def _select_workspace(
    obj: WorkspaceSpec | SpecBundle, name: str | None
) -> WorkspaceSpec:
    if isinstance(obj, WorkspaceSpec):
        if name and name != obj.name:
            raise SystemExit(
                f"Spec describes workspace '{obj.name}', but '--workspace {name}' was provided"
            )
        return obj

    if not name:
        raise SystemExit("Multiple workspaces defined; specify --workspace <name>")

    for workspace in obj.workspaces:
        if workspace.name == name:
            return workspace
    raise SystemExit(f"Workspace '{name}' not found in bundle")


def cmd_render(args: argparse.Namespace) -> None:
    spec_path = Path(args.spec).expanduser().resolve()
    spec_obj, is_bundle = _load_spec_object(spec_path)
    workspace = _select_workspace(spec_obj, args.workspace)
    payload = workspace.render_devcontainer()

    if args.format == "json":
        output = json.dumps(payload, indent=args.indent, sort_keys=True)
    elif args.format == "yaml":
        output = yaml.safe_dump(payload, sort_keys=False)
    else:
        raise SystemExit(f"Unsupported format: {args.format}")

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"Wrote devcontainer to {out_path}")
    else:
        print(output)

    if args.print_meta:
        meta = workspace.metadata
        print("\n[workspace-metadata]")
        print(f"name: {workspace.name}")
        print(f"version: {workspace.version}")
        print(f"org: {meta.org}")
        print(f"project: {meta.project}")
        print(f"environment: {meta.environment}")
        if meta.tags:
            print("tags: " + ", ".join(meta.tags))


def cmd_validate_spec(args: argparse.Namespace) -> None:
    spec_path = Path(args.spec).expanduser().resolve()
    spec_obj, is_bundle = _load_spec_object(spec_path)
    if isinstance(spec_obj, WorkspaceSpec):
        print(f"Workspace '{spec_obj.name}' validated successfully")
    else:
        names = ", ".join(ws.name for ws in spec_obj.workspaces)
        print(f"Validated bundle with workspaces: {names}")


def cmd_list(args: argparse.Namespace) -> None:
    spec_path = Path(args.spec).expanduser().resolve()
    spec_obj, _ = _load_spec_object(spec_path)
    if isinstance(spec_obj, WorkspaceSpec):
        print(spec_obj.name)
        return
    for workspace in spec_obj.workspaces:
        print(workspace.name)


def cmd_workspace_new(args: argparse.Namespace) -> None:
    # We assume args.repo_root is provided or we try to guess?
    # The caller (Nexus scripts) should ideally provide --repo-root if it knows it.
    # Otherwise we fallback to CWD or fail.

    repo_root = Path(args.repo_root or ".").resolve()

    # Simple inputs prompt logic if missing (minimal version for now, assuming flags mostly)
    # If flags are missing, we can fail or prompt. For library purity, failing/requiring args is cleaner,
    # but to maintain parity with the interactive script, we might need to implement interactive prompts here or keep them in the caller.
    # Given the "Consolidate" directive, the logic should move here.
    # BUT: Interactive prompts are awkward in a library CLI.
    # DECISION: The CLI command `workspace new` will support interactive prompts if args are missing.

    def _prompt(text: str, default: Optional[str] = None) -> str:
        p = f"{text} [{default}]: " if default else f"{text}: "
        v = input(p).strip()
        return v or (default or "")

    def _prompt_choice(
        text: str, choices: list[str], default: Optional[str] = None
    ) -> str:
        c_str = "/".join(choices)
        p = f"{text} ({c_str}) [{default}]: " if default else f"{text} ({c_str}): "
        while True:
            v = input(p).strip().lower()
            if not v and default:
                return default
            if v in choices:
                return v
            print(f"Invalid choice. Options: {', '.join(choices)}")

    asset = args.asset
    if not asset:
        asset = _prompt("Asset repo URL or path")
        if not asset:
            print("Error: Asset repo is required.")
            sys.exit(1)

    asset_path = None
    if not (
        asset.startswith("git@")
        or asset.startswith("https://")
        or asset.startswith("http://")
    ):
        p = Path(asset).expanduser().resolve()
        if p.exists():
            asset_path = p

    org_slug = args.org
    if not org_slug:
        org_slug = _slugify(_prompt("Org slug", default="coltec"))

    project_slug = args.project
    if not project_slug:
        project_slug = _slugify(_prompt("Project slug", default=Path(asset).stem))

    env_name = args.environment
    if not env_name:
        default_env = f"{project_slug}-dev"
        env_name = _slugify(_prompt("Environment name", default=default_env))

    p_type = args.type
    if not p_type:
        p_type = _prompt_choice(
            "Project type",
            ["python", "node", "rust", "monorepo", "other"],
            default="python",
        )

    branch = args.branch

    create_remote = args.create_remote

    # Handle local asset repo bootstrap if needed
    def _maybe_init_local_asset(path: Path) -> None:
        if (path / ".git").exists():
            return
        if args.yes:
            print(f"Initializing git repo in {path} (non-interactive --yes)")
            subprocess.run(["git", "init"], check=True, cwd=path)
            return
        choice = _prompt_choice(
            f"Local asset path {path} has no .git. Initialize git repo?",
            ["y", "n"],
            default="y",
        )
        if choice != "y":
            print("Cannot proceed without a git repo for the asset.")
            sys.exit(1)
        subprocess.run(["git", "init"], check=True, cwd=path)
        if shutil.which("gh"):
            if (
                _prompt_choice(
                    "Create a GitHub repo for the asset via gh?",
                    ["y", "n"],
                    default="n",
                )
                == "y"
            ):
                repo_name = _prompt("GitHub repo name", default=path.name)
                try:
                    subprocess.run(
                        ["gh", "repo", "create", repo_name, "--private", "--confirm"],
                        check=True,
                        cwd=path,
                    )
                    subprocess.run(
                        [
                            "git",
                            "remote",
                            "add",
                            "origin",
                            f"git@github.com:{repo_name}.git",
                        ],
                        check=False,
                        cwd=path,
                    )
                    print(f"Linked asset repo to git@github.com:{repo_name}.git")
                except subprocess.CalledProcessError:
                    print(
                        "Warning: gh repo create failed; continuing without remote",
                        file=sys.stderr,
                    )
        else:
            print("gh CLI not available; skipping remote creation")

    if asset_path:
        _maybe_init_local_asset(asset_path)

    if not args.yes:
        if not create_remote:
            # If flag wasn't passed, ask interactively
            cr_choice = _prompt_choice(
                "Create private GitHub repo for workspace?", ["y", "n"], default="n"
            )
            create_remote = cr_choice == "y"

        confirm = _prompt_choice("Proceed?", ["y", "n"], default="y")
        if confirm != "y":
            sys.exit(0)

    try:
        provision_workspace(
            repo_root=repo_root,
            asset_input=asset,
            org_slug=org_slug,
            project_slug=project_slug,
            environment_name=env_name,
            project_type=p_type,
            asset_branch=branch,
            create_remote=create_remote,
            gh_org=args.gh_org,
            gh_name=args.gh_name,
            # If manifest arg is provided, use it, otherwise None (default)
            manifest_path=Path(args.manifest) if args.manifest else None,
            templates_dir=Path(args.templates_root).resolve()
            if args.templates_root
            else None,
            template_overlays=[
                Path(p).resolve() for p in (args.template_overlay or [])
            ],
        )
    except Exception as e:
        print(f"Error provisioning workspace: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_workspace_validate(args: argparse.Namespace) -> None:
    repo_root = Path(args.repo_root or ".").resolve()
    target = Path(args.target).resolve()

    success = validate_workspace_layout(
        workspace_path=target,
        repo_root=repo_root,
        manifest_path=Path(args.manifest) if args.manifest else None,
    )
    sys.exit(0 if success else 1)


def _workspace_paths_from_manifest(manifest_path: Path, repo_root: Path) -> List[Path]:
    manifest_data = load_manifest(manifest_path)
    targets: List[Path] = []
    for org_slug, org_data in (manifest_data.get("manifest") or {}).items():
        project_dir = org_data.get("project_dir") or org_slug
        for _, project_data in (org_data.get("projects") or {}).items():
            for env in project_data.get("environments") or []:
                env_name = env.get("name")
                rel = (
                    env.get("workspace_path") or f"codespaces/{project_dir}/{env_name}"
                )
                targets.append((repo_root / rel).resolve())
    return targets


def _resolve_target(path_str: str, repo_root: Path) -> Path:
    p = Path(path_str)
    if not p.is_absolute():
        p = (repo_root / path_str).resolve()
    return p


def cmd_workspace_update(args: argparse.Namespace) -> None:
    repo_root = Path(args.repo_root or ".").resolve()
    manifest_path = (
        Path(args.manifest)
        if args.manifest
        else (repo_root / "codespaces/manifest.yaml")
    )

    targets: List[Path]
    if args.target:
        targets = [_resolve_target(args.target, repo_root)]
    else:
        targets = _workspace_paths_from_manifest(manifest_path, repo_root)
        if not targets:
            print(
                f"Error: No workspaces found in manifest {manifest_path}",
                file=sys.stderr,
            )
            sys.exit(1)

    any_changed = False

    try:
        from .provision import update_workspace

        for target in targets:
            if not target.exists():
                print(
                    f"Warning: Target workspace '{target}' does not exist; skipping.",
                    file=sys.stderr,
                )
                continue

            changed = update_workspace(
                workspace_path=target,
                repo_root=repo_root,
                manifest_path=manifest_path,
                templates_dir=Path(args.templates_root).resolve()
                if args.templates_root
                else None,
                template_overlays=[
                    Path(p).resolve() for p in (args.template_overlay or [])
                ],
                dry_run=args.dry_run,
                force=args.force,
            )
            any_changed = any_changed or changed

        if not any_changed:
            print("No changes applied.")
    except Exception as e:
        print(f"Error updating workspace: {e}", file=sys.stderr)
        sys.exit(1)


def _storage_targets(
    repo_root: Path,
    manifest_path: Path,
    mapping: StorageMapping,
    workspace: Optional[str],
) -> dict[str, Path]:
    if workspace:
        if workspace not in mapping.workspaces:
            raise RuntimeError(f"Workspace '{workspace}' not found in mapping")
        # Need manifest to locate actual path
        manifest = load_manifest(manifest_path)
        for org_slug, org_data in (manifest.get("manifest") or {}).items():
            project_dir = org_data.get("project_dir") or org_slug
            for project_slug, project_data in (org_data.get("projects") or {}).items():
                for env in project_data.get("environments") or []:
                    env_name = env.get("name")
                    key = f"{org_slug}/{project_slug}/{env_name}"
                    if key == workspace:
                        rel = (
                            env.get("workspace_path")
                            or f"codespaces/{project_dir}/{env_name}"
                        )
                        return {key: (repo_root / rel).resolve()}
        raise RuntimeError(f"Workspace '{workspace}' not found in manifest")

    manifest = load_manifest(manifest_path)
    targets: dict[str, Path] = {}
    for org_slug, org_data in (manifest.get("manifest") or {}).items():
        project_dir = org_data.get("project_dir") or org_slug
        for project_slug, project_data in (org_data.get("projects") or {}).items():
            for env in project_data.get("environments") or []:
                env_name = env.get("name")
                key = f"{org_slug}/{project_slug}/{env_name}"
                if key in mapping.workspaces:
                    rel = (
                        env.get("workspace_path")
                        or f"codespaces/{project_dir}/{env_name}"
                    )
                    targets[key] = (repo_root / rel).resolve()
    return targets


def _storage_env(mapping: StorageMapping) -> dict:
    import os

    env = dict(os.environ)

    # Normalization / Fallbacks
    # 1. Metadata DSN
    dsn_var = mapping.metadata_dsn_env
    if not env.get(dsn_var):
        # Try fallback to JUICEFS_METADATA_URI if standard DSN is missing
        if env.get("JUICEFS_METADATA_URI"):
            env[dsn_var] = env["JUICEFS_METADATA_URI"]

    # Fix postgres scheme (postgresql:// -> postgres://)
    if env.get(dsn_var) and env[dsn_var].startswith("postgresql://"):
        env[dsn_var] = env[dsn_var].replace("postgresql://", "postgres://", 1)

    required = [
        mapping.metadata_dsn_env,
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
    ]
    if mapping.s3_endpoint_env:
        required.append(mapping.s3_endpoint_env)
    ensure_env_vars_present(env, required)
    return env


def cmd_storage_validate(args: argparse.Namespace) -> None:
    repo_root = Path(args.repo_root or ".").resolve()
    mapping_path = Path(args.mapping).resolve()
    mapping = load_storage_mapping(mapping_path)
    env = _storage_env(mapping)
    manifest_path = (
        Path(args.manifest)
        if args.manifest
        else repo_root / "codespaces" / "manifest.yaml"
    )
    targets = _storage_targets(repo_root, manifest_path, mapping, args.workspace)

    if not targets:
        print("No workspaces to validate (mapping/manifest intersection empty).")
        return

    for key, ws_path in targets.items():
        entry = mapping.workspaces.get(key)
        if not entry:
            print(f"[validate] Warning: {key} not in mapping")
            continue
        validate_mounts_match_spec(ws_path, entry)
        print(f"[validate] {key}: mounts match and env present.")


def cmd_storage_provision(args: argparse.Namespace) -> None:
    repo_root = Path(args.repo_root or ".").resolve()
    mapping_path = Path(args.mapping).resolve()
    mapping = load_storage_mapping(mapping_path)
    env = _storage_env(mapping)
    manifest_path = (
        Path(args.manifest)
        if args.manifest
        else repo_root / "codespaces" / "manifest.yaml"
    )
    targets = _storage_targets(repo_root, manifest_path, mapping, args.workspace)
    if not targets:
        print("No workspaces to provision (mapping/manifest intersection empty).")
        return

    commands = JuiceFSCommands()
    for key, _ in targets.items():
        entry = mapping.workspaces[key]
        dsn = env[mapping.metadata_dsn_env]
        endpoint = env.get(mapping.s3_endpoint_env or "", "") or None
        ok = juicefs_status(commands, dsn, env)
        uuid = None

        if not ok:
            if not args.format:
                raise RuntimeError(
                    f"{key}: metadata not formatted; re-run with --format to initialize"
                )
            # Format returns UUID now (if I updated format to return it, but I added get_uuid separate)
            # Actually, I updated format to return it, but let's use get_uuid for consistency
            # or rely on format return if I kept that change.
            # I kept the change to juicefs_format returning Optional[str].
            uuid = juicefs_format(
                commands,
                dsn=dsn,
                bucket=mapping.bucket,
                access_key=env["S3_ACCESS_KEY_ID"],
                secret_key=env["S3_SECRET_ACCESS_KEY"],
                endpoint=endpoint,
                filesystem=mapping.filesystem,
                env=env,
            )
            print(f"[provision] {key}: formatted JuiceFS metadata (UUID: {uuid})")
        else:
            print(f"[provision] {key}: metadata already initialized")
            # Fetch UUID for existing FS
            uuid = juicefs_get_uuid(commands, dsn, env)

        # Persist UUID to mapping if present
        if uuid and args.mapping:
            try:
                current_mapping = load_storage_mapping(mapping_path)
                if key in current_mapping.workspaces:
                    changed = False
                    for m in current_mapping.workspaces[key].mounts:
                        if m.juicefs_uuid != uuid:
                            m.juicefs_uuid = uuid
                            changed = True

                    if changed:
                        data = current_mapping.model_dump(
                            exclude_none=True, mode="json"
                        )
                        mapping_path.write_text(
                            yaml.safe_dump(data, sort_keys=False), encoding="utf-8"
                        )
                        print(f"[provision] Updated {mapping_path} with UUID {uuid}")
            except Exception as e:
                print(f"Warning: Failed to persist UUID to mapping: {e}")

        if args.mount:
            print(
                f"[provision] {key}: mount/umount not supported with docker plugin integration."
            )


def cmd_storage_generate(args: argparse.Namespace) -> None:
    from .storage import generate_storage_mapping

    repo_root = Path(args.repo_root or ".").resolve()
    manifest_path = (
        Path(args.manifest)
        if args.manifest
        else repo_root / "codespaces" / "manifest.yaml"
    )
    output_path = Path(args.output).resolve()

    mapping = generate_storage_mapping(
        repo_root=repo_root,
        manifest_path=manifest_path,
        bucket=args.bucket,
        filesystem=args.filesystem,
        metadata_dsn_env=args.metadata_dsn_env,
        s3_endpoint_env=args.s3_endpoint_env,
        root_prefix=args.root_prefix,
    )

    # model_dump(exclude_none=True) to keep it clean, but ensure defaults are handled
    data = mapping.model_dump(exclude_none=True, mode="json")
    yaml_str = yaml.safe_dump(data, sort_keys=False)
    output_path.write_text(yaml_str, encoding="utf-8")
    print(f"Generated storage mapping at {output_path}")


def cmd_storage_config_show(args: argparse.Namespace) -> int:
    """Show storage configuration."""
    config_path = Path(args.config).resolve()
    try:
        config = load_storage_config(config_path)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1

    # Display config details
    print(f"Storage Config: {config_path}")
    print(f"  Version: {config.version}")
    print(f"  Remote: {config.rclone.remote_name}")
    print(f"  Type: {config.rclone.type}")
    if config.rclone.options:
        print("  Options:")
        for key, val in config.rclone.options.items():
            # Mask secrets
            display_val = "***" if "secret" in key.lower() or "key" in key.lower() else val
            print(f"    {key}: {display_val}")

    if config.global_volumes:
        print(f"  Global volumes: {len(config.global_volumes)}")
        for vol in config.global_volumes:
            print(f"    - {vol.name}: {vol.remote_path} -> {vol.mount_path}")

    if config.projects:
        print(f"  Projects: {list(config.projects.keys())}")
        for proj, vols in config.projects.items():
            print(f"    {proj}: {len(vols)} volumes")

    return 0


def cmd_storage_config_validate(args: argparse.Namespace) -> int:
    """Validate storage configuration."""
    config_path = Path(args.config).resolve()
    try:
        config = load_storage_config(config_path)
        print(f"Config {config_path} is valid (version {config.version})")
        return 0
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Validation error: {e}", file=sys.stderr)
        return 1


def cmd_storage_volume_list(args: argparse.Namespace) -> int:
    """List Docker volumes for persistence."""
    scope = getattr(args, "scope", None)

    # Use docker volume ls to list volumes
    cmd = ["docker", "volume", "ls", "--format", "{{.Name}}"]
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Error listing volumes: {result.stderr}", file=sys.stderr)
        return 1

    volumes = [v.strip() for v in result.stdout.strip().split("\n") if v.strip()]

    # Filter by scope if specified
    if scope:
        prefix_map = {
            "global": "g-",
            "project": "p-",
            "environment": "e-",
        }
        prefix = prefix_map.get(scope)
        if prefix:
            volumes = [v for v in volumes if v.startswith(prefix)]

    if not volumes:
        print("No persistence volumes found.")
        return 0

    print("Persistence volumes:")
    for vol in sorted(volumes):
        # Determine scope from prefix
        if vol.startswith("g-"):
            vol_scope = "global"
        elif vol.startswith("p-"):
            vol_scope = "project"
        elif vol.startswith("e-"):
            vol_scope = "environment"
        else:
            vol_scope = "other"
        print(f"  [{vol_scope}] {vol}")

    return 0


def cmd_storage_seed(args: argparse.Namespace) -> int:
    """Seed a Docker volume with data from remote storage."""
    volume_name = args.volume
    remote_path = args.remote
    force = getattr(args, "force", False)

    # Create volume if it doesn't exist
    print(f"[seed] Ensuring volume '{volume_name}' exists...")
    create_cmd = ["docker", "volume", "create", volume_name]
    result = subprocess.run(create_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error creating volume: {result.stderr}", file=sys.stderr)
        return 1

    # Check if volume is already initialized (has marker file)
    if not force:
        check_cmd = [
            "docker", "run", "--rm",
            "-v", f"{volume_name}:/data:ro",
            "alpine:latest",
            "test", "-f", "/data/.coltec-initialized",
        ]
        check_result = subprocess.run(check_cmd, capture_output=True, text=True)
        if check_result.returncode == 0:
            print(f"[seed] Volume '{volume_name}' already initialized. Use --force to reseed.")
            return 0

    # Sync data from remote using rclone in docker
    print(f"[seed] Syncing from '{remote_path}' to volume '{volume_name}'...")
    sync_cmd = [
        "docker", "run", "--rm",
        "-v", f"{volume_name}:/data",
        "-e", "RCLONE_CONFIG_R2COLTEC_TYPE=s3",
        "-e", "RCLONE_CONFIG_R2COLTEC_PROVIDER=Cloudflare",
        "-e", "RCLONE_CONFIG_R2COLTEC_ACCESS_KEY_ID",
        "-e", "RCLONE_CONFIG_R2COLTEC_SECRET_ACCESS_KEY",
        "-e", "RCLONE_CONFIG_R2COLTEC_ENDPOINT",
        "rclone/rclone:latest",
        "sync", remote_path, "/data",
        "--verbose",
    ]
    sync_result = subprocess.run(sync_cmd, capture_output=True, text=True)
    if sync_result.returncode != 0:
        print(f"Error syncing: {sync_result.stderr}", file=sys.stderr)
        return 1

    # Mark volume as initialized
    mark_cmd = [
        "docker", "run", "--rm",
        "-v", f"{volume_name}:/data",
        "alpine:latest",
        "sh", "-c", f"date -Iseconds > /data/.coltec-initialized",
    ]
    mark_result = subprocess.run(mark_cmd, capture_output=True, text=True)
    if mark_result.returncode != 0:
        print(f"Warning: Failed to mark volume as initialized: {mark_result.stderr}", file=sys.stderr)

    print(f"[seed] Successfully seeded volume '{volume_name}' from '{remote_path}'")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Coltec workspace tooling CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- Spec Commands ---
    render = subparsers.add_parser(
        "render", help="Render a devcontainer.json from a spec"
    )
    render.add_argument("spec", help="Path to a workspace spec (YAML or JSON)")
    render.add_argument("--workspace", help="Workspace name")
    render.add_argument("-o", "--output", help="Optional output path")
    render.add_argument("--format", choices=("json", "yaml"), default="json")
    render.add_argument("--indent", type=int, default=2)
    render.add_argument("--print-meta", action="store_true")
    render.set_defaults(func=cmd_render)

    validate = subparsers.add_parser(
        "validate", help="Validate that a spec file is well-formed"
    )
    validate.add_argument("spec", help="Path to the spec file")
    validate.set_defaults(func=cmd_validate_spec)

    list_cmd = subparsers.add_parser("list", help="List workspaces in a spec bundle")
    list_cmd.add_argument("spec", help="Path to the spec file")
    list_cmd.set_defaults(func=cmd_list)

    # --- Workspace Commands ---
    ws_parser = subparsers.add_parser("workspace", help="Manage workspaces")
    ws_subs = ws_parser.add_subparsers(dest="ws_command", required=True)

    # workspace new
    ws_new = ws_subs.add_parser("new", help="Provision a new workspace")
    ws_new.add_argument("asset", nargs="?", help="Asset repo URL or path")
    ws_new.add_argument("--repo-root", help="Path to Coltec control plane root")
    ws_new.add_argument("--manifest", help="Path to manifest.yaml")
    ws_new.add_argument("--org", help="Org slug")
    ws_new.add_argument("--project", help="Project slug")
    ws_new.add_argument("--environment", help="Environment name")
    ws_new.add_argument("--type", help="Project type")
    ws_new.add_argument("--branch", default="main", help="Asset branch")
    ws_new.add_argument("--yes", action="store_true", help="Skip confirmation")
    ws_new.add_argument(
        "--create-remote", action="store_true", help="Create remote GH repo"
    )
    ws_new.add_argument("--gh-org", help="GitHub Org override")
    ws_new.add_argument("--gh-name", help="GitHub Repo Name override")
    ws_new.add_argument(
        "--templates-root",
        help="Override base templates directory (defaults to <repo_root>/templates)",
    )
    ws_new.add_argument(
        "--template-overlay",
        action="append",
        help="Additional template directory to overlay (expects workspace_scaffold/ inside). Repeatable.",
    )
    ws_new.set_defaults(func=cmd_workspace_new)

    # workspace validate
    ws_val = ws_subs.add_parser("validate", help="Validate an existing workspace")
    ws_val.add_argument("--target", default=".", help="Workspace path to validate")
    ws_val.add_argument("--repo-root", help="Path to Coltec control plane root")
    ws_val.add_argument("--manifest", help="Path to manifest.yaml")
    ws_val.set_defaults(func=cmd_workspace_validate)

    # workspace update
    ws_up = ws_subs.add_parser("update", help="Update workspace templates from source")
    ws_up.add_argument(
        "--target",
        help="Workspace path to update (defaults to all manifest entries)",
    )
    ws_up.add_argument("--repo-root", help="Path to Coltec control plane root")
    ws_up.add_argument("--manifest", help="Path to manifest.yaml")
    ws_up.add_argument(
        "--templates-root",
        help="Override base templates directory (defaults to <repo_root>/templates)",
    )
    ws_up.add_argument(
        "--template-overlay",
        action="append",
        help="Additional template directory to overlay. Repeatable.",
    )
    ws_up.add_argument(
        "--check",
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Dry-run/diff only",
    )
    ws_up.add_argument("--force", action="store_true", help="Apply changes")
    ws_up.set_defaults(func=cmd_workspace_update)

    # storage commands
    storage = subparsers.add_parser("storage", help="Storage operations")
    storage_subs = storage.add_subparsers(dest="storage_command", required=True)

    st_gen = storage_subs.add_parser(
        "generate", help="Generate storage mapping from workspace specs"
    )
    st_gen.add_argument("--repo-root", help="Path to Coltec control plane root")
    st_gen.add_argument("--manifest", help="Path to manifest.yaml")
    st_gen.add_argument(
        "-o", "--output", default="persistence-mappings.yaml", help="Output path"
    )
    st_gen.add_argument("--bucket", required=True, help="S3 Bucket name")
    st_gen.add_argument("--filesystem", required=True, help="JuiceFS filesystem name")
    st_gen.add_argument(
        "--root-prefix", default="workspaces", help="Root prefix in bucket"
    )
    st_gen.add_argument(
        "--metadata-dsn-env", default="JUICEFS_DSN", help="Env var for Metadata DSN"
    )
    st_gen.add_argument(
        "--s3-endpoint-env",
        default="JUICEFS_S3_ENDPOINT",
        help="Env var for S3 Endpoint",
    )
    st_gen.set_defaults(func=cmd_storage_generate)

    st_validate = storage_subs.add_parser(
        "validate", help="Validate storage mapping and env for workspaces"
    )
    st_validate.add_argument("--repo-root", help="Path to Coltec control plane root")
    st_validate.add_argument("--manifest", help="Path to manifest.yaml")
    st_validate.add_argument(
        "--mapping",
        help="Path to storage mapping file",
        default="persistence-mappings.yaml",
    )
    st_validate.add_argument(
        "--workspace",
        help="Workspace key to validate (default all from manifest)",
    )
    st_validate.set_defaults(func=cmd_storage_validate)

    st_prov = storage_subs.add_parser(
        "provision", help="Provision/format JuiceFS per mapping"
    )
    st_prov.add_argument("--repo-root", help="Path to Coltec control plane root")
    st_prov.add_argument("--manifest", help="Path to manifest.yaml")
    st_prov.add_argument(
        "--mapping",
        help="Path to storage mapping file",
        default="persistence-mappings.yaml",
    )
    st_prov.add_argument(
        "--workspace",
        help="Workspace key to operate on (default all from manifest)",
    )
    st_prov.add_argument(
        "--format",
        action="store_true",
        help="Allow formatting metadata if missing",
    )
    st_prov.add_argument(
        "--mount",
        action="store_true",
        help="Mount after format/status to verify access",
    )
    st_prov.add_argument(
        "--mountpoint",
        default="/mnt/coltec-fs",
        help="Mountpoint to use when --mount is set",
    )
    st_prov.add_argument(
        "--cache-size-mb",
        type=int,
        default=1024,
        help="JuiceFS cache size when mounting",
    )
    st_prov.set_defaults(func=cmd_storage_provision)

    # storage config subcommand group
    st_config = storage_subs.add_parser(
        "config", help="Manage V2 storage configuration"
    )
    st_config_subs = st_config.add_subparsers(dest="config_command", required=True)

    st_config_show = st_config_subs.add_parser("show", help="Show storage configuration")
    st_config_show.add_argument(
        "--config",
        required=True,
        help="Path to storage-config.yaml",
    )
    st_config_show.set_defaults(func=cmd_storage_config_show)

    st_config_validate = st_config_subs.add_parser(
        "validate", help="Validate storage configuration"
    )
    st_config_validate.add_argument(
        "--config",
        required=True,
        help="Path to storage-config.yaml",
    )
    st_config_validate.set_defaults(func=cmd_storage_config_validate)

    # storage volume subcommand group
    st_volume = storage_subs.add_parser(
        "volume", help="Manage persistence volumes"
    )
    st_volume_subs = st_volume.add_subparsers(dest="volume_command", required=True)

    st_volume_list = st_volume_subs.add_parser("list", help="List persistence volumes")
    st_volume_list.add_argument(
        "--scope",
        choices=["global", "project", "environment"],
        help="Filter by volume scope",
    )
    st_volume_list.set_defaults(func=cmd_storage_volume_list)

    # storage seed command
    st_seed = storage_subs.add_parser(
        "seed", help="Seed a Docker volume with data from remote storage"
    )
    st_seed.add_argument(
        "--volume",
        required=True,
        help="Name of the Docker volume to seed",
    )
    st_seed.add_argument(
        "--remote",
        required=True,
        help="Remote path (e.g., r2coltec:bucket/path)",
    )
    st_seed.add_argument(
        "--force",
        action="store_true",
        help="Force reseed even if volume is already initialized",
    )
    st_seed.set_defaults(func=cmd_storage_seed)

    # --- Up Command ---
    from .up import cmd_up

    up_parser = subparsers.add_parser("up", help="Start a workspace devcontainer")
    up_parser.add_argument(
        "target", help="Workspace path or name (e.g. psu3d0/leap-landing-dev)"
    )
    up_parser.add_argument("--repo-root", help="Path to Coltec control plane root")
    up_parser.add_argument("--manifest", help="Path to manifest.yaml")
    up_parser.add_argument(
        "--mapping", default="persistence-mappings.yaml", help="Path to storage mapping"
    )
    up_parser.add_argument(
        "--rebuild", action="store_true",
        help="Force rebuild container (removes existing and builds without cache)"
    )
    up_parser.set_defaults(func=cmd_up)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    result = args.func(args)
    # Commands may return an exit code; treat None as success
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
