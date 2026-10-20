# Observe grouping implementation ledger

## Contract and scope

Unify source grouping inputs as `tuple[Deferred | Grouping, ...] | Population`.
Remove `member()` and `MemberAxis` from every public entry point. Keep complete
identity, membership allocation, frozen calendars, member completion, ownership,
and stored definitions unchanged. Public wrappers never enter stored graphs.
Do not edit AGENTS.md or packaged skills; do not commit, push, or publish.

## Baseline

- Initial working tree: only untracked `attribute-implementation-plan.md`; preserve it.
- Baseline focused test log: `/tmp/marivo-grouping-baseline.log` (53 passed, 21 warnings, 14.64s).
- Execution owner: primary agent; no delegated implementation or review.

## Stages

| Stage | Dependency | Status |
| --- | --- | --- |
| S0 baseline and consumer inventory | none | complete |
| S1 neutral Grouping family and public capture | S0 | complete |
| S2 consumers, expressions, audit, public cutover | S1 | complete |
| S3 fixtures, tests, Help and current EN/ZH docs | S2 | complete |
| S4 frozen-source broad gate and contract review | S3 | complete |

## Acceptance

| ID | Evidence required | Status |
| --- | --- | --- |
| A01 | complete identities, composite keys, routes, anchors, ordered axes | complete |
| A02 | axes versus complete domains, empties, coverage, member_count | complete |
| A03 | correlated membership, independent factors, root/bucket deduplication | complete |
| A04 | independent observation scope and frozen output calendar | complete |
| A05 | project/classify/coordinates/values/per admission boundaries | complete |
| A06 | ownership, fixed selections, audit, persistence and cold reads | complete |
| A07 | exports, typing, Help, budgets, retired public symbols | complete |

## Validation and review

Record raw failures and corrections below. Run focused checks before the unrestricted
`make check-agent`. At most two contract-review rounds; unresolved P2+ blocks completion.
Evidence is static/local/cold recovery, not remote Runtime or release acceptance.

- Initial cutover run: 51 passed, 2 failed; raw log `/tmp/marivo-grouping-focused.log`.
  Failures were a helper migration missing explicit members and a retired raw-axis assertion; corrected.

- Focused migration run: 116 passed, one new test failed by requiring equal capture
  metadata for explicit and inferred routes. Corrected the oracle to compare final
  contribution bindings; four new identity/calendar/admission cases passed in 3.32s.
- Full typing passed (305 files). Preliminary broad gate log:
  `/tmp/marivo-grouping-gate1.log`; source was still receiving disclosure and
  semantic-admission corrections, so this is diagnostic rather than final evidence.
- Contract review round 1: narrowed Metric.per to public Grouping constants,
  preserving the existing rejection of raw internal axis records; unified calendar
  capture between by, during and product; registered private variants with native Help.

- Diagnostic broad gate: 4751 passed, 10 failed, 630 warnings, 239.96s.
  Raw failures retained in `/tmp/marivo-grouping-gate1.log`. Failure classes:
  pinned export order (2), old public-axis assertions/internal predicate test input (4),
  a conditional helper substitution (3), and the already corrected path oracle (1).
  No production numerical oracle was changed.
- All repaired scopes: 85 passed, 26 warnings in 13.21s;
  `/tmp/marivo-grouping-repairs.log`. Shared capture/audit scope: 38 passed,
  19 warnings in 14.76s; `/tmp/marivo-grouping-final-focused.log`.
- Contract review round 2: producer-to-capture-to-binding-to-audit/storage traced;
  complete member dependencies, allocation factors, calendar scope and internal-only
  records retained. No unresolved P2+ finding. No third review round is planned.

- Frozen-source full gate: lint, import contracts, typing and all 4761 default tests
  passed (635 warnings, 183.15s). Final API-doc stage found three retired symbols
  in `docs/api/analysis.rst`; replaced them with Grouping/identity and added the
  source grouping versus complete-domain distinction. Python source unchanged.
  Raw run: `/tmp/marivo-grouping-final-gate.log`.

- API-doc follow-up found a Google-style docstring spacing error in identity;
  corrected only section spacing. `make docs-api-agent` then passed; raw logs:
  `/tmp/marivo-grouping-docs.log`, `/tmp/marivo-grouping-docs2.log`.
- Refreezing the same 40 Python files after that docstring-only correction and
  rerunning the full unrestricted command to obtain one successful final gate.

## Final acceptance

### Requested review fixes

- P1: migrate declaration AST dependency admission from the retired public
  membership subclass to Grouping; verify real model loading for field and
  membership samples, both directly and inside tuples.
- P2: disclose grouping admission separately for coordinates, membership,
  project, classify, values and observe in their owning Live Help pages.
- Both fixes implemented. New regression tests failed against the pre-fix source
  (`review-fixes-red.log`); declared-body and self-reference assertions were then
  corrected to respect the existing named Metric representation. All 30 focused
  tests passed; changed modules passed focused typing and lint/format checks.
- Source frozen for a new unrestricted final gate. Logs and source hashes for
  this follow-up are retained separately under `evidence/observe-grouping/`.
- First follow-up broad gate: 4770 passed, one stale observe Help wording
  assertion failed (635 warnings, 220.12s). Updated that assertion to check the
  new Grouping kinds and matching identity completion contract; raw failure
  retained in `review-fixes-gate-failed.log`. Refreeze before the final rerun.
- Final rerun stopped at formatting checks for population.py and semantic/ir.py
  while a separate public-surface optimization was editing the shared tree.
  Those concurrent changes are preserved. The current-tree focused verification
  passed all 36 tests in 5.19s, typing for all three changed production modules,
  and lint/format/import checks for the six files in this review fix.
- Both review findings are fixed. Final unrestricted acceptance of the combined
  tree remains pending the concurrent work's completion; this follow-up does
  not claim a green broad, Runtime or release gate. Evidence:
  `review-fixes-gate-final.log`, `review-fixes-current-focused.log`, and
  `review-fixes-owned-source.json`.

### Original implementation acceptance (historical)

- Unrestricted `make check-agent` passed: lint/format, import contracts, typing
  (305 source files), 4761 default tests (635 existing-library warnings, 203.37s),
  and Sphinx API documentation. No Runtime/release qualification is claimed.
- Final hashes of all 40 changed Python files match the refrozen source manifest.
- Raw baseline, failed gates, documentation failure and successful gate are retained
  under `evidence/observe-grouping/`; `final-source.json` records the frozen hashes.
- A01/A04/A05: `test_grouping_inputs.py` and explicit versioned-path acceptance.
  A02/A03: existing observation-grain, member-count and membership numerical oracles.
  A06: full existing temporal bridge, fixed-selection, audit and cold-read acceptance.
  A07: pinned exports, typing, native Help/attribute reachability/budgets and API docs.
- Two contract-review rounds completed; no unresolved P2+ finding. Original untracked
  attribute implementation plan preserved. No commit, push or publication performed.
