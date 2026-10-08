# Production-code reduction: G0 baseline and pilot definitions

Date: 2026-10-07 (Asia/Shanghai)

Status: G0 implemented and its definition checks passed. Production refactoring,
performance comparison, G1 feasibility and the under-100k target are unverified.

Scope: the user authorized G0 from the
[implementation plan](../plans/2026-10-07-production-code-under-100k-implementation-plan.md).
This delivery freezes current bytes, defines candidates and pilots, and fixes
performance acceptance before production edits. It does not start the first
package's extraction or either execution pilot's refactoring.

## Frozen baseline

The baseline binds `panda@1db2ebc93dcee6014a2c8434b12dd76a0cc0bedb` and the
working-tree bytes captured at **2026-10-07 22:24:47 Asia/Shanghai**. HEAD alone
does not reproduce this baseline. The initial 17 modified tracked files and
three pre-existing untracked files belong to other work or the user and remain
outside G0's edit scope. Their individual ownership cannot be inferred from Git;
they are recorded as `pre_existing_unattributed`, not claimed as G0 changes.

| Module | Frozen nonempty Python lines | Plan budget | Required reduction |
| --- | ---: | ---: | ---: |
| Analysis | 70,569 | 52,000 | 18,569 |
| Semantic | 34,952 | 25,000 | 9,952 |
| Datasource | 16,663 | 13,500 | 3,163 |
| Shared and support | 9,699 | 7,500 | 2,199 |
| Total | **131,883** | **98,000** | **33,883** |

There are **323 production Python files**. Strictly below 100,000 requires
31,884 fewer lines. The frozen count matches the planning reference, but now
has a file inventory and retained source bytes. G0's production net reduction
is **zero**.

Use [production_code_baseline.py](../../../scripts/production_code_baseline.py)
with an explicit repository Python interpreter:

```sh
.venv/bin/python scripts/production_code_baseline.py \
  --output docs/superpowers/specs/evidence/production-code-g0/next-snapshot \
  --context docs/superpowers/plans/2026-10-07-production-code-under-100k-implementation-plan.md
```

The output must be a new Git-ignored directory; existing snapshots cannot be
overwritten. The script reads the selection and bytes twice and compares HEAD,
index, status and the HEAD-to-worktree binary patch. A concurrent change fails
capture before publishing evidence. It does not stash, reset, stage or commit.

The counter's `nonempty-physical-v1` policy is UTF-8 `splitlines()` followed by
`bool(line.strip())`. Comments, docstrings, imports and declarations count.
Selection is deduplicated `git ls-files -co --exclude-standard`; missing files
are excluded and recorded. `marivo/*.py` and nested package Python both count.
The four module groups are mutually exclusive. Ordinary formatting and required
public docstrings continue to apply.

| Evidence | Binding or purpose |
| --- | --- |
| [manifest.json](evidence/production-code-g0/baseline/manifest.json) | Every selected path, SHA-256, byte size, line count, module/category, mode and symlink target; HEAD, environment, counter version/hash |
| [source.tar.gz](evidence/production-code-g0/baseline/source.tar.gz) | All 1,282 selected files, including uncommitted current contents and the ignored implementation plan |
| [worktree.patch](evidence/production-code-g0/baseline/worktree.patch), [status.z](evidence/production-code-g0/baseline/status.z), [index.z](evidence/production-code-g0/baseline/index.z) | Original binary diff, tracked/untracked state and index bindings |
| [baseline-verification.json](evidence/production-code-g0/baseline-verification.json) | Independent archive enumeration, byte/hash verification and count recomputation |
| [candidates.json](evidence/production-code-g0/candidates.json) | Candidate symbols, exact spans/file hashes, named consumers, deletion prerequisites, tests and estimate basis |
| [duplicate-bodies.json](evidence/production-code-g0/duplicate-bodies.json) | Static exact AST-body matches with docstrings excluded from comparison; not deletion authority |
| [pilot-protocol.json](evidence/production-code-g0/pilot-protocol.json) | Immutable pre-refactoring sampling definition, per-phase thresholds and pilot bindings |

The manifest SHA-256 is
`5acfddc4bbc920663f93a515fc5aa7ad9bd9da037327f9e527e3c53e72679746`;
its ordered file-inventory SHA-256 is
`4e817b749b66a8ccd519aabe03561f2aa24f97352bd4ca26b65c453850397042`.
The counter SHA-256 is
`268e91649d463f949504eb4a527fc4af003b15907121abb95ab46f73e93d5a39`.
The archive SHA-256 is
`843327afe4b720fdb9d63010a530556bbc622e7ca797ae28bc10ff5fabcad382`.
These bind measurement evidence; they introduce no product integrity protocol.

### Auxiliary inventory and attribution

The manifest also retains all package non-Python files and all selected Python
outside `marivo/`. At capture there are no package `.pyi` files and two package
resources: the existing packaged Analysis and Semantic skills. There are 459
outside-package Python files, including tests and tools, and 498 other context
files. The package's current non-Python inventory contains only those skill
files; outside-package files form a watchlist. This static inventory does not
determine runtime dependency status or prove the absence of dynamic imports or
embedded implementation.

Before crediting a future deletion, inspect production imports, dynamic registry
entries, entrypoints and packaging. A new resource/generator or an import from
tests/tools must be added to the cost ledger. Inventorying all outside Python
makes moves inspectable; it does not authorize counting them as savings.
Symlinks to repository files are materialized in the archive, with original
targets in the manifest. Paths outside the repository are refused. Ignored local
data, credentials, `.marivo/`, the virtualenv and build products are not archived.

Compare each future candidate's before/after file hashes and nonempty counts;
deduct new helpers, adapters and production resources. Candidate-owned hunks
must be separated from pre-existing or concurrent edits. If another task changes
an affected file, freeze a new candidate-specific before version and retain the
original baseline; do not subtract the other task's changes from this plan.

### Concurrent work at the exit checkpoint

The second counter invocation produced an independently verified
[exit snapshot](evidence/production-code-g0/exit-snapshot/manifest.json). It found
two production files changed by concurrent work; G0 did not edit either file.

| Path | Frozen lines | Exit checkpoint lines | Delta attributed to other work |
| --- | ---: | ---: | ---: |
| `analysis/compiler/history_axes.py` | 93 | 89 | -4 |
| `analysis/materialization/graph_display.py` | 617 | 624 | +7 |
| Production total | 131,883 | 131,886 | +3 |

[concurrent-changes.json](evidence/production-code-g0/concurrent-changes.json)
binds both versions' hashes. The original bytes of all 20 pre-existing dirty
paths remain in the baseline archive; 19 remained identical at this checkpoint,
while `graph_display.py` changed further. No concurrent edit was reset or merged
into G0's reduction credit. Later changes require another snapshot rather than
rewriting this checkpoint.

Because the working tree drifted during the session, repeat the selected checks
in a checkout extracted from the frozen archive. The imported `marivo.__file__`
must resolve inside that checkout, with the same repository virtualenv and
recorded dependency versions. Use this frozen replay as the authoritative G0
behavior evidence; live-tree runs are diagnostic observations. See
[frozen-validation.json](evidence/production-code-g0/frozen-validation.json)
and [exit-checks.json](evidence/production-code-g0/exit-checks.json).
The frozen replay passed 200 daily tests and both selected Runtime nodes;
it reproduced both alternative History failures (Runtime aggregate: two passed,
two failed). All 323 production-file hashes remained equal to the archive after
the replay; see [frozen-verification.json](evidence/production-code-g0/frozen-verification.json).

## Current contract and already-accounted work

Read and bound the current Analysis design, Session/Runtime, operators, Semantic
overview/object model/loading, Datasource, disclosure specification and
[test ownership](../../testing/runtime-coverage.md) in the source archive.
Native source and fixed-result behavior are represented by the concrete tests
below, rather than inferred from older stage headings in those documents.

- The **Store 8 local trust update** at the start of the Session/Runtime and
  Analysis specs overrides older Store 7 integrity descriptions. Keep producer
  input/output contracts, exact Session ownership, execution identity, atomicity,
  resource reconciliation and actionable I/O errors. Do not restore retired
  anti-tamper proof chains.
- Graph capture reuse is already present in `core/graph.py`; the current
  `_CapturedGraph` no longer has historical mutation snapshots. No O1 saving is
  promised again.
- Consumer qualification/specialization already has its owners in
  `methods/consumer_rules.py` and the registry. Current checks and backend
  distinctions are not duplication merely because multiple layers mention them.
- `runtime_metric_lowering._RuntimeGraphBuilder.catalog_graphs`, operation-owned
  semantic interpretation and the compiled expression AST handoff are current
  code. They do not authorize cross-call caches of callable behavior, schema,
  source rows or execution evidence.
- Fixed recovery already consumes the current Store 8 descriptor/continuation
  contract. Existing recovery simplifications and grouped numeric index/reduction
  helpers are part of the frozen count.
- Catalog already has `_DetailsBase`, `_common_detail_sections` and
  `CATALOG_MEMBER_CONTRACTS`. The registry and catalog consume that member owner;
  removing another supposed shadow catalog list is not a new saving.

`AGENTS.md`, packaged skills and existing user changes are preserved. G0 changes
neither the public API nor its bilingual examples.

## Candidate owners, consumers and estimates

All eight candidates are **defined, not implemented**. Conservative credit is
zero until a typed extraction and its independent behavior checks exist.
The planning values below are small code-grounded hypotheses, not committed
savings. Their combined 248-line estimate does **not** establish that a
33,883-line reduction is feasible. Remaining module budgets are unproven.

| Candidate / package | Duplicate and exact scope | Retained owner and consumers | Deletion prerequisite | Planning net lines / basis |
| --- | --- | --- | --- | ---: |
| P1-C01 / first | 15 Details `_detail_sections` calls forwarding the same common fields in `semantic/catalog.py` | Existing `_DetailsBase` and `_common_detail_sections`; all 15 concrete Details classes, starting with Datasource and Entity | Keep concrete fields/signatures, immutable mappings, section order and bounds; share only forwarding | 95: 120 forwarding lines minus 25 helper/adaptation lines |
| P1-C02 / first | 15 identical `summarize(RowMethod)` bodies returning `LogicalStatisticRelation` in `analysis/public_dsl.py` | One private concrete helper under existing `_Value`; numeric/ratio/difference/coefficient Logical and Materialized wrappers listed in JSON | Preserve public wrappers/docstrings, return annotations, closed rejection, `inputs=(self,)`, owner and source/fixed routes; count-returning variants stay separate | 90: exact nine-line bodies with allowance for typed helper and wrapper calls |
| P2-C01 / second | Backend declaration cloning within `statistical_physical.implementations` | Same physical owner consuming `Implementation`/`QualificationKey`; its provider branches and `builtin.implementations` | First map exact types/domains/time/form/route/IDs/precision per branch; real differences stay explicit | 40: conditional scaffolding estimate, no check-removal evidence |
| P3-C01 / third | `_index_rows`, `_original_input` and display `_fixed_rows` row/index constructions | Existing numeric carrier and index owners; original-state/coverage/primary readers, transport and display consumers | Measure N1 first; preserve complete-key uniqueness, deadline checks, exact scalars, Cell tags and representations; pandas/Arrow equivalence remains unverified | 0: positive net deletion and common-carrier equivalence unverified |
| P3-C02 / third | `history_views.load` and `axes_load` closed retained-state decoding scaffolding | History views owner with concrete `ViewState`/`AxisState`; execution and producer validation | Keep distinct schema/error requirements; H1 excludes axes; generic adaptation may cost more than removal | 0: no demonstrated positive extraction |
| P4-C01 / fourth | Exact `_make_ref` / readiness `_exact_ref` 14-kind factory-map duplication | One refs-owned private closed dispatcher; catalog builders and readiness key/ref projection | Keep exact Ref tags and catalog overloads; no public alias, broad cast or new Help path | 10: 17-line duplicate upper bound minus seven routing/adaptation lines |
| P4-C02 / fourth | Exact sorted `_time_dimensions_for_entity` duplication in loader/validator | Registry-owned static selector; cumulative normalization and cumulative validation | Keep semantic-id order and caller-specific missing/ambiguous errors; no source fact cache | 5: eight-line duplicate upper bound minus three routing lines |
| P4-C03 / fourth | Exact `_fingerprint` serialization in event/funnel/lifecycle | Existing shared identity owner selected after format comparison; PatternStep/EventPattern/FunnelLossRate/FromInception | Preserve all JSON options and prefixed SHA-256 bytes; do not merge protocol-specific hashes by name | 8: fourteen duplicate body lines minus six adaptation lines |

The JSON contains every consumer symbol rather than a representative subset, and
binds each production location to its baseline file hash and AST span. It also
lists the necessary existing tests. Static AST identity alone does not establish
caller semantics, import layering or dynamic reachability.

P1-C01 owns only common forwarding call blocks; P1-C02 owns the selected statistic
bodies. P4-C01 owns the distinct factory implementations, not the same file's
Details code. P3-C01's narrow functions do not own the whole execution file.
P2-C01 stays within declaration cloning. Re-scope any later overlap before
adding estimates. Datasource native cursor, cancellation and timeout differences
have no proved deletion candidate in this first inventory and carry zero credit.

For the first package, begin with two Details types and two Logical/Materialized
statistic consumers, measure actual net lines, and extend only if the exact typed
contract holds. The execution pilots do not authorize a general Runtime rewrite.

## Bound pilots

### N1: native original mean, current-row mean and cold reuse

Owner:
`tests/analysis/materialization/test_mean_recovery.py::test_native_original_mean_independent_fixed_and_cold[duckdb]`.
Its source fixture is `SourceData` / `source_case` plus `author_source_project`;
the independent worker is `mean_recovery_worker.run`.

- Local DuckDB native table, 101 rows: 100 records in group `a` of value 1,
  one record in `b` of value 100. BIGINT keys exceed `2**53`; source time is
  UTC microseconds. Published and fixed results use project-local Parquet.
- Public chain: `session.members` → bucket `read` and average `observe` →
  `group_by(bucket).rollup().execute()`.
- Fixed continuation in a new process: resume Session and recover the grouped
  artifact; compare current-row `.group_by().summarize(mv.mean())` with original
  `.rollup()`. Expected values are independently `101/2` and `200/101`.
  Retained states must be exactly `a=(100,100)` and `b=(100,1)`.
- Repeat exact continuations for hot hits, then recover both in another cold
  process. Semantic loading, source construction and native source connections
  are forbidden after production; resource journals must finish empty.

Baseline behavior **passed**. Native source trace recorded **two submissions**:
one `analysis.graph.check` and one `analysis.graph.stage`. The fixed worker made
two Runs/two kernels; the cold worker made zero Runs/zero kernels, and their
output snapshots matched. See
[mean-source-duckdb.json](evidence/production-code-g0/frozen-runtime-traces/mean-source-duckdb.json)
and [mean-recovery-duckdb.json](evidence/production-code-g0/frozen-runtime-traces/mean-recovery-duckdb.json).

### H1: compound-key History and six source/fixed/cold views

Owner:
`tests/analysis/lifecycle/test_analysis_history.py::test_history_views_source_fixed_cold[K33-T01]`.
Its fixture is `lifecycle_fixtures.build_lifecycle_public`; the operation owner is
`history_operations.operations`, and the raw-fact oracle is
`history_oracle.views/assert_view`.

- Local DuckDB native tables, three Subjects and ten Events. Both Subject and
  occurrence identities are compound string/int64 keys, with the integer part
  exceeding `2**53`. UTC microsecond time; a 100-second window, earlier inception,
  simultaneous business-sequenced Events, illegal transitions and a silent Subject.
- Public chain: `session.lifecycle.replay(..., seed=mv.from_inception(),
  completeness=...)` → History → `read(in_state)`, `distribution` **without axes**,
  `transitions`, `violations`, `intervals` and `dwell`.
- Source and fixed versions must match independent raw-fact oracles. After deleting
  the fixture source files, an independent `history_worker` recovers History and
  views with source connection, semantic loading and replay forbidden. Cold
  continuation must recover the saved fixed Artifact references.
- The existing cold worker uses a private fixture `Session._from_runtime` to
  bind the Session, followed by artifact/view consumption. This is local Runtime
  recovery evidence, not installed-package or full public-resume qualification.
  Same-process repeated hits are a required sampling addition before H1 refactoring;
  that extra measurement is presently unverified.

Baseline behavior **passed** (one node, 23.31 seconds including setup and worker
startup). See [history-plain-validation.log](evidence/production-code-g0/history-plain-validation.log)
and the frozen replay's [K33-T01.json](evidence/production-code-g0/frozen-runtime-traces/K33-T01.json).
That elapsed test duration is not an operation-performance sample.

### Baseline failures retained separately

Two inspected candidates are not eligible pilot branches on these baseline bytes:

| Node | Observed failure | Disposition |
| --- | --- | --- |
| `test_history_public_recovery[views]` | `compiler/history_axes.py:88` requests absent `axis_0_0`; Ibis reports only `key_0`, `__instant`, `__join_0` | `failed`; checkpoint-axis branch excluded from H1 |
| `test_history_public_recovery[captured_observations]` | `graph_lowering._fact_relations` cannot find an immediate input identity (`KeyError`) | `failed`; captured source observation branch excluded from H1 |

The full logs are
[runtime-validation.log](evidence/production-code-g0/runtime-validation.log) and
[history-captured-validation.log](evidence/production-code-g0/history-captured-validation.log).
Both defects were also reproduced in
[frozen-validation-1.log](evidence/production-code-g0/frozen-validation-1.log).
They were observed before any G0 production edit; G0 made no production edit.
This establishes a baseline defect, not its introduction commit or task owner.
No product repair is included in G0. A future change touching those owners must
address or explicitly isolate the failed comparison; H1's pass does not cover it.

## Performance sampling and fixed acceptance

The protocol is fixed before production refactoring. Current environment:
macOS 26.6.2, Apple M1, eight logical CPUs, 16 GiB RAM; repository Python 3.12.13,
Ibis 12.0.0, DuckDB 1.5.3, PyArrow 25.0.1, pandas 2.3.3, pytest 9.0.3.
Use one runner, telemetry off, fixture/provider engine-thread defaults recorded
per sample, and local Store 8 Parquet. Do not compare runs with concurrent test
workers or changed dependencies/configuration.

Before the corresponding production edit, collect and bind the following:

1. Two warmups, then seven serial repetitions for each source, fixed miss, hot
   exact hit and cold recovery phase. Misses use fresh projects/Sessions; cold
   uses new OS processes. Keep raw samples and PIDs. Setup/authoring and oracle
   formatting stay outside operation timers. Do not pool different phases.
2. `perf_counter_ns` operation time; separate runs for process peak RSS and
   supplementary `tracemalloc` allocations. RSS is the fresh process lifetime
   high-water mark including imports/setup, with platform-native units recorded
   and converted to bytes. It does not measure remote server memory.
3. Native query counts through the existing `SourceTrace` adapter witness,
   correlated with `SourceSession.submissions` and grouped by purpose. Fixture
   authoring SQL is separate. Fixed/hot/cold queries must be exactly zero.
4. Separate cProfile samples for qualified Arrow/Pandas conversion calls and
   `graph_local_execution._index_rows` / display `_fixed_rows` construction.
   Record caller/representation. Unobservable C-level calls are `unverified`,
   never fabricated zeros. Union/merge and deliberate field projection remain
   distinct consumers. Keep instrumentation out of timing runs.
5. Snapshot/hash, fixture hashes, physical environment, input/output cardinality,
   schemas/parts, values/Cells, errors, Runs, resource state and result references.
   Compare identical scenarios; alternate before/after measurements using the
   saved baseline package bytes and unchanged fixture revision.

| Dimension, per phase | Precommitted acceptance |
| --- | --- |
| Independent behavior | Same numerical/Cell/schema/parts/owner/continuation/error/Run/resource oracles; no hidden route or new source authority |
| Median time | After ≤ before + `max(5% of before, 10 ms)` |
| Median process peak RSS | After ≤ before + `max(5% of before, 16 MiB)` |
| Actual native queries | After ≤ before; preserve purposes/authority; fixed/hot/cold = 0 |
| Equivalent conversions/index builds | No increase for each named representation; account for any new consumer-required representation |
| Production net reduction | Positive after all new helpers/adapters/resources/generators; retain formatting and required docs |

If baseline timing relative median absolute deviation exceeds 10%, repeat the
unchanged protocol with eleven samples. Continued noise remains `unverified`
and prevents rollout. Thresholds cannot be relaxed after seeing refactored
results. These small fixtures establish bounded comparisons; they do not support
extrapolation to large data or all backends. Additional scales require a separately
bound scenario and baseline before its implementation.

Repeated time/RSS/conversion/index baselines and H1 source query counts are
**unverified**. G0 requires their method and thresholds to be defined; their
collection remains a mandatory predecessor to the pilot production edit.
No speedup, memory saving, full Runtime or release qualification is claimed.

## G0 checks and next entry

| Check | Result |
| --- | --- |
| Counter format/lint and import contracts | passed |
| Counter strict typing | passed, one module |
| Independent frozen archive verification | passed, all 1,282 files; counts reproduced |
| Catalog, Metric Details, result protocol and exact exports daily baseline | passed, 200 tests |
| N1 DuckDB source/fixed/hot/cold | passed, one Runtime node |
| H1 compound-key native History source/fixed/cold | passed, one Runtime node |
| Two alternative public History branches | failed, retained baseline defects |
| Repeated performance measurements | unverified; protocol fixed |
| First package / G1 / under-100k | not implemented / unverified / unverified |

Commands, exit codes, logs and durations are retained in
[daily-validation.json](evidence/production-code-g0/daily-validation.json),
[runtime-validation.json](evidence/production-code-g0/runtime-validation.json),
[history-captured-validation.json](evidence/production-code-g0/history-captured-validation.json)
and [history-plain-validation.json](evidence/production-code-g0/history-plain-validation.json).
The initial mixed Runtime run reports one passed and one failed; do not relabel
the whole run as passed. The later H1 selection passed independently.
The immutable replay's daily command passed 200 tests in 10.37 seconds. Its
four-node Runtime command reported two passed and two failed in 45.91 seconds;
the selected N1/H1 nodes passed, and the rejected alternatives failed. This
aggregate remains a failed command, not a passing full Runtime gate.

G0 is ready as the first package's entry: a reproducible baseline, concrete
candidate consumers, two supported bounded pilot scenarios and precommitted
sampling standards exist. Start only the first package's scoped extraction
when authorized. Collect the relevant performance baseline before execution
pilot changes. Package closure still requires `make check-agent`; G0's narrow
tooling/definition checks do not close the first package. No commit or push was
performed. The large evidence directory is deliberately ignored; this report
and the counter can be reviewed separately without modifying ignore rules.
