"""CLI command to start a devcontainer with host-side JuiceFS mounting."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import json
from pathlib import Path
from typing import Optional, Dict, List

from .manifest import load_manifest, find_manifest_entry
from .storage import (
    load_storage_mapping,
    load_storage_config,
    find_storage_config,
    JuiceFSCommands,
    RcloneCommands,
    juicefs_status,
    juicefs_format,
    ensure_env_vars_present,
    ensure_rclone_configured,
    docker_plugin_is_installed,
    docker_volume_exists,
    docker_volume_create,
    create_replicated_volumes,
    create_multi_scope_volumes,
    resolve_rclone_env,
)
from .spec import WorkspaceSpec
import yaml


# Re-implement _storage_env here since it was private in storage.py
def _get_storage_env(mapping) -> Dict[str, str]:
    import os

    env = dict(os.environ)

    # Normalization / Fallbacks
    dsn_var = mapping.metadata_dsn_env
    if not env.get(dsn_var):
        if env.get("JUICEFS_METADATA_URI"):
            env[dsn_var] = env["JUICEFS_METADATA_URI"]

    if env.get(dsn_var) and env[dsn_var].startswith("postgresql://"):
        env[dsn_var] = env[dsn_var].replace("postgresql://", "postgres://", 1)

    if not env.get("S3_ACCESS_KEY_ID") and env.get("CF_S3_ACCESS_KEY_ID"):
        env["S3_ACCESS_KEY_ID"] = env["CF_S3_ACCESS_KEY_ID"]

    if not env.get("S3_SECRET_ACCESS_KEY") and env.get("CF_S3_SECRET_ACCESS_KEY"):
        env["S3_SECRET_ACCESS_KEY"] = env["CF_S3_SECRET_ACCESS_KEY"]

    required = [
        mapping.metadata_dsn_env,
        "S3_ACCESS_KEY_ID",
        "S3_SECRET_ACCESS_KEY",
    ]
    if mapping.s3_endpoint_env:
        required.append(mapping.s3_endpoint_env)
    ensure_env_vars_present(env, required)
    return env


def _validate_required_env_vars(workspace_spec: Optional[WorkspaceSpec]) -> None:
    """Validate required environment variables before devcontainer boot."""
    import os

    missing = []

    # Check networking requirements
    if workspace_spec and workspace_spec.networking.enabled:
        if not os.getenv("TAILSCALE_AUTH_KEY"):
            missing.append("TAILSCALE_AUTH_KEY (required for networking)")

    # Check persistence requirements
    if workspace_spec and workspace_spec.persistence.enabled:
        mode = workspace_spec.persistence.mode

        # Common requirements for both modes
        required_persistence = [
            "S3_ACCESS_KEY_ID",
            "S3_SECRET_ACCESS_KEY",
            "JUICEFS_S3_ENDPOINT",
            "JUICEFS_BUCKET",
        ]

        # Mounted mode also needs DSN
        if mode == "mounted":
            required_persistence.append("JUICEFS_METADATA_URI")

        for var in required_persistence:
            if not os.getenv(var):
                missing.append(f"{var} (required for persistence mode: {mode})")

    if missing:
        print("[up] ERROR: Missing required environment variables:")
        for var in missing:
            print(f"  - {var}")
        print("\nHint: Ensure secrets are properly configured in fnox.toml")
        print(
            "      and run 'eval \"$(mise activate bash)\"' and 'eval \"$(fnox eval)\"'"
        )
        raise RuntimeError(
            "Missing required environment variables. Cannot proceed with devcontainer boot."
        )


def cmd_up(args: argparse.Namespace) -> None:
    repo_root = Path(args.repo_root or ".").resolve()
    target_str = args.target

    manifest_path = (
        Path(args.manifest)
        if args.manifest
        else repo_root / "codespaces" / "manifest.yaml"
    )

    if not manifest_path.exists():
        raise RuntimeError(f"Manifest not found at {manifest_path}")

    manifest_data = load_manifest(manifest_path)

    possible_path = Path(target_str)
    if not possible_path.is_absolute():
        possible_path = (repo_root / target_str).resolve()

    if possible_path.exists():
        workspace_path = possible_path
    else:
        if (repo_root / "codespaces" / target_str).exists():
            workspace_path = (repo_root / "codespaces" / target_str).resolve()
        else:
            raise RuntimeError(f"Could not resolve workspace path for '{target_str}'")

    print(f"[up] Resolved workspace: {workspace_path}")

    # Find manifest entry for org/project/env info
    entry_tuple = find_manifest_entry(manifest_data, workspace_path, repo_root)
    if not entry_tuple:
        print("[up] Warning: Workspace not found in manifest.")
        org, project, env_entry = "unknown", "unknown", {"name": "unknown"}
    else:
        org, project, env_entry = entry_tuple

    env = os.environ.copy()
    mount_args: List[str] = []

    # =========================================================================
    # LOAD WORKSPACE SPEC FIRST - This determines persistence mode
    # =========================================================================
    spec_path = workspace_path / ".devcontainer" / "workspace-spec.yaml"
    workspace_spec = None
    persistence_mode = None

    if spec_path.exists():
        try:
            spec_data = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
            workspace_spec = WorkspaceSpec.model_validate(spec_data)
            if workspace_spec.persistence.enabled:
                persistence_mode = workspace_spec.persistence.mode
                print(f"[up] Persistence mode: {persistence_mode}")
            else:
                print("[up] Persistence disabled in workspace spec")
        except Exception as e:
            print(f"[up] Warning: Failed to load workspace spec: {e}")

    # Validate required environment variables before proceeding
    _validate_required_env_vars(workspace_spec)

    # =========================================================================
    # REPLICATED MODE (V2) - Uses storage-config.yaml, NOT persistence-mappings.yaml
    # =========================================================================
    if persistence_mode == "replicated":
        if not workspace_spec:
            print("[up] Error: replicated mode requires valid workspace-spec.yaml")
            sys.exit(1)

        # Find and load storage-config.yaml
        storage_config_path = find_storage_config(repo_root)
        storage_config = None
        if storage_config_path:
            try:
                storage_config = load_storage_config(storage_config_path)
                print(f"[up] Loaded storage config from {storage_config_path}")
            except Exception as e:
                print(f"[up] Warning: Failed to load storage config: {e}")

        # Get bucket from environment variables
        bucket = env.get("RCLONE_BUCKET") or env.get("JUICEFS_BUCKET")
        if not bucket:
            bucket = "coltec-codespaces-data"  # Default
        print(f"[up] Using bucket: {bucket}")

        # Ensure S3 vars are present
        if not env.get("S3_ACCESS_KEY_ID") and env.get("JUICEFS_ACCESS_KEY_ID"):
            env["S3_ACCESS_KEY_ID"] = env["JUICEFS_ACCESS_KEY_ID"]
        if not env.get("S3_SECRET_ACCESS_KEY") and env.get("JUICEFS_SECRET_ACCESS_KEY"):
            env["S3_SECRET_ACCESS_KEY"] = env["JUICEFS_SECRET_ACCESS_KEY"]

        # Ensure rclone is configured
        try:
            ensure_rclone_configured(env)
        except RuntimeError as e:
            print(f"[up] Error: {e}")
            sys.exit(1)

        # Get rclone config from workspace spec
        rclone_config = workspace_spec.persistence.rclone_config
        if not rclone_config:
            print("[up] Error: persistence.rclone_config is missing but mode is replicated")
            sys.exit(1)

        # Prepare rclone configuration environment variables
        rclone_env_vars = resolve_rclone_env(rclone_config, env)
        env.update(rclone_env_vars)

        remote_name = rclone_config.remote_name
        env["RCLONE_REMOTE_NAME"] = remote_name
        env["RCLONE_BUCKET"] = bucket

        # Create Docker volumes using multi-scope function
        # Note: For replicated mode, mounts are already defined in devcontainer.json
        # so we don't pass --mount args to devcontainer up (unlike mounted mode)
        print(f"[up] Creating replicated volumes for {org}/{project}/{env_entry['name']}...")
        commands = JuiceFSCommands()

        # Create and seed volumes (discard mount_args since devcontainer.json has them)
        _ = create_multi_scope_volumes(
            workspace_spec=workspace_spec,
            storage_config=storage_config,
            org=org,
            project=project,
            env_name=env_entry["name"],
            bucket=bucket,
            commands=commands,
            rclone_env=rclone_env_vars,
        )

        # Perform initial sync from R2 for environment volumes
        print("[up] Checking for existing data in R2...")
        for volume in workspace_spec.persistence.volumes:
            remote_path = volume.remote_path.format(
                org=org, project=project, env=env_entry["name"]
            )
            remote_full = f"{remote_name}:{bucket}/{remote_path}"
            volume_name = f"e-{workspace_spec.name}-{volume.name}"

            print(f"[up] Initial sync for volume '{volume.name}' from {remote_full}...")

            # Check if remote path exists
            check_cmd = ["docker", "run", "--rm"]
            for k, v in rclone_env_vars.items():
                check_cmd.extend(["-e", f"{k}={v}"])
            check_cmd.extend(
                ["rclone/rclone:latest", "lsd", remote_full, "--max-depth", "1"]
            )

            check_result = subprocess.run(
                check_cmd, check=False, capture_output=True, text=True
            )

            if check_result.returncode == 0 and check_result.stdout.strip():
                print(f"[up]   Found existing data in R2, pulling...")
                sync_cmd = [
                    "docker", "run", "--rm",
                    "-v", f"{volume_name}:{volume.mount_path}",
                ]
                for k, v in rclone_env_vars.items():
                    sync_cmd.extend(["-e", f"{k}={v}"])
                sync_cmd.extend([
                    "rclone/rclone:latest",
                    "sync", remote_full, volume.mount_path,
                    "--fast-list", "--transfers", "16",
                ])
                for pattern in volume.exclude:
                    sync_cmd.extend(["--exclude", pattern])

                sync_result = subprocess.run(
                    sync_cmd, check=False, capture_output=True, text=True
                )
                if sync_result.returncode == 0:
                    print(f"[up]   ✓ Initial sync complete for '{volume.name}'")
                else:
                    print(f"[up]   ⚠ Initial sync failed: {sync_result.stderr}")
            else:
                print(f"[up]   No existing data in R2, starting fresh")

    # =========================================================================
    # MOUNTED MODE (V1) - Uses persistence-mappings.yaml (legacy JuiceFS)
    # =========================================================================
    elif persistence_mode == "mounted":
        mapping_path = Path(args.mapping).resolve()
        if not mapping_path.exists():
            print(f"[up] Error: Storage mapping not found at {mapping_path}")
            print("       Mounted mode requires persistence-mappings.yaml")
            sys.exit(1)

        mapping = load_storage_mapping(mapping_path)
        key = f"{org}/{project}/{env_entry['name']}"

        if key not in mapping.workspaces:
            print(f"[up] Error: Workspace {key} not found in persistence-mappings.yaml")
            sys.exit(1)

        ws_storage = mapping.workspaces[key]

        if not shutil.which("juicefs"):
            print("[up] Error: 'juicefs' not found in PATH. Required for initial formatting.")
            sys.exit(1)

        env = _get_storage_env(mapping)
        dsn = env[mapping.metadata_dsn_env]
        endpoint = env.get(mapping.s3_endpoint_env or "", "") or None

        commands = JuiceFSCommands()

        if not docker_plugin_is_installed(commands):
            print("[up] Error: 'juicedata/juicefs' docker plugin not found or not enabled.")
            print("       Run: docker plugin install juicedata/juicefs:1.2.1")
            sys.exit(1)

        if not juicefs_status(commands, dsn, env):
            print(f"[up] Metadata not formatted for {key}. Attempting format...")
            juicefs_format(
                commands,
                dsn=dsn,
                bucket=mapping.bucket,
                access_key=env["S3_ACCESS_KEY_ID"],
                secret_key=env["S3_SECRET_ACCESS_KEY"],
                endpoint=endpoint,
                filesystem=mapping.filesystem,
                env=env,
            )

        # Create root volume
        root_vol_name = f"csvol-{org}-{project}-{env_entry['name']}-root"

        if not docker_volume_exists(commands, root_vol_name):
            print(f"[up] Creating root volume {root_vol_name}...")
            docker_volume_create(
                commands,
                volume_name=root_vol_name,
                fs_name=mapping.filesystem,
                dsn=dsn,
                bucket=mapping.bucket,
                access_key=env["S3_ACCESS_KEY_ID"],
                secret_key=env["S3_SECRET_ACCESS_KEY"],
                subdir="/",
                env=env,
                endpoint=endpoint,
            )

        mount_args.append("--mount")
        mount_args.append(
            f"type=volume,source={root_vol_name},target=/mnt/workspace-storage"
        )

        # Generate storage-links.json for post-start.sh
        links = []
        for m in ws_storage.mounts:
            rel_path = (m.bucket_path or "").lstrip("/")
            source_path = f"/mnt/workspace-storage/{rel_path}"
            links.append({"target": m.target, "source": source_path})

        links_file = workspace_path / ".devcontainer" / "storage-links.json"
        links_file.write_text(json.dumps(links, indent=2))
        print(f"[up] Wrote storage links configuration to {links_file}")

    # =========================================================================
    # START DEVCONTAINER
    # =========================================================================
    cmd = ["devcontainer", "up", "--workspace-folder", str(workspace_path)]
    cmd.extend(mount_args)

    # Add rebuild flags if requested
    if getattr(args, "rebuild", False):
        cmd.extend(["--remove-existing-container", "--build-no-cache"])
        print("[up] Rebuild mode: removing existing container and building without cache")

    print(f"[up] Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True, env=env)
    print(f"[up] Devcontainer started.")
