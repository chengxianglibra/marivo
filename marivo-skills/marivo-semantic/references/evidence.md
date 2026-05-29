# marivo-semantic evidence reference

Semantic authoring is evidence-driven. Agents may use field names as candidate
signals, but business meaning must come from comments, knowledge-base content,
source SQL, preview evidence, or user confirmation.

## Evidence categories

| Evidence | Agent responsibility |
| --- | --- |
| Project evidence | Load the project and inspect existing models, datasets, fields, metrics, relationships, dependencies, and descriptions. |
| Datasource evidence | Inspect configured datasources, redacted fields, env refs, backend type, and connection test results. |
| Table metadata evidence | Fetch column names, Ibis types, table comments, column comments, nullable flags, partition hints, and key hints when the backend exposes them. |
| Raw preview evidence | Run bounded previews for candidate tables and important columns before declaring datasets or time fields. |
| Knowledge evidence | Extract business definitions, guardrails, synonyms, example questions, source SQL, dialect, and source document refs. |
| Runtime evidence | Capture load results, materialization or compile results, semantic previews, dependency graphs, and parity results after authoring. |
| User confirmation evidence | Ask only when evidence conflicts or a business rule cannot be fetched. |

## Fetch automatically

Do not ask the user for information Marivo or the datasource can provide:

- whether a datasource exists
- datasource backend type and redacted config shape
- table names, column names, and column types
- sample values from bounded preview
- existing semantic refs and descriptions
- load errors, structured hints, and parity status

## Ask the user

Ask when available evidence cannot settle a business decision:

- amount unit is unclear
- status code meaning is undocumented
- multiple time axes are plausible
- source SQL and comments conflict
- refund, cancellation, test-data, or exclusion rules are ambiguous
- a no-source metric needs confirmation before marking `declared_status="python_native"`

## Evidence to semantic mapping

| Evidence | Semantic field |
| --- | --- |
| Short object label | `description=` |
| Full business definition | `ai_context["business_definition"]` |
| Misuse boundaries and exclusions | `ai_context["guardrails"]` |
| Natural-language aliases | `ai_context["synonyms"]` |
| Example user questions | `ai_context["examples"]` |
| Migration or owner context | `ai_context["owner_notes"]` |
| SQL oracle | `source_sql=`, `source_dialect=`, `source_document=` |
| Python-native business source | `declared_status="python_native"` |
| Missing provenance | leave `declared_status=None`; readiness reports it as unverified |
