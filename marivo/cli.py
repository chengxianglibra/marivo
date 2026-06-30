"""Marivo CLI — project scaffolding and tooling."""

from __future__ import annotations

import argparse
import shutil
import sys
import tomllib
from pathlib import Path

import tomli_w

from marivo import __version__
from marivo.config import (
    AUTHORED_DIR,
    CLAUDE_SKILLS_DIR,
    CODEX_SKILLS_DIR,
    PROJECT_MANIFEST,
    SKILL_ANALYSIS,
    SKILL_SEMANTIC,
    STATE_DIR,
)


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
        ".claude/skills/marivo-semantic": project_dir / CLAUDE_SKILLS_DIR / SKILL_SEMANTIC,
        ".claude/skills/marivo-analysis": project_dir / CLAUDE_SKILLS_DIR / SKILL_ANALYSIS,
        ".codex/skills/marivo-semantic": project_dir / CODEX_SKILLS_DIR / SKILL_SEMANTIC,
        ".codex/skills/marivo-analysis": project_dir / CODEX_SKILLS_DIR / SKILL_ANALYSIS,
    }


def init_project(force: bool = False, project_dir: Path | None = None) -> None:
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
        manifest_data = {"project": {"name": project_name}}
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
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Initialize a Marivo project")
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Delete existing project artifacts and recreate from scratch",
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        raise SystemExit(0)

    if args.command == "init":
        init_project(force=args.force)
