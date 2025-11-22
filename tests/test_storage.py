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
    provision_workspace(
        repo_root=sandbox,
        asset_input="dummy",
        org_slug="test-org",
        project_slug="test-proj",
        environment_name="env-1",
        project_type="python",
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
    provision_workspace(
        repo_root=sandbox,
        asset_input="dummy",
        org_slug="test-org",
        project_slug="test-proj",
        environment_name="env-1",
        project_type="python",
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
    monkeypatch.setenv("JUICEFS_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("JUICEFS_SECRET_ACCESS_KEY", "sk")
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
    monkeypatch.setenv("JUICEFS_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("JUICEFS_SECRET_ACCESS_KEY", "sk")
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
