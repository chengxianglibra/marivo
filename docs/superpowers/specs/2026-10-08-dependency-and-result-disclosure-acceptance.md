# Dependency compatibility and result interpretation acceptance

Status: the requested engineering and fresh-install checks passed. This record
does not grant R10.4, R10.5, real-Agent or release qualification.

## Implemented scope

- Core metadata pins `sqlglot==30.8.0`; every backend extra inherits the same
  unmarked requirement in both wheel and sdist. No SQL rewrite or execution
  fallback was introduced.
- History distribution discloses checkpoint × model_state × full axes grain.
  Seeded/censored Subject pools repeat across state rows and cannot be summed
  across states. Zero state-cell complements do not identify NotStarted or
  censored Subjects.
- Dwell and Duration cards, contracts and terminal table columns disclose the
  retained unit and ticks-to-seconds conversion. The independent precision
  oracle remains `86412333333 us = 86412.333333 s`.
- Association, coefficient/selected and terminal table columns disclose the
  complete ordered original pairing tuple, including each Entity, field and
  coordinate role. Candidate output keys remain distinct from observation keys.
- Existing Help targets extract History constraints from their producer
  docstrings. Owning specs, API documentation and latest English/Chinese
  examples are aligned. Public exports, call signatures, algorithms, result
  values, Store 8 and packaged skills were not changed.

## Candidate authority

The candidate was built outside the repository at:

`/Users/lichengxiang/.local/share/marivo-qualification/disclosure-compat/20261008-151440`

| Artifact | SHA-256 |
| --- | --- |
| Wheel | `68822f61354d6a5fc268d005ab152cdc269b8da7be296c3586b8629a993f95e3` |
| sdist | `b0c78e537d6931e9000ce3da9c9de6fe6a9fd4b1b463f51a4bb3cbc3d28db96c` |
| Frozen 1308-file inventory | `0d330098cdf34cbc612b3460089d1e154d92757d8c2a1ca6e79335e58dc27bdb` |

Implementation and freeze began at HEAD
`231fa4feb6805cf7a0e2351b5703fa680cd0c12c` on `panda`, with pre-existing
uncommitted work. Concurrent documentation work advanced HEAD to
`4f60182fbe9f8f1a5d172590058a70f44fdf5ff4`; it retained the new owning-spec
paragraphs. Frozen product/dependency bytes were verified against the current
working tree after that commit. `freeze.json`, `manifest.json`, the captured
dirty diff and worktree status bind the snapshot rather than HEAD alone.

Probe extensions for core-only reads are separately bound by
`final-probe-manifest.json`; they did not change the candidate archives. Existing
R10 candidates, indexed original failure evidence and R10.4 work were not
overwritten by this task. This task made no commit or push.

## Validation

| Check | Result |
| --- | --- |
| Focused default tests | 30 passed; subsequent focused disclosure/doc checks also passed |
| Exact Duration and interpretation source/fixed/cold Runtime | 2 passed; included in the final 4-test Runtime run |
| Public History views and captured-observation recovery Runtime | Both risks passed; final combined run: 4 passed |
| DuckDB nine-method statistical source/fixed/cold Runtime | 1 passed |
| Latest English/Chinese executable History example | 1 passed; code blocks identical |
| Final `make check-agent` | Lint/import contracts, 330-module typing, 5114 passed / 1 skipped, API documentation passed |
| Touched probe typing | Passed with imported existing helper diagnostics excluded |
| Site content validation | 343 required files verified |
| Site build | 0 errors / 0 warnings; 321 pages; install outputs and 308 routes verified |
| External `make pypi-build pypi-check` | Both archives and 326-product-file content contract passed |
| Python 3.10 selected installed checks | 7 passed / 16 deselected |
| Python 3.12 selected installed checks | 7 passed / 16 deselected |
| Python 3.10 and 3.12 core-only History/association cold reads | Both passed in separate installed processes |

Each interpreter installed base and DuckDB into separate noneditable virtual
environments. `MARIVO_TEST_CONSTRAINTS` and `PIP_CONSTRAINT` were explicitly
unset; all four recorded install commands have no constraint arguments and all
four dependency closures contain SQLGlot 30.8.0. `pip check`, site-packages
origin and `direct_url.json` bind imports to this exact wheel.

Installed DuckDB checks execute Ibis memtable → table creation, actual reads,
drop and cleanup. Installed interpretation checks use three distinct processes
for source, fixed continuation and cold recovery. Source models and data are
removed; source entry and current Semantic loading are forbidden. Core-only
reads reproduce facts, cards and rows without importing DuckDB or adding Runs.
Terminal column hints, bounded display and composite `order_id` / `member_seq`
roles are covered. Identity values are not added to the disclosed key schema.

Raw build, final broad gate, site and installed logs, per-environment commands,
origin receipts, dependency closures and available Runtime phase/state receipts
remain under the external candidate directory. `qualification.json` binds
their hashes and the final product/probe inventories.

## Boundaries

No real model was run by this task, skipped cost collection was not restarted,
and no R10.4/R10.5 acceptance or publication status was updated. Selected
installed tests are bounded evidence for these repairs, not the full release
suite. Original failure records are not replaced by the new candidate's passes.
