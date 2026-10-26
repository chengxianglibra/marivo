"""List governed inputs or observe an exact Metric with optional axes."""

import argparse
from uuid import uuid4

import marivo.analysis as mv


class Arguments(argparse.Namespace):
    metric_path: str | None
    fields: list[str]
    start: str | None
    end: str | None
    timezone: str | None
    session_id: str | None
    persist: bool
    list_metrics: bool
    list_fields: bool
    full_output: bool


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "metric_path", nargs="?", help="Exact governed Metric path; required to observe"
)
discovery = parser.add_mutually_exclusive_group()
discovery.add_argument("--list-metrics", action="store_true", help="Show governed Metric paths")
discovery.add_argument("--list-fields", action="store_true", help="Show governed field paths")
parser.add_argument(
    "--full-output", action="store_true", help="Show complete Catalog output; requires a list mode"
)
parser.add_argument(
    "--field",
    dest="fields",
    action="append",
    default=[],
    help="Canonical grouping field identity (dimension:path or time_dimension:path); repeat for more axes",
)
parser.add_argument("--start", help="Inclusive interval boundary; requires --end")
parser.add_argument("--end", help="Exclusive interval boundary; requires --start")
parser.add_argument("--timezone", help="New Session and interval timezone (default: UTC)")
parser.add_argument("--session-id", help="Continue an exact existing Session ID")
parser.add_argument(
    "--persist", action="store_true", help="Save the computed result as an Artifact"
)
args = parser.parse_args(namespace=Arguments())
listing = args.list_metrics or args.list_fields
if listing and (
    args.metric_path is not None
    or args.fields
    or args.start is not None
    or args.end is not None
    or args.persist
):
    parser.error(
        "List modes cannot be combined with a Metric path, --field, --start, --end, or --persist"
    )
if not listing and args.metric_path is None:
    parser.error("metric_path is required unless --list-metrics or --list-fields is supplied")
if args.full_output and not listing:
    parser.error("--full-output requires --list-metrics or --list-fields")
if (args.start is None) != (args.end is None):
    parser.error("--start and --end must be supplied together")
if args.session_id is not None and args.timezone is not None:
    parser.error("--timezone cannot be supplied with --session-id; the saved timezone is retained")

if args.session_id is None:
    session = mv.session.get_or_create(
        f"entry-{uuid4().hex[:8]}",
        timezone="UTC" if args.timezone is None else args.timezone,
        domains=None,
    )
else:
    session = mv.session.resume(args.session_id)
catalog = session.catalog
if listing:
    for kind in ("metric",) if args.list_metrics else ("dimension", "time_dimension", "measure"):
        page = catalog.search(kind=kind, limit=10)
        page.show(max_output_bytes=None if args.full_output else 8192)
        while args.full_output and page.next_cursor is not None:
            page = catalog.search(kind=kind, limit=10, cursor=page.next_cursor)
            page.show(max_output_bytes=None)
elif args.metric_path is not None:
    metric_entry = catalog.get("metric:" + args.metric_path)
    assert metric_entry.kind == "metric"
    metric = metric_entry.value
    selected_fields = [catalog.get(identity) for identity in args.fields]
    fields = tuple(
        entry.value
        for entry in selected_fields
        if entry.kind == "dimension" or entry.kind == "time_dimension" or entry.kind == "measure"
    )
    if len(fields) != len(selected_fields):
        parser.error("--field requires a dimension, time_dimension or measure identity")
    scope = (
        session.time_scope(start=args.start, end=args.end)
        if args.start is not None and args.end is not None
        else None
    )
    pending = session.observe(
        {"value": metric}, by=(scope, *fields) if scope is not None else fields
    )
    fixed = pending.compute()
    fixed.show()
    if args.persist:
        saved = fixed.persist()
        saved.artifact.show()
        print(f"Artifact: {saved.artifact.id}")
print(f"Session: {session.id}")
