"""Unit tests for rclone-based replicated persistence (V2)."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
import subprocess

from coltec_codespaces.storage import (
    RcloneCommands,
    ensure_rclone_configured,
    create_replicated_volumes,
    JuiceFSCommands,
    resolve_rclone_env,
)
from coltec_codespaces.spec import (
    WorkspaceSpec,
    WorkspaceMetadata,
    DevcontainerSpec,
    ImageRef,
    TemplateRef,
    PersistenceSpec,
    RcloneVolumeConfig,
    RcloneConfig,
)


class TestRcloneCommands:
    """Test RcloneCommands wrapper class."""

    def test_sync_push_strategy(self):
        """Test sync with push strategy (local -> remote)."""
        commands = RcloneCommands()
        mock_result = MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(commands, "run", return_value=mock_result) as mock_run:
            result = commands.sync(
                local=Path("/workspace/data"),
                remote="r2coltec:bucket/path",
                strategy="push",
            )

            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "rclone"
            assert args[1] == "sync"
            assert args[2] == "/workspace/data"
            assert args[3] == "r2coltec:bucket/path"
            assert "--fast-list" in args
            assert result.returncode == 0

    def test_sync_pull_strategy(self):
        """Test sync with pull strategy (remote -> local)."""
        commands = RcloneCommands()
        mock_result = MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(commands, "run", return_value=mock_result) as mock_run:
            result = commands.sync(
                local=Path("/workspace/data"),
                remote="r2coltec:bucket/path",
                strategy="pull",
            )

            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "rclone"
            assert args[1] == "sync"
            assert args[2] == "r2coltec:bucket/path"
            assert args[3] == "/workspace/data"
            assert "--fast-list" in args

    def test_sync_bidirectional_strategy(self):
        """Test sync with bidirectional strategy (uses bisync)."""
        commands = RcloneCommands()
        mock_result = MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(commands, "run", return_value=mock_result) as mock_run:
            result = commands.sync(
                local=Path("/workspace/data"),
                remote="r2coltec:bucket/path",
                strategy="bidirectional",
            )

            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "rclone"
            assert args[1] == "bisync"
            assert args[2] == "/workspace/data"
            assert args[3] == "r2coltec:bucket/path"
            assert "--check-access" in args
            assert "--max-delete" in args

    def test_sync_with_options(self):
        """Test sync with various options."""
        commands = RcloneCommands()
        mock_result = MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(commands, "run", return_value=mock_result) as mock_run:
            result = commands.sync(
                local=Path("/workspace/data"),
                remote="r2coltec:bucket/path",
                strategy="push",
                dry_run=True,
                exclude=["*.tmp", "node_modules/**"],
                transfers=16,
                bwlimit="10M",
                timeout=300,
            )

            args = mock_run.call_args[0][0]
            assert "--dry-run" in args
            assert "--exclude" in args
            assert "*.tmp" in args
            assert "node_modules/**" in args
            assert "--transfers" in args
            assert "16" in args
            assert "--bwlimit" in args
            assert "10M" in args
            assert "--timeout" in args
            assert "300s" in args

    def test_bisync_basic(self):
        """Test bisync with basic options."""
        commands = RcloneCommands()
        mock_result = MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(commands, "run", return_value=mock_result) as mock_run:
            result = commands.bisync(
                local=Path("/workspace/agent-context"),
                remote="r2coltec:bucket/workspaces/org/proj/env/agent-context",
            )

            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert args[0] == "rclone"
            assert args[1] == "bisync"
            assert args[2] == "/workspace/agent-context"
            assert "--check-access" in args
            assert "--max-delete" in args
            assert "10" in args

    def test_bisync_with_resync(self):
        """Test bisync with resync flag (first time setup)."""
        commands = RcloneCommands()
        mock_result = MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(commands, "run", return_value=mock_result) as mock_run:
            result = commands.bisync(
                local=Path("/workspace/agent-context"),
                remote="r2coltec:bucket/path",
                resync=True,
            )

            args = mock_run.call_args[0][0]
            assert "--resync" in args

    def test_bisync_with_excludes(self):
        """Test bisync with exclude patterns."""
        commands = RcloneCommands()
        mock_result = MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(commands, "run", return_value=mock_result) as mock_run:
            result = commands.bisync(
                local=Path("/workspace/data"),
                remote="r2coltec:bucket/path",
                exclude=["*.pyc", "__pycache__/**"],
            )

            args = mock_run.call_args[0][0]
            assert "--exclude" in args
            assert "*.pyc" in args
            assert "__pycache__/**" in args


class TestEnsureRcloneConfigured:
    """Test ensure_rclone_configured function."""

    def test_rclone_not_installed(self):
        """Test that error is raised if rclone not installed."""
        env = {
            "S3_ACCESS_KEY_ID": "test-key",
            "S3_SECRET_ACCESS_KEY": "test-secret",
            "JUICEFS_S3_ENDPOINT": "https://test.r2.cloudflarestorage.com",
            "JUICEFS_BUCKET": "test-bucket",
        }

        with patch(
            "subprocess.run",
            return_value=MagicMock(returncode=1, stdout="", stderr="not found"),
        ):
            with pytest.raises(RuntimeError, match="rclone not found"):
                ensure_rclone_configured(env)

    def test_missing_env_vars(self):
        """Test that error is raised if required env vars missing."""
        env = {"S3_ACCESS_KEY_ID": "test-key"}

        with patch(
            "subprocess.run",
            return_value=MagicMock(
                returncode=0, stdout="/usr/local/bin/rclone", stderr=""
            ),
        ):
            with pytest.raises(RuntimeError, match="Missing required env vars"):
                ensure_rclone_configured(env)

    def test_success(self):
        """Test successful validation."""
        env = {
            "S3_ACCESS_KEY_ID": "test-key",
            "S3_SECRET_ACCESS_KEY": "test-secret",
            "JUICEFS_S3_ENDPOINT": "https://test.r2.cloudflarestorage.com",
            "JUICEFS_BUCKET": "test-bucket",
        }

        with patch(
            "subprocess.run",
            return_value=MagicMock(
                returncode=0, stdout="/usr/local/bin/rclone", stderr=""
            ),
        ):
            # Should not raise
            ensure_rclone_configured(env)


class TestCreateReplicatedVolumes:
    """Test create_replicated_volumes function."""

    def test_disabled_persistence_returns_empty(self):
        """Test that disabled persistence returns empty mount args."""
        spec = WorkspaceSpec(
            name="test-workspace",
            metadata=WorkspaceMetadata(org="test-org", project="test-project"),
            devcontainer=DevcontainerSpec(
                template=TemplateRef(name="test", path=Path("test.json")),
                image=ImageRef(name="test:latest"),
            ),
            persistence=PersistenceSpec(enabled=False),
        )

        mount_args = create_replicated_volumes(
            workspace_spec=spec,
            bucket="test-bucket",
            org="test-org",
            project="test-project",
            env_name="dev",
        )

        assert mount_args == []

    def test_mounted_mode_returns_empty(self):
        """Test that mounted mode returns empty (not supported by this function)."""
        from coltec_codespaces.spec import PersistenceMount

        spec = WorkspaceSpec(
            name="test-workspace",
            metadata=WorkspaceMetadata(org="test-org", project="test-project"),
            devcontainer=DevcontainerSpec(
                template=TemplateRef(name="test", path=Path("test.json")),
                image=ImageRef(name="test:latest"),
            ),
            persistence=PersistenceSpec(
                enabled=True,
                mode="mounted",
                mounts=[
                    PersistenceMount(
                        name="test", target="/workspace/test", source="test"
                    )
                ],
            ),
        )

        mount_args = create_replicated_volumes(
            workspace_spec=spec,
            bucket="test-bucket",
            org="test-org",
            project="test-project",
            env_name="dev",
        )

        assert mount_args == []

    def test_creates_volumes_and_mount_args(self):
        """Test that volumes are created and mount args returned."""
        spec = WorkspaceSpec(
            name="test-workspace",
            metadata=WorkspaceMetadata(org="test-org", project="test-project"),
            devcontainer=DevcontainerSpec(
                template=TemplateRef(name="test", path=Path("test.json")),
                image=ImageRef(name="test:latest"),
            ),
            persistence=PersistenceSpec(
                enabled=True,
                mode="replicated",
                volumes=[
                    RcloneVolumeConfig(
                        name="agent-context",
                        remote_path="workspaces/{org}/{project}/{env}/agent-context",
                        mount_path="/workspace/agent-context",
                        priority=1,
                    ),
                    RcloneVolumeConfig(
                        name="scratch",
                        remote_path="workspaces/{org}/{project}/{env}/scratch",
                        mount_path="/workspace/scratch",
                        priority=2,
                        read_only=False,
                    ),
                ],
            ),
        )

        mock_commands = MagicMock()
        mock_commands.run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "coltec_codespaces.storage.docker_volume_exists", return_value=False
        ):
            mount_args = create_replicated_volumes(
                workspace_spec=spec,
                bucket="test-bucket",
                org="test-org",
                project="test-project",
                env_name="dev",
                commands=mock_commands,
            )

        # Should have created 2 volumes
        assert mock_commands.run.call_count == 2

        # Check volume names
        call_1 = mock_commands.run.call_args_list[0][0][0]
        call_2 = mock_commands.run.call_args_list[1][0][0]
        assert "e-test-workspace-agent-context" in call_1
        assert "e-test-workspace-scratch" in call_2

        # Check mount args
        assert len(mount_args) == 4  # 2 volumes * 2 args each (--mount, value)
        assert "--mount" in mount_args
        assert any(
            "e-test-workspace-agent-context" in arg
            and "/workspace/agent-context" in arg
            for arg in mount_args
        )
        assert any(
            "e-test-workspace-scratch" in arg and "/workspace/scratch" in arg
            for arg in mount_args
        )

    def test_readonly_volumes(self):
        """Test that read-only volumes get readonly flag."""
        spec = WorkspaceSpec(
            name="test-workspace",
            metadata=WorkspaceMetadata(org="test-org", project="test-project"),
            devcontainer=DevcontainerSpec(
                template=TemplateRef(name="test", path=Path("test.json")),
                image=ImageRef(name="test:latest"),
            ),
            persistence=PersistenceSpec(
                enabled=True,
                mode="replicated",
                volumes=[
                    RcloneVolumeConfig(
                        name="credentials",
                        remote_path="global/credentials",
                        mount_path="/workspace/.config/coltec",
                        read_only=True,
                    ),
                ],
            ),
        )

        mock_commands = MagicMock()
        mock_commands.run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "coltec_codespaces.storage.docker_volume_exists", return_value=False
        ):
            mount_args = create_replicated_volumes(
                workspace_spec=spec,
                bucket="test-bucket",
                org="test-org",
                project="test-project",
                env_name="dev",
                commands=mock_commands,
            )

        # Check that readonly flag is present
        mount_value = mount_args[1]  # Second element is the mount value
        assert "readonly" in mount_value

    def test_existing_volume_not_recreated(self):
        """Test that existing volumes are not recreated."""
        spec = WorkspaceSpec(
            name="test-workspace",
            metadata=WorkspaceMetadata(org="test-org", project="test-project"),
            devcontainer=DevcontainerSpec(
                template=TemplateRef(name="test", path=Path("test.json")),
                image=ImageRef(name="test:latest"),
            ),
            persistence=PersistenceSpec(
                enabled=True,
                mode="replicated",
                volumes=[
                    RcloneVolumeConfig(
                        name="agent-context",
                        remote_path="workspaces/{org}/{project}/{env}/agent-context",
                        mount_path="/workspace/agent-context",
                    ),
                ],
            ),
        )

        mock_commands = MagicMock()

        with patch("coltec_codespaces.storage.docker_volume_exists", return_value=True):
            mount_args = create_replicated_volumes(
                workspace_spec=spec,
                bucket="test-bucket",
                org="test-org",
                project="test-project",
                env_name="dev",
                commands=mock_commands,
            )

        # Should not have called docker volume create
        mock_commands.run.assert_not_called()

        # But should still return mount args
        assert len(mount_args) == 2
        assert "--mount" in mount_args


class TestEnsureGlobalVolume:
    """Test ensure_global_volume function for org-wide read-only volumes."""

    def test_creates_volume_if_not_exists(self):
        """Test that Docker volume is created if it doesn't exist."""
        from coltec_codespaces.storage import ensure_global_volume

        vol = RcloneVolumeConfig(
            name="shared-prompts",
            remote_path="global/shared-prompts",
            mount_path="/workspace/.config/prompts",
            sync="pull-only",
            read_only=True,
        )

        mock_commands = MagicMock()
        mock_commands.run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "coltec_codespaces.storage.docker_volume_exists", return_value=False
        ):
            with patch(
                "coltec_codespaces.storage.is_volume_initialized", return_value=False
            ):
                result = ensure_global_volume(
                    volume=vol,
                    org="coltec",
                    remote_name="r2coltec",
                    bucket="coltec-data",
                    commands=mock_commands,
                )

        # Should have created volume
        create_calls = [
            c for c in mock_commands.run.call_args_list
            if "docker" in c[0][0] and "volume" in c[0][0] and "create" in c[0][0]
        ]
        assert len(create_calls) == 1
        assert "g-coltec-shared-prompts" in create_calls[0][0][0]

    def test_performs_initial_pull_on_new_volume(self):
        """Test that rclone pull is performed on first initialization."""
        from coltec_codespaces.storage import ensure_global_volume

        vol = RcloneVolumeConfig(
            name="shared-prompts",
            remote_path="global/shared-prompts",
            mount_path="/workspace/.config/prompts",
            sync="pull-only",
            read_only=True,
        )

        mock_commands = MagicMock()
        mock_commands.run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "coltec_codespaces.storage.docker_volume_exists", return_value=True
        ):
            with patch(
                "coltec_codespaces.storage.is_volume_initialized", return_value=False
            ):
                with patch(
                    "coltec_codespaces.storage._check_volume_marker", return_value=False
                ):
                    with patch(
                        "coltec_codespaces.storage.mark_volume_initialized"
                    ) as mock_mark:
                        result = ensure_global_volume(
                            volume=vol,
                            org="coltec",
                            remote_name="r2coltec",
                            bucket="coltec-data",
                            commands=mock_commands,
                        )

        # Should have called rclone sync via docker (pull direction)
        # rclone runs inside a container: docker run ... rclone/rclone sync ...
        rclone_calls = [
            c for c in mock_commands.run.call_args_list
            if "rclone/rclone" in " ".join(c[0][0])
        ]
        assert len(rclone_calls) >= 1
        rclone_args = " ".join(rclone_calls[0][0][0])
        assert "sync" in rclone_args
        # Remote should be source (first arg after sync)
        assert "r2coltec:coltec-data/global/shared-prompts" in rclone_args

        # Should have marked as initialized
        mock_mark.assert_called_once()

    def test_skips_pull_if_already_initialized(self):
        """Test that rclone pull is skipped if volume already initialized."""
        from coltec_codespaces.storage import ensure_global_volume

        vol = RcloneVolumeConfig(
            name="shared-prompts",
            remote_path="global/shared-prompts",
            mount_path="/workspace/.config/prompts",
            sync="pull-only",
            read_only=True,
        )

        mock_commands = MagicMock()

        with patch(
            "coltec_codespaces.storage.docker_volume_exists", return_value=True
        ):
            with patch(
                "coltec_codespaces.storage.is_volume_initialized", return_value=True
            ):
                result = ensure_global_volume(
                    volume=vol,
                    org="coltec",
                    remote_name="r2coltec",
                    bucket="coltec-data",
                    commands=mock_commands,
                )

        # Should NOT have called rclone (via docker)
        rclone_calls = [
            c for c in mock_commands.run.call_args_list
            if "rclone/rclone" in " ".join(c[0][0])
        ]
        assert len(rclone_calls) == 0

    def test_returns_readonly_mount_args(self):
        """Test that mount args include readonly flag."""
        from coltec_codespaces.storage import ensure_global_volume

        vol = RcloneVolumeConfig(
            name="shared-prompts",
            remote_path="global/shared-prompts",
            mount_path="/workspace/.config/prompts",
            sync="pull-only",
            read_only=True,
        )

        mock_commands = MagicMock()
        mock_commands.run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "coltec_codespaces.storage.docker_volume_exists", return_value=True
        ):
            with patch(
                "coltec_codespaces.storage.is_volume_initialized", return_value=True
            ):
                result = ensure_global_volume(
                    volume=vol,
                    org="coltec",
                    remote_name="r2coltec",
                    bucket="coltec-data",
                    commands=mock_commands,
                )

        assert result["volume_name"] == "g-coltec-shared-prompts"
        assert result["mount_path"] == "/workspace/.config/prompts"
        assert result["read_only"] is True
        assert "readonly" in result["mount_arg"]

    def test_uses_correct_volume_naming(self):
        """Test that global volumes use g-{org}-{name} naming."""
        from coltec_codespaces.storage import ensure_global_volume

        vol = RcloneVolumeConfig(
            name="credentials",
            remote_path="global/credentials",
            mount_path="/workspace/.creds",
            sync="pull-only",
            read_only=True,
        )

        mock_commands = MagicMock()
        mock_commands.run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "coltec_codespaces.storage.docker_volume_exists", return_value=True
        ):
            with patch(
                "coltec_codespaces.storage.is_volume_initialized", return_value=True
            ):
                result = ensure_global_volume(
                    volume=vol,
                    org="myorg",
                    remote_name="r2coltec",
                    bucket="coltec-data",
                    commands=mock_commands,
                )

        assert result["volume_name"] == "g-myorg-credentials"


class TestEnsureProjectVolume:
    """Test ensure_project_volume function for project-wide shared volumes."""

    def test_creates_volume_if_not_exists(self):
        """Test that Docker volume is created if it doesn't exist."""
        from coltec_codespaces.storage import ensure_project_volume

        vol = RcloneVolumeConfig(
            name="shared-data",
            remote_path="projects/{project}/shared-data",
            mount_path="/workspace/shared",
            sync="bidirectional",
            read_only=False,
        )

        mock_commands = MagicMock()
        mock_commands.run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "coltec_codespaces.storage.docker_volume_exists", return_value=False
        ):
            with patch(
                "coltec_codespaces.storage.is_volume_initialized", return_value=False
            ):
                with patch(
                    "coltec_codespaces.storage._check_volume_marker", return_value=False
                ):
                    result = ensure_project_volume(
                        volume=vol,
                        project="myproject",
                        remote_name="r2coltec",
                        bucket="coltec-data",
                        commands=mock_commands,
                    )

        # Should have created volume with correct naming
        create_calls = [
            c for c in mock_commands.run.call_args_list
            if "docker" in c[0][0] and "volume" in c[0][0] and "create" in c[0][0]
        ]
        assert len(create_calls) == 1
        assert "p-myproject-shared-data" in create_calls[0][0][0]

    def test_performs_initial_bisync_on_new_volume(self):
        """Test that rclone bisync (with resync) is performed on first initialization."""
        from coltec_codespaces.storage import ensure_project_volume

        vol = RcloneVolumeConfig(
            name="shared-data",
            remote_path="projects/{project}/shared-data",
            mount_path="/workspace/shared",
            sync="bidirectional",
            read_only=False,
        )

        mock_commands = MagicMock()
        mock_commands.run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "coltec_codespaces.storage.docker_volume_exists", return_value=True
        ):
            with patch(
                "coltec_codespaces.storage.is_volume_initialized", return_value=False
            ):
                with patch(
                    "coltec_codespaces.storage._check_volume_marker", return_value=False
                ):
                    with patch(
                        "coltec_codespaces.storage.mark_volume_initialized"
                    ) as mock_mark:
                        result = ensure_project_volume(
                            volume=vol,
                            project="myproject",
                            remote_name="r2coltec",
                            bucket="coltec-data",
                            commands=mock_commands,
                        )

        # Should have called rclone bisync via docker with --resync
        rclone_calls = [
            c for c in mock_commands.run.call_args_list
            if "rclone/rclone" in " ".join(c[0][0])
        ]
        assert len(rclone_calls) >= 1
        rclone_args = " ".join(rclone_calls[0][0][0])
        assert "bisync" in rclone_args
        assert "--resync" in rclone_args

        # Should have marked as initialized
        mock_mark.assert_called_once()

    def test_skips_sync_if_already_initialized(self):
        """Test that bisync is skipped if volume already initialized."""
        from coltec_codespaces.storage import ensure_project_volume

        vol = RcloneVolumeConfig(
            name="shared-data",
            remote_path="projects/{project}/shared-data",
            mount_path="/workspace/shared",
            sync="bidirectional",
            read_only=False,
        )

        mock_commands = MagicMock()

        with patch(
            "coltec_codespaces.storage.docker_volume_exists", return_value=True
        ):
            with patch(
                "coltec_codespaces.storage.is_volume_initialized", return_value=True
            ):
                result = ensure_project_volume(
                    volume=vol,
                    project="myproject",
                    remote_name="r2coltec",
                    bucket="coltec-data",
                    commands=mock_commands,
                )

        # Should NOT have called rclone
        rclone_calls = [
            c for c in mock_commands.run.call_args_list
            if "rclone/rclone" in " ".join(c[0][0])
        ]
        assert len(rclone_calls) == 0

    def test_returns_writable_mount_args(self):
        """Test that mount args are writable (no readonly flag)."""
        from coltec_codespaces.storage import ensure_project_volume

        vol = RcloneVolumeConfig(
            name="shared-data",
            remote_path="projects/{project}/shared-data",
            mount_path="/workspace/shared",
            sync="bidirectional",
            read_only=False,
        )

        mock_commands = MagicMock()
        mock_commands.run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "coltec_codespaces.storage.docker_volume_exists", return_value=True
        ):
            with patch(
                "coltec_codespaces.storage.is_volume_initialized", return_value=True
            ):
                result = ensure_project_volume(
                    volume=vol,
                    project="myproject",
                    remote_name="r2coltec",
                    bucket="coltec-data",
                    commands=mock_commands,
                )

        assert result["volume_name"] == "p-myproject-shared-data"
        assert result["mount_path"] == "/workspace/shared"
        assert result["read_only"] is False
        assert "readonly" not in result["mount_arg"]

    def test_uses_correct_volume_naming(self):
        """Test that project volumes use p-{project}-{name} naming."""
        from coltec_codespaces.storage import ensure_project_volume

        vol = RcloneVolumeConfig(
            name="team-resources",
            remote_path="projects/{project}/team-resources",
            mount_path="/workspace/resources",
            sync="bidirectional",
            read_only=False,
        )

        mock_commands = MagicMock()
        mock_commands.run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "coltec_codespaces.storage.docker_volume_exists", return_value=True
        ):
            with patch(
                "coltec_codespaces.storage.is_volume_initialized", return_value=True
            ):
                result = ensure_project_volume(
                    volume=vol,
                    project="acme-corp",
                    remote_name="r2coltec",
                    bucket="coltec-data",
                    commands=mock_commands,
                )

        assert result["volume_name"] == "p-acme-corp-team-resources"


# ============================================================================
# Integration Tests (require real R2 credentials)
# ============================================================================


def _has_r2_credentials():
    """Check if R2 credentials are available in environment."""
    import os
    return all([
        os.environ.get("S3_ACCESS_KEY_ID"),
        os.environ.get("S3_SECRET_ACCESS_KEY"),
        os.environ.get("RCLONE_CONFIG_R2COLTEC_ENDPOINT") or os.environ.get("CF_S3_ENDPOINT"),
    ])


@pytest.mark.skipif(not _has_r2_credentials(), reason="R2 credentials not available")
class TestRcloneIntegration:
    """Integration tests that require real R2 credentials.

    To run these tests, ensure these environment variables are set:
    - S3_ACCESS_KEY_ID
    - S3_SECRET_ACCESS_KEY
    - RCLONE_CONFIG_R2COLTEC_ENDPOINT (or CF_S3_ENDPOINT)

    Run with: pytest tests/test_rclone.py::TestRcloneIntegration -v
    """

    @pytest.fixture
    def rclone_env(self):
        """Set up rclone environment variables."""
        import os
        env = dict(os.environ)
        # Map standard env vars to rclone config vars
        if not env.get("RCLONE_CONFIG_R2COLTEC_ACCESS_KEY_ID"):
            env["RCLONE_CONFIG_R2COLTEC_ACCESS_KEY_ID"] = env.get("S3_ACCESS_KEY_ID", "")
        if not env.get("RCLONE_CONFIG_R2COLTEC_SECRET_ACCESS_KEY"):
            env["RCLONE_CONFIG_R2COLTEC_SECRET_ACCESS_KEY"] = env.get("S3_SECRET_ACCESS_KEY", "")
        if not env.get("RCLONE_CONFIG_R2COLTEC_ENDPOINT"):
            env["RCLONE_CONFIG_R2COLTEC_ENDPOINT"] = env.get("CF_S3_ENDPOINT", env.get("JUICEFS_S3_ENDPOINT", ""))
        env["RCLONE_CONFIG_R2COLTEC_TYPE"] = "s3"
        env["RCLONE_CONFIG_R2COLTEC_PROVIDER"] = "Cloudflare"
        return env

    def test_rclone_lsd_works(self, rclone_env):
        """Test that rclone can list directories in the bucket."""
        result = subprocess.run(
            ["rclone", "lsd", "r2coltec:coltec-codespaces-data"],
            capture_output=True,
            text=True,
            env=rclone_env,
            timeout=30,
        )
        # Should not error even if empty
        assert result.returncode == 0 or "directory not found" in result.stderr.lower()

    def test_volume_seed_and_sync(self, rclone_env, tmp_path):
        """Test seeding a temp volume and syncing data."""
        import uuid

        # Create a unique test prefix
        test_id = str(uuid.uuid4())[:8]
        test_volume = f"test-integration-{test_id}"
        test_remote = f"r2coltec:coltec-codespaces-data/test-integration/{test_id}"

        try:
            # Create test volume
            subprocess.run(
                ["docker", "volume", "create", test_volume],
                check=True,
                capture_output=True,
            )

            # Write test data to volume via docker
            subprocess.run(
                [
                    "docker", "run", "--rm",
                    "-v", f"{test_volume}:/data",
                    "alpine:latest",
                    "sh", "-c", f"echo 'test-data-{test_id}' > /data/test.txt",
                ],
                check=True,
                capture_output=True,
            )

            # Sync to R2 using rclone in docker
            result = subprocess.run(
                [
                    "docker", "run", "--rm",
                    "-v", f"{test_volume}:/data:ro",
                    "-e", f"RCLONE_CONFIG_R2COLTEC_TYPE={rclone_env['RCLONE_CONFIG_R2COLTEC_TYPE']}",
                    "-e", f"RCLONE_CONFIG_R2COLTEC_PROVIDER={rclone_env['RCLONE_CONFIG_R2COLTEC_PROVIDER']}",
                    "-e", f"RCLONE_CONFIG_R2COLTEC_ACCESS_KEY_ID={rclone_env['RCLONE_CONFIG_R2COLTEC_ACCESS_KEY_ID']}",
                    "-e", f"RCLONE_CONFIG_R2COLTEC_SECRET_ACCESS_KEY={rclone_env['RCLONE_CONFIG_R2COLTEC_SECRET_ACCESS_KEY']}",
                    "-e", f"RCLONE_CONFIG_R2COLTEC_ENDPOINT={rclone_env['RCLONE_CONFIG_R2COLTEC_ENDPOINT']}",
                    "rclone/rclone:latest",
                    "sync", "/data", test_remote,
                    "--verbose",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )

            assert result.returncode == 0, f"Sync failed: {result.stderr}"

            # Verify data in R2
            list_result = subprocess.run(
                [
                    "docker", "run", "--rm",
                    "-e", f"RCLONE_CONFIG_R2COLTEC_TYPE={rclone_env['RCLONE_CONFIG_R2COLTEC_TYPE']}",
                    "-e", f"RCLONE_CONFIG_R2COLTEC_PROVIDER={rclone_env['RCLONE_CONFIG_R2COLTEC_PROVIDER']}",
                    "-e", f"RCLONE_CONFIG_R2COLTEC_ACCESS_KEY_ID={rclone_env['RCLONE_CONFIG_R2COLTEC_ACCESS_KEY_ID']}",
                    "-e", f"RCLONE_CONFIG_R2COLTEC_SECRET_ACCESS_KEY={rclone_env['RCLONE_CONFIG_R2COLTEC_SECRET_ACCESS_KEY']}",
                    "-e", f"RCLONE_CONFIG_R2COLTEC_ENDPOINT={rclone_env['RCLONE_CONFIG_R2COLTEC_ENDPOINT']}",
                    "rclone/rclone:latest",
                    "ls", test_remote,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )

            assert "test.txt" in list_result.stdout

        finally:
            # Clean up test volume
            subprocess.run(
                ["docker", "volume", "rm", "-f", test_volume],
                capture_output=True,
            )
            # Clean up R2 test data
            subprocess.run(
                [
                    "docker", "run", "--rm",
                    "-e", f"RCLONE_CONFIG_R2COLTEC_TYPE={rclone_env['RCLONE_CONFIG_R2COLTEC_TYPE']}",
                    "-e", f"RCLONE_CONFIG_R2COLTEC_PROVIDER={rclone_env['RCLONE_CONFIG_R2COLTEC_PROVIDER']}",
                    "-e", f"RCLONE_CONFIG_R2COLTEC_ACCESS_KEY_ID={rclone_env['RCLONE_CONFIG_R2COLTEC_ACCESS_KEY_ID']}",
                    "-e", f"RCLONE_CONFIG_R2COLTEC_SECRET_ACCESS_KEY={rclone_env['RCLONE_CONFIG_R2COLTEC_SECRET_ACCESS_KEY']}",
                    "-e", f"RCLONE_CONFIG_R2COLTEC_ENDPOINT={rclone_env['RCLONE_CONFIG_R2COLTEC_ENDPOINT']}",
                    "rclone/rclone:latest",
                    "purge", test_remote,
                ],
                capture_output=True,
            )
