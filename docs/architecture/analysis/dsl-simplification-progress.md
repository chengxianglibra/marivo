# DSL simplification implementation

Started: 2026-10-22. Base: `067c38d0506bfdbb7320f9ad925b2de84e621949`.
Scope: [approved design](dsl-simplification-proposal.md), D1–D7 and A01–A18.
The proposal and README index existed as local edits at start and are preserved.
The implementation phase created no commit, push, packaged-skill edit or release.
The subsequent commit request authorizes all related DSL changes in one attributed
local commit; pushing remains outside its scope.

## Ownership and dependencies

| Stage | Owner | Work | Dependency | Status |
| --- | --- | --- | --- | --- |
| S0 | root | Scope, ownership and evidence ledger | None | Complete |
| S1 | membership_capture | Shared ordered normalization, observe/per capture and focused shape tests | S0 | Complete; R1 repair verified |
| S1b | output_coverage | Necessary external output keys, subset/empty sample preservation and source checks | S1 capture interface | Complete; R1 repair independently verified |
| S2 | numeric_outer | Fixed outer correspondence, fixed null division and numeric lowering/tests | S0 | Complete; R1 repair independently verified |
| S3 | root | Help, errors, public typing, current docs, latest EN/ZH and integration | S1/S1b/S2 | Complete |
| S4 | independent reviewers + root | Review, repairs, full local gate, relevant Runtime and closeout | S3 | Complete; R1 closed and full local gate passed |

Workers share this checkout and own separate implementation regions. Attribute
memberships and parent_how remain unchanged. Shared numeric rules still apply to
its Metric inputs. Capture does not read business rows. Explicit external domains
cover complete necessary keys before cropping; internal domains select samples.

## Acceptance and evidence

| Acceptance | Evidence owner | Status |
| --- | --- | --- |
| A01–A09, A15 | Grouping/semantic capture, full interleaved schema/rules and membership numeric oracles | Passed |
| A03/A05 coverage, A16–A18 | Population membership and source fusion oracles, including R1 unmatched/nested probes | Passed |
| A10–A11 | Frame correspondence/calendar, zero/cancellation/type and retained-error tests | Passed |
| A12 | Storage, source-offline three-process numeric recovery and source-free continuation | Passed |
| A13 | Surface/Help/typing/exports/CLI tests and unchanged slow context budgets | Passed |
| A14 | Existing Attribute behavior and independent scope tests, retained parent_how | Passed |
| Final local | Unnarrowed `make check-agent`, `make docs-api`, site checks | Passed |
| Relevant Runtime | PostgreSQL native zero/weighted numeric and relationship-fusion/output-coverage cases | Passed, PostgreSQL only |

## Review and failures

At most two formal review rounds. Close actionable P2+ findings and record exact
validation commands/results here. Local, Runtime, installed-package and release
claims remain separate. No release acceptance is planned.

### Development evidence

- Baseline counterexample: the original
  `test_per_completes_ratio_per_sample_and_retains_empty_samples` passed before
  D7, including its old incomplete external P={1} result [40]. The external
  subset assertion now requires a coverage error; internal subset remains valid.
- Initial disclosure/semantic Help run: 90 passed, 6 old contract assertions
  failed; their owners migrated removed divide/memberships targets. Expanded
  surface run: 486 passed, one old observe Help assertion failed and was updated.
- Focused disclosure typing: 14 source files passed. Focused disclosure lint,
  formatting and import contracts passed.
- `make docs-api` passed. `npm run verify:content --prefix site` verified 381
  files. `npm run build --prefix site` passed, including 308 route and 44 latest
  usage-page checks (343 internal targets).
- Slow context journey exposed an overly broad replacement Help lookup after
  divide removal; it now reads the existing focused `Column.__truediv__` page.
  Guidance is being tightened within the existing unchanged context budgets.
- Dedicated PostgreSQL 17.11 was started with the repository environment manager.
  First relevant Runtime command:
  `MARIVO_POSTGRES_ANALYSIS_TEST=1 make runtime-test TESTS='tests/analysis/numeric/test_remote_numeric_query.py tests/analysis/materialization/test_source_population_fusion.py' RUNTIME_WORKERS=1`:
  **10 passed, 31 skipped**, 2 pandas warnings. Other providers were not enabled;
  skips are not acceptance. New focused coverage/zero-denominator cases will be
  rerun after implementation freezes.
- Full `make typecheck`: **325 source files passed** before review repairs.
- Focused surface suite: **39 passed**; slow typing/context journeys: **2 passed**.
  Context thresholds were preserved; focused division Help replaces retired
  divide and duplicate bridge guidance was shortened without removing constraints.
- S1 worker: **76** grouping/authoring/Help tests and **8** relationship-binding
  tests passed; 7 focused typing targets passed. Interleaved rule execution
  checks full ordered keys/schema, complete rules and unequal nested values.
- S1b worker's integrated materialization run: **195 passed, 8 failed**; all
  failures were retired numeric-policy test formulas, subsequently repaired by
  S2 (8 focused Attribute fine-overflow cases passed).

### Formal review round 1

| Finding | Severity | Owner | Resolution |
| --- | --- | --- | --- |
| Noniterable observe.by raised raw TypeError before typed capture validation | P2 | root | Guard tuple extraction; None/1/True regressions pass (3), typing passes; independent reviewer verified structured errors |
| Internal membership unmatched=exclude removed necessary outer coordinates before K | P1 | output_coverage | Internal allocation left-join markers preserve each outer prefix; ordinary/full/subset/open/nested probes pass and independent reviewer verified |
| Cancelled internal weights hid product/numerator overflow (inherited A11 gap) | P2 | root | Compute and check paired numerator before zero branch; scale=1/2 public probes and Top-0 checks pass; independent reviewer verified |

Capture/surface reviewer: focused **102 passed**, no other P2+ findings in that
scope. Numeric/coverage reviewer completed independent execution/storage review.
New PostgreSQL native division/weighted zero tests passed. The external-coverage
Runtime probe exposed a nonnullable empty-key literal error, repaired by making
only the synthetic zero-row NULL sentinel nullable. The final exact Runtime
rerun passed **2 tests**, including coverage before empty internal sampling and
Top-0. PostgreSQL uses its existing key transport; the no-upload assertion stays
with the previously qualified provider paths and local DuckDB fusion.

### Integration closure

- Preliminary unfiltered default-suite attempt (stopped after failures): **4,865 passed, 20 failed**. Failures exposed
  remaining old exact/error policy and implicit-P-cropping fixture expectations;
  each was migrated to fixed outer/null or explicit fact filtering with its
  existing numerical oracle retained. Root-owned explicit-domain bridge and
  producer-check cases passed 2/2 after migration.
- Numeric test migration closure: **14 passed**, then its explicit overflow
  regression passed 1 (2 deselected). D7 final fixture/oracle closure:
  **49 passed, 3 deselected**; 8 production typing targets passed.
- Root numeric/semantic-registry closure: **67 passed**. Historical temporal
  transport fixtures remain unchanged; their frozen contracts retain identity,
  while current recipe fingerprints use grammar version 2.
- Independent capture/surface review: 102 passed. Independent execution review:
  19 storage/cold/definition tests and 3 final D7 probes passed; no open P2+
  findings. All findings closed in one formal round with focused rechecks.
- Final **unnarrowed `make check-agent` passed**: 796 Python files formatted,
  lint and import contracts passed, **325 source files typed**, **5,197 tests
  passed** (261.05 seconds), API documentation built successfully. Existing
  third-party numeric/temporal and pandas warnings remain visible (776).
- `make slow-test TESTS='tests/analysis/materialization/test_numeric_recovery.py'`:
  **1 passed**, 23.32 seconds. Producer, source-offline continuation and cold read
  use separate processes and retain exact results. Slow surface typing/context
  checks passed **2 tests**; final context-only rerun also passed without budget
  increases.
- Both reviewers report no open P2+ findings. No installed-package or release
  acceptance was run or claimed. Other external providers were not qualified.
- The PostgreSQL service and dedicated Colima VM started for this task were
  stopped after the successful final Runtime rerun. Existing volumes preserved.
- Final `npm run build --prefix site` passed after the final broad gate,
  including API build, Astro checks/build, install-script checks and latest
  English/Chinese route/content verification. Existing Pagefind HTML/language
  notices do not prevent its successful exit.
- Final `git diff --check` and workspace scope inspection passed. Protected
  `AGENTS.md`, project-local skills and packaged skills have no changes. No
  commits or pushes were created. All S0–S4 work and A01–A18 are closed within
  the local and explicitly recorded PostgreSQL scope above.

### Commit follow-up

On 2026-10-22, the user requested an attributed local commit of all related DSL
implementation changes, without pushing. Completed validation is reused; normal
repository commit hooks and signing remain enabled.
