# Proposed MySQL owned-query cancellation exception

Status: explicitly authorized by the user on 2026-10-05, limited to cancellation
of the selected source session's own active query under the boundary below.
The registered statement and bounded owned-control path are implemented with
development evidence. Bounded native graph atomicity and guarded control-resource
recovery now have development evidence. Full product qualification remains open; no formal scenario is granted.

The [R9 implementation plan](archive/2026-10-04-marivo-full-algebra-dsl-r9-implementation-plan.md)
does not authorize new internal SQL exceptions. Its SQL governance section
requires counterexamples, alternatives and a minimal operation/backend/purpose
list before a specific authorization decision.

## Observed counterexamples and alternatives

The diagnostic evidence (historical record in Git history)
uses the current SELECT-only MySQL reader and exact Ibis-compiled aggregation.
At a 0.05-second deadline, the current interruption request leaves the server
query active at 0.2 seconds. It returns the structured timeout only after the
independent test administrator rescues the query, at about 0.73 seconds.
Shutting down only the owned native socket releases the client at about 0.21
seconds, but an independent server observation still sees the query active
after shutdown. That query also requires test-only administrator rescue. Client
release therefore does not prove server termination.

The installed mysqlclient 2.2.7 `Connection.kill()` is not a no-SQL alternative:
[its implementation](https://github.com/PyMySQL/mysqlclient/blob/v2.2.7/src/MySQLdb/_mysql.c#L1715-L1730)
constructs a `KILL` statement, which kills the connection. The current MySQL
profile exposes no native query-timeout wrapper. Ibis-generated business reads
remain unchanged; compiler hint rewriting and source-after-local fallback are
excluded. These diagnostics do not grant any R9.3 scenario pass.

## Exact proposed authorization

| Field | Proposed boundary |
| --- | --- |
| Backend | MySQL only |
| Operation | Cancel the active query on the selected source session's own native connection |
| Purpose | Analysis execute deadline or explicit interruption of that owned read |
| Statement ID | `mysql.analysis.cancel_owned_query` |
| Statement | `KILL QUERY {thread_id}` |
| Parameter | A positive integer obtained from that still-owned connection's native `thread_id()`; never caller-authored |
| Execution connection | A separate governed connection to the same datasource using the same SELECT-only identity |
| Effects | Terminate the owned query; no data/schema mutation, cross-session target or arbitrary statement |

Register exactly this statement with the provider governance mechanism and an
Analysis-cancellation scope. Ordinary authoring, timezone handling, raw SQL,
algorithms and other backends gain no new exception. Create and bound the control
connection before the business submission; include its lifetime in the existing
execute budget and resource journal. Interrupt the owned data socket so a failed
control request cannot leave the client blocked; report unconfirmed server status
as `remote_unknown`. Join cancellation work before owner-thread cursor/connection
cleanup. Do not publish if required cleanup is unconfirmed.

Acceptance requires the same reader identity to cancel only its own query, exact
driver submission classification, independent original-thread/query termination,
owner-thread cleanup, and atomic failed Run state with no partial artifacts.
Cross-session IDs, unavailable control, failed cancellation and close failures
must reject or remain unverified. The R9.5 full metadata/control audit remains a
separate obligation. Until implemented and verified, MySQL cancellation remains
unverified; this proposal does not redefine it as passed.

## Bounded implementation evidence

Owned cancellation development (historical record in Git history)
records a real SELECT-only reader cancelling its own long query at a 50ms
execution deadline. The independent observer finds both original and control
connection IDs absent after owner cleanup. Recorded cleanup callbacks execute on
the execution thread. Product receipts retain remote_unknown because control
acknowledgement alone does not establish independent server proof.

Preparation connects before the business submission, checkpoints the shared
budget before and after connection setup, and sets one-second native connect,
read and write bounds on the separate control connection. Repeated preparation
reuses that control. The callback verifies the current native data connection ID
against the prepared identity before submitting the registered statement. A
missing/mismatched control sends no KILL; control failure still shuts down only
the owned data socket. Cleanup waits for control work and attempts data/cursor
release even if control disconnect raises. These bounds do not promise a 50ms
hard end-to-end latency.

The package records 120 affected pure checks, one native cancellation proof and
two affected runtime regressions. Touched-module typing, lint and formatting
pass. Unavailable control, reused data identity, expired preparation, failed
control and control-close failure have direct regressions. Full independent
native submission classification, explicit interrupted graph atomicity and
persistent control-resource reconciliation remain unverified.


Native graph cancellation and resource recovery development (historical record in Git history)
adds creation-before-use reservation and acknowledged-only discharge. Native
cancellation fails the Run, publishes no new artifact, preserves the earlier
artifact and discharges the control obligation. A deliberately missing close
acknowledgement instead retains an incomplete Run and persistent obligation;
guarded recovery refuses to assume release or cancel another connection. Driver
submission spying verifies the exact KILL target/control connection and all
captured business SQL. These bounded results supersede the earlier pending
atomicity/journal statements for these two cases only. Full profile qualification
and the broader R9.5 metadata/control inventory remain separate obligations.


## Qualification binding boundary

MySQL resource binding development (historical record in Git history)
requires all three resource cases: native 50ms cancellation, atomic interrupted
graph failure and unconfirmed control-close recovery. Native data/control IDs and
independent driver observations identify the exact approved KILL target. Actual
business cursor close calls are observed on the execution owner thread. The
binding predicate refuses foreign IDs, missing server proof, incorrect cleanup,
partial publication, lost obligations or unverified recovery. Passed Runtime
invocations are mandatory in addition to receipt fields.

The C04/C05 binder may admit MySQL only after the same frozen candidate supplies
these resource proofs and every existing family consumer/oracle. Without the
proof set it remains blocked. Historical indexes are preserved; development
captures do not grant status or bypass candidate identity.


C03 resource dispatch repair (historical record in Git history)
now admits the same optional validated three-record resource set to its existing
predicate. A matching frozen candidate and all original C03 consumers remain
mandatory. MySQL remains blocked by the independently observed Boolean/int8
route; cancellation authorization or development records do not waive it.
