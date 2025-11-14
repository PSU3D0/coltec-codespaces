"""CLI for rendering Coltec workspace specs into devcontainer artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Tuple

import yaml

from .spec import SpecBundle, WorkspaceSpec


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


def cmd_validate(args: argparse.Namespace) -> None:
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Coltec workspace tooling CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    render = subparsers.add_parser(
        "render", help="Render a devcontainer.json from a spec"
    )
    render.add_argument("spec", help="Path to a workspace spec (YAML or JSON)")
    render.add_argument(
        "--workspace",
        help="Workspace name (required if the spec defines multiple workspaces)",
    )
    render.add_argument(
        "-o",
        "--output",
        help="Optional output path; defaults to stdout",
    )
    render.add_argument(
        "--format",
        choices=("json", "yaml"),
        default="json",
        help="Serialization format (default: json)",
    )
    render.add_argument(
        "--indent",
        type=int,
        default=2,
        help="Indent level for JSON output",
    )
    render.add_argument(
        "--print-meta",
        action="store_true",
        help="Print workspace metadata after rendering",
    )
    render.set_defaults(func=cmd_render)

    validate = subparsers.add_parser(
        "validate", help="Validate that a spec file is well-formed"
    )
    validate.add_argument("spec", help="Path to the spec file")
    validate.set_defaults(func=cmd_validate)

    list_cmd = subparsers.add_parser("list", help="List workspaces in a spec bundle")
    list_cmd.add_argument("spec", help="Path to the spec file")
    list_cmd.set_defaults(func=cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
