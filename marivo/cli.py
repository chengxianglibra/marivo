"""Marivo CLI — project scaffolding and tooling."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tomllib
from pathlib import Path

import tomli_w

from marivo import __version__
from marivo._publish.s3 import default_s3_client_factory
from marivo.config import (
    AGENTS_SKILLS_DIR,
    AUTHORED_DIR,
    CLAUDE_SKILLS_DIR,
    CODEX_SKILLS_DIR,
    PROJECT_MANIFEST,
    SKILL_ANALYSIS,
    SKILL_SEMANTIC,
    STATE_DIR,
)

_s3_client_factory = default_s3_client_factory


def _skills_source_dir() -> Path:
    """Resolve the installed package's skills directory."""
    import marivo.skills

    return Path(marivo.skills.__file__).parent


def _artifact_paths(project_dir: Path) -> dict[str, Path]:
    """Return all artifact paths that init checks or creates."""
    return {
        "marivo.toml": project_dir / PROJECT_MANIFEST,
        "models/": project_dir / AUTHORED_DIR,
        ".marivo/": project_dir / STATE_DIR,
        ".agents/skills/marivo-semantic": (project_dir / AGENTS_SKILLS_DIR / SKILL_SEMANTIC),
        ".agents/skills/marivo-analysis": project_dir / AGENTS_SKILLS_DIR / SKILL_ANALYSIS,
        ".claude/skills/marivo-semantic": project_dir / CLAUDE_SKILLS_DIR / SKILL_SEMANTIC,
        ".claude/skills/marivo-analysis": project_dir / CLAUDE_SKILLS_DIR / SKILL_ANALYSIS,
        ".codex/skills/marivo-semantic": project_dir / CODEX_SKILLS_DIR / SKILL_SEMANTIC,
        ".codex/skills/marivo-analysis": project_dir / CODEX_SKILLS_DIR / SKILL_ANALYSIS,
    }


def _init_project_impl(force: bool = False, project_dir: Path | None = None) -> None:
    """Initialize a Marivo project in the given directory.

    Args:
        force: If True, delete existing artifacts and recreate them from
            scratch. If False, existing artifacts produce a warning and are
            skipped; only missing artifacts are created.
        project_dir: Target directory. Defaults to the current working directory.

    Raises:
        SystemExit: With code 1 when marivo.toml contains invalid TOML and
            force is False, or when directory creation fails due to permissions.

    Example:
        >>> init_project(project_dir=Path("/tmp/my-project"))

    Constraints:
        When force is False, existing files and directories are never removed;
        a per-artifact warning is printed instead. Symlink creation failures
        are non-fatal (warning printed, init continues).
    """
    project_dir = project_dir or Path.cwd()
    artifacts = _artifact_paths(project_dir)
    skills_src = _skills_source_dir()

    print(f"Initializing Marivo project in {project_dir}")

    # --- Guard: refuse to skip invalid marivo.toml without --force ---
    if not force:
        manifest_path = project_dir / PROJECT_MANIFEST
        if manifest_path.is_file():
            try:
                with open(manifest_path, "rb") as f:
                    tomllib.load(f)
            except tomllib.TOMLDecodeError:
                print(
                    f"Error: {PROJECT_MANIFEST} exists but contains invalid TOML. "
                    "Fix or remove it manually, or use --force to overwrite.",
                    file=sys.stderr,
                )
                raise SystemExit(1) from None

    # --- Force: remove existing artifacts ---
    if force:
        for _, path in artifacts.items():
            if path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            elif path.is_file():
                path.unlink()

    # --- Create marivo.toml ---
    manifest_path = project_dir / PROJECT_MANIFEST
    if not manifest_path.exists():
        project_name = project_dir.name
        manifest_data = {"project": {"name": project_name}, "telemetry": {"enabled": "on"}}
        manifest_path.write_text(tomli_w.dumps(manifest_data))
        print(f"  Created {PROJECT_MANIFEST}")
    else:
        print(f"  Warning: {PROJECT_MANIFEST} already exists — skipping", file=sys.stderr)

    # --- Create models/ ---
    models_path = project_dir / AUTHORED_DIR
    if not models_path.exists():
        models_path.mkdir(exist_ok=True)
        print(f"  Created {AUTHORED_DIR}/")
    else:
        print(f"  Warning: {AUTHORED_DIR}/ already exists — skipping", file=sys.stderr)

    # --- Create .marivo/ ---
    state_path = project_dir / STATE_DIR
    if not state_path.exists():
        state_path.mkdir(exist_ok=True)
        print(f"  Created {STATE_DIR}/")
    else:
        print(f"  Warning: {STATE_DIR}/ already exists — skipping", file=sys.stderr)

    # --- Install skills ---
    for agent_dir_name, agent_label in [
        (AGENTS_SKILLS_DIR, "Agents"),
        (CLAUDE_SKILLS_DIR, "Claude Code"),
        (CODEX_SKILLS_DIR, "Codex"),
    ]:
        agent_skill_dir = project_dir / agent_dir_name
        agent_skill_dir.mkdir(parents=True, exist_ok=True)
        for skill_name in (SKILL_SEMANTIC, SKILL_ANALYSIS):
            link_path = agent_skill_dir / skill_name
            source_path = skills_src / skill_name
            if link_path.exists() or link_path.is_symlink():
                if force:
                    link_path.unlink()
                else:
                    print(
                        f"  Warning: {agent_dir_name}/{skill_name} already exists — skipping",
                        file=sys.stderr,
                    )
                    continue
            try:
                link_path.symlink_to(source_path)
            except OSError as exc:
                print(
                    f"  Warning: could not create symlink {link_path}: {exc}",
                    file=sys.stderr,
                )
        print(f"  Installed skills for {agent_label} ({agent_dir_name}/)")


def init_project(force: bool = False, project_dir: Path | None = None) -> None:
    """Initialize a Marivo project and record local usage telemetry."""
    resolved_project_dir = project_dir or Path.cwd()
    from marivo.telemetry import track_operation

    with track_operation(
        "marivo.cli.init",
        family="cli",
        intent="init",
        project_root=resolved_project_dir,
    ):
        _init_project_impl(force=force, project_dir=resolved_project_dir)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for the marivo command.

    Args:
        argv: Command-line arguments. Defaults to sys.argv[1:].

    Example:
        >>> main(["init", "--force"])
    """
    parser = argparse.ArgumentParser(
        prog="marivo",
        description="Marivo project tooling",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Analysis workflow:\n"
            "  .venv/bin/python -c \"import marivo.analysis as mv; mv.help('workflow')\"\n\n"
            "Common diagnostics before live analysis:\n"
            "  marivo doctor --semantic\n"
            "  marivo doctor --datasource <name> --connect"
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Initialize a Marivo project")
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Delete existing project artifacts and recreate from scratch",
    )
    publish_parser = subparsers.add_parser("publish", help="Upload a file or directory to S3")
    publish_parser.add_argument("path", help="File or directory to upload")
    doctor_parser = subparsers.add_parser("doctor", help="Diagnose Marivo environment setup")
    doctor_parser.add_argument("--project-root", default=None, help="Project root to inspect")
    doctor_parser.add_argument("--format", choices=("text", "json"), default="text")
    doctor_parser.add_argument(
        "--fix-snap", action="store_true", help="Print suggested fix commands"
    )
    doctor_parser.add_argument(
        "--semantic", action="store_true", help="Include semantic load/readiness checks"
    )
    doctor_parser.add_argument(
        "--connect", action="store_true", help="Run live datasource connectivity checks"
    )
    doctor_parser.add_argument(
        "--datasource", default=None, help="Limit datasource checks to one name"
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        raise SystemExit(0)

    if args.command == "init":
        init_project(force=args.force)
    elif args.command == "publish":
        from marivo._publish.config import PublishConfigError
        from marivo._publish.static import publish_path

        try:
            result = publish_path(args.path, client_factory=_s3_client_factory)
        except (FileNotFoundError, PublishConfigError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
        suffix = "file" if result.file_count == 1 else "files"
        print(f"Uploaded {result.file_count} {suffix}")
        print(f"URL: {result.url}")
        print(f"S3: {result.uri}")
    elif args.command == "doctor":
        from marivo.doctor import DoctorOptions, exit_code, render_fix_snap, render_text, run_doctor

        report = run_doctor(
            DoctorOptions(
                project_root=args.project_root,
                format=args.format,
                fix_snap=args.fix_snap,
                semantic=args.semantic,
                connect=args.connect,
                datasource=args.datasource,
            )
        )
        if args.fix_snap:
            print(render_fix_snap(report))
        elif args.format == "json":
            print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        else:
            print(render_text(report))
        code = exit_code(report)
        if code:
            raise SystemExit(code)
