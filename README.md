# Marivo

Metric-centered analysis runtime for AI agents.

Marivo is a Python library — not a hosted service or a chat UI — that turns a
data warehouse into something an AI agent can analyze *reliably*. It is not a
text-to-SQL wrapper: Python declarations are the contract, ibis expressions are
the execution language, and typed frames are the boundary between analysis steps.

- **Unified semantics** — Datasources, entities, metrics, dimensions, and
  relationships are declared in Python and addressed by semantic ref
  (`sales.revenue`), not raw table or column names.
- **Metric-centered analysis** — Sessions start from a trusted metric and chain
  typed intents (observe, compare, decompose, correlate, forecast), each
  returning a typed frame.
- **Trustworthy evidence** — Every operator records findings, propositions, and
  assessments, so any conclusion can be traced back to its inputs.
- **Readiness gates** — `catalog.readiness()` blocks incomplete semantic objects
  from analysis until authoring and access issues are resolved.

## Requirements

Python 3.12 or newer.

## Installation

```bash
pip install marivo
```

Install the backend extra that matches your datasource:

| Backend | Install command |
| --- | --- |
| DuckDB | `pip install "marivo[duckdb]"` |
| MySQL | `pip install "marivo[mysql]"` |
| Postgres | `pip install "marivo[postgres]"` |
| ClickHouse | `pip install "marivo[clickhouse]"` |
| Trino | `pip install "marivo[trino]"` |
| All packaged backends | `pip install "marivo[all]"` |

Marivo is deployed by installing the library where the agent runs, checking in
the `models/` declarations, and providing datasource secrets through environment
variables referenced by `*_env` fields. There is no separate server.

## Quick Start

Scaffold a project with the CLI:

```bash
marivo --version
marivo init
```

`marivo init` creates the project skeleton (`marivo.toml`, `models/`, `.marivo/`)
and installs the `marivo-semantic` and `marivo-analysis` skills for Claude Code
and Codex, so a coding agent can author the semantic layer with you.

Declare semantics under `models/semantic/`, then load and analyze:

```python
import marivo.semantic as ms
import marivo.analysis as mv

catalog = ms.load()
report = catalog.readiness()
if report.status == "blocked":
    report.show()                       # resolve blockers before analysis

session = mv.session.get_or_create(name="revenue-check", question="Why did Q4 drop?")
revenue = session.catalog.get("sales.revenue")
region = session.catalog.get("sales.orders.region")

current = session.observe(revenue, timescope={"start": "2026-10-01", "end": "2027-01-01"}, grain="month", dimensions=[region])
baseline = session.observe(revenue, timescope={"start": "2025-10-01", "end": "2026-01-01"}, grain="month", dimensions=[region])

delta = session.compare(current, baseline)
session.decompose(delta, axis=region).show()
```

In-project help is available at every surface: `ms.help()` and `mv.help()` list
the surface; `ms.help("metric")` and `mv.help("observe")` drill into a symbol.

## Documentation

Full guides live in the documentation site (built from [`site/`](site/)):

- **Installation** — install, `marivo init`, deploy.
- **Quick Start** — author declarations, load a catalog, run a first analysis.
- **Concepts** — semantic layer, analysis workflow, readiness, and evidence.

Maintained agent guidance lives in `marivo/skills/marivo-semantic` and
`marivo/skills/marivo-analysis`.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,duckdb,trino]"
```

Repository commands run through the local virtualenv via `make`:

```bash
make lint
make typecheck
make examples-check
make test
make check
```

Read [`agent-guide.md`](agent-guide.md) before contributing. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the full workflow.
</content>
</invoke>
