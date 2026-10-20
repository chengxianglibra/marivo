# AnalysisFrame cutover

Status: cutover contract, 2026-10-19. Implementation acceptance is recorded in
[the progress ledger](../../implementation/analysis/analysis-frame-implementation-progress.md); this note does
not announce a package version, release or new provider Runtime qualification.
The [AnalysisFrame design](query-result-reuse.md) owns the final behavior.

## Data, completion and audit

AnalysisFrame replaces public Query with one immutable data type across pending
expressions, complete memory and exact stored data. Explicit source constructors
observe/read/values/frame all return it. Keep their distinct purposes: Metrics,
nonempty attributes on one Entity identity domain, literal rows, and general
Population keys. Source construction and inspection do not read business rows or
create a Run. Use returned compute/persist Frames to retain exact values and keys.

```python
import marivo.analysis as mv

session = mv.session.create("cutover-example", timezone="UTC")
pending = session.values(by=(), columns={"amount": "CNY"}, rows=(((), {"amount": 100}),))
fixed = pending.compute()
saved = fixed.select({"adjusted": fixed["amount"] + 10}).persist()
saved.show(n=5)
saved.artifact.show()
saved_session_id = session.id
saved_artifact_id = saved.artifact.id
resumed = mv.session.resume(saved_session_id)
reopened = resumed.persisted_frame(saved_artifact_id)
continued = reopened.where(reopened["adjusted"] > 0).compute()
continued.show()
```

Compute returns complete memory and writes no saved result or Run. Expression
persist completes evaluation before Run admission, then publishes the existing
result ID. Memory persist never reevaluates; stored persist checks commit metadata
and returns self without another Run. A new memory publication can have a new
Artifact while preserving the same result/audit. Unknown outcome is reconciled
against the original transaction and journal, never reevaluated to settle it.

Keep exact Session ID and frame.artifact.id; persisted_frame accepts that string
in the original Session. Artifact is only submission/audit: show() without n,
findings() and evidence_digest(). It has no to_pandas, rows or computation input.
Frame owns show(n=20), to_pandas and supported data operations. A pending derived
Frame does not inherit an ancestor's Artifact. There is no session.artifact or
execute compatibility route, public carrier constructor or old Query alias.

## Value names, axes and comparison

User-declared value names are single nonempty segments: no dots, backslashes,
control characters or reserved left_present/right_present. Invalid names fail
with a renaming repair; they are not silently escaped. Generated values use
left.q/right.q/absolute.q/relative.q paths. output_names lists values only;
presence_names lists independent boolean paths. getitem looks up exact full
names. coord takes a single-component FrameAxis; complete composite identities
remain one axis in by/on, not one scalar value.

Column/FrameAxis handles belong to exact inputs, not names/fingerprints or a
reconstructed source. Ordinary axes require complete explicit on; zero axes and
one sole interval or calendar axis per input permit only the stated inference. Match is
chosen explicitly. Align preserves left/right values, compare generates four
groups; relative is (left-right)/abs(right). Missing endpoints and NA differ,
with independent presence. Outer keeps left row order then right-only rows.

## New source facts remain explicit

Given a Session, resolved revenue Metric and region/city Fields, and explicit
current_period, fix a parent selection before asking for a new breakdown:

```python
regional = session.observe({'revenue': revenue}, by=(current_period, region))
chosen = regional.top(5, by=regional["revenue"]).compute()
parents = session.population_from(chosen, on=((chosen.axes[1], region),))
cities = session.observe({'revenue': revenue}, by=(current_period, region, city), where=mv.in_population(parents)).compute()
cities.show()
```

This keeps fixed selected regions if source values change; city and revenue are
new source facts with a new read time. It does not recover historical city
selection or require the original semantic definition versions. Explicit new
inputs still satisfy kind/root/grain/unit/time/null checks and business versioned
identity rules. No shared snapshot between selection and new observation is promised.

Population bridge on is required, including legal on=(). It projects the selected
ordinary complete joint tuple and deduplicates. Empty input remains empty, rather
than meaning no filter; zero-axis empty and singleton {()} remain distinct.

```python
scalar = session.values(by=(), columns={"count": "1"}, rows=(((), {"count": 1}),)).compute()
unit_domain = session.population_from(scalar, on=())
keys_only = session.frame(unit_domain).compute()
keys_only.show()
```

Only ordinary field or complete identity axes bridge onto explicit resolved
Field/Entity inputs. No interval/calendar/paired/value-column bridge, automatic source
object recovery, Field-to-Entity promotion or per-axis Cartesian reconstruction
is provided. by=Population is the selected complete output domain; use in_population for
contribution filtering. Population.count captures a complete identity domain independently.

## Removed and postponed capabilities

Frame has current-row select/where/global top/reduce, full-axis align/compare,
coordinate selection and supported frozen-calendar match. It has no original
rollup/drill/share or population receiver, partitioned top, CalendarAxis.coarsen,
identity corresponding/broadcast, arbitrary relationship maps, automatic scalar
broadcast or repeated alignment of paired axes. Original Metric overall, new
attributes, source windows and source grain need explicit source construction.
Current-row mean 55 is not original overall 1000/91; distinct sum 4 can have
union distinct 3. Reduce is not an equivalent replacement for original rollup.
Unsupported intents without a valid explicit source route remain unsupported;
external arithmetic must state its own fixed input, denominator and limitations.

Interval ordinal requires one declared record per side with the same boundary
timezone. Calendar ordinal consumes compatible complete nominal/actual partitions
without compressing saved positions. `offset(*, years=n)` / `offset(*, periods=n)`
replace `previous_year()` and `period_offset()` without public aliases; exactly
one non-boolean integer keyword is
required. Source CalendarAxis.shift admits unit-step builtin week/month/year full
buckets or bucket-start prefixes and freezes exact sparse/empty target selections.
Data-side complete day/quarter matching remains supported; partial day/quarter
reject. Missing ISO weeks/civil cutoffs are legal absence, never clamped dates.

Compare takes a nonempty named Frame mapping, preserving current axes/unqualified
values and adding each alias's baseline/absolute/relative groups. Alias-keyed
on/match bind complete axes; a compatible immediate frozen shift relation can
supply match. Wrong anchors/conflicting rules reject. Exact checks actual complete
keys; outer appends mapped baseline-only keys and rejects actual baseline time
rows outside the current declared domain. Align retains paired output and the
general two-ended union. No Metric, source or current TZ reconstruction occurs.


## Read integrity, interpretation and audit time

Metadata opening, Frame.show(n=0) and Artifact audit read no payload. Positive
show decodes the actual row prefix and selected frozen coordinate records;
receipt hashing/decompression may read more bytes. Metadata/preview success does
not certify unread payload. Full stored compute/export validates all required
data and attachments; missing/corrupt records are not ordinary absent endpoints.
Complete memory is detached from old files after loading and can be derived or
published even if those old files disappear. Reading the old stored root still fails.

Export retains raw-key MultiIndex columns and exact row order. Isolated typed
attrs hold axes, units and frozen_coordinates tables with labels/bounds/ordinals
and paired maps. Prefer show for interpretation. No new visible projection
column, endpoint method or attrs importer is added. Complete data contains no
producer graph, original semantic object, connection or hidden aggregate state.

One audit contains inherited source facts (exact-value dedup only) plus current
evaluation. It does not accumulate all intermediate history. Source read,
evaluation and commit times remain distinct; a recent commit is not refreshed
data. Use runs/get_run and output references for publishing history; there is no
public Session graph or latest-answer selector. RawSqlResult stays on its independent datasource reader and
does not enter Frame Store, typed operands or a new importer.

## Session and observe inputs

Use strict `mv.session.create(name, timezone=None)` for a new name. Omission freezes
the creation-time system zone; exact-ID `resume` restores that value, regardless of
later TZ changes. `recent` discovers names/IDs, `inspect(session_id)` reads bounded
metadata, and `reconcile_run(session_id=..., run_id=...)` checks an original publish
transaction. There is no implicit current Session, graph or Session source_bindings.

TimeScope/CalendarAxis belong in observe.by. An interval is a real output axis,
including single-bucket results. P.count() captures a complete Entity identity
Population, including absent identities; counts of P/Q are independent lazy
Metrics. by=P selects output keys rather than asserting coverage of all source
coordinates. Time plus ordinary axes requires complete on; use explicit ordinal
for a two-period comparison. Ordinary bridges select only ordinary axes.

## Older Stores to Store 15

Store 15 refuses every older generation's Session/result and owned Finding/Evidence
data reads without changing any old database/WAL/SHM/payload bytes. There is no
migration, dual read or downgrade. The restriction covers data access beyond
persisted_frame; renaming a Session or selecting an old descriptor cannot bypass
it. create cannot initialize a new generation around an old-only
project. Read-only recent discovery may return an empty page
without exposing old state; that does not grant an older-data read exception.

Before switching, export required results with the version that wrote them.
Retain a compatible version/environment when Session or audit reads are needed.
Keep the original files and environment information; this note invents no command
for an unknown historical version. Exported tables do not become new typed
semantic authority. A separately prepared fresh project can run an explicit new
analysis, but that produces new source facts rather than recovering old state.


## Store 13 to Store 14: explicit Attribute and method facts

Historical transition record. The Store15 section below owns the current generation.

The current Store is generation 14 under `.marivo/analysis/generations/v14/`.
All Frames use `marivo.frame_output/v3`, `frame.v3` semantics and the
`frame-output-v3` collection, including Frames with no method facts. The generation-13 baseline used its own closed v2 schema; adding required
method descriptors therefore advances the shared protocol rather than reusing v2.

`session.attribute` is the sole explicit-source entry. Two immutable
`AttributeEndpoint` inputs supply Metric mappings and endpoint scopes; `by`
supplies new classifications. Default investigation returns ordinary left/right/
absolute values and independent presence without contribution proof or new
method facts. Optional contribution requires the qualified physical DuckDB table
root/fiber statement route, with complete component and sample proof. Files,
remote providers and arbitrary window/per shapes are not thereby qualified.
An unsupported contribution fails; a valid default investigation is a separate
explicit request. No Frame attribute/drill/rollup/share receiver is restored.

Contribution results are ordinary Frames with complete immutable `method_facts`.
The facts explain original totals, schemas, parents and coordinates; filtering,
ranking or reducing rows does not change original denominators or recertify a
new decomposition. Current numerical results retain actual completed native
types and units; no universal Decimal precision or read count is promised.

Stored manifests retain bounded method descriptors. Full owned method tables
and their receipts join the existing single atomic publication/readback and
unknown-commit reconciliation. Saved derivatives own copied payloads; the
original Artifact, Catalog, source and calendar reconstruction are unnecessary.
Metadata opening, repr, preview, Artifact cards and idempotent stored persist do
not read method files. `to_pandas()` validates current data and required current
attachments only. The explicit `method_facts` getter validates complete method
payloads without primary rows; stored compute and evaluated derivatives consume
both. Method-file damage can leave metadata/export usable while full consumers
fail. Pending fact access requires compute/persist first.

Store14 refuses Store13 and all older data without changing old bytes, migration
or dual reading. An old-only project cannot initialize a bypass. Preserve that
Store and create a new named Session in a fresh project. Reading/exporting with
the original compatible environment is a separate deliberate workflow, not a
Store14 migration. This local implementation boundary does not establish new
provider Runtime, installed-package or release qualification.


## Store 15: exact period comparison and frozen relations

The current Store is `.marivo/analysis/generations/v15/`. All Frames use
`marivo.frame_output/v4`, `frame.v4` semantics and `frame-output-v4` evidence.
Required closed schema facts now include exact direct calendar origins/maps and
named comparison value/presence bindings, including transported presence-only
facts. Frozen calendar directories use calendar.v2. Independent interval.v1,
physical-part and Attribute method-payload tags retain their own contracts.

The Store13 to Store14 section above records the earlier Attribute transition;
it is historical version context. Store15 refuses Store14 and all earlier data
unchanged, with no migration, dual read or same-project bypass. Preserve an old
compatible environment for old data or explicitly reconstruct source questions
in a fresh project. Restoring v4 endpoints and reports reads finite saved data,
not Metric definitions, current models, callbacks or timezone reconstruction.

Positive direct/comparison preview validates required finite endpoint/map
authority while bounding primary rows. Zero preview and Artifact audit remain
payload-free. Exports expose isolated comparisons/axes/units/frozen-coordinate
metadata for interpretation only; attrs are never a recovery input. This current
contract does not claim provider Runtime or release acceptance.
