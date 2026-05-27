"""Entry point for ``python -m marivo.semantic_py``."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    """Top-level CLI dispatcher."""
    parser = argparse.ArgumentParser(
        prog="python -m marivo.semantic_py",
        description="marivo.semantic_py CLI",
    )
    sub = parser.add_subparsers(dest="command")

    # -- check --
    check_parser = sub.add_parser("check", help="Validate the semantic project")
    check_parser.add_argument(
        "--project",
        type=str,
        default=None,
        help="Explicit project root directory; default uses find_project(cwd)",
    )
    check_parser.add_argument(
        "--strict-provenance",
        action="store_true",
        default=False,
        help="Non-zero exit if any metric has unverified parity status",
    )
    check_parser.add_argument(
        "--parity",
        action="store_true",
        default=False,
        help="Run parity check for all metrics with source_sql",
    )
    check_parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )

    args = parser.parse_args(argv)

    if args.command == "check":
        from marivo.semantic_py.cli.check import run_check

        return run_check(args)
    elif args.command == "refactor":
        raise NotImplementedError("scheduled for next iteration")
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
