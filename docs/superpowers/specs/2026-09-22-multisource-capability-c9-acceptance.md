# C9 Event and Lifecycle acceptance

Updated: 2026-09-23. The initial C9 baseline was `620822598f`; this continuation
starts at `0ba8e4514b` on `lazy-dataset`. C8 changes are preserved. Status:
**partial C9: the qualified cells below are open; remaining SQLite lowering and some direct derived cells are not complete**. MySQL is excluded from further work by
the subsequent user instruction.

## Exact method/backend state

All newly open source cells require exact unversioned table inputs and int64
subject/occurrence identities. PostgreSQL journeys support two or three steps;
Trino and ClickHouse journeys support two. Journey matching includes
first-per-subject and every-start shared/exclusive. PostgreSQL Lifecycle uses
two trigger Events. Trino/ClickHouse Lifecycle currently qualifies one int64
component per subject and occurrence identity. Trino requires Iceberg base tables and ClickHouse requires
MergeTree tables; other physical engines/connectors have not been qualified.

| Exact method unit | PostgreSQL | SQLite | MySQL | Trino | ClickHouse |
| --- | --- | --- | --- | --- | --- |
| Event journey matching | Open | Observed missing native digest; closed | Excluded; previously observed resource failure | Open | Open |
| Lifecycle history/replay | Open | Closed behind native identity/time and replay lowering | Excluded; closed | Open via native array fold | Open via native array fold |
| Direct ungrouped Event funnel | Open | Closed | Excluded; closed | Open | Observed planner/memory failure; closed |
| Direct grouped Event funnel | Open | Closed | Excluded; closed | Observed 306-stage reconciliation exceeds 150; closed | Closed behind failed funnel qualification |
| Direct time-to-event | Open | Closed | Excluded; closed | Open for first-per-subject | Observed memory failure; closed |
| Direct Event subject selection | Open | Closed | Excluded; closed | Open | Observed repeat memory failure; closed |
| Direct Lifecycle distribution (ungrouped/grouped), transitions, dwell, violations, selection | Open | Closed behind replay | Excluded; closed | Not yet qualified directly; complete retained continuations remain available | Not yet qualified directly; complete retained continuations remain available |
| Complete funnel comparison | Open via existing retained continuation | Closed behind source funnel | Excluded; closed | Open for ungrouped funnels via retained continuation | Closed behind source funnel |
| Complete funnel attribution | Open via existing retained continuation | Closed behind source funnel | Excluded; closed | Closed behind grouped funnel reconciliation | Closed behind source funnel |

A closed source cell does not revoke the existing allowed continuations of an
explicitly materialized complete result. No backend is opened merely because its
SQL compiles. A missing implementation is not labelled a physical impossibility.

## Current continuation mechanisms and evidence

### PostgreSQL replay and derived methods

Recursive replay uses native recursive SQL. Equal-time confluence carries a
BIGINT array of remaining ordinals and JSONB per-occurrence outcomes, preserves
within-Event identity order, and rejects divergent terminal state or violation
outcomes. A read-only repeatable-read transaction keeps assertions, primary rows,
ledger, transitions and violations on the same source snapshot. Materialized CTEs
are statement-local; there are no temporary source tables or installed UDFs.

The independently expected history contains three intervals: subject 1 is open
for three clipped hours and done for 21 hours; subject 2 is open for 22 hours;
subject 3 is not incepted. Expected evidence is `(rows=3, subjects=3, seeded=2,
not_incepted=1, transitions=1, violations=1, left_clipped=1)`. Tests cover compatible
and ambiguous cross-Event ties, missing inception, repeated/NULL identity,
unknown coverage, illegal transitions that preserve the prior state, empty
output with duplicate membership and a late stream failure with no artifact. Native driver interception verifies the reader identity,
submitted SQL and transaction control independently of Runtime receipts. After
source tables are dropped, a cold process opens history and runs transitions
without source submissions. Direct reducers have independent counts/durations;
shifted-cohort funnel comparison and attribution assert nonzero changes.

### Trino Event

The Trino path uses named ROW identities, canonical JSON/SHA-256, exact seconds
plus fractional-microsecond arithmetic, and ranked successor joins instead of
ASOF. Tagged occurrence roles avoid redundant ordinal joins. A read-only Iceberg
repeatable-read transaction covers every assertion and output. Independent
violation counts are submitted separately to avoid repeating the entire match
beyond the default stage budget; only counts cross this boundary. Window FILTER
becomes CASE, floating division is explicit, and membership predicates keep their
parentheses. Primary sorting stays at the outermost query.

Tests independently assert the canonical journey hashes, a two-microsecond
interval, endpoints, shared/exclusive completion allocation, duplicate/NULL
identity, NULL time, missing participants, ambiguity, empty and censored output,
late-read failure and cold recovery. HTTP request interception verifies actual
reader SQL and a common transaction ID; parameterized metadata statements are
observed as driver `EXECUTE IMMEDIATE` requests. Direct ungrouped funnel,
time-to-event, subject selection and ungrouped comparison pass. Grouped funnel
reconciliation still needs 306 stages on a server limited to 150; attribution
therefore rejects before source access. No stage limit was raised.

### ClickHouse Event

Journey matching uses one ordered validation/proof/primary packet query and
explicitly enabled materialized CTEs. The reader remains `readonly=1` and must be
allowed to set `enable_materialized_cte=1`. Merely accepting `AS MATERIALIZED`
syntax is insufficient: a random-value probe observed repeated evaluation when
the setting was zero. The two-step tests include independent hashes, exact
microseconds, matching policies, coverage, invalid input, empty output, late
failure and cold recovery. Server query-log inspection independently observes
one reader Event packet statement.

A direct subject-selection experiment used one packet query over a shared
MergeTree snapshot without materialized CTEs, with query-plan optimizations
disabled. It passed once in isolation, but subsequent normal, empty,
duplicate-membership and unknown-coverage cases all exceeded the 2 GiB limit
when run after the journey suite. That cell was therefore withdrawn rather than
marked passed. The experimental planning permission was removed; no disabled-
optimization path remains registered.

Funnel experiments hit ClickHouse 26.3's nested materialized-CTE execution error
(`materialization was planned: false`). Plain CTEs exceeded 10,000 plan
optimizations; disabling that optimization pass then hit the existing 2 GiB
memory limit. Time-to-event also exceeded memory after fixing signed count-union
and Boolean membership lowering. These cells were withdrawn from registration;
no resource limit was raised. An earlier C8 hidden-axis attribution failure was also reproduced at the
untouched `0ba8e4514b` baseline under the same 2 GiB limit in a disposable detached
worktree, subsequently removed. The final combined ClickHouse run passed all
61 cases, including that C8 case and the 13 admitted Event cases.

### SQLite and the original recursive probe

SQLite 3.53.1 accepts `json_object` but a native `sha256('event')` probe reports
`no such function: sha256`. Its generic compiler also lacks native struct
lowering. This is a missing source implementation under the adopted no-installed-
UDF boundary, not proof that SQLite could never support the contract. Its 53
existing method tests pass.

A reader probe on Trino 483 rejects an 11-step recursive query at the default
recursion depth of ten. Silently stopping replay there would violate complete
history. The September 23 continuation replaces this approach with native array
folds and exact source-side confluence exploration. It does not raise the recursive
cap or truncate history. Direct derived method qualification remains separate.

## September 23 Lifecycle continuation

Trino and ClickHouse now lower complete subject sequences through native array
folds. Each occurrence retains before-state, after-state and evaluation. Tied
cross-Event groups explore every compatible interleaving, preserving the governed
order within each Event. Confluence compares terminal state and each occurrence's
evaluation/violation state, so equal terminal states alone do not admit ambiguity.
No recursion-depth, event-count or tied-group-size truncation is introduced;
resource errors still fail the run without publication.

Trino uses its existing read-only Iceberg transaction. Canonical history integrity
checks run as separate scalar counts under that snapshot; native ROW joins replace
unsupported correlated subqueries. ClickHouse emits all assertions, exact coverage
and count evidence, history, transitions, subject ledger and violations in one
ordered packet query over a shared MergeTree snapshot. Ordinary CTEs avoid the
observed nested materialization engine error. Only complete contracted outputs
cross into local storage; no raw occurrence replay happens in Python. Packet
ordinals, part counts, late failures and publication are checked independently.

ClickHouse native tuples represent an absent identity with null components; scoped
lowering and transport normalize that representation to the existing nullable
struct contract. Lead over a non-null source string now explicitly returns NULL
past the last row, and Boolean payloads retain Boolean JSON types. The source
integrity assertions exposed these differences before any result was published.

Live acceptance includes independent interval/count expectations, histories longer
than ten events, same-Event tied groups longer than ten, illegal/terminal transitions,
compatible and divergent cross-Event ties, equal-terminal-state/different-outcome
ambiguity, duplicate/NULL identities, missing participants, NULL times, empty
output with invalid membership, unknown coverage, microsecond intervals and late
primary/part failures. Query-log and HTTP evidence verify reader SQL, with the
ClickHouse driver's trailing Native format clause accounted for explicitly.
Source tables are dropped before cold-process reads and allowed retained
continuations. Final outcomes are recorded below.

## Initial PostgreSQL Event source proof

`postgres_event_sql.py` compiles the existing Event lowerer into one read-only `WITH` statement. PostgreSQL `MATERIALIZED` CTEs freeze membership, governed occurrences and matched rows once within that statement. Role-tagged packets carry all preflight counts, the exact Event output proof and ordered complete journey rows. The adapter reads validation and proof packets before exposing primary batches; the existing publication gate commits only after full output. A scoped compiler lowers PostgreSQL named row JSON and SHA-256 to preserve the journey digest contract. No temporary relation, UDF, source write privilege, new public method, result shape or Store revision was added.

The twelve reader tests use disposable administrator-created tables and the restricted `analysis_reader` account. The first-per-subject reference independently expects four ordered rows, two journeys, completion statuses `[complete, complete, incomplete, incomplete]`, three non-null occurrence times, and both canonical SHA-256 journey IDs. Added cases verify three journeys and six rows for every-start exclusive and shared assignment, including shared reuse of one completion, plus three-step matching with the same Event used in two roles and distinct selected occurrence identities, including three-step shared and exclusive every-start assignment. Out-of-order insertion and exclusive time endpoints preserve the expected result. The tests observe exactly one `event_bundle` source submission, its SQL beginning with `WITH`, and no source DML/DDL submission. Duplicate or NULL occurrence identity, equal-time assignment ambiguity, and duplicate membership with an empty journey reject before publication; the Store artifact table remains empty. Separate Python processes open first-per-subject and every-start journeys by ArtifactRef without source submissions. The PostgreSQL service probe reported `analysis_reader` denied CREATE and TEMP privileges. The tests do not yet inspect PostgreSQL server audit logs independently of Runtime submissions, and do not qualify other Event shapes or Event-derived methods.

MySQL experimental lowering represented one-component identities as canonical source-side JSON, used native SHA-256 and microsecond difference, and submitted checks, proof, and rows in one read-only query. A small valid input and a duplicate-identity rejection passed under the restricted reader after disabling derived-table merging for that session. The same valid-input test later ended with MySQL container exit 137 and a lost connection; three subsequent cases could not run. This resource failure was repeatable enough to withhold admission. The experimental runtime path and tests were removed, leaving the existing pre-I/O rejection. This is an observed qualification failure of that query shape under the 768 MB service, not a proof that MySQL cannot implement Event matching.

## Remaining implementation gates

- Qualify direct Trino/ClickHouse Lifecycle derived methods. Complete source history,
  confluence and retained parts now have native array-fold implementations.
- Complete SQLite native identity, exact time and digest lowering within the
  adopted source-only/no-installed-UDF boundary before opening its Event cells.
- Reduce Trino grouped-funnel reconciliation stages and qualify attribution.
- Replace ClickHouse's unqualified funnel/time-to-event plans within existing
  reader/resource limits, then qualify comparison and attribution.
- Qualify additional identity, versioned-source and pattern shapes individually.

Packaged skills were not edited. No new public method, result shape or Store
revision was introduced. No commit, push, release or deployment belongs to this
continuation.

## September 22 verification evidence

- Focused Event/Lifecycle compiler and contracts: 69 passed.
- Exact C9 admission probes: 22 passed.
- DuckDB Event/Lifecycle Runtime and continuations: 28 passed.
- PostgreSQL existing methods, Event and Lifecycle: 82 passed. The additional
  illegal-transition case passed separately (1 case).
- SQLite existing methods: 53 passed.
- Trino existing methods and initial Event suite: 58 passed. Subsequent direct
  ungrouped funnel passed separately; time-to-event, ungrouped comparison and late
  stream failure passed together (3 cases). Direct selection and HTTP request
  verification also passed. The final full Event suite, including the qualified
  direct methods, passed all 16 cases. Grouped-funnel qualification failed as
  described above.
- Final ClickHouse existing methods plus qualified Event matching: 61 passed.
- `make check-agent`: lint/import contracts and typing passed; 5,683 default tests
  passed with 16 skips; API documentation built successfully. The first broad
  attempt identified metadata-test stub initialization and the bilingual example
  count snapshot; both were repaired, with 47 focused checks passing before the
  full successful rerun.
- Targeted typing for the five new/changed backend acceptance/helper modules
  passed (external untyped-driver imports excluded from that test-only command).
- Final source-schema stub typing annotations and the additional PostgreSQL
  transition test passed targeted typing and lint; source-schema tests passed
  all 20 cases. The pre-test ClickHouse-running/Trino-stopped service state was
  restored.
- Site content verification: 343 required files. Site build: 321 pages, including
  verification of the standard and Chinese install-script outputs.
- `git diff --check` passed; the index is unchanged.

## Earlier verification evidence

- Event/Lifecycle compiler, contract and exact admission tests: 87 passed.
- DuckDB Event/Lifecycle runtime and continuation: 28 passed.
- PostgreSQL existing methods plus initial Event acceptance: 50 passed; expanded Event acceptance subsequently passed 5 tests.
- SQLite existing methods: 53 passed. ClickHouse existing methods: 48 passed. Trino existing methods: 47 passed after restarting its service alone; the first Trino attempt had 46 connection errors because the ClickHouse start script stopped Trino.
- MySQL existing methods: 39 passed, 1 failed in `test_hidden_axis_attribution` because the expected C8 resource-limit phrase differs from the current generic `metric.expand_axes` rejection. This is outside the Event/Lifecycle path and remains unresolved.
- After the NULL-identity compiler repair and exact admission updates, `make check-agent` passed with 5,679 tests and 16 skips; site content verification passed 343 required files and the build completed 321 pages. `git diff --check` passed.
- After broadening the PostgreSQL matching gate, the focused compiler/contract/admission group passed 70 tests, PostgreSQL methods and Event acceptance passed 56 tests, and the DuckDB Event/Lifecycle runtime group passed 28 tests. Both first-per-subject and every-start journeys recovered in a cold process without source submissions. `make check-agent` again passed with 5,679 tests and 16 skips, and the site build completed 321 pages.
- The MySQL experiment ran under the restricted reader. After one valid output and one duplicate-identity rejection, a repeated valid run ended with service exit 137. The original MySQL methods group, before the experimental path was withdrawn, had 41 passing cases and the same unrelated C8 `test_hidden_axis_attribution` assertion mismatch. The dedicated MySQL service was restarted and its reader privilege probe passed. No MySQL Event method remains registered.

No C9 push, release or deployment has occurred.

## September 23 verification evidence

- Focused compiler, Event/Lifecycle contracts and exact admission: 91 passed.
  Four additional compound-identity admission rejections brought the final exact
  C9 admission group to 26 passing cases.
- DuckDB Event/Lifecycle Runtime and continuations: 28 passed.
- ClickHouse Lifecycle final standalone suite: 18 passed, including source-offline
  cold distribution, transitions, dwell, violations and subject selection, exact
  reader query-log SQL, and governed order across trigger roles of the same Event.
- ClickHouse combined Lifecycle/Event/existing-method run: 76 passed, 2 failed.
  The first failure was a test comparison that omitted the driver's appended
  `FORMAT Native`; corrected exact-query verification passed on rerun and in the
  final Lifecycle suite. The second was existing C8
  `test_hidden_axis_attribution_complete_contributions[1]`, which exceeded the
  unchanged 2 GiB server memory limit, also on isolated rerun. The earlier
  September 22 baseline reproduction for this same C8 case remains recorded
  above; this continuation does not change its execution path or raise limits.
  The combined source gate is therefore not recorded as fully green.
- `make check-agent`: formatting, lint/import contracts and typing (357 source
  files) passed; the final rerun passed 5,687 default tests with 16 skips; API
  docs built.
- Final targeted analysis typing: 216 files passed. Five backend test/helper
  modules passed targeted typing; targeted lint/import contracts passed.
- Site content: 343 required files verified. Site build: 321 pages and standard/
  Chinese install scripts verified.
- Trino combined Lifecycle/Event/existing-method suite: 80 passed. After folding
  directly over the native event array instead of generating an index sequence,
  governed tied order and compatible replay were rerun: 2 passed. This avoids an
  unnecessary generated-array size boundary without changing replay semantics.
- PostgreSQL shared Lifecycle publication/reducer regression: 17 passed.

- Final ClickHouse rerun after folding directly over the event array: all 18
  Lifecycle cases passed again. The pre-test ClickHouse-running/Trino-stopped
  service state was restored. `git diff --check` passed; the index remains empty.
  No packaged skill, public method, result schema or Store revision was changed;
  no commit or push was made.
