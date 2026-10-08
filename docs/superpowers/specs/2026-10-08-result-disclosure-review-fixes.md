# Result disclosure review fixes

Status: both review findings repaired; engineering and selected installed checks
passed on the new frozen candidate.

This follow-up addresses the two P2 findings against HEAD
`4f60182fbe9f8f1a5d172590058a70f44fdf5ff4` plus the dependency/disclosure working
changes. It supersedes the prior candidate for the repaired presentation paths;
the [original acceptance record](2026-10-08-dependency-and-result-disclosure-acceptance.md)
and its candidate/evidence remain preserved at their original scope.

## Repairs and independent regressions

Identical terminal-column facts are shared once with their ordered column
labels. Grouping uses both the fact name and value, so different units, keys and
boundaries remain separate. A 16-column table from the original adversarial
fixture now displays its metadata in 2927 bytes under the unchanged 8192-byte
default budget; the reviewed implementation required 8801 bytes and raised.
The regression also exercises bounded row display and source/fixed/cold recovery.

Logical Dwell parent contracts obtain Duration types through the existing History
field inventory and physical field type owner. Materialized parents continue
reading their retained Arrow schema. Fresh logical parents and logical selections
from recovered parents expose mean/median/p90 units and conversions. The
three-process regression forbids source entry/current Semantic loading during
recovery and checks that disclosure adds no Run. Independent microsecond values
and the original calculation remain unchanged.

Owning specs, API reference and latest English/Chinese guidance document shared
column facts and logical-parent units. No public exports, signatures, algorithms,
Store version or packaged skills changed.

## Frozen candidate

Evidence directory:
`/Users/lichengxiang/.local/share/marivo-qualification/disclosure-review/20261008-153939`

| Artifact | SHA-256 |
| --- | --- |
| Wheel | `c0a7eb6488cb7ded1ef445e5169458066d35b5adac749a8956e6abb3b4ea5795` |
| sdist | `621aa61aacffdfd4080cba602b4f6ea779e3c41f12847b3ef9c3e5759180f6b7` |

The candidate was frozen and built outside the repository. The inventory, dirty
diff and HEAD bind the snapshot; the prior `68822f61354d...` candidate was not
overwritten. `review-reproduction.json` records the original counterexamples
after repair. Final check logs and installed origin/command receipts bind this
new candidate rather than transferring the earlier candidate's passes.

## Validation

- Focused default disclosure tests: 28 passed.
- Focused interpretation/Duration Runtime tests: 2 passed.
- Touched product and probe typechecks: passed.
- `make check-agent`: lint/import contracts, 330-module typing, 5115 passed /
  1 skipped, and API documentation passed.
- Site content validation and build passed, including install outputs and 308 routes.
- External `make pypi-build pypi-check`: both archives and content checks passed.
- Python 3.10 and 3.12: each selected installed suite passed all 4 tests, with
  19 deselected. Each uses separate noneditable base/DuckDB environments and
  verifies archive metadata, SQLGlot 30.8.0, real DuckDB table lifecycle,
  source/fixed/cold interpretation and core-only cold reads. External
  `MARIVO_TEST_CONSTRAINTS` and `PIP_CONSTRAINT` were unset.

`qualification.json` binds the command logs, installed dependency/origin receipts,
archive hashes and frozen inventory. Product and changed regression bytes still
match the frozen candidate; HEAD remained `4f60182fbe` through final verification.

This is engineering and selected installed-package evidence. It does not update
R10.4, R10.5 or release qualification. No real model or skipped cost collection
was run. Existing unrelated work and original acceptance records are preserved.
