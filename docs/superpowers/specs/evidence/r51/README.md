# R5.1 static freeze evidence

Status: documentation/static checks only. No new product, source, fixed, Runtime,
backend or installed-wheel qualification is claimed. Existing 14 skips and eight
historical failures are not executed or reclassified by this slice.

Baseline is `panda` / `dd42c7cca070fba60d6a2761331d86dbaca135ce`; see
[baseline.json](baseline.json) for initial Git state and hashes. The only initial
untracked file was the R5 implementation plan. R4.6 was already committed.

## Evidence files

- [consumer-snapshot.json](consumer-snapshot.json): AST snapshot over `marivo/`,
  `tests/` and `devtools/` for its explicit selected modules and observation prefix.
  Contains source SHA-256, definitions, direct imports, alias-resolved call sites,
  registration candidates, SQL/execution candidates and dynamic module strings.
  It does not resolve receiver types or prove runtime reachability. M01-M17 in
  the [migration ledger](../../2026-09-28-marivo-full-algebra-dsl-r5-migration-ledger.md)
  supply ownership, actual consumer interpretation and deletion gates.
- [debt-collection.log](debt-collection.log): real collection output, 103 nodes,
  exit 0; all D01-D22 node IDs are present. This is not 103 passing tests.
- [debt-mapping.json](debt-mapping.json): exact node IDs, current skip decorators
  or historical failure status, owner and oracle copied from the ledger and
  checked against collection/source. No numerical oracle was executed here.
- [static-checks.json](static-checks.json): results of document links/anchors,
  complete F/M/V/D identifiers, debt/source digests, no source/test/index changes,
  withdrawn weight boundary, and whitespace checks. Existing unrelated broken
  document links are outside the added-line check and are not silently repaired.
- [manifest.json](manifest.json): hashes of changed/new documentation and evidence,
  excluding itself; identifies the final document candidate without a commit.

## Commands and verification boundary

Collection was run from the repository root using the repository interpreter:

```sh
.venv/bin/pytest --collect-only -q -m '' tests/test_lazy_source_algebra.py tests/test_lazy_local_placement.py tests/test_lazy_status_fold_admission.py tests/test_lazy_retained_compiler.py tests/test_sqlite_semantic_integration.py tests/test_lazy_runtime_concurrency.py
```

The captured stdout is debt-collection.log. Collection uses no test body and does
not change skip markers. The eight Runtime failures retain their prior evidence
in [R4.4 baseline log](../r44/baseline-concurrency.log) and
[diagnosis](../r44/runtime-initial-observation.md).

Static checks use `.venv/bin/python` with stdlib ast/json/hashlib/pathlib/re:
parse local Markdown links added to tracked documents (all links in new files),
resolve paths/headings, compare all scanned source digests, check every ledger
ID/node against collection and skip decorators, then run `git diff --check`.
The source inventory uses ast Import/ImportFrom alias resolution for Call nodes;
registration/SQL names are candidates, not classifications of business reads.
Dynamic module-string matches cover `tests.*worker*` and selected `marivo.*`
modules. Reviewers can locate each candidate by the saved file/line and verify
its stored source digest at the recorded baseline. Regenerate at each later
package's SHA; this snapshot is not a permanent executable routing registry.

No product tests, Runtime tests, site/API build, broad check-agent, wheel build,
release-check or MinIO were run for R5.1. Only documentation changed, so these are
not required to prove this static freeze; each activating package still owes its
own implementation/disclosure/Runtime gates. No packaged skill was edited.

The user reaffirmed withdrawal of the named statistical-weight interface during
this task. The R5 plan and owners now exclude its dependent Analysis binding and
current-row weighted mean; existing Metric weighted mean remains in scope. This
is a scope correction, not an execution failure or a deferred activation gate.
