# Slice 8b source-checkout activation acceptance

Date: 2026-09-12

Status: **Slice 8b complete in the source checkout**. Installed-package
verification (8c), final backend/adversarial/real-Agent acceptance (9), and
release remain pending.

## Baseline and atomic activation

The authorized task began on `lazy-dataset` at
`6c2c68f85cbf62644cd98045826321f3486df5d1`, tree
`db35fb46327f93fa7929bc868ddb1c58b3d23825`. Git reflog showed that the accepted
Slice 8a candidate had already been fast-forwarded into the source checkout.
All 1,182 tracked file contents matched the final 8a manifest, with no extra
tracked file and none of its 283 deleted files present. The existing
`CLAUDE.md` symlink still targeted unchanged `AGENTS.md`.

The frozen candidate SHA-256 is
`8d78f2e574fabcf5985d7b5f190b4b5b3234bb1d2b83daa82d934f1ae4d4739a`.
Its complete patch independently hashes to
`c7c8d8f2ab2bb415a4f94257f2b757c9845d4f2fa2e6dcf76db8f74ec2b59f06`.
No patch was reapplied and no alternate baseline was assembled.

Fresh inspection matched all 100 ordered exports by native object identity,
callable signatures and rendered focused Help against the 8a interface map.
The deletion inventory and its 123 retired-test-module replacement records were
verified. The current-code/disclosure forbidden-path scan found no matches and
all retired public bindings were absent. Historical release/design records and
the terminal pandas boundary retain the documented scan exceptions.

These facts are recorded in
[the baseline](../../../evidence/slice-8b/2026-09-12-activation/baseline.json),
[assembly reconciliation](../../../evidence/slice-8b/2026-09-12-activation/assembly-reconciliation.json),
[interface mapping](../../../evidence/slice-8b/2026-09-12-activation/interface-mapping.json),
and [forbidden-path audit](../../../evidence/slice-8b/2026-09-12-activation/forbidden-path-audit.json).

## Failure and bounded repair

The first `make check-agent` passed lint/import contracts and typing, then
reported **1 failure and 4,823 passing tests**. API documentation was not reached.
`test_eager_intent_modules_are_deleted` found the retired `intents/` directory
still present in this long-lived checkout. Git had removed its tracked source
but left ignored interpreter caches.

Inspection found only `.pyc` files under `__pycache__` in six fully retired
directories: `analysis/evidence/extraction`, `analysis/executor`,
`analysis/frames`, `analysis/intents`, `analysis/scripts` and `analysis/windows`,
all beneath `marivo/`. None had tracked descendants or symlinks. Exactly 452
cache files and their now-empty directories were removed. No source, test,
assertion, marker or collection rule was changed. The original failure log is
retained; the dedicated import/removed-path regression then passed 45 tests.

The [cleanup inventory](../../../evidence/slice-8b/2026-09-12-activation/retired-cache-cleanup.json)
records every removed cache file and its hash. All 595 pre-existing evidence
files were fingerprinted before this task and preserved.

## Fresh acceptance on the source checkout

Every command used the source checkout, with `PYTHONPATH` explicitly pointing
there. Runtime commands used at most two workers. Each gate verified the same
complete frozen candidate manifest before and after execution.

| Gate | Result |
| --- | --- |
| Focused exports, Dataset protocol, Session/Store, storage configuration, Help, semantic normalization, current examples and skills | 230 passed |
| Import and removed-path regression after cache cleanup | 45 passed |
| Final `make check-agent` | 4,824 default tests; 332 source modules typed; formatting, lint, import contracts and API documentation passed |
| Exact six-module affected Runtime selection from 8a | 46 passed |
| Current EN/ZH documentation and native Help Runtime examples | 3 passed |
| Additional project-storage failure Runtime selection | 5 passed |
| `npm run build` in `site/` | Astro check/build passed; 321 pages; standard and Chinese install-script output verified |
| `npm run verify:content` in `site/` | 343 required site files verified |

The focused and regression counts overlap the broad default gate and are not
added to it. Exact commands, durations, exit codes, before/after fingerprints
and log hashes, including the superseded failure, are in
[the gate receipt](../../../evidence/slice-8b/2026-09-12-activation/gates.json).

Acceptance covers pure logical construction without datasource I/O or Runs,
cross-Session rejection, logical/materialized state protocols, stable Entity
identity separate from version coordinates, and rejection of existing Store
generations 0, 2 and 4 without changing their bytes. Public Session execution
uses default local Parquet and supports source-offline cold reads and Runtime
Metric projection. Distinct and both percentile methods retain exact native
state and reject missing or mutated private parts without origin replay.

Explicit object bindings and versioned private Parquet reads use SDK-stubbed
object storage in this selection. The additional five configuration failures
prove recorded failure without source work or local fallback. No live object
service, full Runtime matrix or installed wheel was exercised. Bilingual
workflow/evidence/tutorial blocks agree; their public execution and native Help
examples pass through the current APIs.

## Final state and handoff to Slice 8c

The implementation, tests and current public documentation remain byte-identical
to the tested candidate. Post-gate changes are this execution record, the
current-status annotations in the cutover plan and 8a record, and one precise
`.gitignore` exception that makes this record visible under the existing
selective-plan-tracking convention. No library implementation repair was required.

The [final handoff](../../../evidence/slice-8b/2026-09-12-activation/handoff-final.json)
binds the unchanged HEAD, complete final file manifest, status-documentation
working-tree patch, final audit, preserved prior evidence and gate-log hashes.
Manifest hashes use SHA-256 over canonical JSON of the sorted `path`/`sha256`
file records (`sort_keys=True`, separators `(',', ':')`); symlink targets and
Git file modes are verified separately. This separates the tested candidate
from the final status-record additions and their ignore exception without
claiming a new tested code state.

Use that exact HEAD plus the recorded working-tree patch for 8c. First verify
the handoff hashes and content before building or installing the package under
8c authorization. No commit, push, installation or publication was performed
by this task. Slice 8b completion does not close parent Slice 8 or establish
release readiness.
