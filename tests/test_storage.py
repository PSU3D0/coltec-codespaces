import os
import pytest
from pathlib import Path

from coltec_codespaces.storage import (
    load_storage_mapping,
    validate_mounts_match_spec,
)
from coltec_codespaces.provision import provision_workspace
from coltec_codespaces.__main__ import main as cli_main


def _write_mapping(tmp: Path, key: str, mounts: list[dict]) -> Path:
    mapping = {
        "version": 1,
        "bucket": "coltec-codespaces-data",
        "filesystem": "myjfs",
        "root_prefix": "workspaces",
        "metadata_dsn_env": "JUICEFS_DSN",
        "s3_endpoint_env": "JUICEFS_S3_ENDPOINT",
        "default_scope": "project",
        "workspaces": {
            key: {
                "org": "test-org",
                "project": "test-proj",
                "env": "env-1",
                "scope": "project",
                "mounts": mounts,
            }
        },
    }
    path = tmp / "persistence-mappings.yaml"
    path.write_text(
        __import__("yaml").safe_dump(mapping, sort_keys=False), encoding="utf-8"
    )
    return path


def test_load_mapping_autofills_bucket_path(tmp_path):
    key = "test-org/test-proj/env-1"
    mfile = _write_mapping(
        tmp_path,
        key,
        mounts=[
            {"name": "agent-context", "target": "/workspace/agent-context", "source": "agent-context", "type": "symlink"},
            {"name": "scratch", "target": "/workspace/scratch", "source": "scratch", "type": "symlink"},
        ],
    )
    mapping = load_storage_mapping(mfile)
    entry = mapping.workspaces[key]
    paths = [m.bucket_path for m in entry.mounts]
    assert paths == [
        "workspaces/test-org/test-proj/env-1/agent-context",
        "workspaces/test-org/test-proj/env-1/scratch",
    ]


def test_validate_mounts_match_spec(sandbox, mock_run, mock_git_utils, tmp_path):
    # Use mounted mode for legacy JuiceFS validation
    provision_workspace(
        repo_root=sandbox,
        asset_input="dummy",
        org_slug="test-org",
        project_slug="test-proj",
        environment_name="env-1",
        project_type="python",
        persistence_mode="mounted",
    )
    workspace_path = sandbox / "codespaces/test-org/env-1"
    key = "test-org/test-proj/env-1"
    mfile = _write_mapping(
        tmp_path,
        key,
        mounts=[
            {"name": "agent-context", "target": "/workspace/agent-context", "source": "agent-context", "type": "symlink"},
            {"name": "scratch", "target": "/workspace/scratch", "source": "scratch", "type": "symlink"},
        ],
    )
    mapping = load_storage_mapping(mfile)
    validate_mounts_match_spec(workspace_path, mapping.workspaces[key])


def test_validate_mounts_mismatch_raises(sandbox, mock_run, mock_git_utils, tmp_path):
    # Use mounted mode for legacy JuiceFS validation
    provision_workspace(
        repo_root=sandbox,
        asset_input="dummy",
        org_slug="test-org",
        project_slug="test-proj",
        environment_name="env-1",
        project_type="python",
        persistence_mode="mounted",
    )
    workspace_path = sandbox / "codespaces/test-org/env-1"
    key = "test-org/test-proj/env-1"
    mfile = _write_mapping(
        tmp_path,
        key,
        mounts=[
            {"name": "agent-context", "target": "/workspace/agent-context", "source": "agent-context", "type": "symlink"},
            {"name": "scratch", "target": "/workspace/WRONG", "source": "scratch", "type": "symlink"},
        ],
    )
    mapping = load_storage_mapping(mfile)
    with pytest.raises(RuntimeError):
        validate_mounts_match_spec(workspace_path, mapping.workspaces[key])


def test_cli_storage_validate(monkeypatch, sandbox, mock_run, mock_git_utils, tmp_path, capsys):
    # Use mounted mode for legacy JuiceFS validation
    provision_workspace(
        repo_root=sandbox,
        asset_input="dummy",
        org_slug="test-org",
        project_slug="test-proj",
        environment_name="env-1",
        project_type="python",
        persistence_mode="mounted",
    )
    key = "test-org/test-proj/env-1"
    mfile = _write_mapping(
        sandbox,
        key,
        mounts=[
            {"name": "agent-context", "target": "/workspace/agent-context", "source": "agent-context", "type": "symlink"},
            {"name": "scratch", "target": "/workspace/scratch", "source": "scratch", "type": "symlink"},
        ],
    )
    monkeypatch.setenv("JUICEFS_DSN", "dsn://test")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("JUICEFS_S3_ENDPOINT", "https://example.com")

    ret = cli_main(
        [
            "storage",
            "validate",
            "--repo-root",
            str(sandbox),
            "--mapping",
            str(mfile),
        ]
    )
    assert ret == 0
    out = capsys.readouterr()
    assert "mounts match" in out.out


@pytest.mark.skip(reason="JuiceFS mount functions removed in V2 rclone migration - needs test rewrite")
def test_cli_storage_provision_format(monkeypatch, sandbox, mock_run, mock_git_utils, tmp_path, capsys):
    provision_workspace(
        repo_root=sandbox,
        asset_input="dummy",
        org_slug="test-org",
        project_slug="test-proj",
        environment_name="env-1",
        project_type="python",
    )
    key = "test-org/test-proj/env-1"
    mfile = _write_mapping(
        sandbox,
        key,
        mounts=[
            {"name": "agent-context", "target": "/workspace/agent-context", "source": "agent-context", "type": "symlink"},
            {"name": "scratch", "target": "/workspace/scratch", "source": "scratch", "type": "symlink"},
        ],
    )
    monkeypatch.setenv("JUICEFS_DSN", "dsn://test")
    monkeypatch.setenv("S3_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("JUICEFS_S3_ENDPOINT", "https://example.com")

    calls = {"format": 0, "status": 0, "mount": 0, "umount": 0}

    import coltec_codespaces.__main__ as cli

    def fake_status(commands, dsn, env):
        calls["status"] += 1
        return False

    def fake_format(*args, **kwargs):
        calls["format"] += 1

    def fake_mount(*args, **kwargs):
        calls["mount"] += 1

    def fake_umount(*args, **kwargs):
        calls["umount"] += 1

    monkeypatch.setattr(cli, "juicefs_status", fake_status)
    monkeypatch.setattr(cli, "juicefs_format", fake_format)
    monkeypatch.setattr(cli, "juicefs_mount", fake_mount)
    monkeypatch.setattr(cli, "juicefs_umount", fake_umount)

    ret = cli_main(
        [
            "storage",
            "provision",
            "--repo-root",
            str(sandbox),
            "--mapping",
            str(mfile),
            "--format",
            "--mount",
        ]
    )
    assert ret == 0
    assert calls["status"] == 1
    assert calls["format"] == 1
    assert calls["mount"] == 1
    assert calls["umount"] == 1


# --- Tests for rclone V2 storage CLI ---

class TestStorageConfigCLI:
    """Test storage config CLI commands for rclone V2."""

    def test_storage_config_show(self, sandbox, capsys):
        """Test storage config show displays config."""
        # Create a storage-config.yaml
        config_path = sandbox / "storage-config.yaml"
        config_path.write_text("""
version: 2
rclone:
  remote_name: r2coltec
  type: s3
global:
  - name: shared-prompts
    remote_path: global/shared-prompts
    mount_path: /workspace/.prompts
    sync: pull-only
    read_only: true
""", encoding="utf-8")

        ret = cli_main([
            "storage", "config", "show",
            "--config", str(config_path),
        ])
        assert ret == 0
        out = capsys.readouterr()
        assert "r2coltec" in out.out
        assert "s3" in out.out
        assert "shared-prompts" in out.out

    def test_storage_config_show_missing_file(self, sandbox, capsys):
        """Test storage config show with missing file."""
        ret = cli_main([
            "storage", "config", "show",
            "--config", str(sandbox / "nonexistent.yaml"),
        ])
        assert ret != 0

    def test_storage_config_validate_valid(self, sandbox, capsys):
        """Test storage config validate with valid config."""
        config_path = sandbox / "storage-config.yaml"
        config_path.write_text("""
version: 2
rclone:
  remote_name: r2coltec
  type: s3
global:
  - name: shared-prompts
    remote_path: global/shared-prompts
    mount_path: /workspace/.prompts
    sync: pull-only
    read_only: true
""", encoding="utf-8")

        ret = cli_main([
            "storage", "config", "validate",
            "--config", str(config_path),
        ])
        assert ret == 0
        out = capsys.readouterr()
        assert "valid" in out.out.lower()

    def test_storage_config_validate_invalid(self, sandbox, capsys):
        """Test storage config validate with invalid config (global not pull-only)."""
        config_path = sandbox / "storage-config.yaml"
        config_path.write_text("""
version: 2
rclone:
  remote_name: r2coltec
  type: s3
global:
  - name: shared-prompts
    remote_path: global/shared-prompts
    mount_path: /workspace/.prompts
    sync: bidirectional
    read_only: false
""", encoding="utf-8")

        ret = cli_main([
            "storage", "config", "validate",
            "--config", str(config_path),
        ])
        assert ret != 0


class TestStorageVolumeCLI:
    """Test storage volume CLI commands."""

    def test_storage_volume_list(self, sandbox, capsys, monkeypatch):
        """Test storage volume list shows volumes."""
        from unittest.mock import MagicMock, patch

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "e-test-workspace-agent-context\ne-test-workspace-scratch\ng-coltec-shared\n"

        with patch("subprocess.run", return_value=mock_result):
            ret = cli_main([
                "storage", "volume", "list",
            ])
        assert ret == 0
        out = capsys.readouterr()
        assert "agent-context" in out.out or "e-test" in out.out

    def test_storage_volume_list_with_filter(self, sandbox, capsys, monkeypatch):
        """Test storage volume list with scope filter."""
        from unittest.mock import MagicMock, patch

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "g-coltec-shared\ng-coltec-prompts\n"

        with patch("subprocess.run", return_value=mock_result):
            ret = cli_main([
                "storage", "volume", "list",
                "--scope", "global",
            ])
        assert ret == 0


class TestStorageSeedCLI:
    """Test storage seed CLI commands."""

    def test_storage_seed_creates_volume_and_syncs(self, sandbox, capsys, monkeypatch):
        """Test storage seed creates volume and performs initial sync."""
        from unittest.mock import MagicMock, patch, call

        # Track subprocess calls
        calls = []

        def mock_run(cmd, *args, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            return result

        with patch("subprocess.run", side_effect=mock_run):
            ret = cli_main([
                "storage", "seed",
                "--volume", "g-coltec-shared",
                "--remote", "r2coltec:coltec-data/global/shared",
            ])

        assert ret == 0
        out = capsys.readouterr()
        assert "seed" in out.out.lower() or "sync" in out.out.lower()
        # Verify docker volume create was called
        create_calls = [c for c in calls if "volume" in str(c) and "create" in str(c)]
        assert len(create_calls) >= 1

    def test_storage_seed_with_force_reseeds(self, sandbox, capsys, monkeypatch):
        """Test storage seed --force re-seeds an initialized volume."""
        from unittest.mock import MagicMock, patch

        calls = []

        def mock_run(cmd, *args, **kwargs):
            calls.append(cmd)
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            return result

        with patch("subprocess.run", side_effect=mock_run):
            ret = cli_main([
                "storage", "seed",
                "--volume", "g-coltec-shared",
                "--remote", "r2coltec:coltec-data/global/shared",
                "--force",
            ])

        assert ret == 0
        # Should have sync calls even if volume exists
        rclone_calls = [c for c in calls if "rclone" in str(c)]
        assert len(rclone_calls) >= 1
