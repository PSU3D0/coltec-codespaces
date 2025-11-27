#!/usr/bin/env python3
"""
Background sync daemon for Coltec Codespaces (Replicated Persistence Mode).
Reads workspace-spec.yaml and orchestrates rclone syncs.

Features:
- Configurable bidirectional (bisync) or one-way sync
- Graceful shutdown handling (emergency sync on SIGTERM)
- Prioritized volume syncing
- Robust error handling and logging
"""

import os
import sys
import time
import signal
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
    format="%(asctime)s [%(levelname)s] [sync-daemon] %(message)s",
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
        self.priority = config.get("priority", 2)
        self.exclude = config.get("exclude", [])
        self.sync_mode = config.get("sync", "bidirectional")

        # Resolve remote path placeholders
        self.remote_path = self.remote_raw.format(org=org, project=project, env=env)
        self.remote_full = f"{remote_name}:{self.remote_path}"

        self.last_sync = 0
        self.error_count = 0

    def should_sync(self, now: float) -> bool:
        return (now - self.last_sync) >= self.interval

    def sync(self, emergency: bool = False):
        """Perform the sync operation."""
        cmd = ["rclone"]

        # Flags matching the bash script for safety/performance
        common_flags = [
            "--fast-list",
            "--transfers",
            "4",
        ]

        for pattern in self.exclude:
            common_flags.extend(["--exclude", pattern])

        if self.sync_mode == "bidirectional":
            # Use bisync for bidirectional
            # Matches bash: --check-access --max-delete 10 --fast-list
            cmd.extend(
                ["bisync", self.mount_path, self.remote_full, "--create-empty-src-dirs"]
            )

            # Workdir for bisync metadata
            workdir = Path(f"/tmp/rclone-bisync/{self.name}")
            workdir.mkdir(parents=True, exist_ok=True)
            cmd.extend(["--workdir", str(workdir)])

            # Safety flags from bash script
            cmd.extend(["--check-access", "--max-delete", "10"])

            if emergency:
                # In emergency, we might want to force push?
                # Actually, bisync should just run. But if we are shutting down,
                # we want to ensure our changes get out.
                # Bisync will sync both ways. If we want to prioritize PUSH,
                # maybe we should switch to 'copy' for emergency?
                # Spec says: "Emergency sync... Sync P1 volumes first... bisync"
                # The bash script continues to use the configured mode (bisync) even in emergency.
                pass

        elif self.sync_mode in ("push-only", "one-way-push"):
            cmd.extend(["sync", self.mount_path, self.remote_full])
        elif self.sync_mode == "pull-only":
            cmd.extend(["sync", self.remote_full, self.mount_path])
        else:
            logger.warning(f"Unknown sync mode {self.sync_mode} for {self.name}")
            return

        cmd.extend(common_flags)

        log_prefix = "[EMERGENCY] " if emergency else ""
        logger.info(f"{log_prefix}Syncing {self.name} ({self.sync_mode})...")

        try:
            # Timeout logic
            timeout = 30 if emergency else 600

            subprocess.run(
                cmd, capture_output=True, text=True, check=True, timeout=timeout
            )

            self.last_sync = time.time()
            self.error_count = 0
            if emergency:
                logger.info(f"{log_prefix}Completed {self.name}")
        except subprocess.TimeoutExpired:
            logger.error(f"{log_prefix}Timed out syncing {self.name}")
            self.error_count += 1
        except subprocess.CalledProcessError as e:
            self.error_count += 1
            logger.error(f"{log_prefix}Sync failed for {self.name}: {e.stderr.strip()}")


class Daemon:
    def __init__(self, spec_path: Path):
        self.spec_path = spec_path
        self.volumes: List[SyncVolume] = []
        self.running = True

    def load_config(self):
        try:
            with open(self.spec_path, "r") as f:
                spec_data = yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Failed to parse spec: {e}")
            sys.exit(1)

        persistence = spec_data.get("persistence", {})
        if not persistence.get("enabled"):
            logger.info("Persistence disabled. Exiting.")
            sys.exit(0)

        if persistence.get("mode") != "replicated":
            logger.info(
                f"Persistence mode is '{persistence.get('mode')}', not 'replicated'. Exiting."
            )
            sys.exit(0)

        metadata = spec_data.get("metadata", {})
        org = metadata.get("org", "default")
        project = metadata.get("project", "default")
        env = metadata.get("environment", "default")

        rclone_config = persistence.get("rclone_config", {})
        remote_name = rclone_config.get("remote_name", "r2-coltec")

        self.volumes = []
        for vol_config in persistence.get("volumes", []):
            try:
                vol = SyncVolume(vol_config, remote_name, org, project, env)
                self.volumes.append(vol)
                logger.info(
                    f"Registered volume: {vol.name} -> {vol.remote_full} (P{vol.priority})"
                )
            except Exception as e:
                logger.error(f"Failed to register volume {vol_config.get('name')}: {e}")

    def emergency_sync(self, signum, frame):
        """Handle shutdown signals by syncing everything, prioritized."""
        logger.info(f"Caught signal {signum}. Starting EMERGENCY SYNC...")
        self.running = False

        start_time = time.time()
        # 90s total timeout for emergency sync (Docker gives 10s default, usually configured higher for persistence)
        # We'll assume we have some time, but act fast.

        # Sort volumes by priority (1 is highest/critical)
        sorted_vols = sorted(self.volumes, key=lambda v: v.priority)

        # Phase 1: Critical (P1)
        for vol in sorted_vols:
            if vol.priority == 1:
                vol.sync(emergency=True)

        # Phase 2: Important (P2)
        # Check if we have time left (assuming 90s safe window)
        if time.time() - start_time < 60:
            for vol in sorted_vols:
                if vol.priority == 2:
                    vol.sync(emergency=True)
        else:
            logger.warning("Skipping P2 volumes due to time constraints")

        logger.info(
            f"Emergency sync finished in {time.time() - start_time:.2f}s. Exiting."
        )
        sys.exit(0)

    def run(self):
        self.load_config()

        signal.signal(signal.SIGTERM, self.emergency_sync)
        signal.signal(signal.SIGINT, self.emergency_sync)

        logger.info("Daemon loop started (Interval: 60s)")

        while self.running:
            now = time.time()
            for vol in self.volumes:
                if vol.should_sync(now):
                    vol.sync()

            # Use smaller sleep chunks to respond to signals faster?
            # Actually python signal handlers interrupt sleep.
            time.sleep(60)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: sync_daemon.py <path_to_workspace_spec.yaml>")
        sys.exit(1)

    spec_path = Path(sys.argv[1])
    if not spec_path.exists():
        logger.error(f"Spec file not found: {spec_path}")
        sys.exit(1)

    daemon = Daemon(spec_path)
    daemon.run()
