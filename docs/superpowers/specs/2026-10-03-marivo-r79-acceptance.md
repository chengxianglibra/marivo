# R7.9 engineering closeout and full R7 acceptance audit

Entry: `panda@cdd00fd949bdb8dd5aa7a533c5d6c70f1c72f373`, 2026-10-03.
The remaining exclusive legacy Event consumer chain is retired. The audit is
complete; the R7.9 exit and full R7 acceptance are **not accepted**. Original
mandatory targets remain unverified and installed scopes retain failures and
user-requested skips. Passing engineering checks do not replace those targets.

The [evidence index](2026-10-03-marivo-r79-evidence-index.md) records the final
candidate, execution receipts, unsuccessful attempts and reproduction commands.
The [migration ledger](2026-10-01-marivo-full-algebra-dsl-r7-migration-ledger.md)
owns M01–M16 and the R8–R10 handoff. The original R7.1 snapshot and its **13500**
mandatory requirement IDs, routes and flags remain unchanged.

## Current engineering result

The old Event Dataset exports, source facade, family/producer registrations,
compiler branches, descriptor evidence, dedicated codecs, bundle adapters and
backend SQL files are removed. Current Journey pattern normalization now belongs
to `materialization/graph_journey.py`; its captured definitions, fingerprint,
participant-path, key and time validation are preserved. The new graph/methods/
Runtime/Store 7 path is the sole Event/Journey/History/Anchor/retention owner.

The independent retirement guard checks 39 exclusive modules from R7.4–R7.9,
their file/import absence, public exports, family registrations and rejection of
retired producer/semantics contracts. Shared Population, Metric, Association,
Forecast and Candidate owners remain exercised by the normal broad gate.

Acceptance also repaired two existing shared regressions: retention registration
no longer shadows History Subject transport, and categorical selection retains
its Cell and classification continuations. Ten History/retention observation
counterexamples and two categorical/example regressions passed before rebuilding
the installed candidate.

| Check | Current result |
| --- | --- |
| `make check-agent TESTS='-n 0'` | Passed: lint/import contracts, 424 typed source files, 5096 default tests passed, 5 skipped, API documentation; earlier parallel import timeouts remain separate |
| `npm --prefix site run build` | Passed: 321 pages and English/Chinese install-script checks |
| `make pypi-build pypi-check` | Passed: one wheel and sdist, archive/content checks |
| Same noneditable wheel | Audit completed with failures: retained XML reports contain 2002 passed and 9 failed; 6 commands retain failures and 3 commands contain user-requested interruptions |
| Basic public journeys and CLI | Passed: 16 table/Parquet journeys × 3 fresh processes = 48 phases; both CLI Help entry points passed with identical output |
| Original mandatory method/route cells | All 13500 preserved; 3129 verified only in the recorded scope, 10371 unverified; no full R7 completion granted |

The six failed command records cover R6 recovery, R7.4 Findings-cap continuation,
the R7.6 module deadline, four R7.8 continuation timeouts, the initial relationship
test migration omission, and both A10 continuation timeouts. The relationship
oracle was corrected and its two cases passed against the unchanged wheel;
the initial failed receipt remains separate. R7.8 belongs to both the failure
and user-interruption accounting because its failures preceded the stop request.

The user's instructions to skip the remaining R7.7 and R7.8 tests are applied.
R7.7 retained 19/90 complete profiles and XML with 47 passed; its remaining 71
profiles are user-requested skips. R7.8 retained 24/90 complete bounded profiles
and XML with 37 passed and four continuation timeouts that occurred before the
interruption. Its other 62 profiles and the uncollected evidence module are
user-requested skips. Those dispositions retain the original mandatory flags.

R7.6 completed all 90 History-view profiles, comprising 1620 independently
validated source/fixed/cold assertions. Its module timed out at 7200 seconds with
only 16/90 shared-consumer records and no A10 record. Positive phase evidence
qualifies its recorded History duty; the failed complete module remains failed.
The two A10 journeys were then attempted separately against the same wheel;
both fixed continuations timed out at 900 seconds, and cold phases did not run.

The scoped cell accounting is deliberately narrower than full acceptance:

| Current recorded scope | Cells |
| --- | ---: |
| Private preparation | 270 |
| Bounded local History/Anchor duty | 2859 |
| Shared History duty only | 144 |
| Funnel numerical duty only | 18 |
| Unverified | 10209 |

The 144 shared-duty and 18 numerical-only cells do not close their original full
targets. The [closure manifest](2026-10-03-marivo-r79-closure.json) retains each
requirement ID, original route, mandatory flag, actual scope and disposition.
Independent validation checks all original cell fields, all 236 legacy-node
records, current replacement owners, initial and corrected test inputs, archive/
member hashes, original inputs and every recorded attachment. Original legacy
statuses remain intact beside explicit current retirement/replacement fields.

## Remaining acceptance boundary

Starts-only `MaterializedAnchorDomain` cannot construct Event binding, a new
relative Metric observation or retention requiring absent captured inputs. It
rejects before source I/O or Run allocation. Fixed numeric/result transports do
not execute those kernels. This audit preserves the accepted current-interface
decision and introduces no capture API.

The original native Journey-start target is also unqualified; its current local
canonical assignment is recorded under its actual route. Funnel and public
Journey key/time coverage remain limited to their executed profiles. Retention
workers with no complete historical-axis matrix cannot qualify the original
full-profile targets. Original cells stay mandatory and unverified where the
actual method, input parts, historical axes or route differ.

Consequently **full R7 is not accepted**. The R7.9 implementation can retire the
old consumer chain without granting the unfinished fixed/native targets or
erasing qualification gaps from earlier phases.

The user's concurrent cancellation of Git tracking for historical qualification
files is preserved; their disk contents remain hashed test inputs. Existing R7.7
untracked evidence is preserved. No push, release, external service, MinIO or
real-Agent validation is authorized or performed. AGENTS.md and both
packaged workflow skills are unchanged.
