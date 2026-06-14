"""Marivo CLI — project scaffolding and tooling."""

from __future__ import annotations

import argparse
import shutil
import sys
import tomllib
from pathlib import Path

import tomli_w

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
        "marivo/": project_dir / AUTHORED_DIR,
        ".marivo/": project_dir / STATE_DIR,
        ".claude/skills/marivo-semantic": project_dir / CLAUDE_SKILLS_DIR / SKILL_SEMANTIC,
        ".claude/skills/marivo-analysis": project_dir / CLAUDE_SKILLS_DIR / SKILL_ANALYSIS,
        ".codex/skills/marivo-semantic": project_dir / CODEX_SKILLS_DIR / SKILL_SEMANTIC,
        ".codex/skills/marivo-analysis": project_dir / CODEX_SKILLS_DIR / SKILL_ANALYSIS,
    }


def init_project(force: bool = False, project_dir: Path | None = None) -> None:
    """Initialize a Marivo project in the given directory.

    Args:
        force: If True, overwrite existing artifacts (except non-empty .marivo/
            and invalid marivo.toml).
        project_dir: Target directory. Defaults to the current working directory.

    Raises:
        SystemExit: With code 1 when artifacts exist and force is False,
            when marivo.toml contains invalid TOML (even with force),
            or when directory creation fails due to permissions.

    Example:
        >>> init_project(project_dir=Path("/tmp/my-project"))

    Constraints:
        Never removes .marivo/ if it contains any files. Never overwrites
        marivo.toml if it contains invalid TOML. Symlink creation failures
        are non-fatal (warning printed, init continues).
    """
    project_dir = project_dir or Path.cwd()
    artifacts = _artifact_paths(project_dir)
    skills_src = _skills_source_dir()

    # --- Detect existing artifacts ---
    existing = [label for label, path in artifacts.items() if path.exists() or path.is_symlink()]

    if existing and not force:
        print(f"Marivo project artifacts already exist in {project_dir}:", file=sys.stderr)
        for label in existing:
            print(f"  {label}", file=sys.stderr)
        print("Use --force to overwrite.", file=sys.stderr)
        raise SystemExit(1)

    # --- Guard: refuse to overwrite invalid marivo.toml ---
    manifest_path = project_dir / PROJECT_MANIFEST
    if manifest_path.is_file():
        try:
            with open(manifest_path, "rb") as f:
                tomllib.load(f)
        except tomllib.TOMLDecodeError:
            print(
                f"Error: {PROJECT_MANIFEST} exists but contains invalid TOML. "
                "Fix or remove it manually before reinitializing.",
                file=sys.stderr,
            )
            raise SystemExit(1) from None

    # --- Force: remove conflicting artifacts ---
    if existing and force:
        for label in existing:
            path = artifacts[label]
            if label == ".marivo/" and any(path.rglob("*")):
                # Skip if it has content (any file or nested entry)
                print(
                    f"  Warning: {label} has content — skipping removal.",
                    file=sys.stderr,
                )
                continue
            if path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            elif path.is_file():
                path.unlink()

    # --- Create marivo.toml ---
    project_name = project_dir.name
    manifest_data = {"project": {"name": project_name}}
    (project_dir / PROJECT_MANIFEST).write_text(tomli_w.dumps(manifest_data))
    print(f"Initialized Marivo project in {project_dir}")
    print(f"  Created {PROJECT_MANIFEST}")

    # --- Create marivo/ ---
    (project_dir / AUTHORED_DIR).mkdir(exist_ok=True)
    print(f"  Created {AUTHORED_DIR}/")

    # --- Create .marivo/ ---
    (project_dir / STATE_DIR).mkdir(exist_ok=True)
    print(f"  Created {STATE_DIR}/")

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
                link_path.unlink()
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
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Initialize a Marivo project")
    init_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing project artifacts",
    )

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        raise SystemExit(0)

    if args.command == "init":
        init_project(force=args.force)
