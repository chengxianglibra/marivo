# R8.2 deviation implementation evidence

Status: **implementation and qualification in progress; R8.2 is not complete**.

This index records the connected `deviation.zscore@v1` and `deviation.mad@v1`
implementation. It does not grant runs, Association/Forecast expansion,
retirement, installed-wheel, remote-backend or real-Agent qualification.

## Baseline and immutable requirements

Implementation entered on `panda` at
`b779dcf31dbbfe3fe5ad08cc562bb0ee5bbf05f3` with the dirty R8.1 freeze.
`.superpowers/r82/baseline.json`, `status.txt`, `unstaged.txt`, `staged.txt` and
`untracked.txt` preserve entry input/diff hashes. The R8.1 verifier accepted the
current inputs before product changes. The R8.1 freeze was then committed as
`ae759a5680`; no R8.2 implementation commit existed at this baseline.

The historical R8.1 snapshot and compressed inventories remain unchanged.
The R8.2 subset is exactly the rows whose responsibility is R8.2 and whose
method is one of the two deviation methods: **15,564 mandatory IDs**, including
**2,232 originally blocked F11 rows**. There are 3,840 integrated kernel rows,
9,348 Decimal-law rows, 2,304 F11 rows and 72 individual scenario rows. A matching
registration, pure fixture, source-tree import or transport-only recovery does
not close a kernel row. Required IDs are never removed.

## Connected behavior

The seventeen frozen numeric receivers construct a LogicalDeviationResult.
The four fields are owned projections. Result selection and same-producer
field rank/limit/table preserve full fitted authority; selection before a new
deviation fits its current rows. Derived fields do not acquire original Metric
state or Subject authority. Observed carries only its captured original parts.

Arithmetic retains exact integer, Decimal and binary64 ratios, population
variance, exact median, the 7413/5000 MAD coefficient and mean absolute deviation
fallback around the same median. Unit fields use one HALF_EVEN finish; directed
Decimal root certificates begin at 120 digits and refine under the shared
execute deadline. Recovery verifies witnesses and equations without refitting.

Fit inputs/state, actual grid/Subject maps, independent receipts, Evidence and
empty Findings use Store 7 and its existing common transaction. Source routes
remain ibis_python and fixed routes artifact_python. Parquet preparation records
the actual file timestamp unit independently of the emitted Arrow carrier;
native source qualification remains microsecond-only. SQLite retains its
existing UTC physical keys. Temporal preparation keeps its captured precision
and zone. A deviation consumer without a time-coordinate
axis has NoTime; a grid consumer retains the exact captured TimeShape. Source
preparation still qualifies and records its own temporal keys. Fixed grid
restoration uses the captured physical TimeShape; non-grid fixed relations
retain NoTime and their observation scopes in the original parts.

F11 now resolves original member support through actual Subject parts, prepares
all source dependencies before local consumption, restricts grid contributions
per captured cell, and supports exact prepared Decimal sum. Cumulative/fold
admission is unchanged. A fixed selected Subject lacking a captured follow-up
observation contract raises `r8.retained_part` before a source read or Run.

## Evidence and commands

Detailed command logs and fixtures are retained under `.superpowers/r82/`.
Durable copies of entry diffs, command logs, receipt attachments, environment
hashes and the full requirement ledger are also saved in the working tree under
[`2026-10-04-marivo-r82-evidence/`](2026-10-04-marivo-r82-evidence/manifest.json).
`scripts/r82_deviation_evidence.py` archives current verified primary/part receipt
digests, exact source/fixed keys, original fit scope, input/state digests and
Artifact/Run identities. It forbids current Semantic and source access. The
qualification workers use a separate raw Fraction/Decimal oracle in
`tests/deviation_r82_oracle.py`. They assert fixture-owned input values and full
typed keys before checking the fit. Cold qualification executes a new kernel
over retained rank.values inputs, then checks an exact hit without another Run.
Receipt capture is an audit of those executions, not another kernel proof.

| Evidence | Actual command / log | Outcome at this checkpoint |
| --- | --- | --- |
| Public contract typing | `make test TESTS='tests/test_analysis_deviation_static_r82.py'`; `static-typing.log` | 2 passed; 17 positive receivers and 11 individually rejected shapes, with an isolated mypy fixture cache |
| Numeric/Cell authority | `make test TESTS='tests/test_analysis_deviation_state_r82.py'`; narrow state logs | 59 passed, including independent IPC value/key/view carrier damage, null-key rejection, nested prior-fit authority, and certificate precision/digit/padded-width damage |
| Nested current fit | `make runtime-test TESTS='tests/test_analysis_deviation_r82.py -k selected_time_score'`; `nested-time-fit-scope-final.log` | 4 passed: both methods, nonempty/empty selected temporal score inputs, new fitted scope and offline original authority verification |
| Nullable int64 output | `tests/test_analysis_deviation_r82.py -k observed_nullable_large_int64`; `observed-int64-null-current.log` | 2 passed: both methods preserve original near-limit int64 values with a Null Cell in pandas and restoration |
| Root refinement | `make test TESTS='tests/test_analysis_deviation_r82.py -k root_certificate'`; root refinement log | 2 passed; at least 640 certificate digits, independent 800-digit oracle, and deadline interruption. Primitive evidence only |
| Requirement matcher | `make test TESTS='tests/test_analysis_deviation_evidence_r82.py'` | 44 passed; each independent authority mutation follows a passing complete fixture. Origin method/type/domain/precision and follow-up contribution type are checked. Matcher fixtures grant no Runtime qualification |
| Deviation and fault Runtime | `make runtime-test TESTS='tests/test_analysis_deviation_r82.py tests/test_analysis_deviation_faults_r82.py -n 1'`; `deviation-and-faults-current-final.log` | 128 passed in 1,980.87 seconds; both methods, shared fit, scope selection, Cell states, source/fixed/cold continuations and fault cases |
| F11 source chain | `make runtime-test TESTS='tests/test_analysis_deviation_r82.py -k f11_complete_source_chain --maxfail=5'`; `f11-shape-current.log` | 32 passed; both methods, eight contribution profiles, scalar/grid follow-up sum and count_defined |
| Expanded F11 source chain | `tests/test_analysis_deviation_f11_r82.py`; `f11-rest.log`, `f11-profile-current.log` | 39 passed in 8,542.01 seconds, then the three earlier cases reran with independent profile metadata: 3 passed in 1,048.37 seconds. All 42 non-second source fixtures complete. Full Subject keys, grid values, prepared sum state and source-before-local order are checked |
| Atomic/receipt faults | `tests/test_analysis_deviation_faults_r82.py`; `uncertain-commit-repaired.log`, `reader-typing-repair-regression.log` | 48 cases passed in the combined Runtime run; 2 additional lost-acknowledgment cases passed, with no replay or additional source reads after the commit; the added actual Subject receipt mutations passed in the 10-case receipt/missing-contract selection |
| Non-time matrix | `make runtime-test TESTS='tests/test_analysis_deviation_domains_r82.py --basetemp=.superpowers/r82/fixtures/non-time'`; `domains-final-repaired.log` | 10 passed; missing new cold-kernel duties subsequently executed by `cold-qualification-repair.log`, 7 isolated workers all exit 0 |
| Nanosecond input | `make runtime-test TESTS='tests/test_analysis_deviation_time_r82.py::test_time_profiles_in_independent_processes[parquet-ns-UTC-False-KI] --basetemp=.superpowers/r82/fixtures/time-ns-repaired'`; `time-ns-final.log` | 1 passed; new cold kernel subsequently passed in the isolated repair run |
| Complete Decimal laws | `tests/test_analysis_deviation_laws_r82.py`; `laws-final.log`, `laws-resume.log` | Initial composite-key run: 42 passed, 34 failed after a duplicate registry declaration was introduced during execution. The declaration is repaired. All 76 fixtures now have complete producer/fixed/cold manifests; every resumed phase records its actual argv, exit 0 and output. All 779 profiles in both forms and both methods have 9,348 archived kernel receipts |
| Complete temporal matrix | `tests/test_analysis_deviation_time_r82.py`; `time-final.log`, `time-current.log`, `time-rest.log` | Six native-table/day cases complete; remaining 30 non-second cases passed in 9,374.31 seconds. All 2,880 non-second integrated time duties have distinct kernel receipts. Six Parquet-second cases retain their IDs and remain unqualified because the fixture writer stores milliseconds |
| Current certificate recovery | `make test TESTS='tests/test_analysis_deviation_state_r82.py tests/test_analysis_deviation_r82.py -k "not runtime"'`; `certificate-width-recovery-current.log`, `certificate-width-public-regression.log` | 854 pure tests passed; 2 public source/fixed recovery cases passed after the final certificate width check. The earlier 4-case recovery/nullable run also passed. Primitive evidence does not close an integrated route duty |
| Shared graph regression | Focused PeriodChange/F13 Runtime tests; `shared-transport-regression.log` | 32 passed; original endpoint Subject maps and source-prefix transport |
| Touched test/tool typing | Explicit `.venv/bin/mypy --explicit-package-bases` targeted commands in recorded logs | Eleven worker/evidence files and six Runtime test files passed; product-wide typing is separately covered by check-agent |
| Broad compact gate | `make check-agent`; `check-agent-final-width.log` | Exit 0: 6,012 passed, 5 skipped in 176.20 seconds; lint, product typing and API documentation passed |
| Site content/build | `npm run verify:content`, `npm run build`; `site-content-final.log`, `site-build-final.log` | Both exit 0: 343 required files and 321 pages |

The five default-gate skips were independently enumerated with
`.venv/bin/pytest tests/test_datasource_metadata_schema_only.py tests/test_lazy_row_expression_admission.py -k 'sqlite or degrade' -n 1 -q -rs`
(exit 0, seven passed and five skipped; `default-skip-reasons.log`). Four SQLite
fixtures lack Decimal storage; one metadata owner propagates a channel error to
the dispatcher fallback. They grant no deviation qualification.

Failures and superseded experiments remain visible. The initial law run used
string-only keys, was interrupted after 38 passes (make exit 2), and closes no
composite-key law duty. Its corrected 11-case smoke passed. Initial Scalar
publication, scalar float error-bound reduction, Decimal extrema validation and
nanosecond observation lowering failures were repaired and narrowly rerun.
Missing basetemp-parent and direct-script-import attempts were harness failures,
not product qualification. The first nested temporal regression fixture selected an empty day window; it was corrected to the actual populated month before the missing captured-grid coverage was repaired. A clean nested authority is checked before each independent prior-version/center corruption. The certificate verifier originally accepted an altered precision fact; it now checks the 120 + 40k progression, actual bound coefficient digits and exact interval width. All three independent precision/digit/padding mutations reject after clean recovery.

Earlier cold runs that only hit the fixed Artifact
do not close new cold-kernel duties.

The duplicate registry declaration caused fresh-process import failures in law
and temporal workers; its failed logs remain intact and their missing phases are
explicitly rerun. Receipt collection refused partially completed manifests.
The first current broad gate found a SQLite time-key regression and obsolete
millisecond rejection assertions; the implementation and assertions were
repaired, narrow checks passed, and the broad gate was rerun successfully.
The initial lost-acknowledgment assertion assumed one source reader; it now
compares the actual read count at commit with the final count, and both cases
pass. None of these failed attempts is recorded as passed qualification.

## Local review fixes

The review input was bound to `panda`, HEAD
`ae759a56801836f755d1c217ec180f42b17a540a` and dirty-input digest
`5efcbd8ed80b3eafe9c4ba7955d64bdcec38d1e1bc521d4bb6ae5b32f2c6fb3d`.
The entry diff and input summary were preserved before editing. All four
confirmed findings are repaired:

| Finding | Repair and independent regression |
| --- | --- |
| Mixed tables dropped fitted authority | A closed `table_fits` capture binds every fitted column to its current primary, exact input signature and complete original parts. Categories, original values and another fit retain their own column values. Coherent primary/columns damage and eight independent capture mutations reject after clean recovery |
| Rank tables treated ranks as scores | The same-producer shortcut requires an actual owned fitted quantity; derived ranks use their verified current rank projection and original ranking parts. Both methods work in either column order, including Decimal time grids |
| Materialized ranks omitted disclosed parts | Logical execution and materialized projection use one retained-part rule. `contract()` and `show()` agree with actual retained parts and preserve original fit scope |
| Corrupt arithmetic escaped typed repair | Zero center/scale denominators and invalid Decimal certificate encodings reject as `r8.retained_part`. The added table-verification deadline regression preserves `r7.execute_timeout` |

Expanded original display regressions also exposed the earlier R8.2 restoration
change assigning an upstream TimeShape to non-grid fixed member values. Restoring
their existing NoTime shape repairs corresponding category partitioning; grid
inputs still retain their captured TimeShape. The failed 20/46 attempt remains
in the archive: eighteen original partitioned-ranking failures and two incorrect
all-Defined assertions in the new grid fixture. The grid oracle now checks actual
Defined Decimal values and preserves empty Null cells.

The repaired intermediate Runtime selection passed **46 tests**. The final
unchanged Python snapshot passed **32 Runtime tests** covering both methods,
mixed tables, derived ranks, Decimal grids, shared fits, selection boundaries
and separate producer/fixed/cold processes. Offline processes prohibit current
Semantic, SourceSession and DuckDB access. Missing/corrupt table receipts reject
recovery and exact hits without a new Run. These are bounded regressions; they
do not close full matrix or exact composite-key scenario duties.

Final checks: **71 state tests**, **12 touched product modules** and **3 direct
test/worker modules** passed their focused checks. `make check-agent` exited 0
with **6,024 passed, 5 skipped**, plus full lint, typing and API documentation.
The five skips were independently enumerated again: four SQLite Decimal fixture
exclusions and one metadata channel-failure fallback. Site content verification
and the **321-page** build both exited 0.

[`review-fixes.json`](2026-10-04-marivo-r82-evidence/review-fixes.json)
binds the four findings, additional regression, code/oracle hashes, related
requirement IDs, fourteen actual commands/exits, failures and scope limits.
[`review-fix-logs.json.gz`](2026-10-04-marivo-r82-evidence/review-fix-logs.json.gz)
retains all local logs and the entry diff. The manifest hashes both attachments.
No mandatory requirement ID is newly closed by this repair checkpoint; the
prior qualification ledger and its counts remain unchanged. AGENTS.md and
packaged skills are unchanged. This checkpoint does not claim R8.2 completion.

## Requirement closure authority

Kernel and F11 attachments are distinct. A F11 source trace records consumed fit part hashes and verified terminal sum/statistic receipts; those consumed hashes are not persisted fit-part receipts or fixed/cold scoring evidence. Receipt collectors isolate each fixture in a spawned process, prohibit Semantic/source/DuckDB access, and publish an attachment only after every selected complete manifest verifies.

The matcher preserves the exact original method/type/domain/route, source form and precision, typed key profile, grid profile, required parts and arithmetic/codec versions. F11 also requires the actual contribution type and captured grid-window flag, sum then count_defined, one fit and all source reads before local consumption. A narrow Decimal contribution cannot fill a wider contribution duty merely because both scoring inputs widened to D(38,s).

The prior qualification checkpoint records **13,212 passed, 2,280 blocked and 72 unverified** out
of all **15,564 mandatory IDs**, with **phase_complete=false** and all **2,232
originally blocked IDs preserved**. The ledger command exited 0. Earlier counts
used partial attachments or a less strict contribution-type matcher and are
retained only as superseded command history.

| Durable attachment family | Verified records / matched requirements |
| --- | --- |
| `kernel-non-time.json.gz` | 480 integrated non-time kernel proofs / 480 requirements |
| `kernel-time-table-day.json.gz`, `kernel-time-batch-01` through `04` | 2,880 distinct integrated time kernel proofs / 2,880 requirements |
| `kernel-laws-batch-01` through `03` | 9,348 Decimal-law kernel proofs / 9,348 requirements; batch 01 is byte-sharded |
| `source-f11-batch-01` through `04` | 672 complete source chains from 42 fixtures / 504 exact frozen requirements; the 168 non-second narrow-Decimal keys remain blocked |
| `requirements.json.gz` reconstructed from its two hashed fragments | All 15,564 original rows with status, exact attachment record, Artifact/Run, execution key and repair reason |
| [`commands.json`](2026-10-04-marivo-r82-evidence/commands.json), `command-logs.json.gz` | Exact retained capture argv, recorded exits and all original successful/failed logs; unrecovered invocation details or numeric exits remain explicitly unavailable |
| [`environment.json`](2026-10-04-marivo-r82-evidence/environment.json), [`manifest.json`](2026-10-04-marivo-r82-evidence/manifest.json) | Workspace/dependency/oracle/input hashes and SHA-256 of every durable attachment |

The requirement ledger SHA-256 is
`8f3065564cfa6853f1c1d99734d6c15cfc715d7efbdbc5cdc0aaf2a653c24cb3`.
The raw Store 7 fixtures remain in the ignored workspace; the durable index does
not claim an installed-wheel, remote-backend or real-Agent execution.

The check-added-large-files hook limits each added file to 1,000 KiB. The
3,284,044-byte kernel-law batch and 1,339,433-byte requirement archive are
stored as independently hashed, 800,000-byte-or-smaller binary fragments. The
manifest binds fragment paths, sizes and hashes to each original compressed
archive's size and SHA-256. Run
`.venv/bin/python -m scripts.r82_evidence_shards docs/superpowers/specs/2026-10-04-marivo-r82-evidence`
to verify every fragment and attachment, reassemble each original archive in
memory, compare its full hash and check the expected record count. The original
gzip bytes therefore remain recoverable without adding an oversized file.

## Open exits

The individual scenario duties and the following contract decisions remain
open. The frozen F11 D(9,2)/D(18,6) rows specify those contribution
types as deviation input types, while accepted sum/change rules produce D(38,s).
The original requirement IDs remain present; their exact-key disposition awaits
the user's contract decision. No cast back to a narrower Decimal carrier is used.

The second-precision Parquet fixture is physically stored as milliseconds by
the writer. Its second-precision rows remain mandatory and unqualified; no
millisecond execution is relabeled as second precision.

Expanded fixed/cold F11 observation-contract capture/continuation and remaining
individual public scenario, corruption and disclosure exits must obtain their
own evidence. Isolated score kernels, source F11 passes and typed missing-part
rejection do not close these duties. R8.2 must remain incomplete until every
mandatory exit is closed.

The 72 individual scenario rows retain their original keys. V03/V04 include
`nonfinite`, `extreme_float`, `subnormal` and `decimal_scale_precision` while
freezing int64 and composite(string,int64). Tests of those phenomena on their
actual carriers are useful regressions but cannot silently replace the frozen
key. Their requirement disposition, and exact scenario-bound proof attachments
for the other rows, remain unverified.
