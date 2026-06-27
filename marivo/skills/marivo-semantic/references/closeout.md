# marivo-semantic closeout

Closeout decides whether authored semantic refs are ready for
`marivo-analysis`.

## Reload

```python
catalog = ms.load()
```

## Readiness Gate

Run `ms.readiness(...)` for the refs that will be handed to analysis:

```python
report = ms.readiness(refs=("sales.orders", "sales.revenue"))
report.show()
if report.status == "blocked":
    raise SystemExit("Semantic project is not ready for analysis handoff.")
```

Do not hand blocked refs to `marivo-analysis`.

Use dedicated runtime checks separately when needed:

- `md.discover_*` for datasource evidence;
- `catalog.preview(...)` for semantic preview;
- `ms.parity_check(...)` for source-SQL parity;
- `ms.richness(...)` for enrichment gaps.

Warnings are not the same as blockers. Fix blockers first, then report warning
items as follow-up work when they do not prevent the requested handoff.
