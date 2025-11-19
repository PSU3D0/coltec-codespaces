import pytest
from pathlib import Path
from coltec_codespaces.provision import update_workspace


def test_update_no_changes(sandbox, mock_run, mock_git_utils):
    """
    Verifies that update_workspace detects no changes when content matches.
    """
    # 1. Provision a workspace
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

    # 2. Modify template
    # We need to modify the template in the sandbox
    template_file = (
        sandbox / "templates/workspace_scaffold/README-coltec-workspace.md.jinja2"
    )
    # It might not exist in the mock sandbox if we didn't copy it fully,
    # but `test_provision.py` implies we did or created dummy.
    # Let's overwrite it to ensure a diff.
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
    assert "# MODIFIED" not in target_file.read_text()

    # 5. Run update (apply)
    update_workspace(
        workspace_path=workspace_path,
        repo_root=sandbox,
        force=True,
    )
    assert "# MODIFIED" in target_file.read_text()


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
