import os
import shutil
import pytest
from pathlib import Path

# Define the root of the real repo to copy templates from
# We assume the tests run inside `coltec-codespaces/codebase`
# so we go up 4 levels to find `nexus/templates`
REAL_REPO_ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def sandbox(tmp_path):
    """
    Creates a fake Coltec repo root in a temp dir.
    Copies real templates to ensure rendering works.
    """
    # Copy templates
    src_templates = REAL_REPO_ROOT / "templates"
    dst_templates = tmp_path / "templates"

    # In CI or incomplete clones, templates might not exist.
    # We create dummy templates if real ones aren't found to make tests portable.
    if src_templates.exists():
        shutil.copytree(src_templates, dst_templates)
    else:
        # Fallback for isolated unit testing
        dst_templates.mkdir()
        scaffold = dst_templates / "workspace_scaffold"
        scaffold.mkdir()
        (scaffold / "README.md").write_text("Dummy README", encoding="utf-8")

    # Create manifest
    codespaces = tmp_path / "codespaces"
    codespaces.mkdir()
    (codespaces / "manifest.yaml").write_text(
        "version: 1\nmanifest: {}", encoding="utf-8"
    )

    # Create dummy asset repo to clone from
    asset_repo = tmp_path / "dummy-asset"
    asset_repo.mkdir()
    (asset_repo / ".git").mkdir()

    return tmp_path


@pytest.fixture
def mock_run(mocker):
    """
    Mocks the internal _run function to capture commands without executing them.
    """
    return mocker.patch("coltec_codespaces.provision._run")


@pytest.fixture
def mock_git_utils(mocker):
    """
    Mocks git URL validation/remote detection so we don't need real git repos.
    """
    mocker.patch(
        "coltec_codespaces.provision.get_asset_repo_url",
        return_value="https://github.com/mock/repo.git",
    )
