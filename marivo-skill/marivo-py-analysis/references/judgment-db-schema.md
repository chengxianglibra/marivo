# judgment.db Schema

The Python analysis evidence store is project-local under
`<project_root>/.marivo/analysis/<session>/judgment.db`. It uses SQLite WAL and
stores artifacts, findings, propositions, assessments, blocking issues, and
followups in one transactional store.

```sql
PRAGMA user_version = 1;
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE artifacts (
  artifact_id              TEXT PRIMARY KEY,
  session_id               TEXT NOT NULL,
  step_type                TEXT NOT NULL,
  artifact_type            TEXT NOT NULL,
  artifact_schema_version  TEXT NOT NULL,
  subject_payload          TEXT NOT NULL,
  lineage_payload          TEXT NOT NULL,
  confidence_scope         TEXT,
  quality_summary          TEXT,
  evidence_status          TEXT NOT NULL,
  frame_path               TEXT,
  frame_sha                TEXT,
  triggered_by_followup    TEXT,
  committed_at_us          INTEGER NOT NULL
);
CREATE INDEX idx_artifacts_session_type ON artifacts(session_id, step_type);

CREATE TABLE findings (
  finding_id               TEXT PRIMARY KEY,
  session_id               TEXT NOT NULL,
  artifact_id              TEXT NOT NULL REFERENCES artifacts(artifact_id),
  finding_type             TEXT NOT NULL,
  canonical_item_key       TEXT NOT NULL,
  subject_axis             TEXT,
  subject_payload          TEXT NOT NULL,
  observed_window_start_us INTEGER,
  observed_window_end_us   INTEGER,
  quality_status           TEXT,
  payload                  TEXT NOT NULL,
  artifact_schema_version  TEXT,
  extractor_version        TEXT,
  committed_at_us          INTEGER NOT NULL,
  UNIQUE (artifact_id, finding_type, canonical_item_key)
);
CREATE INDEX idx_findings_session_type ON findings(session_id, finding_type);

CREATE TABLE propositions (
  proposition_id     TEXT PRIMARY KEY,
  session_id         TEXT NOT NULL,
  proposition_type   TEXT NOT NULL,
  origin_kind        TEXT NOT NULL,
  derivation_version TEXT NOT NULL,
  subject_key        TEXT NOT NULL,
  payload            TEXT NOT NULL,
  seed_finding_refs  TEXT NOT NULL,
  created_at_us      INTEGER NOT NULL,
  UNIQUE (session_id, proposition_id)
);
CREATE INDEX idx_propositions_session_type ON propositions(session_id, proposition_type);
CREATE INDEX idx_propositions_subject ON propositions(session_id, subject_key);

CREATE TABLE assessment_snapshots (
  snapshot_id      TEXT PRIMARY KEY,
  proposition_id   TEXT NOT NULL REFERENCES propositions(proposition_id),
  session_id       TEXT NOT NULL,
  supersedes_id    TEXT,
  status           TEXT NOT NULL,
  confidence       REAL,
  confidence_basis TEXT,
  payload          TEXT NOT NULL,
  created_at_us    INTEGER NOT NULL,
  is_latest        INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX idx_assess_latest ON assessment_snapshots(proposition_id, is_latest);

CREATE TABLE assessment_edges (
  snapshot_id  TEXT NOT NULL REFERENCES assessment_snapshots(snapshot_id),
  finding_id   TEXT NOT NULL REFERENCES findings(finding_id),
  role         TEXT NOT NULL,
  PRIMARY KEY (snapshot_id, finding_id, role)
);

CREATE TABLE blocking_issues (
  issue_id            TEXT PRIMARY KEY,
  session_id          TEXT NOT NULL,
  artifact_id         TEXT NOT NULL REFERENCES artifacts(artifact_id),
  kind                TEXT NOT NULL,
  severity            TEXT NOT NULL,
  payload             TEXT NOT NULL,
  resolved_by_step_id TEXT,
  created_at_us       INTEGER NOT NULL
);
CREATE INDEX idx_blocking_issues_session_kind ON blocking_issues(session_id, kind);
CREATE INDEX idx_blocking_issues_artifact ON blocking_issues(artifact_id);

CREATE TABLE followups (
  followup_id        TEXT PRIMARY KEY,
  session_id         TEXT NOT NULL,
  source_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
  category           TEXT NOT NULL,
  source_issue_id    TEXT REFERENCES blocking_issues(issue_id),
  operator           TEXT,
  payload            TEXT NOT NULL,
  executed_step_id   TEXT,
  created_at_us      INTEGER NOT NULL
);
CREATE INDEX idx_followups_session ON followups(session_id);
CREATE INDEX idx_followups_source ON followups(source_artifact_id);
```

## Surface Mapping

| Surface field | Store location |
| --- | --- |
| `artifact_id`, `subject`, `source_refs`, `lineage`, `confidence_scope`, `quality`, `evidence_status` | `artifacts` |
| `blocking_issues` | `blocking_issues` |
| `recommended_followups` | `followups` |
| `knowledge.facts(...)` | `propositions` plus latest `assessment_snapshots` and seed `findings` |
| `knowledge.open_items(...)` | pending or inconclusive propositions and assessments |

## Time Discipline

All persisted timestamps use integer microseconds since the Unix epoch in UTC.
Columns with that unit use the `_us` suffix. Python-facing datetime values are
timezone-aware UTC.
