# marivo-semantic readiness reference

Semantic readiness is the final validation step before handing semantic refs to
`marivo-analysis`.

## Standard API

Use `project.readiness(...)` after project reload, raw datasource preview,
semantic preview, and parity checks:

```python
import marivo.analysis as mv
import marivo.semantic as ms

project = ms.find_project()
assert project is not None
project.load()

backend_factory = lambda name: mv.datasources.build_backend(name)

report = project.readiness(
    strict_provenance=True,
    require_preview=True,
    raw_previews=("sales.orders",),
    primary_keys_sampled=("sales.orders",),
    backend_factory=backend_factory,
)

print(report.status)
print(report.to_dict())
```

`backend_factory` is a callable from datasource semantic id to backend. Do not
pass a backend instance directly.

## CLI/check helper

For machine-readable checks:

```bash
.venv/bin/python -m marivo.semantic.check --root .marivo/semantic --format=json --readiness --raw-preview sales.orders
```

Use `--no-require-preview` only when live backend validation is intentionally
out of scope for the current run. Record that limitation in the handoff.

## Evidence inputs

- `raw_previews=(...)` records bounded raw table previews collected with `mv.datasources.preview(...)`.
- `failed_raw_previews=(...)` records raw preview attempts that failed.
- `knowledge_documents=(...)` records source docs or knowledge-base refs used in definitions.
- `user_confirmations=(...)` records user-provided business decisions.
- `confirmed_relationships=(...)` suppresses join-key confirmation blockers.
- `primary_keys_sampled=(...)` suppresses primary-key sampling warnings.
- `raw_sql_required_refs=(...)` blocks refs that cannot be expressed through the semantic API.

## Blockers

Do not hand refs to `marivo-analysis` when any blocker remains:

- project load or reload failed
- datasource required for validation is unreachable
- new dataset lacks raw preview evidence
- required comments, knowledge, or user confirmation are missing
- dataset, field, time field, or metric preview failed
- metric materialization or compilation failed
- metric source SQL parity is drifted
- metric is unverified in a strict workflow
- relationship join keys are unconfirmed
- metric spans multiple datasources in a workflow without federation support
- metric body requires raw SQL to express the business logic

## Warnings

Warnings may allow analysis handoff when the user accepts the residual risk:

- metric is explicitly `declared_status="python_native"`
- preview sample is small but materialization succeeds
- primary key uniqueness was not sampled
- string refs resolve but are refactor-fragile
- comments are missing but source SQL, knowledge, and user confirmation are sufficient

## Parity status rules

- `drifted` blocks readiness.
- `unverified` blocks strict readiness and is otherwise a warning.
- `python_native` is visible as a warning but does not block by itself.
- derived metrics inherit the weakest component status.
