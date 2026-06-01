# Semantic And Report Publishing Design

Date: 2026-06-01

Status: approved scope, pending written-spec review

## Problem

Marivo projects need a way to share formal semantic-layer assets and finished
analysis reports outside the local `.marivo/` workspace. The two sharing cases
have different lifecycles:

- A business expert publishes a formal semantic layer for a domain so others
  can align on the same business definitions.
- An analyst publishes an analysis report so others can open an HTML document
  directly and inspect the conclusion, evidence chain, and reproducibility
  attachments.

The current project-local `.marivo/` directory mixes source files, analysis
session state, SQLite indexes, frame parquet files, evidence records, temporary
files, and local active-session metadata. Treating the whole directory as an S3
filesystem would blur ownership boundaries and rely on object storage for local
filesystem semantics such as atomic replace and SQLite WAL behavior.

Publishing therefore needs package-level boundaries, not directory-level
synchronization.

## Goals

- Introduce two first-class publish artifacts:
  - `semantic_release`: a formal semantic-layer release.
  - `analysis_report`: an HTML-first analysis report package.
- Store published artifacts in S3 under a path that includes the exporting user.
- Keep semantic release publishing separate from semantic consumption. This
  design does not define install, import, reference, or remote-read behavior for
  semantic releases.
- Make analysis reports directly readable by opening `index.html`; no report
  consumption API is required.
- Include the semantic objects used by an analysis report inside the report
  package as a snapshot. The report package must not require an external
  semantic release to remain available.
- Keep Marivo Python APIs deterministic. The library validates, stages,
  manifests, hashes, scans, and uploads packages; it does not ask an agent to
  generate prose, HTML, charts, or replay scripts.
- Put agent-generated report work in the Marivo skill workflow. The skill may
  create `index.html`, `replay.py`, grounding metadata, and explanatory content,
  then call deterministic library publishing helpers.
- Configure the default S3 bucket and prefix without storing access keys in the
  project. Credentials come from the standard AWS credential chain.

## Non-Goals

- No semantic release consumption API.
- No remote `.marivo` workspace mounted on S3.
- No Marivo library API that generates narrative report content with an agent.
- No report consumption API. Readers open the generated HTML document.
- No credentials, secret values, or `~/.marivo/secrets.toml` in published
  artifacts.
- No default publication of row-level frame data unless the caller opts in with
  an explicit data policy.
- No organization or project segment in the S3 path in this phase.

## Published S3 Layout

The default S3 layout is user-scoped:

```text
s3://<bucket>/<prefix>/
  semantic-releases/<domain>/<version>/
  analysis-reports/<report_id>/<export_id>/
  blobs/<content_hash>/
```

The default prefix expands to:

```text
marivo/users/<username>
```

The publishing code must validate that the username embedded in the target path
matches `manifest.exported_by`. The username is intentionally stored in both
places:

- The path makes ownership visible to humans browsing S3.
- The manifest preserves ownership if the package is copied elsewhere.

`blobs/<content_hash>/` stores large or reusable files such as frame snapshots,
rendered chart assets, screenshots, and large evidence payloads. The manifest
references blob objects by URI and hash. Small package-local files may remain
inline in the release/report directory.

The final `manifest.json` is the publish-complete marker. Readers and tools must
ignore a target without `manifest.json`.

## Semantic Release Package

A semantic release is a formal release of semantic-layer source and provenance.
It is a governance artifact, not an analysis session export.

Example layout:

```text
semantic-releases/sales/1.2.0/
  manifest.json
  semantic/
    sales/
      _model.py
      datasets.py
      fields.py
      metrics.py
      relationships.py
      _evidence/
  readiness.json
  richness.json
  release-notes.md
```

Required content:

- Semantic Python files from `.marivo/semantic/<model>/`.
- Semantic evidence ledger if present.
- Readiness report.
- Manifest.
- Content hash covering the published semantic files and reports.

Optional content:

- Richness report.
- Preview summaries.
- Example questions or usage notes.
- Release notes.

Excluded content:

- Datasource credentials.
- User-global secret cache files.
- Analysis sessions.
- SQLite files, WAL files, locks, caches, bytecode, and temporary files.
- Data samples by default.

### Semantic Release Manifest

```json
{
  "kind": "semantic_release",
  "manifest_version": 1,
  "exported_by": "alice",
  "exported_at": "2026-06-01T10:00:00Z",
  "domain": "sales",
  "version": "1.2.0",
  "semantic_models": ["sales"],
  "content_hash": "sha256:...",
  "marivo_version": "...",
  "readiness": {
    "status": "ready",
    "generated_at": "2026-06-01T09:59:00Z",
    "blocker_count": 0,
    "warning_count": 2
  },
  "richness": {
    "generated": true,
    "gap_count": 5
  },
  "files": {
    "semantic_root": "semantic/",
    "readiness_report": "readiness.json",
    "richness_report": "richness.json"
  }
}
```

By default, semantic release publishing fails unless readiness is `ready`. A
caller may force publication of an unready release, but the manifest must record
the non-ready status and the published package must not pretend to be approved.

## Analysis Report Package

An analysis report package is an HTML-first artifact. Its main user experience
is opening `index.html`. The remaining files support audit and reproducibility.

Example layout:

```text
analysis-reports/sales_may_review/exp_20260601_103000/
  manifest.json
  index.html
  replay.py
  flow.json
  grounding.json
  evidence/
  semantic-snapshot/
    sales/
      _model.py
      datasets.py
      fields.py
      metrics.py
      relationships.py
      _evidence/
  assets/
  frames/
```

Required content:

- `index.html`.
- Manifest.
- `replay.py`.
- Flow DAG or step list.
- Evidence chain sufficient for audit, or an explicit partial-evidence status.
- Semantic snapshot for the semantic objects used by the report.
- Grounding metadata for agent-generated report claims.

Optional content:

- Frame parquet snapshots.
- Rendered charts, images, and HTML assets.
- Extracted tables used in the report.
- Raw evidence payloads if policy allows.

`replay.py` is required, but it is an attachment, not the read path. The manifest
records whether it was only statically checked or actually executed.

### Analysis Report Manifest

```json
{
  "kind": "analysis_report",
  "manifest_version": 1,
  "exported_by": "alice",
  "exported_at": "2026-06-01T10:30:00Z",
  "report_id": "sales_may_review",
  "export_id": "exp_20260601_103000",
  "entrypoint": "index.html",
  "content_hash": "sha256:...",
  "marivo_version": "...",
  "semantic_snapshot": {
    "included": true,
    "root": "semantic-snapshot/",
    "hash": "sha256:..."
  },
  "analysis": {
    "flow": "flow.json",
    "evidence_root": "evidence/",
    "artifact_count": 12
  },
  "generation": {
    "html": {
      "generated_by": "skill",
      "source_artifacts": ["art_1", "art_2"],
      "grounding": "grounding.json"
    },
    "replay_script": {
      "path": "replay.py",
      "generated_by": "skill",
      "validation": "static_only"
    }
  },
  "data_policy": {
    "frame_snapshots": "omitted",
    "row_level_data": "omitted"
  },
  "evidence_status": "complete"
}
```

### Agent-Generated Report Boundary

The analysis skill, not the Marivo library, owns report generation. A typical
skill workflow is:

```text
collect session context
  -> generate index.html
  -> generate replay.py
  -> generate grounding.json
  -> call Marivo package validation
  -> call Marivo S3 publishing
```

`grounding.json` classifies report claims:

- `evidence_backed`: supported by proposition, assessment, finding, or artifact
  evidence.
- `derived_from_flow`: supported by flow structure, parameters, or frame
  metadata.
- `commentary`: agent interpretation, summary, or recommendation.

The library does not judge prose quality. It validates that the HTML and
grounding files exist, that referenced artifact/evidence IDs resolve in the
package, and that unsupported main claims are not marked as evidence-backed.

The HTML must make evidence completeness visible to readers. If evidence is
partial or unavailable, both `manifest.json` and `index.html` must say so.

## Library Surface

Publishing APIs are deterministic and file/package oriented.

Semantic release publishing may stage from the current project because semantic
files and readiness are library-owned state:

```python
import marivo.semantic as ms

ms.publish.semantic_release(
    model="sales",
    domain="sales",
    version="1.2.0",
    exported_by="alice",
    target=None,
)
```

`target=None` resolves from publish configuration. The API does not define how
another project consumes the release.

Analysis report publishing accepts an already generated package directory:

```python
import marivo.analysis as mv

mv.publish.report_package(
    package_dir=".marivo/publish/staging/reports/sales_may_review",
    exported_by="alice",
    target=None,
)
```

The package directory must already contain `index.html`, `flow.json`,
`grounding.json`, `replay.py`, the semantic snapshot, and any optional generated
files. The library does not generate these files with an agent.

The shared lower-level pieces are:

- Package staging builders.
- Manifest writer.
- Content hasher.
- Secret scanner.
- Username/path validator.
- S3 uploader.
- Publish-complete marker writer.

## S3 Configuration And Credentials

Non-secret publish configuration lives in project-local state:

```toml
# .marivo/publish.toml
[storage.s3]
bucket = "my-marivo-share"
prefix = "marivo/users/{username}"
region = "ap-southeast-1"
profile = "analytics-publisher"
endpoint_url = ""
```

Environment variables override the file:

```bash
MARIVO_PUBLISH_S3_BUCKET=my-marivo-share
MARIVO_PUBLISH_S3_PREFIX='marivo/users/{username}'
MARIVO_PUBLISH_S3_REGION=ap-southeast-1
MARIVO_PUBLISH_S3_PROFILE=analytics-publisher
MARIVO_PUBLISH_S3_ENDPOINT_URL=https://s3.example.com
```

Resolution order:

1. Explicit API `target`.
2. `MARIVO_PUBLISH_S3_*` environment variables.
3. `.marivo/publish.toml`.

Raw access keys are not stored in `.marivo/publish.toml` or manifests. S3 access
uses the standard AWS credential chain:

- `AWS_ACCESS_KEY_ID`.
- `AWS_SECRET_ACCESS_KEY`.
- `AWS_SESSION_TOKEN`.
- `AWS_PROFILE`.
- Shared AWS config and credentials files.
- Web identity, instance role, container role, or other SDK-supported providers.

`profile` is allowed in `.marivo/publish.toml` because it is an identity selector,
not a secret. The library must not persist raw access keys in project state or
published packages.

When a target is resolved from config, the path expands as:

```text
s3://<bucket>/<prefix>/semantic-releases/<domain>/<version>/
s3://<bucket>/<prefix>/analysis-reports/<report_id>/<export_id>/
```

`{username}` must resolve from `exported_by`, and the resulting path must include
that username segment.

## Publish Pipeline

Both package types use the same publish lifecycle:

```text
collect or receive package inputs
  -> stage locally
  -> validate
  -> upload content
  -> verify remote object size/hash
  -> write final manifest.json
```

Staging uses project-local temporary state:

```text
.marivo/publish/staging/<kind>/<id>/
```

The upload writes content files first and `manifest.json` last. If upload fails
before the final manifest is written, the remote target is incomplete and must
not be treated as published.

Existing targets are immutable by default. Publishing to an existing target
fails unless the caller explicitly opts into overwrite behavior.

## Validation Rules

Shared validation:

- `exported_by` is non-empty.
- Target path username matches `exported_by`.
- Content hash can be recomputed.
- Manifest kind and version match the package type.
- No credential files or obvious secret-bearing files are included.
- No bytecode, lock files, SQLite WAL files, active-session markers, or temporary
  files are included.

Semantic release validation:

- Semantic files load.
- Readiness report exists.
- Readiness is ready unless forced.
- Manifest model/domain/version match the staged semantic content.

Analysis report validation:

- `index.html` exists and matches `manifest.entrypoint`.
- HTML can be read without importing Marivo or contacting S3 APIs.
- Semantic snapshot exists and hash matches the manifest.
- `replay.py` exists.
- `flow.json` exists.
- `grounding.json` exists.
- Grounding references resolve to package-local artifacts or evidence records.
- Evidence status is either complete or explicitly marked partial/unavailable.
- Data policy matches the actual included files. For example, `row_level_data =
  "omitted"` cannot publish frame parquet snapshots containing row-level data.
- Manifest validation records whether the replay script was statically checked
  or executed.

## Failure Behavior

- Validation failure: do not upload; keep the staging directory and write a
  validation report.
- Upload failure: do not write final `manifest.json`; allow retry.
- Existing target: fail by default.
- Username mismatch: fail.
- Semantic readiness blockers: fail by default; forced publication records the
  non-ready status.
- Partial report evidence: allow publication only when both HTML and manifest
  make the limitation visible.
- Ungrounded main report claims: fail validation or require the skill to
  downgrade them to commentary before publishing.

## Testing

Semantic release tests:

- Staging includes semantic files, manifest, and readiness report.
- Content hash is stable and recomputable.
- Secrets and datasource credentials are excluded.
- Username/path mismatch is rejected.
- Existing target rejects overwrite by default.
- Readiness blockers reject publication by default.
- Forced unready publication records non-ready status in the manifest.

Analysis report tests:

- `index.html` is the manifest entrypoint.
- HTML can be opened as a standalone local file.
- `replay.py` is required.
- Semantic snapshot is included and hash-checked.
- Flow and grounding files are required.
- Grounding IDs are validated against package-local evidence/artifacts.
- Partial evidence is visible in manifest and HTML.
- Data policy controls whether frame snapshots are included.
- `replay.py` validation mode is reflected in the manifest.
- Unsupported evidence-backed claims are rejected.

S3 publish tests:

- Config resolves from explicit target, environment, then `.marivo/publish.toml`.
- `{username}` expands from `exported_by`.
- Raw AWS access keys are not read from or written to project config.
- Upload writes `manifest.json` last.
- Interrupted uploads without final manifest are not considered published.
- Existing target behavior is immutable by default.

## Phase Decisions

- Semantic release versions must be non-empty and path-safe. Semver is
  recommended but not required in this phase.
- Analysis report packages require `replay.py`, even though normal readers only
  open `index.html`.
- HTML assets may live package-local under `assets/`. Large reusable objects use
  the user-level `blobs/<content_hash>/` namespace and are referenced from the
  manifest.
