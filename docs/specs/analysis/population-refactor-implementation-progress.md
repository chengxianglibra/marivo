# Population refactor implementation progress

## Baseline and scope

- Started 2026-10-20 at `da81a5a447c9df2cc20ee20cf2ba80bff9ecf261`.
- Authority: [design and plan](population-refactor-design-and-plan.md); user authorized implementation using the historical agent-team workflow.
- Preserve the preexisting README changes and untracked Attribute, Session/observe and public-surface documents. No commit or push is authorized.
- Do not edit AGENTS.md or packaged skills. Reuse existing execution, storage and fixture ownership; no compatibility aliases or expanded provider matrix.
- Independent development, review and acceptance. At most two formal review rounds; unresolved P2+ findings block completion.

## Owners, dependencies and evidence

| Work | Owner | Dependencies | Acceptance | Status |
| --- | --- | --- | --- | --- |
| P1/P2/P3 production implementation | core developer | approved design | V1-V5; focused typing/lint | Implemented; candidate frozen |
| Test migration and targeted regressions | test developer | production API coordination | V1-V6; existing fixtures | Stable focused gate passed: 375 tests |
| Current contracts, Help alignment and latest EN/ZH documentation | docs; coordinator owns runtime Help/exports | target signatures | V6 | Documentation complete; final runtime alignment pending |
| Integration and final candidate | coordinator | developers | source freeze; unrestricted check-agent | Complete; final unrestricted gate passed |
| Independent review | review agent | candidate | at most two rounds; no unresolved P2+ | Round 2 complete; R1 P2 closed, no new findings |
| Independent local acceptance | acceptance agent | candidate | targeted numerical/public witnesses; reuse existing tests | Complete; final 298-file freeze matched |

## Failures and review record

Historical 81-test review evidence is baseline evidence only.

- Runtime Help and exports migrated by coordinator: removed identities and Population.memberships, disclosed complete identity tuple keys, generic count and per/observe memberships. Live removed-target probes reject; own-file Ruff checks passed.
- Test developer's first focused run: 89 passed / 8 failed, raw output `/tmp/population-tests-first.log`. Six failures exercised unfinished per capture; two test expectations were corrected against the new contract. These are integration failures, not final candidate evidence.
- Documentation developer reports no affected packaged-skill workflow assumptions; neither skill requires an edit for this implementation.
- Documentation checks passed: `npm run verify:content` (381 required files), 111 Python snippets across 13 changed documents, latest EN/ZH snippet parity, and diff whitespace. These do not substitute for runtime API evidence.
- Integration caught consumer membership facts missing from fixed Frame source audit while numerical output/pending show remained correct. Core moved these facts to consumer-owned scope; targeted regression retained.
- Additional focused runs: primary group 97 passed / 3 stale recipe/order assertions corrected; surface/typing/optimization group 120 passed / 1 stale repair expectation corrected; Frame group 123 passed / 2 failures (fingerprint expectation and the audit defect above). Final reruns and broad gate remain pending; raw focused logs retained under `/tmp/population-tests-*.log`.
- Core focused typing passed for changed execution modules. Coordinator runtime Help/export files passed Ruff; full final static evidence remains pending.
- A consolidated focused run overlapped introduction of the coordinate matching helpers: 256 passed / 25 transient worker ImportErrors, raw `/tmp/population-tests-final-focused.log`. This moving-source run is invalid final acceptance evidence; test execution paused until core freeze.
- Core identified output/sample membership layer leakage during self-review; binding is being corrected so each layer uses only its own explicitly captured rules. Independent reviewer was asked to examine this seam.
- Core final focused typing/lint/import checks passed (25 execution source files); coordinator Help/export typing passed (8 files). Core reports no provider-specific physical execution or transport change.
- Frozen 298 production Python files at `/tmp/marivo-population-refactor-20261020-da81a5a/source-freeze.json`. Unrestricted `make check-agent` raw output is `/tmp/marivo-population-refactor-20261020-da81a5a/check-agent.log`. Test developer's stable consolidated run is `/tmp/population-tests-stable-focused.log`.
- Stable focused gate passed: 375 tests in 38.58s; changed-test lint/import checks (26 files) and typing passed. No test modifications pending at that freeze.

## Final acceptance

- First unrestricted `make check-agent`: formatting/lint, imports and typing passed; default tests 4768 passed / 2 failed (176.41s). Failures are stale semantic per Help wording and the semantic live registry's closed input-family set lacking the new memberships tuple. Raw log is retained at the first gate path above.
- Independent initial acceptance passed 4 temporary oracle probes and 7 existing targeted boundary cases. Complete key/Null/count/classification/anchored identity/membership/Frame selection/audit checks passed; 298-file hash freeze matched. Probe: `/tmp/marivo-population-independent-acceptance.py`.
- Independent review round 1: one P2, nested per same field/different membership paths loses layer ownership and raises KeyError. Baseline equivalent reproduced the same failure; this is an inherited defect in the touched binding seam. Evidence: `/tmp/population-review-r1-probe.py`, `/tmp/population-review-r1-current.log`, `/tmp/population-review-r1-base.log`. Core is repairing per-layer binding and tests retain an independent numerical regression. Round 2 required before completion.
- Runtime is required only if a provider-specific physical execution or key-transport route changes. No such route changed; Runtime and release acceptance are outside this local completion evidence.
- R1 repair preserves (axes, rules) for each nested per layer until binding concrete paths. Exact reviewer repro now returns 150/9; independent nonlinear route-order oracle returns 140/3 vs 50. Existing membership/query-results suite passed 64 tests; binder typing/lint/import checks passed. Tests retain the two route orderings in one parametrized regression.
- Semantic Help wording assertion migrated and the new memberships input family registered. Source changes after R1 are limited to the nested-layer binder and this registry fact. Final 298-file freeze is `/tmp/marivo-population-refactor-20261020-da81a5a/source-freeze-final.json`; final unrestricted gate raw output is `check-agent-final.log` in the same directory. Independent review round 2 and affected acceptance are running.
- Round 2 closed the original finding with reviewer-owned reruns (150/9, 140/3 vs 50): `/tmp/population-review-r2-fixed.log` and `/tmp/population-review-r2-numeric.log`. No new actionable findings; two formal rounds completed.
- Repair-focused tests passed: 82 membership/semantic Help/authoring cases and 43 semantic registry cases; raw logs `/tmp/population-r1-tests.log` and `/tmp/population-r1-registry-tests.log`. Changed tests lint/import checks passed.
- Independent final affected acceptance passed the complete-9-bucket membership oracle (1 case) and both nested-per route orderings (2 cases). All 298 production hashes match the final manifest. Initial unmodified-domain/count/anchor/Frame witnesses remain valid; no duplicate full acceptance matrix was run.
- Second unrestricted gate: static checks passed (303 typed files), all 4772 default tests passed (140.53s), but the final Sphinx stage rejected an indented continuation in coordinates docstring. Coordinator converted that docstring to ordinary Google-style sections; no executable statement changed. The original gate log remains `check-agent-final.log`; final source will be frozen again after this documentation repair.
- Targeted `make docs-api-agent` after the docstring repair passed (`docs-api-repair.log`); Ruff passed for that module. The reviewed manifest is retained as `source-freeze-reviewed.json`, and `source-freeze-final.json` now includes the docstring-only repair. Final unrestricted rerun is recorded in `check-agent-complete.log`.
- Final unrestricted `make check-agent` passed: formatting/Ruff, import contracts, typing (303 source files), all 4772 default tests (711 warnings, 139.70s), and Sphinx API documentation. Raw log: `/tmp/marivo-population-refactor-20261020-da81a5a/check-agent-complete.log`.
- Final site content check passed (381 required files); plan/progress references and examples plus `git diff --check` passed. Production hashes still match all 298 final manifest entries. No unresolved findings, protected-file edits, commits or pushes. Preexisting unrelated worktree documents are preserved.
