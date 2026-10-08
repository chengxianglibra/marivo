# Analysis disclosure context acceptance

This benchmark measures characters and reading pages as context proxies. It
does not measure model tokens, task latency, or real Agent efficiency.

The baseline is commit `7a3d5911c0fd86c68e290f94b28c496bdd485106` on `panda`,
with a clean worktree before implementation. The same public-journey harness
was copied into an isolated Git archive of that commit. The checked-in
`tests/surface/analysis_context_baseline.json` retains the complete raw output,
normalized output, metrics, and revision. No pre-existing worktree edits were
included or changed.

## Reading boundary

`test_task_context_budget` performs eight fixed journeys through the public
Help coordinator, real DSL contracts, retained result previews, and a real
structured error. Each captured output counts in full. Repeated reads count
again; canonical Help identity detects a repeated page even when one read uses
a callable and another uses its string target. A separate regression verifies
that accounting. These eight journeys have no repeated page within a journey;
the same root read in several journeys is charged separately each time.

Only explicit project/package/interpreter paths and known generated Artifact
and Run identities are normalized. Business values, required inputs,
constraints, contract actions, and repairs are retained. Raw character counts
are reported alongside normalized counts. Logical and Materialized contracts
are unchanged and remain part of the reading cost.

| Journey | Normalized before | Normalized after | Reduction | Raw before | Raw after | Pages before/after |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Member observation and original rollup | 10,694 | 9,273 | 13.29% | 10,820 | 9,398 | 9 / 9 |
| Current-row mean | 6,333 | 3,898 | 38.45% | 6,459 | 4,023 | 6 / 5 |
| Comparison and ratio | 5,044 | 4,645 | 7.91% | 5,044 | 4,645 | 4 / 4 |
| Filtered member recovery | 3,429 | 3,052 | 10.99% | 3,429 | 3,052 | 4 / 4 |
| Materialized fixed statistic | 3,005 | 2,657 | 11.58% | 3,031 | 2,683 | 3 / 3 |
| Spearman association | 4,482 | 3,880 | 13.43% | 4,577 | 3,974 | 5 / 5 |
| Missing Subject repair | 4,217 | 3,626 | 14.01% | 4,312 | 3,720 | 5 / 5 |
| Drift forecast | 4,443 | 3,944 | 11.23% | 4,538 | 4,038 | 5 / 5 |
| **Total** | **41,647** | **34,975** | **16.02%** | **42,210** | **35,533** | **41 / 40** |

All journeys shrink, none gains a page, and cumulative normalized characters
fall more than the required 15%. Cold discovery of `summarize` removes one
navigation page: `analysis.methods` now directly links to
`analysis.methods.metric.summary`. Comparison and fixed statistics also select
an exact Help target directly from the current contract, without reading a
method directory or type page. Existing single-page structure budgets remain
hard failures; no new truncation or budget relaxation is used.

## Behavioral verification

Independent tests cover parameter meaning, truthful acquisition paths,
resolvable type producers, complete family membership, signature/field/method
reflection, missing example inputs, invalid family declarations, syntax,
navigation reachability, and page budgets. Correlation receivers already share
one method and keep its canonical target; only actual receiver variants share
a discovery-family summary.

Rendered focused examples execute observation, selection, current-row mean,
ranking, ratio, Spearman association, and drift forecasting on real public DSL
objects. Their return types, retained state, and numerical forecast are checked.
The fixed-statistic journey executes after its source file is made unavailable;
the missing-Subject journey checks the structured error and its repair route.
The existing bilingual statistical example remains an executable check.

Verification on this worktree:

- `make check-agent`: lint, import contracts, typing (328 source files),
  daily tests (4,805 passed, one skipped), and API documentation passed.
- Focused Runtime disclosure, cumulative-budget, and bilingual-example checks:
  nine passed.
- `npm run verify:content`: 343 required site files verified.
- `npm run build`: Astro checks/build and postbuild install-script/documentation
  route verification passed (308 original documentation routes).
- New regression modules also pass strict targeted typing with imported
  fixture diagnostics suppressed; the production broad typing gate is unchanged.
- Comparing the public DSL AST after removing docstrings confirms unchanged
  executable bodies and signatures. Public export snapshot checks remain in
  the daily gate.

The environment is local DuckDB and retained local Artifact storage. These
checks do not qualify every backend, installed-wheel/cold-process recovery,
the full Runtime release suite, or real Agent behavior. Packaged skills are
unchanged.

## Reproduction

Run the ordinary acceptance path (recording mode must be unset):

```sh
make runtime-test-agent TESTS='tests/surface/test_analysis_context_budget.py::test_task_context_budget'
```

To inspect complete current outputs, explicitly collect a report:

```sh
MARIVO_HELP_BUDGET_RECORD=/tmp/marivo-help-context.json make runtime-test-agent TESTS='tests/surface/test_analysis_context_budget.py::test_task_context_budget'
```

Recording mode collects evidence; it does not run the reduction assertions.
The ordinary test enforces at least 15% aggregate reduction, at most 10%
growth per journey, no page growth, and the removed cold-summary page.
