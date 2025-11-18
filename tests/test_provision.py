import pytest
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
