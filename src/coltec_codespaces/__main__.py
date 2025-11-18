"""CLI for rendering Coltec workspace specs into devcontainer artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Tuple, Optional

import yaml

from .spec import SpecBundle, WorkspaceSpec
from .validate import validate_workspace_layout
from .provision import provision_workspace, _slugify


def _load_spec_object(path: Path) -> Tuple[WorkspaceSpec | SpecBundle, bool]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not data:
        raise SystemExit(f"Spec file {path} is empty")
    if "workspaces" in data:
        return SpecBundle.model_validate(data), True
    return WorkspaceSpec.model_validate(data), False


def _select_workspace(
    obj: WorkspaceSpec | SpecBundle, name: str | None
) -> WorkspaceSpec:
    if isinstance(obj, WorkspaceSpec):
        if name and name != obj.name:
            raise SystemExit(
                f"Spec describes workspace '{obj.name}', but '--workspace {name}' was provided"
            )
        return obj

    if not name:
        raise SystemExit("Multiple workspaces defined; specify --workspace <name>")

    for workspace in obj.workspaces:
        if workspace.name == name:
            return workspace
    raise SystemExit(f"Workspace '{name}' not found in bundle")


def cmd_render(args: argparse.Namespace) -> None:
    spec_path = Path(args.spec).expanduser().resolve()
    spec_obj, is_bundle = _load_spec_object(spec_path)
    workspace = _select_workspace(spec_obj, args.workspace)
    payload = workspace.render_devcontainer()

    if args.format == "json":
        output = json.dumps(payload, indent=args.indent, sort_keys=True)
    elif args.format == "yaml":
        output = yaml.safe_dump(payload, sort_keys=False)
    else:
        raise SystemExit(f"Unsupported format: {args.format}")

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"Wrote devcontainer to {out_path}")
    else:
        print(output)

    if args.print_meta:
        meta = workspace.metadata
        print("\n[workspace-metadata]")
        print(f"name: {workspace.name}")
        print(f"version: {workspace.version}")
        print(f"org: {meta.org}")
        print(f"project: {meta.project}")
        print(f"environment: {meta.environment}")
        if meta.tags:
            print("tags: " + ", ".join(meta.tags))


def cmd_validate_spec(args: argparse.Namespace) -> None:
    spec_path = Path(args.spec).expanduser().resolve()
    spec_obj, is_bundle = _load_spec_object(spec_path)
    if isinstance(spec_obj, WorkspaceSpec):
        print(f"Workspace '{spec_obj.name}' validated successfully")
    else:
        names = ", ".join(ws.name for ws in spec_obj.workspaces)
        print(f"Validated bundle with workspaces: {names}")


def cmd_list(args: argparse.Namespace) -> None:
    spec_path = Path(args.spec).expanduser().resolve()
    spec_obj, _ = _load_spec_object(spec_path)
    if isinstance(spec_obj, WorkspaceSpec):
        print(spec_obj.name)
        return
    for workspace in spec_obj.workspaces:
        print(workspace.name)


def cmd_workspace_new(args: argparse.Namespace) -> None:
    # We assume args.repo_root is provided or we try to guess?
    # The caller (Nexus scripts) should ideally provide --repo-root if it knows it.
    # Otherwise we fallback to CWD or fail.

    repo_root = Path(args.repo_root or ".").resolve()

    # Simple inputs prompt logic if missing (minimal version for now, assuming flags mostly)
    # If flags are missing, we can fail or prompt. For library purity, failing/requiring args is cleaner,
    # but to maintain parity with the interactive script, we might need to implement interactive prompts here or keep them in the caller.
    # Given the "Consolidate" directive, the logic should move here.
    # BUT: Interactive prompts are awkward in a library CLI.
    # DECISION: The CLI command `workspace new` will support interactive prompts if args are missing.

    def _prompt(text: str, default: Optional[str] = None) -> str:
        p = f"{text} [{default}]: " if default else f"{text}: "
        v = input(p).strip()
        return v or (default or "")

    def _prompt_choice(
        text: str, choices: list[str], default: Optional[str] = None
    ) -> str:
        c_str = "/".join(choices)
        p = f"{text} ({c_str}) [{default}]: " if default else f"{text} ({c_str}): "
        while True:
            v = input(p).strip().lower()
            if not v and default:
                return default
            if v in choices:
                return v
            print(f"Invalid choice. Options: {', '.join(choices)}")

    asset = args.asset
    if not asset:
        print("Error: Asset repo argument is required.")
        sys.exit(1)

    org_slug = args.org
    if not org_slug:
        org_slug = _slugify(_prompt("Org slug", default="coltec"))

    project_slug = args.project
    if not project_slug:
        project_slug = _slugify(_prompt("Project slug", default=Path(asset).stem))

    env_name = args.environment
    if not env_name:
        default_env = f"{project_slug}-dev"
        env_name = _slugify(_prompt("Environment name", default=default_env))

    p_type = args.type
    if not p_type:
        p_type = _prompt_choice(
            "Project type",
            ["python", "node", "rust", "monorepo", "other"],
            default="python",
        )

    branch = args.branch

    if not args.yes:
        confirm = _prompt_choice("Proceed?", ["y", "n"], default="y")
        if confirm != "y":
            sys.exit(0)

    try:
        provision_workspace(
            repo_root=repo_root,
            asset_input=asset,
            org_slug=org_slug,
            project_slug=project_slug,
            environment_name=env_name,
            project_type=p_type,
            asset_branch=branch,
            create_remote=args.create_remote,
            gh_org=args.gh_org,
            gh_name=args.gh_name,
            manifest_path=Path(args.manifest) if args.manifest else None,
        )
    except Exception as e:
        print(f"Error provisioning workspace: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_workspace_validate(args: argparse.Namespace) -> None:
    repo_root = Path(args.repo_root or ".").resolve()
    target = Path(args.target).resolve()

    success = validate_workspace_layout(
        workspace_path=target,
        repo_root=repo_root,
        manifest_path=Path(args.manifest) if args.manifest else None,
    )
    sys.exit(0 if success else 1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Coltec workspace tooling CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- Spec Commands ---
    render = subparsers.add_parser(
        "render", help="Render a devcontainer.json from a spec"
    )
    render.add_argument("spec", help="Path to a workspace spec (YAML or JSON)")
    render.add_argument("--workspace", help="Workspace name")
    render.add_argument("-o", "--output", help="Optional output path")
    render.add_argument("--format", choices=("json", "yaml"), default="json")
    render.add_argument("--indent", type=int, default=2)
    render.add_argument("--print-meta", action="store_true")
    render.set_defaults(func=cmd_render)

    validate = subparsers.add_parser(
        "validate", help="Validate that a spec file is well-formed"
    )
    validate.add_argument("spec", help="Path to the spec file")
    validate.set_defaults(func=cmd_validate_spec)

    list_cmd = subparsers.add_parser("list", help="List workspaces in a spec bundle")
    list_cmd.add_argument("spec", help="Path to the spec file")
    list_cmd.set_defaults(func=cmd_list)

    # --- Workspace Commands ---
    ws_parser = subparsers.add_parser("workspace", help="Manage workspaces")
    ws_subs = ws_parser.add_subparsers(dest="ws_command", required=True)

    # workspace new
    ws_new = ws_subs.add_parser("new", help="Provision a new workspace")
    ws_new.add_argument("asset", help="Asset repo URL or path")
    ws_new.add_argument("--repo-root", help="Path to Coltec control plane root")
    ws_new.add_argument("--manifest", help="Path to manifest.yaml")
    ws_new.add_argument("--org", help="Org slug")
    ws_new.add_argument("--project", help="Project slug")
    ws_new.add_argument("--environment", help="Environment name")
    ws_new.add_argument("--type", help="Project type")
    ws_new.add_argument("--branch", default="main", help="Asset branch")
    ws_new.add_argument("--yes", action="store_true", help="Skip confirmation")
    ws_new.add_argument(
        "--create-remote", action="store_true", help="Create remote GH repo"
    )
    ws_new.add_argument("--gh-org", help="GitHub Org override")
    ws_new.add_argument("--gh-name", help="GitHub Repo Name override")
    ws_new.set_defaults(func=cmd_workspace_new)

    # workspace validate
    ws_val = ws_subs.add_parser("validate", help="Validate an existing workspace")
    ws_val.add_argument("--target", default=".", help="Workspace path to validate")
    ws_val.add_argument("--repo-root", help="Path to Coltec control plane root")
    ws_val.add_argument("--manifest", help="Path to manifest.yaml")
    ws_val.set_defaults(func=cmd_workspace_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
