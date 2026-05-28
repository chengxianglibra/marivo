from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, cast

from marivo.agent_skills import SUPPORTED_AGENT_NAMES, merge_reports, sync_skills
from marivo.transports.cli._exitcodes import EXIT_FAILURE, EXIT_INVALID_USAGE
from marivo.transports.cli._output import CliError


def add_arguments(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="agent_command")

    sync_parser = subparsers.add_parser(
        "sync-skills", help="Sync bundled Marivo skills into an agent skill directory"
    )
    sync_parser.add_argument(
        "--agent",
        choices=SUPPORTED_AGENT_NAMES,
        default=None,
        help="Agent whose default skill directory should receive Marivo skills",
    )
    sync_parser.add_argument(
        "--all",
        action="store_true",
        help="Sync Marivo skills to all supported agents",
    )
    sync_parser.add_argument(
        "--target",
        type=str,
        default=None,
        help="Explicit skill root directory; when used without --agent, records agent as custom",
    )
    sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned actions without writing files",
    )
    sync_parser.add_argument(
        "--force",
        action="store_true",
        help="Back up and overwrite conflicting skill directories",
    )
    sync_parser.add_argument(
        "-f",
        "--format",
        type=str,
        choices=["json", "text"],
        default=None,
        help="Output format",
    )
    sync_parser.set_defaults(handler=handle)


def handle(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "agent_command", None) != "sync-skills":
        raise CliError(EXIT_INVALID_USAGE, "Missing agent subcommand")

    agent = getattr(args, "agent", None)
    target = getattr(args, "target", None)
    sync_all = bool(getattr(args, "all", False))

    if sync_all and (agent is not None or target is not None):
        raise CliError(
            EXIT_INVALID_USAGE,
            "--all cannot be combined with --agent or --target",
        )
    if not sync_all and agent is None and target is None:
        raise CliError(
            EXIT_INVALID_USAGE,
            "Provide --agent, --all, or --target",
        )

    dry_run = bool(getattr(args, "dry_run", False))
    force = bool(getattr(args, "force", False))

    if sync_all:
        report = merge_reports(
            [
                sync_skills(agent=name, dry_run=dry_run, force=force)
                for name in SUPPORTED_AGENT_NAMES
            ]
        )
    else:
        target_root = Path(target).expanduser() if target is not None else None
        report = sync_skills(
            agent=agent or "custom",
            target_root=target_root,
            dry_run=dry_run,
            force=force,
        )

    if report["status"] == "conflict" and not dry_run:
        raise CliError(
            EXIT_FAILURE,
            "Marivo skill sync found conflicts; rerun with --force to back up and overwrite",
            json_data=cast("dict[str, Any]", report),
        )
    return cast("dict[str, Any]", report)
