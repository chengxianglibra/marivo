# R7.7 Anchor, relative window and Metric evidence

Entry: clean `panda@f1a8f49cff538d285c6f93b20eea7790436f5258`.
The requested R7.6 predecessor was already committed. The entry status and six
owning-input digests are retained in the qualification record from
`/tmp/marivo-r77-entry.json`. This work is uncommitted; no push or release occurred.
AGENTS.md and packaged skills were not edited. Concurrent historical R8 document
changes belong to another task and are preserved.

## Public and execution owners

`session.anchors` has the frozen Event and Journey overloads and returns
LogicalAnchorDomain; explicit execution returns MaterializedAnchorDomain.
Event starts require a logical population. Journey starts retain the canonical
assignment, order, coverage and exact original population definition. A fixed
Journey requires its compatible fixed population. Foreign Session, wrong Subject,
mixed input modes and a Journey order override reject before rows or a Run.
`during` selects starts only. Complete Subject/Event/occurrence keys identify
each Anchor; repeated starts survive and Subjects without starts are absent.

Duration preserves signed int64 ticks in s/ms/us/ns. Exactly one named integer
unit is accepted; hours/minutes convert to seconds. ElapsedWindow requires positive
ticks. CalendarWindow keeps local wall time plus whole days in the named IANA zone.
Actual ambiguous/nonexistent deadlines reject with a typed repair; inexact or
overflowing deadlines reject without truncation. The admitted captured carrier
remains UTC/us from R7.2. Parquet ns input is not ns-exact qualification.

Every observation uses `[anchor, deadline)`, excludes its own complete occurrence
identity and requires captured business order for distinct same-instant uses.
The per-Anchor parts retain exact deadlines, full Subject mappings, coverage,
original numeric components and every `(Anchor, component occurrence)` use.
Overlap may share a contribution across Anchors. The current contract omits
original-state rollup that removes the Anchor coordinate, and the callable rejects
that operation.

`anchor.bind@v1` and `anchor.observe@v1` use the one core/method registry/planner,
Runtime and Store 7 publication/recovery path. Event-origin elapsed observation
lowers through public Ibis. Calendar and current local Journey starts prepare all
source dependencies in a finite envelope before consuming actual Anchor windows.
Versioned raw candidates acquire member/version authority at contribution time.
No local Anchor upload, source-after-local scan, rematching, SQL fallback or second
Store executor is used. All stages share the existing 600-second execute deadline.

The R5 numeric owners handle count, int64/float64/Decimal(38,6)/Duration sums,
corresponding original ratios, multi-root float64 linear and int64 ratio. Integer,
Decimal and Duration state is exact; float validation uses the existing R5 absolute
magnitude/roundoff owner. Missing, damaged or wrong-bound parts reject before a new
Run or exact hit. Failed publication, cancellation and timeout leave no successful
new Artifact, close resources and preserve prior committed Artifacts.

The core numeric grid instantiates Duration(us) and Decimal(38,6); it does not
claim an additional cross product of every possible Metric Duration unit or Decimal
precision. Supplementary execution/recovery preserves all four elapsed authoring
units s/ms/us/ns when their deadlines are exactly representable.

## Original qualification and actual execution

The [qualification record](2026-10-02-marivo-r77-qualification.json) references
five SHA256-checked cell shards retaining all **6750 original cells**: 25 profiles
× 9 full key profiles × 10 time profiles × source/fixed/cold. Original IDs, routes,
requirements and mandatory flags stay unchanged. Per-cell execution fields
distinguish the tested continuation from the original method target.

The module-owned matrix defines 90 three-process cases. At the user's direction,
the run stopped after **56 complete cases**; the other 34 were not run. Each
completed producer executes 25 source outputs, compares 22 relative observations
against an independent raw-row/Fraction/Decimal oracle, saves members and canonical
Journeys, then deletes source databases and Parquet files. Offline phases poison
Semantic loading, SourceSession entry/batches, DuckDB/Ibis DuckDB connections and
matching. They execute two fixed Journey Anchor bindings and 22 retained numeric
transports. Cold recovery recreates exact Artifact references. P17 checks retained
Anchor read and the public Subject binding helper; it does not execute fixed Event
binding. No full-grid acceptance is claimed for the interrupted matrix.

Every core key/time case includes snapshot and validity dependencies in both Event
and Metric routes. Eight supplementary table/Parquet × snapshot/validity ×
elapsed/calendar cases change Subject mapping at contribution time. They do not
reinterpret historical mapping as a current Subject join.

The separate three-process Anchor slice executes **152 current disclosed fixed K**
entries, including transport, qualified current-row statistics and Subject images,
and verifies cold exact hits for each. This is bounded Anchor evidence for A13;
retention, subject any/every and complete A13 keep their later owners.

P24's Anchor responsibility is checked through full-key retained part transport in
the 56 completed cases and supplementary positive/empty actual Subject-image
observation. Those source cases guard source preparation before local selection.
Other P24 producers retain their prior owners; retention remains R7.8.

The interrupted pytest run recorded 118 passed tests and no test failures before
KeyboardInterrupt; this comprises 56 completed matrix cases and the separate
boundary tests. The qualification record reports **2856 passed bounded cells,
1344 executed cells still unverified against original targets, and 2550 cells not
run**. All 6750 original IDs/routes/mandatory flags remain represented. Mandatory
original targets remain unfinished even when an adjacent continuation passes:
**2070 fixed method targets** (P17 and P25–P35/P37–P47), plus **90 P48 source native
targets**. Starts-only fixed Anchors lack captured new Metric input; their new live
observation rejects. Retained numeric transport cannot qualify an unexecuted fixed
`anchor.observe`. P48's shared/first canonical starts have local evidence for the
completed cases; the original native target remains unverified. No unsupported
fixed method registration manufactures qualification from these tests.

## Owning counterexamples and checks

| Responsibility | Executable owner |
| --- | --- |
| V06/V12/V13 | anchors_r77_oracle.py, 56 completed matrix cases, elapsed/calendar spring/fall DST, half-open deadline, same-time order, own exclusion, multiple starts, overlap, empty/no-Anchor domains, history mappings and multi-root state |
| Numeric state | All 11 count/sum/ratio/linear variants; null/empty policies; int64 beyond 2**53; Decimal/Duration precision; all four sum overflow families; native float cancellation |
| V14 | Five missing/corrupt receipt roles, five wrong-bound exchanges, pre-read fixed dependency rejection and three-process exact recovery |
| V15 | Seven publication/cancel/deadline fault points; local cancellation/deadline; forced cross-batch source capture; resource journal cleanup and prior Artifact readability |
| V16 | Sole graph/registry/Runtime/Store path, early relative-template rejection, no matching in offline workers, no false fixed/native qualification |
| V17 | Independent exports and native Help/reachability/budget checks; precise typing fixture; repr/show/contract; API build; identical executed latest English/Chinese example |

Final validation results are recorded in the
[acceptance record](2026-10-02-marivo-r77-acceptance.md). Raw local logs and receipt
paths plus SHA256 digests are retained in the qualification JSON. Failed/abandoned
development runs are not acceptance evidence: `runtime-final2` was interrupted;
the final `runtime-history` run was also interrupted at the user's request after
56 matrix cases; and earlier `runtime-history` and boundary runs exposed repaired
historical-path/fixture defects. The preceding 62-case boundary run remains
supplementary and is not promoted to the full grid.

No installed-wheel, remote backend, full R7, retention, subject any/every, complete
A13, release or pre-R77 cross-revision Artifact compatibility qualification is
granted by this record.
