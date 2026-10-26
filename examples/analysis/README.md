# Runnable analysis entry

Work from a project root containing governed `models/` and an installed Marivo.
The entry script uses the public Catalog → Session observation → compute route;
the caller selects the governed Metric, optional fields and time interval.

## Start with one source result

Run [entry.py](entry.py) from your governed project root with a required exact
Metric path. Point an interpreter with Marivo installed at the script:

```sh
/path/to/marivo/.venv/bin/python /path/to/marivo/examples/analysis/entry.py sample.measurement
```

The selectors here use the neutral synthetic fixture below. Substitute the
task's governed Metric path in an existing project. No field is needed for an
overall result. Add one `--field` per requested grouping axis, and supply both
`--start` and `--end` for an interval:

```sh
/path/to/marivo/.venv/bin/python /path/to/marivo/examples/analysis/entry.py sample.measurement \
  --field sample.readings.group --start 2026-01-01 --end 2026-01-03 --timezone UTC
```

If the Metric path is unknown, list Metrics, choose an exact displayed path,
then resume the printed Session ID for the observation. For a requested grouping
field with an unknown path, list fields in that same Session and choose its path:

```sh
/path/to/marivo/.venv/bin/python /path/to/marivo/examples/analysis/entry.py --list-metrics
/path/to/marivo/.venv/bin/python /path/to/marivo/examples/analysis/entry.py sample.measurement \
  --session-id "session_<saved-id>"
/path/to/marivo/.venv/bin/python /path/to/marivo/examples/analysis/entry.py --list-fields \
  --session-id "session_<saved-id>"
/path/to/marivo/.venv/bin/python /path/to/marivo/examples/analysis/entry.py sample.measurement \
  --session-id "session_<saved-id>" --field sample.readings.group
```

List modes show one public Catalog collection without computing a Metric. The
default card is bounded and announces omitted output. To see the complete
collection, repeat the same list command with `--full-output`, for example:

```sh
/path/to/marivo/.venv/bin/python /path/to/marivo/examples/analysis/entry.py --list-metrics \
  --session-id "session_<saved-id>" --full-output
```

The script resolves only supplied observation selectors. Omitted fields and
time produce `by=()`; with an interval, its TimeScope precedes the supplied fields
in `by`. The interval is half-open: start inclusive, end exclusive. An incomplete
boundary pair fails before Session creation.

Each invocation without `--session-id` creates a fresh Session, defaulting to
UTC. Continuation uses the exact Session's frozen domains and timezone.
`--timezone` is only for new Sessions and is rejected with `--session-id`. A
continued observation still reads new source facts. For an independent cold
reproduction, omit `--session-id`; use exact Artifact recovery below to reopen
fixed data.

The minimal ungrouped source sequence is:

```python
from uuid import uuid4
import marivo.analysis as mv

session = mv.session.get_or_create(
    f"analysis-{uuid4().hex[:8]}", timezone="UTC", domains=None
)
catalog = session.catalog
metric = catalog.metrics.get("sample.measurement")
pending = session.observe({"value": metric})
fixed = pending.compute()
fixed.show()
```

`domains=None` loads all authoring domain directories and freezes that scope in
the Session. Use this default for open-ended analysis. An explicit name or
sequence loads only those directories, without automatically adding dependency
domains; include every required domain before creating a restricted Session.
To widen the scope, create a new Session with `domains=None` or the complete
required sequence. Catalog loading is metadata-only and needs no separate
source-data audit.

`by=()` is one overall cell. Capture is lazy; `compute()` returns complete
memory data. Continue from `fixed`, rather than from `pending`, when those
values must stay fixed. Persistence is optional: add `--persist` to the entry
command when a saved Artifact is needed. It saves the completed result without
reevaluating its source and prints the Session and Artifact IDs. In Python, the
same continuation is:

```python
saved = fixed.persist()
saved.artifact.show()
```

Saved standalone reproduction scripts should create a fresh Session by default
and accept a continuation Session ID as an explicit runtime argument, rather
than unconditionally resuming the original Session.
Keep `session.id` and `saved.artifact.id`; use exact recovery only when
intentionally continuing that saved Session and its frozen scope:

```python
resumed = mv.session.resume(session.id)
restored = resumed.persisted_frame(saved.artifact.id)
restored.show()
```

For missing syntax, use `import marivo` and a focused exact target such as
`marivo.help("analysis.session.observe")`, or the public bound callable
`marivo.help(session.observe)`. If the target is unknown, start at
`marivo.help("analysis")` and follow its printed targets rather than guessing
paths. `analysis.inputs.catalog` owns input selection; `semantic.expressions`
owns composition.

## Reproduce on a separate synthetic project

The local fixture defines additive readings in one declared unit. Its groups
include a null reading, a real zero and keys present on only one side. It is a
syntax demonstration, with no business definition or coverage inference for
another project.

From this repository root, copy this directory into a fresh temporary project:

```sh
example_python="$PWD/.venv/bin/python"
example_project=$(mktemp -d)
cp -R examples/analysis/. "$example_project/"
cd "$example_project"
"$example_python" seed.py
"$example_python" entry.py sample.measurement
"$example_python" entry.py sample.measurement --field sample.readings.group
```

`seed.py` replaces only the copied project's `readings` table. Its default
starting date is today; it prints that date. An ISO starting date can be supplied
as the first argument to `seed.py` to reproduce the same fixture later.

For shared source conditions, complete-axis comparison or paired association,
continue with [the axis examples](AXES.md).
