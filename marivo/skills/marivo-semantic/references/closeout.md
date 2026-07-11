# Static verification, scoped preview, and readiness

Reload after writing one Python object and navigate to its typed catalog value:

```python
catalog = ms.load()
orders = catalog.domains.get("sales").entities.get("orders")
region = orders.dimensions.get("region")
```

Close out in this order:

```python
catalog.verify_object(region).show()
catalog.preview(region, using=snapshot).show()
report = catalog.readiness(refs=[region])
report.show()
```

Static verification and readiness execute no datasource query. Preview is the
explicitly scoped runtime check and must use the matching snapshot; multi-entity
objects require an exact entity-keyed snapshot mapping. Readiness consumes fresh
static verification and preview evidence. Do not hand blocked objects to
`marivo-analysis`.

`ms.richness(...)` remains advisory. `ms.parity_check(...)` may run potentially
unbounded metric and provenance SQL and is never required for readiness.
