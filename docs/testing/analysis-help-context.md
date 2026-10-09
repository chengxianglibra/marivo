# Analysis disclosure context budget

The [disclosure journey tests](../../tests/surface/test_analysis_context_budget.py)
measure captured characters and reading pages as context proxies, not model
tokens, task latency or real-Agent efficiency. The
[baseline data](../../tests/surface/analysis_context_baseline.json) owns the
reference revision, complete outputs and metrics.

## Measurement and assertions

The Runtime journey reads public Help, real DSL contracts, retained previews
and structured error repair. Every read counts in full, including repeated
pages; callable and string Help targets share canonical page identity.

Normalization replaces explicit project/package/interpreter paths and known
generated Artifact/Run identities. Business values, inputs, constraints,
actions and repairs remain intact. Reports retain both raw and normalized
character counts.

`test_task_context_budget` owns the thresholds: at least 15% aggregate normalized
character reduction from the baseline, at most 10% growth per journey, no page
growth, and fewer pages for the current-row mean journey. Daily tests check
repeated-read accounting and complete baseline outputs.

## Reproduction

Run the Runtime assertions with recording mode unset:

```sh
env -u MARIVO_HELP_BUDGET_RECORD make runtime-test-agent TESTS='tests/surface/test_analysis_context_budget.py::test_task_context_budget'
```

Collect complete current outputs for inspection:

```sh
MARIVO_HELP_BUDGET_RECORD=/tmp/marivo-help-context.json make runtime-test-agent TESTS='tests/surface/test_analysis_context_budget.py::test_task_context_budget'
```

Recording mode collects evidence and bypasses the reduction assertions.
The budget check does not establish backend, installed-package, release or
real-Agent qualification; see [execution gates](runtime-coverage.md).
