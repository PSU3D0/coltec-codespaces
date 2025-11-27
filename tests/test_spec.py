"""Unit tests for workspace spec models, focusing on V2 persistence features."""

import pytest
from pydantic import ValidationError

from coltec_codespaces.spec import (
    PersistenceSpec,
    RcloneConfig,
    RcloneVolumeConfig,
    PersistenceMount,
    WorkspaceSpec,
    WorkspaceMetadata,
    DevcontainerSpec,
    ImageRef,
    TemplateRef,
    StorageConfig,
    MultiScopeVolumeSpec,
)
from pathlib import Path


class TestRcloneConfig:
    """Test RcloneConfig model."""

    def test_valid_config(self):
        config = RcloneConfig(
            remote_name="r2coltec",
            type="s3",
            options={
                "provider": "Cloudflare",
                "access_key_id": "${S3_ACCESS_KEY_ID}",
            },
        )
        assert config.remote_name == "r2coltec"
        assert config.type == "s3"
        assert config.options["provider"] == "Cloudflare"

    def test_empty_remote_name_fails(self):
        with pytest.raises(ValidationError, match="remote_name cannot be empty"):
            RcloneConfig(
                remote_name="",
                type="s3",
            )

    def test_whitespace_remote_name_stripped(self):
        config = RcloneConfig(
            remote_name="  r2coltec  ",
            type="s3",
        )
        assert config.remote_name == "r2coltec"

    def test_default_values(self):
        config = RcloneConfig()
        assert config.remote_name == "r2coltec"
        assert config.type == "s3"
        assert config.options == {}


class TestRcloneVolumeConfig:
    """Test RcloneVolumeConfig model."""

    def test_valid_volume_config(self):
        vol = RcloneVolumeConfig(
            name="agent-context",
            remote_path="workspaces/{org}/{project}/{env}/agent-context",
            mount_path="/workspace/agent-context",
            sync="bidirectional",
            interval=60,
            priority=1,
        )
        assert vol.name == "agent-context"
        assert vol.sync == "bidirectional"
        assert vol.priority == 1
        assert vol.interval == 60

    def test_default_values(self):
        vol = RcloneVolumeConfig(
            name="scratch",
            remote_path="workspaces/org/proj/env/scratch",
            mount_path="/workspace/scratch",
        )
        assert vol.sync == "bidirectional"
        assert vol.interval == 300
        assert vol.priority == 2
        assert vol.exclude == []
        assert vol.read_only is False

    def test_relative_mount_path_fails(self):
        with pytest.raises(ValidationError, match="must be an absolute path"):
            RcloneVolumeConfig(
                name="test",
                remote_path="test",
                mount_path="workspace/test",  # Not absolute
            )

    def test_invalid_priority_fails(self):
        with pytest.raises(ValidationError, match="priority must be 1"):
            RcloneVolumeConfig(
                name="test",
                remote_path="test",
                mount_path="/workspace/test",
                priority=4,  # Invalid
            )

    def test_zero_interval_fails(self):
        with pytest.raises(ValidationError, match="interval must be positive"):
            RcloneVolumeConfig(
                name="test",
                remote_path="test",
                mount_path="/workspace/test",
                interval=0,
            )

    def test_all_sync_strategies(self):
        for sync in ["bidirectional", "pull-only", "push-only"]:
            vol = RcloneVolumeConfig(
                name="test",
                remote_path="test",
                mount_path="/workspace/test",
                sync=sync,
            )
            assert vol.sync == sync


class TestPersistenceSpec:
    """Test PersistenceSpec with mode field."""

    def test_persistence_spec_validates_mode(self):
        """Test that mode field validates correctly."""
        spec = PersistenceSpec(enabled=False, mode="mounted")
        assert spec.mode == "mounted"

        spec = PersistenceSpec(enabled=False, mode="replicated")
        assert spec.mode == "replicated"

    def test_persistence_spec_invalid_mode_fails(self):
        """Test that invalid mode raises error."""
        with pytest.raises(
            ValidationError, match="Input should be 'mounted' or 'replicated'"
        ):
            PersistenceSpec(enabled=True, mode="invalid")

    def test_persistence_spec_default_mode(self):
        """Test that default mode is 'mounted' for backward compatibility."""
        spec = PersistenceSpec(enabled=False)
        assert spec.mode == "mounted"

    def test_mounted_mode_requires_mounts(self):
        """Test that mounted mode requires mounts when enabled."""
        with pytest.raises(
            ValidationError, match="mounted mode requires at least one mount"
        ):
            PersistenceSpec(
                enabled=True,
                mode="mounted",
                mounts=[],  # Empty
            )

    def test_mounted_mode_with_mounts_valid(self):
        """Test that mounted mode works with mounts."""
        spec = PersistenceSpec(
            enabled=True,
            mode="mounted",
            scope="project",
            mounts=[
                PersistenceMount(
                    name="agent-context",
                    target="/workspace/agent-context",
                    source="agent-context",
                )
            ],
        )
        assert spec.mode == "mounted"
        assert len(spec.mounts) == 1

    def test_replicated_mode_requires_volumes(self):
        """Test that replicated mode requires volumes when enabled."""
        with pytest.raises(
            ValidationError, match="replicated mode requires at least one volume"
        ):
            PersistenceSpec(
                enabled=True,
                mode="replicated",
                volumes=[],  # Empty
            )

    def test_replicated_mode_with_volumes_valid(self):
        """Test that replicated mode works with volumes."""
        spec = PersistenceSpec(
            enabled=True,
            mode="replicated",
            volumes=[
                RcloneVolumeConfig(
                    name="agent-context",
                    remote_path="workspaces/org/proj/env/agent-context",
                    mount_path="/workspace/agent-context",
                    priority=1,
                )
            ],
        )
        assert spec.mode == "replicated"
        assert len(spec.volumes) == 1
        assert spec.volumes[0].priority == 1

    def test_disabled_persistence_allows_empty_fields(self):
        """Test that disabled persistence doesn't require mounts or volumes."""
        spec = PersistenceSpec(enabled=False, mode="mounted", mounts=[])
        assert spec.enabled is False

        spec = PersistenceSpec(enabled=False, mode="replicated", volumes=[])
        assert spec.enabled is False


class TestWorkspaceSpecRendering:
    """Test WorkspaceSpec.render_devcontainer() with new persistence mode."""

    def test_persistence_config_yaml_loads(self):
        """Test that a valid persistence config can be loaded."""
        # This tests that our schema works end-to-end
        spec = WorkspaceSpec(
            name="test-workspace",
            metadata=WorkspaceMetadata(
                org="test-org",
                project="test-project",
                environment="dev",
            ),
            devcontainer=DevcontainerSpec(
                template=TemplateRef(
                    name="test",
                    path=Path("templates/test.json.jinja2"),
                ),
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
                        sync="bidirectional",
                        interval=60,
                        priority=1,
                    )
                ],
            ),
        )
        assert spec.persistence.mode == "replicated"
        assert len(spec.persistence.volumes) == 1

    def test_workspace_spec_renders_with_replicated_mode(self):
        """Test that WorkspaceSpec renders containerEnv with PERSISTENCE_MODE."""
        spec = WorkspaceSpec(
            name="test-workspace",
            metadata=WorkspaceMetadata(
                org="test-org",
                project="test-project",
                environment="dev",
            ),
            devcontainer=DevcontainerSpec(
                template=TemplateRef(
                    name="test",
                    path=Path("templates/test.json.jinja2"),
                ),
                image=ImageRef(name="test:latest"),
            ),
            persistence=PersistenceSpec(
                enabled=True,
                mode="replicated",
                volumes=[
                    RcloneVolumeConfig(
                        name="agent-context",
                        remote_path="workspaces/org/proj/env/agent-context",
                        mount_path="/workspace/agent-context",
                    )
                ],
            ),
        )

        rendered = spec.render_devcontainer()
        assert "containerEnv" in rendered
        assert rendered["containerEnv"]["PERSISTENCE_MODE"] == "replicated"
        assert rendered["containerEnv"]["PERSISTENCE_ENABLED"] == "true"

    def test_backward_compatible_with_mounted_mode(self):
        """Test that old mounted mode still works."""
        spec = WorkspaceSpec(
            name="test-workspace",
            metadata=WorkspaceMetadata(
                org="test-org",
                project="test-project",
                environment="dev",
            ),
            devcontainer=DevcontainerSpec(
                template=TemplateRef(
                    name="test",
                    path=Path("templates/test.json.jinja2"),
                ),
                image=ImageRef(name="test:latest"),
            ),
            persistence=PersistenceSpec(
                enabled=True,
                mode="mounted",
                scope="project",
                mounts=[
                    PersistenceMount(
                        name="agent-context",
                        target="/workspace/agent-context",
                        source="agent-context",
                    )
                ],
            ),
        )

        rendered = spec.render_devcontainer()
        assert rendered["containerEnv"]["PERSISTENCE_MODE"] == "mounted"
        assert rendered["containerEnv"]["PERSISTENCE_ENABLED"] == "true"
        assert rendered["containerEnv"]["PERSISTENCE_SCOPE"] == "project"


class TestStorageConfig:
    """Test StorageConfig model for global storage configuration."""

    def test_empty_config_valid(self):
        """Test that empty config with defaults is valid."""
        config = StorageConfig()
        assert config.version == 2
        assert config.rclone.remote_name == "r2coltec"
        assert config.global_volumes == []
        assert config.projects == {}

    def test_global_volumes_must_be_pull_only(self):
        """Test that global volumes must have sync='pull-only'."""
        with pytest.raises(ValidationError, match="must have sync='pull-only'"):
            StorageConfig(
                global_volumes=[
                    RcloneVolumeConfig(
                        name="org-creds",
                        remote_path="global/org-creds",
                        mount_path="/workspace/.config/coltec",
                        sync="bidirectional",  # Invalid for global
                        read_only=True,
                    )
                ]
            )

    def test_global_volumes_must_be_read_only(self):
        """Test that global volumes must have read_only=true."""
        with pytest.raises(ValidationError, match="must have read_only=true"):
            StorageConfig(
                global_volumes=[
                    RcloneVolumeConfig(
                        name="org-creds",
                        remote_path="global/org-creds",
                        mount_path="/workspace/.config/coltec",
                        sync="pull-only",
                        read_only=False,  # Invalid for global
                    )
                ]
            )

    def test_valid_global_volume(self):
        """Test valid global volume configuration."""
        config = StorageConfig(
            global_volumes=[
                RcloneVolumeConfig(
                    name="org-creds",
                    remote_path="global/org-creds",
                    mount_path="/workspace/.config/coltec",
                    sync="pull-only",
                    read_only=True,
                )
            ]
        )
        assert len(config.global_volumes) == 1
        assert config.global_volumes[0].name == "org-creds"

    def test_project_volumes(self):
        """Test project-specific volume configuration."""
        config = StorageConfig(
            projects={
                "formualizer": [
                    RcloneVolumeConfig(
                        name="rust-cache",
                        remote_path="projects/formualizer/rust-cache",
                        mount_path="/workspace/.cache/rust-target",
                        sync="bidirectional",
                    )
                ]
            }
        )
        vols = config.get_project_volumes("formualizer")
        assert len(vols) == 1
        assert vols[0].name == "rust-cache"

    def test_resolve_volume_global(self):
        """Test resolving a global volume by name."""
        config = StorageConfig(
            global_volumes=[
                RcloneVolumeConfig(
                    name="org-creds",
                    remote_path="global/org-creds",
                    mount_path="/workspace/.config/coltec",
                    sync="pull-only",
                    read_only=True,
                )
            ]
        )
        vol = config.resolve_volume("org-creds", "global")
        assert vol is not None
        assert vol.name == "org-creds"

        # Non-existent volume
        assert config.resolve_volume("nonexistent", "global") is None

    def test_resolve_volume_project(self):
        """Test resolving a project volume by name."""
        config = StorageConfig(
            projects={
                "formualizer": [
                    RcloneVolumeConfig(
                        name="rust-cache",
                        remote_path="projects/formualizer/rust-cache",
                        mount_path="/workspace/.cache/rust-target",
                        sync="bidirectional",
                    )
                ]
            }
        )
        vol = config.resolve_volume("rust-cache", "project", "formualizer")
        assert vol is not None
        assert vol.name == "rust-cache"

        # Wrong project
        assert config.resolve_volume("rust-cache", "project", "other") is None


class TestMultiScopeVolumeSpec:
    """Test MultiScopeVolumeSpec model."""

    def test_empty_spec_valid(self):
        """Test that empty spec is valid."""
        spec = MultiScopeVolumeSpec()
        assert spec.global_refs == []
        assert spec.project_refs == []
        assert spec.environment == []

    def test_with_references(self):
        """Test spec with volume references."""
        spec = MultiScopeVolumeSpec(
            global_refs=["org-creds"],
            project_refs=["rust-cache"],
            environment=[
                RcloneVolumeConfig(
                    name="agent-context",
                    remote_path="workspaces/org/proj/env/agent-context",
                    mount_path="/workspace/agent-context",
                )
            ],
        )
        assert spec.global_refs == ["org-creds"]
        assert spec.project_refs == ["rust-cache"]
        assert len(spec.environment) == 1

    def test_yaml_alias_global(self):
        """Test that 'global' YAML key maps to global_refs."""
        # Simulate loading from YAML with 'global' key
        data = {"global": ["org-creds"], "project": ["rust-cache"]}
        spec = MultiScopeVolumeSpec.model_validate(data)
        assert spec.global_refs == ["org-creds"]
        assert spec.project_refs == ["rust-cache"]


class TestPersistenceSpecMultiScope:
    """Test PersistenceSpec with multi-scope volume format."""

    def test_v2_dict_format(self):
        """Test V2 multi-scope dict format."""
        spec = PersistenceSpec(
            enabled=True,
            mode="replicated",
            volumes={
                "global": ["org-creds"],
                "project": ["rust-cache"],
                "environment": [
                    {
                        "name": "agent-context",
                        "remote_path": "workspaces/org/proj/env/agent-context",
                        "mount_path": "/workspace/agent-context",
                    }
                ],
            },
        )
        assert spec.mode == "replicated"
        vol_refs = spec.get_all_volume_refs()
        assert vol_refs.global_refs == ["org-creds"]
        assert vol_refs.project_refs == ["rust-cache"]
        assert len(vol_refs.environment) == 1

    def test_v1_list_format_backward_compat(self):
        """Test V1 flat list format is converted to environment scope."""
        spec = PersistenceSpec(
            enabled=True,
            mode="replicated",
            volumes=[
                {
                    "name": "agent-context",
                    "remote_path": "workspaces/org/proj/env/agent-context",
                    "mount_path": "/workspace/agent-context",
                }
            ],
        )
        # Should be converted to environment scope
        vol_refs = spec.get_all_volume_refs()
        assert vol_refs.global_refs == []
        assert vol_refs.project_refs == []
        assert len(vol_refs.environment) == 1
        assert vol_refs.environment[0].name == "agent-context"

    def test_volumes_property_returns_environment(self):
        """Test that .volumes property returns environment volumes."""
        spec = PersistenceSpec(
            enabled=True,
            mode="replicated",
            volumes={
                "global": ["org-creds"],
                "environment": [
                    {
                        "name": "agent-context",
                        "remote_path": "workspaces/org/proj/env/agent-context",
                        "mount_path": "/workspace/agent-context",
                    }
                ],
            },
        )
        # .volumes property should return environment volumes for backward compat
        assert len(spec.volumes) == 1
        assert spec.volumes[0].name == "agent-context"
