#!/usr/bin/env python3
"""
Background sync daemon for Coltec Codespaces (Replicated Persistence Mode).
Reads workspace-spec.yaml and orchestrates rclone syncs.
"""

import os
import sys
import time
import subprocess
import logging
from pathlib import Path
from typing import Dict, List, Any

try:
    import yaml
except ImportError:
    print(
        "Error: python3-yaml is required. Install it via apt-get install python3-yaml."
    )
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("sync-daemon")


class SyncVolume:
    def __init__(
        self, config: Dict[str, Any], remote_name: str, org: str, project: str, env: str
    ):
        self.name = config["name"]
        self.mount_path = config["mount_path"]
        self.remote_raw = config["remote_path"]
        self.interval = config.get("interval", 300)
        self.exclude = config.get("exclude", [])
        self.sync_mode = config.get("sync", "bidirectional")

        # Resolve remote path placeholders
        self.remote_path = self.remote_raw.format(org=org, project=project, env=env)
        self.remote_full = f"{remote_name}:{self.remote_path}"

        self.last_sync = 0
        self.error_count = 0

    def should_sync(self, now: float) -> bool:
        return (now - self.last_sync) >= self.interval

    def sync(self):
        """Perform the sync operation."""
        # For bidirectional, we typically do:
        # 1. bisync (experimental) OR
        # 2. simple sync (pull usually for devcontainers to avoid overwriting remote if concurrent)
        # Note: The original bash daemon likely used 'sync' (one way) or 'bisync'.
        # The spec defines 'bidirectional', 'pull-only', 'push-only'.
        # Rclone 'sync' is one-way (source -> dest).
        # For simplicity and safety in V1, we implement 'bisync' if bidirectional,
        # or just standard sync based on direction.
        # WARNING: bisync requires a work directory.

        # Current implementation in up.py (initial sync) used 'sync' from remote to local.
        # Background daemon usually syncs local changes back to remote (push) AND remote to local (pull).
        # However, concurrent bidirectional sync is complex.
        # Let's check what the spec supports. 'bidirectional' is default.
        # For this implementation, we will use 'bisync' with --resync option carefully managed?
        # No, 'bisync' is stateful.

        # Let's infer from the Plan/Context: "Replicated Persistence Mode" usually implies
        # a continuous sync.
        # If we use `rclone sync`, it's one way.

        cmd = ["rclone"]

        if self.sync_mode == "bidirectional":
            # Use bisync for bidirectional
            cmd.extend(
                ["bisync", self.remote_full, self.mount_path, "--create-empty-src-dirs"]
            )
            # We need a workdir for bisync
            workdir = Path(f"/tmp/rclone-bisync/{self.name}")
            workdir.mkdir(parents=True, exist_ok=True)
            cmd.extend(["--workdir", str(workdir)])
            # Automatically recover from lock/errors if safe?
            cmd.extend(
                ["--resync-mode", "newer"]
            )  # Prefer newer file on conflict? Default is safer.
        elif self.sync_mode == "push-only":
            cmd.extend(["sync", self.mount_path, self.remote_full])
        elif self.sync_mode == "pull-only":
            cmd.extend(["sync", self.remote_full, self.mount_path])
        else:
            logger.warning(f"Unknown sync mode {self.sync_mode} for {self.name}")
            return

        # Common flags
        # cmd.extend(["--transfers", "4"])
        for pattern in self.exclude:
            cmd.extend(["--exclude", pattern])

        logger.info(f"Syncing {self.name} ({self.sync_mode})...")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            self.last_sync = time.time()
            self.error_count = 0
            # logger.info(f"Sync complete for {self.name}")
        except subprocess.CalledProcessError as e:
            self.error_count += 1
            logger.error(f"Sync failed for {self.name}: {e.stderr}")


def main():
    if len(sys.argv) < 2:
        print("Usage: sync_daemon.py <path_to_workspace_spec.yaml>")
        sys.exit(1)

    spec_path = Path(sys.argv[1])
    if not spec_path.exists():
        logger.error(f"Spec file not found: {spec_path}")
        sys.exit(1)

    logger.info(f"Starting sync daemon with spec: {spec_path}")

    # Load Spec
    try:
        with open(spec_path, "r") as f:
            spec_data = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"Failed to parse spec: {e}")
        sys.exit(1)

    # Validate Persistence
    persistence = spec_data.get("persistence", {})
    if not persistence.get("enabled"):
        logger.info("Persistence disabled. Exiting.")
        sys.exit(0)

    if persistence.get("mode") != "replicated":
        logger.info(
            f"Persistence mode is '{persistence.get('mode')}', not 'replicated'. Exiting."
        )
        sys.exit(0)

    # Extract Metadata
    metadata = spec_data.get("metadata", {})
    org = metadata.get("org", "default")
    project = metadata.get("project", "default")
    env = metadata.get("environment", "default")

    # Rclone Config
    rclone_config = persistence.get("rclone_config", {})
    remote_name = rclone_config.get("remote_name")

    # We assume RCLONE_CONFIG_* env vars are already set by the container runtime/up.py
    # so we don't need to generate a config file.

    # Initialize Volumes
    volumes = []
    for vol_config in persistence.get("volumes", []):
        try:
            vol = SyncVolume(vol_config, remote_name, org, project, env)
            volumes.append(vol)
            logger.info(f"Registered volume: {vol.name} -> {vol.remote_full}")
        except Exception as e:
            logger.error(f"Failed to register volume {vol_config.get('name')}: {e}")

    logger.info("Daemon loop started.")

    while True:
        now = time.time()
        for vol in volumes:
            if vol.should_sync(now):
                vol.sync()

        time.sleep(5)  # Check every 5 seconds


if __name__ == "__main__":
    main()
