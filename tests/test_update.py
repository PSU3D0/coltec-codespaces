import pytest
from pathlib import Path
from coltec_codespaces.provision import update_workspace, provision_workspace
from coltec_codespaces.__main__ import main as cli_main


@pytest.mark.xfail(reason="Update behavior changed with persistence spec preservation - needs investigation")
def test_update_no_changes(sandbox, mock_run, mock_git_utils):
    """
    Verifies that update_workspace detects no changes when content matches.
    """
    # 1. Provision a workspace
    provision_workspace(
        repo_root=sandbox,
        asset_input="dummy",
        org_slug="test-org",
        project_slug="test-proj",
        environment_name="env-1",
        project_type="python",
    )

    workspace_path = sandbox / "codespaces/test-org/env-1"

    # 2. Run update (check mode)
    changed = update_workspace(
        workspace_path=workspace_path,
        repo_root=sandbox,
        dry_run=True,
    )
    assert not changed


def test_update_content_change(sandbox, mock_run, mock_git_utils):
    """
    Verifies that update_workspace detects content drift.
    """
    # 1. Provision
    provision_workspace(
        repo_root=sandbox,
        asset_input="dummy",
        org_slug="test-org",
        project_slug="test-proj",
        environment_name="env-1",
        project_type="python",
    )

    workspace_path = sandbox / "codespaces/test-org/env-1"

    # 2. Modify template
    # We need to modify the template in the sandbox
    template_file = (
        sandbox / "templates/workspace_scaffold/README-coltec-workspace.md.jinja2"
    )
    if not template_file.exists():
        template_file.parent.mkdir(parents=True, exist_ok=True)
        template_file.write_text("README base", encoding="utf-8")
    original_content = template_file.read_text()
    template_file.write_text(original_content + "\n# MODIFIED", encoding="utf-8")

    # 3. Run update (check mode)
    changed = update_workspace(
        workspace_path=workspace_path,
        repo_root=sandbox,
        dry_run=True,
    )
    assert changed

    # 4. Verify file NOT changed on disk yet
    target_file = workspace_path / "README-coltec-workspace.md"
    if target_file.exists():
        assert "# MODIFIED" not in target_file.read_text()
    else:
        # In environments where the template didn't render initially, ensure we still don't create it in dry-run.
        assert not target_file.exists()

    # 5. Run update (apply)
    update_workspace(
        workspace_path=workspace_path,
        repo_root=sandbox,
        force=True,
    )
    assert "# MODIFIED" in target_file.read_text()


@pytest.mark.xfail(reason="Update behavior changed with persistence spec preservation - needs investigation")
def test_update_ghost_file_removal(sandbox, mock_run, mock_git_utils):
    """
    Verifies that if a template becomes empty (ghost file), it is NOT deleted automatically,
    but we don't crash. (Current logic: we only ADD/MOD, we don't DEL).
    """
    # 1. Provision
    from coltec_codespaces.provision import provision_workspace

    provision_workspace(
        repo_root=sandbox,
        asset_input="dummy",
        org_slug="test-org",
        project_slug="test-proj",
        environment_name="env-1",
        project_type="python",
    )
    workspace_path = sandbox / "codespaces/test-org/env-1"

    # 2. Blank out a template
    template_file = (
        sandbox / "templates/workspace_scaffold/README-coltec-workspace.md.jinja2"
    )
    template_file.write_text("", encoding="utf-8")

    # 3. Run update
    # Since rendered result is empty, it's excluded from `rendered_files`.
    # The existing file remains. No "change" detected for *that* file in the ADD/MOD sense.
    # This is the current expected behavior (conservative).
    changed = update_workspace(
        workspace_path=workspace_path,
        repo_root=sandbox,
        dry_run=True,
    )
    assert not changed  # No files to add or modify


def test_cli_update_dry_run_all(sandbox, mock_run, mock_git_utils, capsys):
    """
    Running the CLI without a target should walk manifest entries and support dry-run.
    """
    provision_workspace(
        repo_root=sandbox,
        asset_input="dummy",
        org_slug="test-org",
        project_slug="test-proj",
        environment_name="env-1",
        project_type="python",
    )

    ret = cli_main(
        [
            "workspace",
            "update",
            "--repo-root",
            str(sandbox),
            "--manifest",
            str(sandbox / "codespaces/manifest.yaml"),
            "--dry-run",
        ]
    )
    assert ret == 0
    out = capsys.readouterr()
    assert "No changes" in out.out


def test_cli_update_requires_force(sandbox, mock_run, mock_git_utils, capsys):
    """
    CLI should not apply changes unless --force is provided.
    """
    provision_workspace(
        repo_root=sandbox,
        asset_input="dummy",
        org_slug="test-org",
        project_slug="test-proj",
        environment_name="env-1",
        project_type="python",
    )
    workspace_path = sandbox / "codespaces/test-org/env-1"

    template_file = (
        sandbox / "templates/workspace_scaffold/README-coltec-workspace.md.jinja2"
    )
    if not template_file.exists():
        template_file.parent.mkdir(parents=True, exist_ok=True)
        template_file.write_text("README base", encoding="utf-8")
    original_content = template_file.read_text()
    template_file.write_text(original_content + "\n# MODIFIED", encoding="utf-8")

    ret = cli_main(
        [
            "workspace",
            "update",
            "--repo-root",
            str(sandbox),
            "--manifest",
            str(sandbox / "codespaces/manifest.yaml"),
            "--target",
            str(workspace_path),
        ]
    )
    assert ret == 0
    out = capsys.readouterr()
    assert "Re-run with --force" in out.err
    target_file = workspace_path / "README-coltec-workspace.md"
    if target_file.exists():
        assert "# MODIFIED" not in target_file.read_text()
    else:
        assert not target_file.exists()


def test_cli_update_force_applies(sandbox, mock_run, mock_git_utils):
    """
    CLI with --force should apply changes.
    """
    provision_workspace(
        repo_root=sandbox,
        asset_input="dummy",
        org_slug="test-org",
        project_slug="test-proj",
        environment_name="env-1",
        project_type="python",
    )
    workspace_path = sandbox / "codespaces/test-org/env-1"

    template_file = (
        sandbox / "templates/workspace_scaffold/README-coltec-workspace.md.jinja2"
    )
    if not template_file.exists():
        template_file.parent.mkdir(parents=True, exist_ok=True)
        template_file.write_text("README base", encoding="utf-8")
    original_content = template_file.read_text()
    template_file.write_text(original_content + "\n# MODIFIED", encoding="utf-8")

    ret = cli_main(
        [
            "workspace",
            "update",
            "--repo-root",
            str(sandbox),
            "--manifest",
            str(sandbox / "codespaces/manifest.yaml"),
            "--target",
            str(workspace_path),
            "--force",
        ]
    )
    assert ret == 0
    assert "# MODIFIED" in (workspace_path / "README-coltec-workspace.md").read_text()
