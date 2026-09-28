# R4.4 Runtime diagnostic observation

Baseline: `2da1b6963bae501aff340097f7bb3df177a7d881`.
The initial working tree was clean (no tracked diff or untracked files).
This file is a tool-output summary, not a full console transcript.

Command run during implementation:

```sh
make runtime-test TESTS='tests/test_analysis_graph_publication_r44.py tests/test_analysis_dsl_j1_runtime.py tests/test_lazy_runtime_concurrency.py tests/test_lazy_reconciliation_snapshot.py'
```

Observed: **16 passed, 8 failed**, exit 2, 98.29 seconds.
All eight failures belong to `tests/test_lazy_runtime_concurrency.py`:

- `test_activation_is_guarded_and_existing_handle_owner_is_stable`
- `test_different_sessions_overlap_inside_real_duckdb_queries[local]`
- `test_busy_contender_preserves_real_producer[thread-same-local]`
- `test_busy_contender_preserves_real_producer[thread-different-local]`
- `test_busy_contender_preserves_real_producer[process-same-local]`
- `test_busy_contender_preserves_real_producer[process-different-local]`
- `test_busy_contender_preserves_real_producer[reentrant-same-local]`
- `test_busy_contender_preserves_real_producer[reentrant-different-local]`

The direct failures are the existing `dataset.source_admission` rejection of
legacy `session.observe` routes. Thread/process variants wait for a quality
hook that the rejected execution cannot reach; the overlap case also waits for
an execution hook. No test was skipped, relaxed, or redirected to legacy SQL.

A clean managed worktree at the baseline SHA reproduced the exact same eight
failures and three passes with:

```sh
PYTHONPATH=/Users/lichengxiang/.codex/worktrees/r44-baseline/marivo \
/Users/lichengxiang/source/oss/marivo/.venv/bin/pytest \
  -m runtime -n 2 --tb=short -q tests/test_lazy_runtime_concurrency.py
```

The worktree was clean before the check; the command ran with that worktree as
its working directory. See `baseline-concurrency.log` for the full output
(**8 failed, 3 passed**, exit 1, 93.00 seconds). The temporary baseline worktree
was then retired through the managed archive tool. These failures remain R5
legacy-source migration obligations, not R4.4 acceptance passes.

`targeted.log` is the intermediate 222-test directed regression after the
receipt/commit safeguards. Final `check-agent.log` supersedes it for the final
source candidate, including the subsequent method-owner routing and independent
capture counterexamples. Final `runtime.log` records the v7 process and v6 J1
Runtime selection, separately from the failed legacy concurrency selection.
