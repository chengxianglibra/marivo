# Proposed MySQL certified-authoring timeout exception

Status: explicitly authorized by the user on 2026-10-05, limited to the two
certified-authoring timeout statements below. Implementation and product
qualification remain distinct. The two product statements are now registered,
purpose/parameter constrained and exercised by the native calendar consumer;
formal qualification and complete control audit remain pending. The owned-query cancellation proposal was separately authorized
by the user on 2026-10-05; this setting does not prove fetch/cancellation coverage.

## Governing boundary and observed failure

[R9 implementation plan section 5.2](2026-10-04-marivo-full-algebra-dsl-r9-implementation-plan.md)
explicitly does not approve new internal SQL exceptions. It requires reproducible
counterexamples, alternatives and the exact operation/backend/purpose boundary.
The [native calendar development](2026-10-04-marivo-r93-evidence/c06-certified-calendar-development-01/README.md)
records MySQL's certification failure: no adapter-enforced authoring timeout.
Synthetic calendars or an elapsed client deadline cannot satisfy that requirement.

## Alternative study and independent diagnostic

[MySQL 8.4 system-variable documentation](https://dev.mysql.com/doc/refman/8.4/en/server-system-variables.html#sysvar_max_execution_time)
assigns max_execution_time a session-scoped millisecond limit on read-only SELECT
execution. Stored programs and data-modifying stored functions are outside this
mechanism. This proposal is limited to generated direct-column certification
reads; it does not authorize arbitrary raw SQL or stored-program execution.
Optimizer hints would modify compiled SQL and are excluded. Socket read timeout
bounds client waiting, not server termination. Global server configuration would
mutate unrelated sessions and cannot represent each authored scope's timeout.
No suitable native mysqlclient query-deadline API is currently exposed.

The [test-only diagnostic](2026-10-04-marivo-r93-evidence/mysql-authoring-timeout-proposal-01/diagnostic.json)
uses the existing SELECT-only analysis_reader on its own disposable connection.
A 50ms session limit terminates an unmodified Ibis-generated cross-join aggregate
with native error 3024 after about 0.052s. Independent administrator observation
confirms that the original query is absent; no rescue was needed, and SELECT 1
still succeeds on the original connection. The script, original log and query
hash are retained. These observations are feasibility evidence, not product
qualification or a guarantee for metadata/fetch/overall operation deadlines.

## Exact proposed authorization

| Field | Proposed boundary |
| --- | --- |
| Backend | MySQL only |
| Operation | Install and verify a server limit on a fresh owned certification connection |
| Purpose | Exhaustive bounded period-calendar, temporal-set or work-schedule preview |
| Set statement ID | mysql.authoring.install_select_deadline |
| Set template | SET SESSION max_execution_time = {timeout_ms} |
| Verify statement ID | mysql.authoring.read_select_deadline |
| Verify template | SELECT @@session.max_execution_time |
| Parameter | Integer derived from validated scope timeout_seconds, converted to milliseconds; 1 through 4294967295; bool, zero, overflow and caller SQL are rejected |
| Connection | The isolated SELECT-only reader connection created by terminal_scope; no global mutation or shared cached connection |
| Effects | Limit read-only SELECT execution on this connection; no data/schema writes; disconnect on scope exit or preparation failure |

Register only these fixed templates through the existing audited provider
statement channel with a closed certified-authoring purpose. Install before
source binding and collection; verify exact equality with the requested value.
Both submissions join the actual control audit. Do not route through init_command,
raw SQL terminals, SQL-string rewriting or an unlogged driver shortcut. The
returned fact, not an attribute marker alone, controls the authoring guard.
Missing capability, denied setting, mismatched value or unsupported timeout range
fails before certification read with structured query_executed=False evidence.
No reset statement is needed: the connection is fresh and discarded.

## Acceptance after authorization

- Pin exactly these two templates, their purpose and integer bounds; reject
  unregistered/cross-provider/cross-purpose submissions and untrusted values.
- Verify actual driver submissions and returned effective limit on the restricted
  reader; retain initialization, query error and connection/cursor cleanup.
- Re-run the existing MySQL native certified-calendar consumer and direct scoped
  preview regressions; validate no publication after failed configuration/read.
- Independently observe original-query termination for a slow generated read;
  distinguish server timeout, metadata/fetch limits and cancellation qualification.
- Update capability disclosure, static snapshots, owning specs and current
  English/Chinese guidance affected by the newly admitted authoring capability.

The registered implementation now has bounded positive-calendar and native
slow-view timeout evidence. Full formal qualification and metadata/fetch/control
coverage remain separate. Authorization is distinct from the separately approved
owned-query KILL proposal. This document grants no scenario statuses.

## Native slow-source product evidence

[Certified slow-source development](2026-10-04-marivo-r93-evidence/mysql-certified-slow-development-01/README.md)
executes catalog.preview against a native aggregate view with the authored
one-second scope timeout. The actual SELECT-only driver observes both registered
controls and the unmodified Ibis business read. MySQL raises error 3024; the
isolated owner closes its cursor/connection, the independent observer confirms
termination, and no certificate is published. No KILL or administrator rescue is
used. The initial test exposed a raw driver-exception leak; certified captures
now report a structured SemanticRuntimeError with actual submission facts,
timeout and sanitized native code. Existing typed errors and prior certificates
are preserved by direct regressions. This selected SELECT-execution proof does
not cover metadata/fetch latency or formal complete-profile qualification.
