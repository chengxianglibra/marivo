# Production-code reduction: execution pilots and G1 decision

Date: 2026-10-07

Status: **P3-C01 and P3-C02 abandoned; G1 feasibility report complete.** Neither
candidate was applied to production. The latest user instruction rejects small
reductions and code growth without further validation. After comparisons,
candidate acceptance and `make check-agent` are `authorized_skipped` under that
instruction. The under-100k objective remains **unverified and incomplete**.

This record follows the
[implementation plan](../plans/2026-10-07-production-code-under-100k-implementation-plan.md).
The original [G0 record](2026-10-07-production-code-g0-baseline-and-pilots.md) and
[accepted P1 record](2026-10-07-production-code-p1-result-disclosure-convergence.md)
retain their evidence and acceptance boundaries.

## Baseline and net-reduction ledger

The implementation started from clean
`panda@c1f98a4c0ebfd9a81680bce0928728daaefa7285`: **323 production Python files,
131,678 nonempty physical lines**. The counter remains `nonempty-physical-v1`,
including comments, docstrings, imports and declarations. The frozen
[before manifest](evidence/production-code-p3/before/manifest.json), source archive,
worktree patch and index/status bind the package and measurement fixtures.
[Archive verification](evidence/production-code-p3/before-verification.json)
independently reproduced the count and all archived hashes.

Both formatted proposals remained in the ignored evidence directory. The
[ledger](evidence/production-code-p3/final-net-reduction-ledger.json) charges all
helper, import, typed-adapter and caller-routing lines, including their cost of
preserving distinct errors, decoding and deadline ownership.

| Candidate | Proposed deleted lines | Proposed added lines | Proposed net reduction | Applied reduction | Decision |
| --- | ---: | ---: | ---: | ---: | --- |
| P3-C01: complete-key indexing | 21 | 40 | **-19** | 0 | Abandoned: increases code |
| P3-C02: strict History state decoding | 17 | 14 | **3** | 0 | Abandoned: insufficient reduction |
| Actual production total | 0 | 0 | 0 | **0** | Production unchanged |

The [final production snapshot](evidence/production-code-p3/final-production-snapshot.json)
records cleanup only: every production file still matches its frozen before
hash. It is not an adopted-candidate after snapshot or a performance result.
P1's previously accepted **208-line** reduction remains the only credited saving.
The separate three-line increase from other work between G0 and P1 is still
excluded from this plan's benefit.

## Owners and consumers

**P3-C01.** The proposed `graph_exchange._indexed_rows` shared complete-key
insertion and duplicate rejection. `graph_local_execution._index_rows` retained
Arrow row decoding, per-row deadline checks and its own duplicate error.
`graph_display._fixed_rows` retained the exact pandas scalar carrier, null
normalization and its own error. `_original_input` reaches the local index;
its original-state, coverage and primary indexes carry distinct required facts.
`_transport_rows` also serves `_transport_stage` and `_transport_part_keys`, so
moving index-only deadline checks into it would change other consumers.
The corrected [proposal and accounting](evidence/production-code-p3/c01-accounting-v2.json)
include those adaptations. The earlier incomplete prototype is retained as a
rejected intermediate. Neither was applied. No cross-read cache, projection,
union or overwrite merge was introduced.

**P3-C02.** The proposed private decoder in `history_views` used a constrained
type parameter with concrete `TypeAdapter[AxisState]` and
`TypeAdapter[ViewState]` returns. Only strict JSON validation and canonical
encoding comparison moved. `axes_load` and `load` retained their respective
state types, table-shape checks and structured error labels. The
[formatted proposal](evidence/production-code-p3/c02-proposal.patch) reduces
680 nonempty lines to 677 after adaptation. It was abandoned before adoption.

The same independent numeric oracle and recovery worker were reused. H1's
selected chain covers six views without checkpoint axes. Independent decoder
checks cover AxisState and ViewState; the H1 runtime boundary does not qualify
checkpoint-axis execution. The two G0 History failure branches remain recorded
separately and were neither repaired nor promoted to passed acceptance.

## Evidence collected before the stop instruction

Measurement tooling was completed and frozen before any proposed production
adoption. Source, fixed miss, exact same-process hot hit and fresh-process cold
recovery were sampled separately. Time and process-lifetime RSS used two warmups
and seven serial observations per pilot and phase; cProfile and allocation
observations ran separately. The baseline runner completed before the stop
instruction could terminate it. Its
[raw samples and binding](evidence/production-code-p3/baseline-samples/binding.json)
and [completion marker](evidence/production-code-p3/baseline-samples/complete.json)
are retained. These are **before-only observations**, not evidence of speedup,
behavioral equivalence or candidate acceptance.

The protocol retains the original thresholds and distinguishes Python wrapper
and index calls from unobservable Arrow C-layer conversion calls. An absent
cProfile C-layer event remains `unverified`, not zero. Source tracing observes
N1's two submissions and H1's 42 submissions; fixed/hot/cold observe zero source
queries with source access blocked. Local cold recovery is not installed-wheel
or full public-resume qualification.

| Completed check | Evidence boundary |
| --- | --- |
| Existing N1 and H1 Runtime nodes | 2 passed against unchanged production before implementation |
| Independent strict/canonical state-decoder checks | 14 daily tests passed against unchanged production; later archived and removed with the abandoned pilot tooling |
| Sampler/helper format, Ruff and targeted strict typing | Passed for the tool scope; follow-imports=silent typing is not the broad library gate |
| Frozen sampler smoke and before collection | Completed for N1/H1 and all four phases; package hashes stayed unchanged |
| Initial tooling failures | Preserved smoke logs; corrected before the frozen collection |

No proposed production behavior, performance, typing or lint acceptance is
claimed. Before/after alternation, after sampling, affected candidate acceptance
and the final broad gate were **authorized_skipped** when the user instructed
abandonment. No full release or backend matrix was started.

All seven trial code files are retained in the local
[tool archive](evidence/production-code-p3/trial-tooling/manifest.json). The two
owned tracked test-module changes were restored to HEAD, and five newly created
test/tool files were removed. The raw G0 and before evidence was preserved.
The [disposition record](evidence/production-code-p3/disposition.json) binds this
cleanup and the skipped validation boundaries.

## G1 feasibility decision

At the unchanged total of **131,678**, the gap to **99,999** is **31,679**;
the gap to the **98,000** planning target is **33,678**.

Only the remaining mutually exclusive G0 candidates with concrete current code
locations are counted. Their selected function ASTs still match G0, including
catalog symbols whose surrounding file changed in P1. Their estimates retain
explicit adaptation costs and have no proven deletable semantic checks; thus
all conservative values remain zero. See the
[current candidate mapping](evidence/production-code-p3/remaining-candidates-current.json).

| Remaining candidate | Concrete scope | Conservative | Planning |
| --- | --- | ---: | ---: |
| P2-C01 | `statistical_physical.implementations` repeated clone scaffolding | 0 | 40 |
| P4-C01 | Catalog `_make_ref` and readiness `_exact_ref` bodies | 0 | 10 |
| P4-C02 | Loader/validator `_time_dimensions_for_entity` | 0 | 5 |
| P4-C03 | Event/funnel/lifecycle `_fingerprint` bodies | 0 | 8 |
| Total | Nonoverlapping symbol scopes | **0** | **63** |

Neither total covers either gap. Even the planning total leaves **31,616** lines
to the strict limit and **33,615** to the planning target. No pilot ratio is
extrapolated to module budgets. **Equivalent refactoring has not established a
route below 100,000 lines**, and this report grants no condition to start the
second package, third-package expansion or G2. The proven P1 changes remain.
The small remaining candidates are inventory only, not newly authorized work.

There are no production, public API, export, Help, persistence-version or support
set changes in this delivery. `AGENTS.md` and packaged skills were not modified.
No commit or push was made. The G1 decision is a completed report, not completion
of the production-line objective.
