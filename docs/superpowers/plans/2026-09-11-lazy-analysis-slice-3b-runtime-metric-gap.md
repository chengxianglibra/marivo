# Slice 3b prerequisite: Runtime Metric observation handoff

## Finding and ownership

Status: **resolved and accepted in the isolated candidate** after integrating
`5ae1de7eac4d8a610eda764b8641904894b09603`. The reproduction below records the
original gap at `a813d2f8`; it is not the current behavior.

The Observation Model explicitly includes `RuntimeMetricExpr` in
`MetricObservationInput` and requires the same governed Entity/aggregation
contracts as catalog Metrics. The native Observation Help provider also advertises
this input, and the accepted public manifest retains `runtime_metric`.

However, `make_observation` in `marivo/analysis/observation/metric.py` rejects
`RuntimeMetricExpr` before constructing a Metric graph, with the message
`runtime expression outside this slice`. Its successful native Help examples
construct expressions without sending them through `Session.observe`, so they
do not establish this required end-to-end capability.

The public-cutover plan assigns missing private implementation back to its
original Slice. This gap belongs to Slice 3b and its semantic graph prerequisite;
it must not be hidden by removing the public promise, narrowing the accepted
manifest, adding a stub, or claiming Slice 8a completion.

## Reproduction

The assembly directory contains `reproduce_runtime_metric_gap.py` and the
captured `runtime-metric-gap.json`. The script creates a temporary authored
project with an explicitly typed Entity, a governed Measure and a catalog sum
Metric. It acquires one public Session and resolves the Measure through that
Session's catalog. The catalog Metric is the positive construction control.

```python
session = mv.session.get_or_create("reproduction", report_timezone="UTC")
measure = session.catalog.require(ms.ref.measure("sales.orders.amount")).ref
control = session.observe(ms.ref.metric("sales.revenue"))
expression = mv.runtime_metric.aggregate(measure, agg="sum", label="runtime_total")
result = session.observe(expression)
```

Observed result:

- control: `LogicalMetricDataset`;
- expression: `RuntimeAggregateExpr`;
- failure: `DatasetConstructionError` at `observation.construction`;
- expected: `initial governed catalog Metric graph`;
- received: `runtime expression outside this slice`;
- zero admitted Runs and zero datasource statements.

This isolates the absent input implementation from storage, Parquet transport,
source availability and execution resource limits.

## Required private acceptance before resuming assembly

Implement normalization and execution of the declared Runtime Metric inputs in
the original Observation/semantic graph owner. Preserve ordered multi-Metric
identity, governed leaves and common Entity spine, label validation, filters,
fold authority, complete dependency retention, same-Session authority and exact
cold recovery. Exercise real `Session.observe(expression)` construction and
execution for the admitted constructors and nested graphs, with independent
numerical and adversarial tests. Construction must remain free of datasource
I/O; invalid ownership and labels must fail before Run admission.

Publish candidate-bound private acceptance evidence. Slice 8a can then rebase
its isolated assembly onto that completed input and rerun affected integration
gates. Final public activation remains Slice 8b.


## Integrated acceptance on 2026-09-11

All 32 files in the private supplement were integrated together. The post-review
private manifest matches all 1,442 source files at the new source HEAD. Its
recorded 7,221 default tests and 53 Runtime tests remain private prerequisite
evidence; they are not reused as proof of the public candidate.

The isolated candidate passed 1,953 Dataset default tests, full typing of 331
source modules, formatting/lint/import contracts, and 26 packaged-skill/timezone
tests. Runtime integration covers 70 distinct cases: 28 public/Runtime Metric/
Parquet acceptance cases and 42 adjacent retained/distinct/distribution cases.
The adjacent run passed 41 cases; the remaining corruption test expected the
retired database error wording. Its assertion now requires the actual Parquet
size-integrity error and passed independently. No implementation fallback or
error suppression was introduced.

Public `Session.observe(runtime_metric.aggregate(...))` now constructs without
source work, executes the independent sum oracle 751.5, and projects through an
owned retained field after cold Session recovery with the source file offline.
The three-process mixed-forest journey verifies nested filters, weighted/linear
and ratio results, absence of source connection/compilation, and zero work on
exact cold hits. Parquet native queries are allowed for new retained operations;
source-fence and forbidden-source-call assertions preserve the no-origin proof.

Both percentile methods retain exact implementation identity. Missing and
frequency-mutated private Parquet parts reject consumption while primary rows
remain readable. Public Help for Metric projection now exposes current
DatasetFieldRef selection and cold `fields.get(name)` acquisition.

The assembly directory owns `3b-prerequisite-verification.json`, the integration
logs and refreshed checkpoint. This closes this prerequisite only. The broad
8a gate still fails on unported eager test imports; complete current disclosure,
legacy test replacement and final cutover acceptance remain Slice 8a work.
