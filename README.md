# Marivo

[简体中文](README.zh-CN.md) | English

Metric-centered analysis runtime for AI agents.

Marivo is a Python library — not a hosted service or a chat UI — that turns a
data warehouse into something an AI agent can analyze *reliably*. It gives
agents three things they otherwise lack: a typed **semantic layer** that fixes
what every metric and dimension means, a **metric-centered analysis workflow**
of typed operators, and an **auditable evidence trail** behind every conclusion.

Marivo is not a text-to-SQL wrapper. Python declarations are the contract, ibis
expressions are the execution language, and typed frames are the boundary
between analysis steps — an agent composes trusted building blocks instead of
generating raw SQL and hoping it is correct.

- **Unified semantics** — Datasources, entities, metrics, dimensions, and
  relationships are declared in Python and addressed by semantic ref
  (`sales.revenue`), not raw table or column names.
- **Metric-centered analysis** — Sessions start from a trusted metric and chain
  typed intents (observe, compare, attribute, correlate, forecast), each
  returning a typed frame.
- **Auditable evidence** — Operators record findings, propositions, and
  assessments, so every result can be traced back to the inputs that produced it.
- **Readiness gates** — `catalog.readiness()` blocks incomplete semantic objects
  from analysis until authoring and access issues are resolved.

## Why Marivo

Point an agent at a raw warehouse and ask it to write SQL, and the failure modes
are predictable: invented joins, dropped filters, ambiguous columns, and
confident answers no one can verify. Every query starts from zero, so nothing
accumulates and nothing is reviewable.

Marivo replaces *generate-SQL-and-hope* with a contract the agent works through:

| Without a semantic runtime | With Marivo |
|---|---|
| Table and column names guessed per query | Trusted **semantic refs** (`sales.revenue`) carrying human-authored meaning and guardrails |
| Definitions re-derived every time | One **declared contract** in version control, shared across agents and sessions |
| Free-form SQL; mistakes surface as wrong numbers | **Typed intents and frames** — invalid steps fail loudly, before any backend work |
| Conclusions you cannot check | An **evidence trail** linking every result to the inputs that produced it |
| Half-specified models analyzed anyway | A **readiness gate** that blocks incomplete semantics from analysis |

## How it works

1. **Declare** datasources in `models/datasources/` and semantics (domains,
   entities, dimensions, metrics) in `models/semantic/` — using
   `marivo.datasource` (`md`) and `marivo.semantic` (`ms`).

2. **Load and gate.** `ms.load()` builds the catalog; `ms.readiness()` blocks
   analysis until every object an agent needs is complete and reachable.

3. **Open a session** from a guiding question and resolve a trusted metric from
   the catalog.

4. **Chain typed intents** — `observe → compare → attribute`, and more — each
   returning a typed frame and leaving an evidence trail.

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

If setup fails, run the read-only doctor first:

```bash
marivo doctor
marivo doctor --semantic
marivo doctor --datasource warehouse --connect
```

The default doctor checks installation, project layout, datasource declarations,
backend extras, secret references, and existing `.marivo/` state without
connecting to databases or writing secrets. Use `--semantic` for semantic
load/readiness diagnostics and `--connect` only when you intentionally want a
live datasource round-trip.

## Quick Start

Scaffold a project with the CLI:

```bash
marivo --version
marivo init
```

`marivo init` creates the project skeleton (`marivo.toml`, `models/`, `.marivo/`)
and installs the `marivo-semantic` and `marivo-analysis` skills into the shared
`.agents/skills` directory plus Claude Code and Codex compatibility directories,
so a coding agent can author the semantic layer with you.

Declare semantics under `models/semantic/`, then load and analyze:

```python
import marivo.semantic as ms
import marivo.analysis as mv

catalog = ms.load()
report = catalog.readiness()
if report.status == "blocked":
    report.show()                       # resolve blockers before analysis

session = mv.session.get_or_create(name="revenue-check", question="Why did Q4 drop?")
revenue = session.catalog.get("metric.sales.revenue")
region = session.catalog.get("dimension.sales.orders.region")

current = session.observe(revenue, time_scope={"start": "2026-10-01", "end": "2027-01-01"}, grain="month", dimensions=[region])
baseline = session.observe(revenue, time_scope={"start": "2025-10-01", "end": "2026-01-01"}, grain="month", dimensions=[region])

delta = session.compare(current, baseline)
attribution = session.attribute(delta, axes=[region])
attribution.show()
```

In-project help is available at every surface: `ms.help()` and `mv.help()` list
the surface; `ms.help("metric")` and `mv.help("observe")` drill into a symbol.

## Publish

Upload finished artifacts to S3:

```bash
marivo publish ./report.html
marivo publish ./output-dir
```

Configure non-secret S3 settings in `marivo.toml` and credentials in
`~/.marivo/secrets.toml`. See the [documentation](https://marivo.io/en/latest/installation/#deploy-and-develop) for details.

## Documentation

Full guides at [marivo.io](https://marivo.io/en/latest/):

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
make format
make lint
make typecheck
make examples-check
make test
make check
```

Read [`agent-guide.md`](agent-guide.md) before contributing. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the full workflow.