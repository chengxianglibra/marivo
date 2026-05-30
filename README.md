# Marivo

Marivo is now a Python-native library for declaring semantic models and running
typed analysis workflows from code. The public surface is intentionally small:

- `marivo.datasource` for project-local datasource declarations.
- `marivo.semantic` for datasets, fields, time fields, metrics, and relationships.
- `marivo.analysis` for sessions, frames, windows, evidence, and analysis intents.
- `marivo-skills/marivo-*` for agent-facing examples and usage guidance.

Marivo is not a text-to-SQL wrapper. Python declarations are the contract, ibis
expressions are the execution language, and analysis output is stored under the
project's `.marivo/` directory.

## Installation

```bash
pip install marivo
```

Optional extras:

```bash
pip install "marivo[duckdb]"
pip install "marivo[mysql]"
pip install "marivo[trino]"
pip install "marivo[all]"
```

## Quick Start

Create datasource declarations under `.marivo/datasource/`:

```python
import marivo.datasource as md

warehouse = md.DatasourceSpec(
    name="warehouse",
    backend_type="duckdb",
    path="warehouse.duckdb",
)
md.datasource(warehouse)
```

Create semantic declarations under `.marivo/semantic/<model>/`:

```python
import marivo.datasource as md
import marivo.semantic as ms

ms.model(name="sales", default=True)
warehouse = md.ref("warehouse")


@ms.dataset(name="orders", datasource=warehouse)
def orders(backend):
    return backend.table("orders")


@ms.time_field(dataset=orders, data_type="date", granularity="day")
def order_date(orders):
    return orders.created_at.cast("date")


@ms.metric(datasets=[orders], decomposition=ms.sum(), name="revenue")
def revenue(orders):
    return orders.amount.sum()
```

Run analysis from Python:

```python
import marivo.analysis as mv

session = mv.session.start(project_root=".")
current = session.observe("sales.revenue", window="2026-01-01..2026-01-31")
baseline = session.observe("sales.revenue", window="2025-01-01..2025-01-31")
delta = session.compare(current, baseline, compare_type="yoy")

print(delta.summary())
```

## Development

Set up a local editable install:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,duckdb,trino]"
```

Repository commands use the local virtualenv explicitly:

```bash
make lint
make typecheck
make examples-check
make test
make check
```

Build and validate package artifacts:

```bash
make pypi-build
make pypi-check
```

## Agent Skills

The maintained agent guidance lives in:

- `marivo-skills/marivo-semantic`
- `marivo-skills/marivo-analysis`

Examples under each skill are executable and checked by `make examples-check`.
