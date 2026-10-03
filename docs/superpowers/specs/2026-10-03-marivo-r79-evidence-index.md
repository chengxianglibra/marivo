# R7.9 retirement and installed candidate evidence

Date: 2026-10-03. Entry branch `panda`, HEAD
`cdd00fd949bdb8dd5aa7a533c5d6c70f1c72f373`. The [acceptance audit](2026-10-03-marivo-r79-acceptance.md) distinguishes
engineering closure from original full R7 qualification.

During execution, user-owned commits `d853307742` (qualification ignore rules)
and `3e3d0ac8ae` (R8 implementation plan) advanced HEAD. They change no candidate
Python source or test file. Entry and observed HEAD are recorded separately;
the six staged untracking deletions and the R8 plan are preserved. The installed
gate remains bound to the candidate's production hashes and archive digest. The
875-file initial source/test inventory and the single later test-input correction
are separately retained; every production file is unchanged.

## Candidate and reproducible gates

Raw execution attachments live under ignored `.superpowers/r79/`. Tracked closure
metadata retains their hashes, command outcomes, original cell dispositions,
source/test inventories and every original legacy test-node disposition. The
R7.1 consumer snapshot remains the immutable baseline; earlier acceptance records
are historical inputs rather than installed-package authority.

```sh
make check-agent
make check-agent TESTS='-n 0'
npm --prefix site run build
make pypi-build pypi-check
MARIVO_R79_EVIDENCE_DIR="$PWD/.superpowers/r79" .venv/bin/pytest -n 0 -m release -q --tb=short --maxfail=1 tests/test_analysis_runtime_wheel.py
git diff --check
```

`tests/test_analysis_runtime_wheel.py` stages explicit independent R5/R6/R7 tests
and helper/worker inputs into an external temporary directory, installs one
noneditable archive in a fresh virtual environment with pinned dependencies,
checks archive contents, and records every executed command and XML receipt.
`tests/installed_wheel_probe.py` verifies resolved `site-packages` origins and
the archive SHA256 at test-session boundaries and subprocess start/exit. A
deliberately poisoned source `PYTHONPATH` must be rejected. The later file view
used by static retirement tests resolves only to installed-package files.

The final wheel SHA256 is
`670669d66fba6d4c621451cfb14d2fc079ca4743d897b5706edc0fed532ad3b8`;
the corrected sdist SHA256 is
`830ec42d80b7916119337aad870b3cd4e0cbc3c1f326d4733589610422719bf3`.
The initial sdist digest
`69230eab369db015c9d1c35ee13cfe232d94ff0f15d5330db90af5268d626590`
remains separately retained. Rebuilding after the test correction below changes
exactly one test member; all 786 other members and the installed wheel are unchanged.
The independent installation is
`/tmp/marivo-r79-670669d6-mj6epc35/installed-check`; its source/test inventory
retains the initial 875-file inventory. The relationship regression below is the
only revised test input; it does not change the installed wheel or production files.

The same installed candidate runs R7.2–R7.8 independent oracles, resource and
corruption counterexamples, A09 Journey/Findings, A10 History and A13 bounded
Anchor/retention source/offline/cold journeys. R7.7/R7.8 worker receipts retain
actual kernels, fixed continuations, full keys/time profiles and cold exact hits;
successful adjacent continuations do not qualify a missing original kernel.
Affected J1–J4 and A02/A07/A08 public regressions retain the existing independent
three-process table/Parquet checks. This is local installation evidence; R9
remote/native forms, R10 real Agents and release remain separate.

## Inspectable audit metadata

The [closure manifest](2026-10-03-marivo-r79-closure.json) retains original
requirement IDs/routes/mandatory flags, current scoped dispositions, all 236
legacy-node dispositions, current replacement test owners, source/test and
historical input hashes, candidate archive hashes, exact installed commands,
per-module results and process-origin counts. The payload is compressed to keep
the full cell audit bounded in Git; its uncompressed SHA256 is independently
checkable. A scoped pass does not grant full R7 completion.

```sh
.venv/bin/python - <<'PY'
import base64
import hashlib
import json
import zlib
from pathlib import Path

manifest = json.loads(Path('docs/superpowers/specs/2026-10-03-marivo-r79-closure.json').read_text())
raw = zlib.decompress(base64.b64decode(manifest['payload']['data']))
assert hashlib.sha256(raw).hexdigest() == manifest['payload']['sha256_uncompressed']
audit = json.loads(raw)
assert len(audit['qualification_cells']) == 13500
assert len(audit['legacy_test_dispositions']) == 236
for name, expected in audit['historical_input_sha256'].items():
    assert hashlib.sha256(Path(name).read_bytes()).hexdigest() == expected, name
print(json.dumps(manifest['counts'], indent=2))
Path('.superpowers/r79/decoded-closure.json').write_text(json.dumps(audit, indent=2) + '\n')
PY
```

Reproduction uses the preserved on-disk historical inputs, including the user's
untracked qualification files. Their raw reconstruction copy remains in ignored
`.superpowers/r79/historical-inputs.json`; original files are not re-tracked or
restored into the index. Result summaries and original-cell dispositions remain
inspectable in the tracked manifest without local execution logs.

## Engineering checks and unsuccessful attempts

The focused default retirement/disclosure/Journey/funnel/History/retention gate
passed **129** tests. Full typecheck passed **424** source files. The corrected
broad gate passed **5096**, with **5 skipped**, plus lint/import and API-doc stages.
The site produced **321** pages. Wheel/sdist packaging checks passed.

Early retirement iterations had focused collection failures from a singleton
family-registration tuple and a stale disclosure binding, followed by three
expected export/order/family snapshot mismatches. The tuple/binding and the owning
snapshots were corrected before the 129-test final focused gate. Production typing
also caught that tuple shape; the final broad typecheck passed. Initial formatting
and import-cleanup failures were repaired before the final lint gate. These logs
remain development diagnostics and grant no acceptance.

The broad gate first exposed implicit timezone metadata preparation through an
obsolete Event fixture. The current no-I/O test now prepares `ZoneInfo('UTC')`
before the audited boundary; its narrow retest and the broad gate passed. The
failed and final logs remain separate.

An intermediate parallel Runtime invocation was stopped after failures while
retirement edits were still changing the candidate. It grants no acceptance.
The serial diagnostic and installed candidate own later results. The first
installed attempt failed before product tests: a static file-view link masked
the intentionally poisoned import. Moving that link after the poison check
restored the intended rejection. That early harness failure is retained apart
from final installed receipts.

A direct test-helper mypy invocation initially encountered duplicate module names.
With explicit package bases it reported existing errors in legacy test helpers
plus one new retirement assertion; the new assertion now narrows the dynamic
export list explicitly. A final focused check of all three changed installation/
retirement modules with `--explicit-package-bases --follow-imports=silent` passed.
Production typing and dedicated positive/negative contract checks also passed
through `make check-agent`.

The shared PostgreSQL adapter had an unused old Event-bundle error template after
its caller was removed. The template and reason were deleted. The earlier
installed attempt was stopped as superseded before R7 qualification; the package
was rebuilt and the full engineering gate passed again. Only the final archive is
eligible for installed acceptance. Earlier receipts and archive digests remain separate.

The serial Runtime diagnostic then reproduced a shared R7.8 registration defect:
retention's exact Entity transport key displaced the History consumer while
declaring only Subject/retention parts. History state selection into a new prepared
Metric failed admission. Its explicit regression failed before repair; the shared
registration now declares the already supported History-view/retention parts and
the actual graph local consumer. All six table/Parquet History selections and four
retention Subject-image observation cases passed (**10** total).

The installed common Runtime suite also found categorical `where()` dropping its
Cell because categorical reads have no numeric quantity. This removed classification
values, broke the existing coordinate/count example and hid valid continuation
calls. Both failures reproduced in the source tree. `graph_relation.where` now
preserves a bound field Cell as well as numeric quantity metadata. The strengthened
categorical-value assertion and executable example passed (**2** total). Existing
public/category contracts and identical EN/ZH examples are preserved. The earlier
installed candidate recorded **96 passed, 2 failed** and is not final evidence.
Final engineering and installed gates use a rebuilt archive after both repairs.

A final static review found that the remaining migration-stage annotation still
included literal 7 although every retained branch returns 8. The annotation was
narrowed to `Literal[8] | None`. Its preceding installed invocation had passed
452 contracts, 98 common Runtime cases and the first R5 modules before being
stopped as superseded. The final gate starts from the new archive; none of those
superseded passes are combined into its result.

The next installed invocation reached R7.4 with all earlier modules passing but
failed its executable latest-example check: staging had copied only three concept
pages and omitted `first-analysis.mdx`. R7.4 recorded **51 passed, 1 failed**;
the outer gate failed after 3225.59 seconds. This was a harness input omission,
not a new product counterexample. Staging now copies the complete latest English
and Chinese trees, with all **98** files checked byte-for-byte. Harness lint/typing
and packaging passed before rebuilding and restarting the complete installed
gate. Earlier package results are not combined with the final candidate.

The unchanged final archive then failed the full installer gate after **3433.88**
seconds. R6 recovery recorded **10 passed, 1 failed**: A07/Parquet's continuation
subprocess exceeded its existing 120-second test timeout. All preceding modules
passed. A serial diagnostic also exceeded 120 seconds, during production. That
diagnostic reused the completed outer pytest's temporary installation directory;
pytest retention cleanup subsequently removed it and invalidated the exit-origin
receipt. The diagnostic grants no qualification.

The remaining unchanged R7 modules and public three-process journeys run against
the **same archive hash** in a fresh, noneditable installation under an independent
temporary directory outside pytest retention cleanup. Earlier same-archive
receipts remain separate, the R6 timeout remains failed, and this continuation
does not turn the original full installer invocation into a pass. Its exact
selection and commands are retained in the closure manifest.

That remaining invocation passed 452 contracts, 98 common Runtime cases, 160
R7.2 cases and 34 R7.3 cases. R7.4 then recorded **51 passed, 1 failed** in
857.00 seconds: `test_finding_cap_retains_eligible_emitted_truncated_authority`
exceeded its existing 180-second `cap-continue` subprocess timeout. It is a
failed current Findings-cap cold continuation, not a numerical pass or a new
qualification. The unchanged R7.5–R7.9 modules and public table/Parquet journeys
are subsequently attempted independently in the same installation. Every
command failure remains in the manifest; a failed scope does not prevent the
remaining independent scopes from being audited.

The independent tail completed R7.5 with **153 passed, no failures or skips**,
in 1737.182 seconds. Its 90 P10 key/time profiles each completed the source,
fixed and fresh-process cold checks. This qualifies the recorded local History
duty for 270 cells; it does not qualify unfinished original cells in other
method profiles. R7.6 has written all **90** History-view profile records after
source/fixed/cold assertions. An independent check found **1620** unique assertion
labels, 18 per profile. Its complete module exceeded the 7200-second installer
command limit (**exit 124**, 7200.010 seconds), leaving **16/90** validated shared
consumer records with **144** unique phase labels and **0/2** A10 records. No final
JUnit XML was written. These positive phase artifacts are independently checked
against the exact profile IDs, expected methods/phases, source forms and staged
oracle code. The oracle writes each record only after all three phase assertions
complete. A successful record qualifies only its recorded method/route duty; the
module remains failed and missing profiles remain unverified. An available XML
failure takes precedence, and artifacts cannot substitute for a different kernel
or historical-axis scope. The
command budget failure is distinct from a product execute-deadline failure or
a numerical mismatch.

The user subsequently requested skipping the remaining R7.7 tests. The running
module was interrupted with SIGINT after 3028.805 seconds (**exit 2**). It retained
**19/90** complete profile records and final XML with **47 passed, no failures or
errors**. The remaining **71** profiles are explicitly `skipped_remaining_by_user_request`;
original requirement IDs, routes and mandatory flags stay unchanged. This is a
user-requested interruption, separately classified from the timeout failures.

The user also requested skipping the remaining R7.8 tests. SIGINT stopped the
running module after **4025.467 seconds** (**exit 2**) with **24/90** complete
bounded worker records. Its XML retains **37 passed, 4 failed**. All four failures
occurred before interruption: the `table/us/UTC` profiles `c-s`, `c-i`, `s-c` and
`parquet/s/America/New_York/i-c` exceeded the existing 180-second continuation
timeout. Those failures remain failed. The other **62** profiles without a
complete pass or failure receipt are `skipped_remaining_by_user_request`. The
following R7.8 evidence process was interrupted during Python startup, before
pytest collection (**exit 1**, 0.230 seconds); it executed no tests and is a
user-requested skip. The original 1080 P19–P22 cells remain mandatory and
unverified because bounded workers omit the frozen historical-axis matrix;
fixed P19/P20 additionally lack captured return inputs. Successful worker
continuations grant no full-profile qualification.

The installed relationship regression exposed an incomplete test migration:
its Metric execution still used the old Dataset interface, which Store 7 correctly
rejects. The corrected oracle uses public `members().observe()` with the authored
relationship, declared Entity count, historical region coordinate, grouping and
rollup. It retains independent regional counts `east=2`, `west=1`. Journey rows
now assert the two exact Subject/start identities and funnel reach counts `2, 1`,
matching the current one-row-per-Journey contract. Pure schema authoring and
pattern normalization retain their existing no-source-I/O assertion.

Narrow correction attempts first exposed the old measure-count empty policy and
old per-step Dataset row expectations; a separate construction attempt confirmed
that the typed member API performs permitted schema preflight. Those development
failures grant no qualification. The final source test passed both cases. The
corrected test is then staged and executed against the same noneditable wheel;
the original failed test receipt and initial input inventory remain separate.
Both corrected cases passed in the installed wheel, in a separate 55.352-second
command. The changed test module passed its focused typing check. No product file
or installed wheel changes during this test-only correction; the sdist is rebuilt
to include the corrected test, with member-by-member equivalence verified for
all other files. Original failed receipts stay failed and are not replaced by
the corrected module's receipt.

The broad gate after this test correction retained **5094 passed, 2 failed,
5 skipped**: two import subprocesses exceeded their existing 30-second limits
during the parallel run. A serial check of the whole import module passed
**10** tests in 26.15 seconds without changing source or timeout budgets. A
complete `make check-agent TESTS='-n 0'` then passed the same broad default
scope: **5096 passed, 5 skipped, 2897 deselected**, in 712.74 seconds, plus
lint/import contracts, typing of 424 source files and API documentation. The
parallel failure remains separately retained. Default skips and deselected
Runtime/release scopes grant no installed-method qualification.

The R7.6 module deadline had prevented its two A10 journeys from running. A
separate same-wheel invocation therefore selected only the existing A10 table
and Parquet cases. Both producer phases completed, but both fixed continuation
workers exceeded their unchanged **900-second** limits. The command retained
**2 failed, no passes or skips**, **exit 1**, in **1123.058 seconds** (XML time
1118.268 seconds). Cold phases did not execute, and no complete A10 profile
receipt was written. The original R7.6 module timeout and the two new A10
continuation failures remain separate; neither unfinished scope grants A10
acceptance or new method-cell qualification.

The independent tail completed all **16** table/Parquet J1–J4/A02/A06/A07/A08
journeys, with **48 passed** producer/continuation/cold phases and no phase
failure. Every journey uses three distinct processes and compares retained
identities, facts, independent outputs and continuation/run counts. Both module
and console CLI Help commands passed with byte-identical output. R7.9 retirement
passed **10** cases; the business-order suite passed. The original R6 120-second
recovery failure remains failed even though the later public journeys completed
under their separate 900-second per-command budgets.

The completed manifest retains **2002 passed, 9 failed** in its per-module XML
reports, **6** command records with failures and **3** user-interrupted commands.
R7.8 has both pre-interruption failures and a user-stop disposition. Positive
R7.6 phase artifacts are accounted separately because its failed module wrote
no final XML. These counts do not total every repeated invocation or substitute
for the original mandatory-cell ledger. Of **13500** preserved cells, **3129**
are verified only in their recorded scope and **10371** remain unverified.
Full R7 and the R7.9 exit remain incomplete.

The first collector invocation treated the generated installed `tests/__init__.py`
as a repository input; its staged hash is now checked separately. Independent
validation also caught current retirement/replacement labels overwriting the
236 frozen labels. The current labels now have explicit `current_` fields,
preserving every original field. Those metadata diagnostics grant no product
qualification; the corrected manifest is independently checked against the
frozen snapshot, current files, archive members and all receipt hashes.

## Retirement, disclosure and handoff

R7.9 physically deletes 17 remaining exclusive Event modules and their dedicated
test/worker/remote-fixture chain. Earlier funnel/Lifecycle deletions remain guarded.
Shared semantic registry/database fixture data moves to `event_semantic_fixtures`
and `event_source_fixtures`; neither recreates an old source facade or Runtime
executor. Existing relationship regressions use the public Journey path.

The native Help/family registry, public export/order snapshots, result repairs,
API docs, current design and Runtime coverage documentation are aligned. Latest
English/Chinese examples already use the unified public path and are executed
by the R7 suites. Release notes remain historical. General packaged workflow
skills remain applicable and require no edit; AGENTS.md and skills are unchanged.

M01–M16 separately record replacement execution, old consumer unreachability,
physical deletion and actual shared owners. All 236 original legacy-node IDs
remain accounted for; removal of backend-specific old tests grants no R9 pass.
R8 retains the accepted numeric/Duration/Subject-image boundaries. R9 receives
the original method/route/time/physical matrix and local issued-SQL evidence.
R10 receives reproducible installed commands and failures, without real-Agent
or release acceptance.
