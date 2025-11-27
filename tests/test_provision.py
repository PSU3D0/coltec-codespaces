import pytest
import yaml
from pathlib import Path
from coltec_codespaces.provision import provision_workspace


def test_provision_happy_path(sandbox, mock_run, mock_git_utils):
    """
    Verifies that provision_workspace creates the correct file structure
    and updates the manifest.
    """
    provision_workspace(
        repo_root=sandbox,
        asset_input="dummy",  # mock_git_utils handles this
        org_slug="test-org",
        project_slug="test-proj",
        environment_name="env-1",
        project_type="python",
    )

    workspace_path = sandbox / "codespaces/test-org/env-1"

    # 1. Directory Structure
    assert workspace_path.exists()
    assert (workspace_path / ".devcontainer/devcontainer.json").exists()
    assert (workspace_path / ".devcontainer/workspace-spec.yaml").exists()
    assert (workspace_path / "agent-context/.gitkeep").exists()
    assert (workspace_path / "agent-project.yaml").exists()

    # 2. Content Check (Spec)
    spec_content = (workspace_path / ".devcontainer/workspace-spec.yaml").read_text()
    assert "test-org" in spec_content
    assert "env-1" in spec_content

    # 3. Manifest Update
    manifest = (sandbox / "codespaces/manifest.yaml").read_text()
    assert "env-1" in manifest
    assert "codespaces/test-org/env-1" in manifest


def test_idempotency_safeguard(sandbox, mock_run, mock_git_utils):
    """
    Ensures we don't overwrite existing workspaces.
    """
    # First run
    provision_workspace(
        repo_root=sandbox,
        asset_input="dummy",
        org_slug="test-org",
        project_slug="test-proj",
        environment_name="env-1",
        project_type="python",
    )

    # Second run should fail
    with pytest.raises(RuntimeError, match="already exists"):
        provision_workspace(
            repo_root=sandbox,
            asset_input="dummy",
            org_slug="test-org",
            project_slug="test-proj",
            environment_name="env-1",
            project_type="python",
        )


def test_template_overlay_applies(sandbox, mock_run, mock_git_utils):
    """
    Ensures overlays can add extra files.
    """
    overlay = sandbox / "templates/overlays/human_zsh"
    # If the real overlay isn't present (CI fallback), create a minimal one
    if not overlay.exists():
        (overlay / "workspace_scaffold").mkdir(parents=True)
        (overlay / "workspace_scaffold/.zshrc.jinja2").write_text(
            "echo overlay", encoding="utf-8"
        )

    provision_workspace(
        repo_root=sandbox,
        asset_input="dummy",
        org_slug="test-org",
        project_slug="test-proj",
        environment_name="env-overlay",
        project_type="python",
        template_overlays=[overlay],
    )

    workspace_path = sandbox / "codespaces/test-org/env-overlay"
    assert (workspace_path / ".zshrc").exists()


def test_remote_provisioning_defaults(sandbox, mock_run, mock_git_utils):
    """
    Verifies that --create-remote defaults to user/env-name.
    """
    provision_workspace(
        repo_root=sandbox,
        asset_input="dummy",
        org_slug="test-org",
        project_slug="test-proj",
        environment_name="env-1",
        project_type="python",
        create_remote=True,
        # No gh_org or gh_name provided
    )

    # Find 'gh repo create' call
    # Note: _run args are [cmd_list, ...] so we check call.args[0] which is the cmd_list
    gh_calls = []
    for call in mock_run.call_args_list:
        # call.args[0] is the first argument to _run, which is the command list
        cmd_list = call.args[0]
        if cmd_list[0] == "gh" and "create" in cmd_list:
            gh_calls.append(cmd_list)

    assert len(gh_calls) == 1
    cmd = gh_calls[0]

    # Should default to just the env name (which gh CLI interprets as user/name)
    assert "env-1" in cmd
    # Should NOT contain explicit slash if defaulting to user
    assert "/" not in cmd[3]


def test_remote_provisioning_org_override(sandbox, mock_run, mock_git_utils):
    """
    Verifies --gh-org overrides the destination.
    """
    provision_workspace(
        repo_root=sandbox,
        asset_input="dummy",
        org_slug="test-org",
        project_slug="test-proj",
        environment_name="env-1",
        project_type="python",
        create_remote=True,
        gh_org="custom-org",
    )

    gh_calls = []
    for call in mock_run.call_args_list:
        cmd_list = call.args[0]
        if cmd_list[0] == "gh" and "create" in cmd_list:
            gh_calls.append(cmd_list)
    assert "custom-org/env-1" in gh_calls[0]


def test_remote_provisioning_name_override(sandbox, mock_run, mock_git_utils):
    """
    Verifies --gh-name overrides everything.
    """
    provision_workspace(
        repo_root=sandbox,
        asset_input="dummy",
        org_slug="test-org",
        project_slug="test-proj",
        environment_name="env-1",
        project_type="python",
        create_remote=True,
        gh_name="my-org/custom-repo",
    )

    gh_calls = []
    for call in mock_run.call_args_list:
        cmd_list = call.args[0]
        if cmd_list[0] == "gh" and "create" in cmd_list:
            gh_calls.append(cmd_list)
    assert "my-org/custom-repo" in gh_calls[0]


def test_rollback_on_failure(sandbox, mock_run, mock_git_utils):
    """
    Ensures directory is cleaned up if provisioning crashes mid-way.
    """
    # Make the 3rd call (git commit? spec gen?) fail
    mock_run.side_effect = [None, None, RuntimeError("Boom")]

    with pytest.raises(RuntimeError, match="Boom"):
        provision_workspace(
            repo_root=sandbox,
            asset_input="dummy",
            org_slug="fail-org",
            project_slug="fail-proj",
            environment_name="env-fail",
            project_type="python",
        )

    workspace_path = sandbox / "codespaces/fail-org/env-fail"
    assert not workspace_path.exists()


def test_provision_replicated_mode_default(sandbox, mock_run, mock_git_utils):
    """
    Verifies that new workspaces default to replicated mode with multi_scope_volumes.
    """
    provision_workspace(
        repo_root=sandbox,
        asset_input="dummy",
        org_slug="test-org",
        project_slug="test-proj",
        environment_name="env-replicated",
        project_type="python",
    )

    workspace_path = sandbox / "codespaces/test-org/env-replicated"
    spec_path = workspace_path / ".devcontainer/workspace-spec.yaml"
    assert spec_path.exists()

    spec_data = yaml.safe_load(spec_path.read_text())

    # Verify replicated mode
    assert spec_data["persistence"]["mode"] == "replicated"
    assert spec_data["persistence"]["enabled"] is True

    # Verify multi_scope_volumes structure
    volumes = spec_data["persistence"]["multi_scope_volumes"]
    assert "global_refs" in volumes
    assert "project_refs" in volumes
    assert "environment" in volumes

    # Verify environment volumes have correct structure
    env_vols = volumes["environment"]
    assert len(env_vols) >= 2
    agent_context = next(v for v in env_vols if v["name"] == "agent-context")
    assert agent_context["sync"] == "bidirectional"
    assert agent_context["priority"] == 1
    assert agent_context["mount_path"] == "/workspace/agent-context"

    scratch = next(v for v in env_vols if v["name"] == "scratch")
    assert scratch["sync"] == "push-only"
    assert scratch["priority"] == 2

    # Verify rclone_config exists
    assert spec_data["persistence"]["rclone_config"]["remote_name"] == "r2coltec"


def test_provision_mounted_mode_explicit(sandbox, mock_run, mock_git_utils):
    """
    Verifies that mounted mode can still be specified explicitly.
    """
    provision_workspace(
        repo_root=sandbox,
        asset_input="dummy",
        org_slug="test-org",
        project_slug="test-proj",
        environment_name="env-mounted",
        project_type="python",
        persistence_mode="mounted",
    )

    workspace_path = sandbox / "codespaces/test-org/env-mounted"
    spec_path = workspace_path / ".devcontainer/workspace-spec.yaml"
    spec_data = yaml.safe_load(spec_path.read_text())

    # Verify mounted mode
    assert spec_data["persistence"]["mode"] == "mounted"
    assert spec_data["persistence"]["enabled"] is True

    # Verify mounts exist (legacy format)
    mounts = spec_data["persistence"]["mounts"]
    assert len(mounts) >= 2
    mount_names = {m["name"] for m in mounts}
    assert "agent-context" in mount_names
    assert "scratch" in mount_names


def test_provision_replicated_devcontainer_has_volume_mounts(sandbox, mock_run, mock_git_utils):
    """
    Verifies that devcontainer.json includes persistence volume mounts for replicated mode.
    """
    import json

    provision_workspace(
        repo_root=sandbox,
        asset_input="dummy",
        org_slug="test-org",
        project_slug="test-proj",
        environment_name="env-vols",
        project_type="python",
    )

    workspace_path = sandbox / "codespaces/test-org/env-vols"
    devcontainer_path = workspace_path / ".devcontainer/devcontainer.json"
    devcontainer = json.loads(devcontainer_path.read_text())

    # Verify mounts include persistence volumes
    mounts = devcontainer.get("mounts", [])
    mount_targets = [m.split(",")[1].split("=")[1] for m in mounts if "target=" in m]

    assert "/workspace/agent-context" in mount_targets
    assert "/workspace/scratch" in mount_targets

    # Verify volume names follow naming convention
    mount_sources = [m.split(",")[0].split("=")[1] for m in mounts if "source=" in m]
    assert "e-env-vols-agent-context" in mount_sources
    assert "e-env-vols-scratch" in mount_sources
