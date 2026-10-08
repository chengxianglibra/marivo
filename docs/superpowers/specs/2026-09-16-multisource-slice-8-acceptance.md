# Slice 8: consolidated disclosure and installed-package acceptance

## Scope and artifact identity

Baseline: `38e40dec80b6cdd4b8558fd4324a2a728b44fc02`, with a clean worktree
at implementation start. Slice 7 was already committed. This slice changes
execution disclosure and acceptance infrastructure, not backend capability,
public exports, callable signatures, Store format or package dependencies.
No commit, push, release or publication is part of this acceptance.

Final candidate: `marivo-0.5.3.dev0-py3-none-any.whl`, SHA-256
`ecd8ad834667b50620368fcbfa066b6fc489f461b1c84ddc3c544fe125091004`.
The corresponding sdist SHA-256 is
`52f768267dec8a81952196939e905b833d11bae736872d7c7a7cbe45ed750fce`.
The version is unchanged. All five new backends passed the final-candidate
installed journeys. Independent final evidence review is recorded below.

## Disclosure ownership

The existing `analysis.actions.execute` declaration now discloses remote
read-only accounts, no retained import/upload, Trino Iceberg and ordinary local
ClickHouse MergeTree boundaries. Datasource connectivity and semantic readiness
do not establish execution support. Native Help and dynamic contracts retain
their shared owner; no second method inventory or public entry was added.

The analysis skill adds that workflow distinction and the canonical Help route,
without duplicating signatures or a backend matrix. The owning analysis spec and
English/Chinese latest workflow pages agree. CLI behavior is unchanged; installed
contract tests exercise Help routing and generated agent skill links.

## Backend matrix

| Backend | Source Runtime evidence | Final installed wheel evidence | Rejection-only / not newly qualified |
| --- | --- | --- | --- |
| DuckDB | Existing adapter and retained Runtime gates | Existing public Dataset contract suite, three-process recovery, CLI/environment checks | No new capability qualification |
| PostgreSQL | Slices 3 and 7 | Sum, mean, weighted mean, ratio, relationship, daily-date Forecast, cold retained rollup, validation/source failure | Exact distinct rejects; other unqualified advanced methods retain source rejection coverage |
| MySQL InnoDB | Slices 4 and 7 | Same installed journeys under binary text collation | Same rejection boundary |
| SQLite | Slices 4 and 7 | Same installed journeys on declared physical types | Same rejection boundary; no server-account claim |
| Trino Iceberg | Slices 5 and 7 | Same installed journeys under the restricted reader | Same rejection boundary; other connectors unqualified |
| ClickHouse ordinary local MergeTree | Slices 6 and 7 | Same installed journeys under the restricted reader | Same rejection boundary; other table engines unqualified |

[Consolidated final-wheel receipts](2026-09-16-multisource-slice-8-execution-receipts.json)
contain all six backends: the installed DuckDB baseline and 30 real source-backed
reductions across the five new backends, with Runtime SQL inventories and transfer
counters, permission probes, publication failures and cold continuations.
Each scalar/relationship output transfers two public rows in one primary query;
the date-to-Forecast source stage transfers four. Metadata and validation queries
are additional and counted separately. Diagnostic dependency constraints and
full origin inventories remain in the raw receipt directories.

The wider native-date bucket/version matrix, large-input economics, multi-page
transport, cancellation and process-crash cases remain **source Runtime evidence**
from Slices 3–7, not newly repeated installed journeys. Timestamp/DST, private-state
Group D and remote retained import remain unsupported; none is labeled live success.
This completes bounded multi-datasource delivery, not full DuckDB feature parity.

## Evidence boundaries

The installed probe authors datasource and semantic modules through the public
`md`/`ms` surface, calls `ms.load()`, and executes public `mv.Session`/Dataset
operations. Internal statistics and artifact counts are test observers only.
A wheel-hash and installed-origin guard rejects editable/source imports and checks
all loaded Marivo modules. Producer and consumer are separate Python processes.

Every new backend uses four source rows and two customer rows. Grouped sum is
`[30, 70]`; mean and ratio are `[15, 35]`; weighted mean is `[17.5, 30]`.
The cold retained rollups are mean=`25`, ratio=`25`, and weighted mean=`130/6`, computed from
published sufficient state, not displayed group means. Relationships yield
EU/US means `[15, 35]`. Native-date daily history transfers four aggregate rows;
local naive Forecast returns `[40, 40]` with four training rows.

Exact distinct is rejection-only evidence: no Run and no source statements.
A duplicate customer identity must create a failed Run and publish no Artifact.
After the producer exits, administrative fixture cleanup removes both source
tables. Cold binding reuse and rollup run with source connection creation forbidden.
A new source computation then fails through actual source metadata access without
publishing. This is removed-table acceptance, not a service-shutdown or network
fault experiment. Local retained DuckDB queries may count as primary queries;
that counter alone is not evidence of remote access. Each composed-metric cold
receipt captures the binding hit and the retained rollup separately, including
statement roles and Arrow row/byte counters, before the next action resets them.

The consolidated DuckDB entry includes its actual producer, continuation and
recovery receipts plus installed contract origins and commands. Its statistics
are the last execute in each phase, not phase-wide totals. In `continue`, that is
the downstream retained rank/limit; the returned artifact/run still identifies
the original Delta being audited. The foreign-import guard deliberately exits
with code 1, explicitly recorded as its expected outcome.

Remote permission probes verify `analysis_reader` identity and actual INSERT
denial on the fixture. They do not independently re-prove every DDL privilege;
those broader read-only and driver-lifetime checks remain in Slices 3–7. SQLite
has no server account; its production query-only connection boundary retains its
existing Runtime tests. Fixture administration is separate from Dataset reads.

Runtime receipts distinguish metadata, validation and primary statement roles,
and record transferred Arrow rows/bytes. They do not capture every driver-internal
operation, wire bytes or server scan/pruning metrics. Those unavailable metrics
are null, not zero. Construction makes no source query; capability discovery does
not claim live server verification.

## Reproduction and checks

Use the explicit serial service groups and wheel target in
[the environment runbook](../../../tests/multisource_environment/README.md#slice-8-installed-package-acceptance).
Tests never start services. Only the dedicated `marivo-multisource` services and
UUID fixture tables are used. Trino and ClickHouse run in separate groups.

The isolated venv installs the existing `all` extra under the current working
dependency constraints. On this host PostgreSQL needs the test-only
`psycopg[binary]` package; mysqlclient 2.2.7 was rebuilt against the existing
MariaDB connector because a cached wheel had unresolved native symbols. Neither
fix changes Marivo dependency declarations or copies Marivo from the checkout.
The third-party wheel hash is
`30d4f5ed9a16ebad33e4c250d6392ce155b4cdfee0f4f55e9e04ec33c6050351`.

- Focused Help, resolver, disclosure, skill, CLI, example and admission tests:
  **192 passed**.
- Touched test-module typing with `--follow-imports=silent`: passed; this limits
  the check to the new typed probes instead of untyped existing service helpers.
- Final `make check-agent`: **5,061 passed**, production typing, lint/import
  contracts and API documentation passed.
- Site content verification: **343 required files**; Astro **0 errors / 0 warnings**;
  site build and standard/Chinese installer output checks passed.
- Final wheel and sdist build/check passed.
- Final-wheel SQLite/PostgreSQL/MySQL/Trino group: **4 passed, 1 deselected-backend skip**.
- Final-wheel ClickHouse group: **1 passed, 4 deselected-backend skips**.
  Together every requested backend passed; skips do not count as live evidence.
- Final-wheel DuckDB Dataset/CLI environment checks: **4 passed**, including
  staged native contract tests and the independent three-process retained journey.
- Independent code review: **No findings** after repairing the reported test
  false-positive, receipt-overwrite and permission-evidence gaps. Independent final
  evidence review also reported **No findings**: all 40 phase receipts matched
  their summaries, PIDs and hashes; all recorded commands succeeded; the five
  backends and DuckDB matched the final wheel hash.

Debugging found incorrect test fixture declarations (native-date parser,
SQLite physical type, PostgreSQL bounded text and MySQL collation), a mistaken
remote-read inference from local primary-query counters, and native driver setup
issues. These were corrected in test inputs and observation; production execution
was not relaxed. Earlier failed attempts are not counted as green evidence.


## Review follow-up

Accepted the two evidence gaps: cold statistics now capture each binding hit and
rollup, and the consolidated JSON includes the real installed DuckDB baseline
instead of only cross-referencing it. Added an existing CLI-to-focused-Help
navigation example without inventing a backend execution command.

Kept the packaged skill self-contained: the current packaged-skill contract
requires exactly one `SKILL.md` and prohibits packaged references. Its workflow
judgment belongs there. Kept duplicate-identity rejection, service-group selection
and test-host driver prerequisites because they were part of the approved
installation acceptance. No adapter abstraction or cosmetic refactoring was added.

The package implementation is unchanged, so these follow-up runs use the same
wheel hash above. Fresh producer/consumer projects recaptured all five new
backends, including 15 retained rollups with actual local statement roles and
transfer counters, plus the DuckDB three-process baseline. Binding hits report
zero source SQL/transfer, not zero local persistence I/O.

Follow-up checks: **78 focused tests passed**; **4 + 1 installed backend journeys
passed** across the serial service groups; **4 DuckDB/CLI installed tests passed**;
touched test typing and lint passed. The broad and site gates above are the
original Slice 8 implementation gates; this follow-up changes only test observers,
receipts and the runbook. Independent final follow-up review reported **No findings**:
all 43 phase receipts matched raw hashes, PIDs and results; the 15 cold rollups and
DuckDB statistic scopes were verified; the expected foreign-import rejection was
kept distinct from successful commands.
