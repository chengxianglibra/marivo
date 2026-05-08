# AOI Schema Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the AOI v0.1 design document so the planned `aoi-spec/` schema layout uses one user-friendly canonical schema file instead of many small schema files.

**Architecture:** This is a documentation-only change to the AOI design spec. Keep `spec.md` as the narrative source and make `schema/aoi.schema.json` the single canonical JSON Schema source with `$defs` sections for primitives, requests, artifacts, and capability declaration. Preserve the existing explanations for fields deliberately removed from AOI v0.1; only move them out of any split-schema or extension framing.

**Tech Stack:** Markdown documentation, JSON Schema draft-style terminology, repository checks with `rg`, `git diff --check`, and `git diff`.

---

## File Structure

- Modify: `docs/superpowers/specs/2026-05-07-aoi-v0.1-design.md`
  - Responsible for AOI v0.1 design decisions, repository layout, file-size/readability guidance, and Marivo migration rationale.
- Do not modify: `aoi-spec/`
  - The real spec directory is not being materialized in this task; only the design document layout plan changes.
- Do not modify: runtime or test files.
  - Existing unrelated local edits must remain untouched.

---

### Task 1: Capture Current Split-Schema References

**Files:**
- Inspect: `docs/superpowers/specs/2026-05-07-aoi-v0.1-design.md`

- [ ] **Step 1: Find current schema layout references**

Run:

```bash
rg -n "schemas/|foundations/|intents/|capability/|aoi-core\\.schema\\.json|File-size discipline|schema\\.json|\\$ref" docs/superpowers/specs/2026-05-07-aoi-v0.1-design.md
```

Expected: output includes Section 7.1 directory structure, Section 7.2 file-size discipline, and any `$ref` guidance that assumes many schema files.

- [ ] **Step 2: Review the relevant layout section**

Run:

```bash
sed -n '540,620p' docs/superpowers/specs/2026-05-07-aoi-v0.1-design.md
```

Expected: output shows the current `aoi-spec/` tree with `schemas/foundations/`, `schemas/intents/`, `schemas/capability/`, and `aoi-core.schema.json`.

- [ ] **Step 3: Confirm removed-field rationale sections still exist**

Run:

```bash
rg -n "removed|Do not include|outside AOI|not AOI|Migration|Mapping to Current Marivo Schemas|Compare type / calendar / additivity" docs/superpowers/specs/2026-05-07-aoi-v0.1-design.md
```

Expected: output includes Section 5 row-level exclusions and Section 8 mapping tables. These explanations must remain in the document after the layout edit.

---

### Task 2: Replace Directory Layout With Canonical Single-Schema Layout

**Files:**
- Modify: `docs/superpowers/specs/2026-05-07-aoi-v0.1-design.md`

- [ ] **Step 1: Edit Section 7.1 directory structure**

Replace the `aoi-spec/` tree in Section 7.1 with this exact structure:

````markdown
```
aoi-spec/
  README.md                         # repo face: what AOI is, status, links
  VERSION                           # contents: 0.1.0
  CHANGELOG.md
  spec.md                           # authoritative narrative spec

  schema/
    aoi.schema.json                 # canonical JSON Schema, all $defs inline

  examples/
    observe/
      scalar-success.json
      time-series-success.json
      failed.json
    compare/
      scalar-delta.json
      comparability-failed.json
    decompose/
      top-contributors-success.json
    capability/
      marivo-capabilities.json
```
````

Expected: no `schemas/foundations/`, `schemas/intents/`, `schemas/capability/`, or `aoi-core.schema.json` remain in Section 7.1.

- [ ] **Step 2: Add `$defs` organization text after the tree**

Immediately after the tree, add:

```markdown
`schema/aoi.schema.json` is the single validation entry point. It uses top-level `$defs` sections instead of cross-file schema fragments:

| `$defs` section | Contains |
|-----------------|----------|
| `primitives` | `Predicate`, `TimeScope`, `TimeGranularity`, `CompareType`, `ArtifactRef`, `ArtifactItemRef`, `StepRef`, `AnalysisFailure`, `HypothesisContract` |
| `requests` | `ObserveRequest`, `CompareRequest`, `DecomposeRequest`, `CorrelateRequest`, `DetectRequest`, `TestRequest`, `ForecastRequest` |
| `artifacts` | All eleven artifact envelope/result shapes |
| `capability` | `CapabilityDeclaration` |

This keeps the public artifact easy to copy, validate, and review while preserving internal navigation through `$defs` anchors.
```

Expected: the document explains why single-file schema is preferred for AOI v0.1 usability.

---

### Task 3: Replace File-Size Discipline With Readability Discipline

**Files:**
- Modify: `docs/superpowers/specs/2026-05-07-aoi-v0.1-design.md`

- [ ] **Step 1: Replace Section 7.2**

Replace the existing Section 7.2 text with:

```markdown
### 7.2 Schema readability discipline

- `schema/aoi.schema.json` is the canonical schema. It should favor reader ergonomics over file-count minimization.
- Use `$defs` anchors and stable ordering: primitives first, requests second, artifacts third, capability declaration last.
- Avoid cross-file `$ref` for v0.1. Users should not need to clone a folder tree or chase relative references to understand or validate AOI.
- Keep examples outside the schema file. Examples remain separate JSON files under `examples/`.
- If future maintainers want split files for authoring, they may generate the single public schema during release, but the published v0.1 source of truth remains `schema/aoi.schema.json`.
```

Expected: the old per-file line limits are gone.

- [ ] **Step 2: Update Section 7.3 outline title if needed**

Run:

```bash
sed -n '620,650p' docs/superpowers/specs/2026-05-07-aoi-v0.1-design.md
```

If Section 7.3 still follows the replaced Section 7.2 naturally, make no edit. If it references split schema validation, change that phrase to “single-schema validation.”

Expected: Section 7 flows as `7.1 Directory structure`, `7.2 Schema readability discipline`, `7.3 spec.md outline`.

---

### Task 4: Update References Outside Section 7

**Files:**
- Modify: `docs/superpowers/specs/2026-05-07-aoi-v0.1-design.md`

- [ ] **Step 1: Search for stale split-schema references**

Run:

```bash
rg -n "schemas/foundations|schemas/intents|schemas/capability|aoi-core\\.schema\\.json|foundations/\\*\\.schema|intents/\\*\\.schema|cross-file|referenced file exists" docs/superpowers/specs/2026-05-07-aoi-v0.1-design.md
```

Expected: no output after the edits. If there is output, update those references to `schema/aoi.schema.json` and `$defs`.

- [ ] **Step 2: Preserve removed-field explanations**

Run:

```bash
rg -n "query_hash|executed_at|direction|presence|unit|bucket_pairing|data_coverage_summary|additivity_constraints|flag_level|numeric_sample_summary|rate_sample_summary|derived intents" docs/superpowers/specs/2026-05-07-aoi-v0.1-design.md
```

Expected: output still includes the Section 5 and Section 8 explanations for fields removed from AOI v0.1. Do not delete these sections while changing schema layout.

- [ ] **Step 3: Ensure terminology matches the current compare-mode design**

Run:

```bash
rg -n "calendar_comparison|CalendarComparison|calendar-comparison|supported_calendar_comparisons" docs/superpowers/specs/2026-05-07-aoi-v0.1-design.md
```

Expected: no output. If there is output, replace stale terms with `compare_type`, `CompareType`, `compare-type`, or `supported_compare_types` as appropriate.

---

### Task 5: Verify The Documentation Edit

**Files:**
- Verify: `docs/superpowers/specs/2026-05-07-aoi-v0.1-design.md`

- [ ] **Step 1: Run Markdown whitespace check**

Run:

```bash
git diff --check -- docs/superpowers/specs/2026-05-07-aoi-v0.1-design.md
```

Expected: no output and exit code 0.

- [ ] **Step 2: Review the documentation diff**

Run:

```bash
git diff -- docs/superpowers/specs/2026-05-07-aoi-v0.1-design.md
```

Expected: diff only changes AOI design documentation. It should show the single-schema layout, `$defs` organization, removed split-schema file-size limits, and preserved removed-field rationale.

- [ ] **Step 3: Check worktree scope**

Run:

```bash
git status --short
```

Expected: `docs/superpowers/specs/2026-05-07-aoi-v0.1-design.md` is modified. Other pre-existing unrelated files may still be modified, but do not stage them for this task.

---

### Task 6: Commit The Documentation Change

**Files:**
- Stage: `docs/superpowers/specs/2026-05-07-aoi-v0.1-design.md`

- [ ] **Step 1: Stage only the AOI design doc**

Run:

```bash
git add docs/superpowers/specs/2026-05-07-aoi-v0.1-design.md
```

Expected: only the AOI design doc is staged for this task.

- [ ] **Step 2: Confirm staged scope**

Run:

```bash
git status --short --untracked-files=all
git diff --cached --name-status
```

Expected: `git diff --cached --name-status` shows only:

```text
M	docs/superpowers/specs/2026-05-07-aoi-v0.1-design.md
```

- [ ] **Step 3: Commit with required attribution**

Run:

```bash
git commit -m "$(cat <<'EOF'
docs: simplify AOI schema layout

Co-Authored-By: Codex:GPT-5 [Edit] [Bash] [Review]
EOF
)"
```

Expected: commit succeeds and includes the required Marivo AI co-author attribution.

---

## Self-Review

- Spec coverage: The plan updates the AOI design doc to prefer `schema/aoi.schema.json`, documents `$defs` organization, removes split-schema file-size guidance, and preserves removed-field rationale in Sections 5 and 8.
- Placeholder scan: No placeholder markers, deferred implementation notes, or unspecified test steps are present.
- Type consistency: The plan uses `CompareType`, `compare_type`, `compare-type.schema.json`, and `supported_compare_types`, matching the current AOI compare-mode naming.
