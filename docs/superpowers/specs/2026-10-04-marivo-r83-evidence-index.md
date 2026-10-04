# R8.3 complete-grid runs implementation evidence

Status: **connected implementation with bounded evidence; R8.3 is not fully accepted**.

The implementation uses the current `panda` R8.2 work without closing or expanding
R8.2. Its separate ledger remains 15,564 mandatory IDs: 13,212 passed, 2,280 blocked,
72 unverified. R8.4-R8.6, installed-wheel, remote-backend and real-Agent acceptance
are outside this execution. No commit, release, AGENTS.md or packaged-skill edit
was performed by this task.

## Connected behavior

`NumericRelation.runs(where=...)` constructs the typed `time.runs@v1` graph on the
original complete time grid. Source and fixed execution share the registered
classifier and maximal-segment consumer, independent of the legacy Candidate
chain. Result fields are start/end Temporal, int64 count and microsecond Duration.
Result selection preserves all original condition classifications and interval
identities; it only restricts the current output keys and their four views.
Count ranking and exact elapsed Duration filtering are connected. Duration
ranking is rejected at construction.

The capture owns every condition dependency's signature, physical type, binding,
Cell reasons, original grid coverage and actual Subject image. Missing/duplicate
keys, partial grids, false coverage and malformed Cells fail before publication.
Every predicate leaf is visited; an unavailable ordinary operand dominates
Boolean composition. State predicates use their total definedness definition.
False/unavailable cells separate maximal true segments. The complete non-time
coordinate tuple separates series; batches and physical row order do not delimit
segments. Frozen UTC endpoints determine elapsed duration, including DST and
certified unequal calendar periods. Scope termination means observation ended.

Store 7 retains versioned condition_cells/run_cells, original per-cell grid map,
zero Findings policy and actual Subject parts when present. Recovery verifies
receipts, capture digests, classification/maximality witnesses, mapping, owned
projections and selection scope using only frozen parts. It does not read current
Semantic/calendar or source data. Subject projection uses the original actual
image and retains interval multiplicity before set projection. The public source
prefix prepares the next explicit observation before local Subject selection.

## Evidence boundary and immutable denominator

`scripts/r83_runs_requirements.py` extracts the exact **1,708** R8.1 requirement
IDs assigned to R8.3. The historical snapshot is unchanged. The compressed
[requirement ledger](2026-10-04-marivo-r83-evidence/requirements.json.gz) retains
every frozen field and ID. It attaches only exact integrated keys supported by
the [public process manifest](2026-10-04-marivo-r83-evidence/public-processes.json).
The manifest records implementation/qualification keys, codecs, retained parts,
input/state and receipt digests, Artifact references and source-offline status.

| Mandatory status | Count |
| --- | ---: |
| passed | 3 |
| failed | 0 |
| blocked | 0 |
| skipped | 0 |
| unverified | 1,705 |

The three attached rows are the int64 UTC builtin-day source, fixed and fresh
process source-offline kernel keys. Calendar, score and fault checks below are
bounded evidence; they do not automatically close another frozen scenario or
numeric/key/origin profile. The V06-V09 behavior, related V01/V15-V18 disclosure
and recovery checks are exercised below, but their full ID matrices remain open.
There is no full-phase acceptance claim while any mandatory ID remains open.

## Verification

The independent oracle in `tests/test_analysis_runs_kernel_r83.py` enumerates all
243 five-cell true/false/unavailable patterns directly from fixture states. It
checks maximal endpoints/count/duration, original cells, termination reasons and
full classification without importing product segmentation helpers for expected
values. Additional laws cover state predicates, all dependencies, adjacent
opposite signs, unavailable siblings, missing/duplicate/coverage errors,
row/batch permutation and DST elapsed boundaries of 71/73 hours.

`tests/test_analysis_runs_r83.py` exercises public construction, fixed selection,
Duration filtering, pre-selection rejection, Duration rank rejection and missing
parts. It corrupts and removes every primary/part receipt, asserting recovery,
read, selection and fixed exact-hit refusal without another Run. The process
worker runs separate producer, disconnected fixed consumer and cold consumer for
builtin days and certified 1/2/3-day calendar periods. Expected fixture values are
1/2/7: threshold 1 joins cells two/three; threshold 2 selects only cell three.
Both zscore and MAD thresholds, complete unavailable scores, count rank/table,
actual Subject selection and the next explicit observation are exercised.

Focused tests, typing/lint and the broad gate are recorded at the final checkpoint
below. Default-test totals exclude Runtime-marked tests. These are source-tree
checks, not same-wheel or release acceptance.

Earlier failed iterations were repaired: the added reason-vector zip omitted its
fifth argument; the zero-scale fixture was corrected to a constant reference;
Duration rank admission was closed; and receipt exact-hit checks were bound to a
fixed kernel rather than a fresh source read. Failed attempts are not attached as
passed qualification.

## Final checkpoint

| Check | Command | Result |
| --- | --- | --- |
| Independent oracle and laws | `make test TESTS='tests/test_analysis_runs_kernel_r83.py'` | 10 passed |
| Disclosure, exports, method and examples | Focused default selection of the oracle, API drift, public surface, lazy disclosure, method registry and documentation example files | 106 passed at the preceding checkpoint |
| Public Runtime and separate processes | `MARIVO_R83_EVIDENCE=docs/superpowers/specs/2026-10-04-marivo-r83-evidence/public-processes.json make runtime-test TESTS='tests/test_analysis_runs_r83.py'` | 3 passed; one pre-existing DuckDB fetch_arrow_table deprecation warning in the reused R8.2 fixture |
| Product and ledger typing | `make typecheck TYPECHECK_TARGETS='marivo/analysis scripts/r83_runs_requirements.py'` | 283 source files passed |
| New worker/test typing | `.venv/bin/mypy --python-version 3.10 --follow-imports=silent tests/runs_r83_worker.py tests/test_analysis_runs_kernel_r83.py tests/test_analysis_runs_r83.py` | 3 source files passed |
| Broad gate | `make check-agent` | Exit 0; lint/import checks, 431 typed source files, 6,034 default tests passed, 5 skipped; API documentation built |
| Whitespace/scope | `git diff --check`; AGENTS.md and packaged skill diff | Passed; no protected-file changes |

The final public manifest SHA-256 is
`973ced1c04c4f96410aa130c8b9de2899065f43d35b8d73254757c3efb730e70`.
The compressed requirement ledger SHA-256 is
`8e374b1dfe1a0696fbba76989df0da6f3b8da6c495ce82e48ed29db0b29985aa`.
Regenerate the ledger with:

```sh
.venv/bin/python -m scripts.r83_runs_requirements \
  docs/superpowers/specs/2026-10-04-marivo-r83-evidence/public-processes.json \
  docs/superpowers/specs/2026-10-04-marivo-r83-evidence/requirements.json.gz
```

The remaining 1,705 mandatory IDs require exact additional evidence. Broad
default counts, source-tree fixture passes, registration and retained witness
checks do not replace those executions or close R8.2 blockers.

## Adversarial review repairs

Admission now stops tracing selection at the owning TimeProduct boundary, so
Subject selection before a fresh complete observation is admitted. Both where
and limit after that boundary reject before execution; dynamic contract uses
the same check. StatisticalRelationError routes runs failures to the runs Help
owner. The source/fixed regression verifies fresh-grid execution, early limit
refusal, unchanged Run history on rejection and absence of invalid runs guidance.
The focused Runtime file passed 5 tests after these repairs. Its additional
checks do not change the frozen qualification ledger or the R8.2 status.
