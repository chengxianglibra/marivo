# R7.2 private occurrence preparation evidence index

Date: 2026-10-01. Entry: `panda` at
`4a472dab4cedc833ebc7b333f2e34a4b51b7b62f`; R7.1 state is preserved.
The [qualification record](2026-10-01-marivo-r72-qualification.json) retains the
270 original P01 requirement IDs and separate source/fixed/cold preparation status.
The historical [R7.1 snapshot](2026-10-01-marivo-r71-consumer-snapshot.json) remains
unchanged. Final validation receipts are recorded below.

## Precision amendment and capture authority

The user accepts native source/Ibis/driver precision loss, including ns→us;
this decision supersedes the earlier source-lossless requirement. All checks,
bounds, versions and consumers use captured timestamp(us, UTC). T09/T10's 54
S/F/C IDs pass with **possibly lossy** disclosure. Original s profiles have
actual Parquet ms storage in the selected PyArrow implementation. Positive,
negative, boundary, truncation, precision-created ties and fixed/cold disclosure
have independent oracles. No source-side tick extraction or lossless claim is
introduced. Metadata records declared/file/captured units and native behavior;
receipt-bound schemas and Evidence descriptor identity retain it. Public result
`.show()` disclosure is mandatory when those later result families connect.

## Implementation and phase responsibility

Closed Event/StateModel/BusinessOrder captures and occurrence.prepare@v1 enter the
one core/registry/snapshot/Runtime/Store 7 path. Native table preparation uses
same-connection DuckDB transactions; local Parquet uses hashed read-only capture
files and a content manifest. Ibis authors and lowers every analytical statement;
the driver receives its SQL unchanged. Driver APIs own transactions/cancellation.
Full occurrence/Subject keys, participant mapping, predicate, version, order and
coverage are verified; empty input retains closed schema. Order does not use IDs.
Insufficient follow-up remains unknown; malformed coverage fails.

V01-V03: typed complete-key/participant/time/predicate/validity/snapshot/order and
coverage oracles. V13 preparation duties: explicit capture sharing/independent
realization, full dependencies, source-first F13 plan and no upload/fallback.
V15 preparation duties: issued SQL, transaction/file replacement, cross-batch
keys/schema/close, real native interruption, 600-second checkpoints and atomic
precommit failure. The common deadline adds no row/memory quotas.

Source preparation pushes member/time bounds, predicates, participant/history
joins, projections and integrity checks into Ibis. Temporal comparison exchanges
only bounded actual candidate times. Business-order DAG cycle/ambiguity checking
remains local over projected captured rows; a full-equivalent public-Ibis
precedence kernel is not qualified. F13 local restriction depends on a selection
computed after preparation, so it consumes retained keyed sufficient components.
Scalar preaggregates lack exact restriction support and are not admitted. The
prepared schema retains contribution keys/time, full member keys, amounts and
required coordinate values. Actual Arrow candidate rows/bytes are observable;
there is no fixed transfer quota or a claim of backend performance qualification.

## Independent validation and limits

`tests/test_analysis_domain_preparation_r72.py` owns the nine key × ten physical
profiles and independently verifies occurrence and Subject sets, captured time,
source/fixed equality and a fresh source-offline process executing actual fixed
continuation. Other oracles cover integer/enum/precedence, repeated StateModel
triggers, observed/declared/mixed/unknown coverage, exact overlapping Anchor
restriction, historical coordinates, empty and strict-subset selections, snapshots,
file replacement, native SQL, cancellation and publication faults.

Count/int64/float64 sum/mean are the finite connected F13 foundation. Decimal,
Duration, ratio/linear and other relative Metric matrix cells remain with R7.7.
The Anchor restriction primitive does not qualify public Anchor production.
Matching/replay/public domain APIs, Finding closure, legacy retirement, same-wheel,
full R7, R7.9 and R9/new backends remain pending. AGENTS.md and packaged skills
are unchanged. No commit, push or release was made.

## Final validation receipts

- Final R7.2 Runtime: 145 passed in 131.12 seconds, including all 90 P01 cold processes and six review regressions.
- Historical/deadline/review follow-up: 19 passed.
- Final `make check-agent`: 5368 passed, 5 skipped; typing 426 modules, lint/import contracts and API documentation passed.
- Site build: 321 pages; English/Chinese install script verification passed.
- `git diff --check`: passed.
- A superseded repair batch had 142 passed and 3 deadline-context failures; commit-marker isolation was repaired before the complete passing rerun.
- Qualification implementation hashes were refreshed after the repairs; protected R7.1, AGENTS.md and packaged skill hashes are unchanged.


### R7.2 adversarial review repairs (2026-10-01)

The six reproduced review findings are repaired: prepared contributions use ordered
Subject identity rather than relationship key order; prepared observation admits
normalized historical relationship contracts and checks root version nonoverlap;
version checks share the native captured time, including localized timestamp_ns;
native table precision metadata reads the actual physical timestamp scale.
Deadline checks stop ordinary execution before commit. Once Store 7 durably
commits, acknowledgement and exact original-Run recovery preserve the committed
outcome instead of reporting an ordinary timeout after successful publication.
Six independent regression cases and the existing historical cases with normalized
version flags exercise these boundaries. Preparation qualification still excludes
matching, replay, public domain results and new backends.
