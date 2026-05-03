# Marivo Infrastructure Reference

Use this file when the task is about **data-plane setup or operational troubleshooting** rather than evidence interpretation.

Skip this file if the task is mainly about session evidence, proposition explanation, or semantic contract design.

This file owns operational setup, datasource registration, live browse, engines, execution auth,
jobs, and observability guidance. Canonical evidence behavior lives in `steps.md`.

## Shared Entry Points

These surfaces often bridge semantic discovery and operational work:

- health
- catalog search
- typed ref resolution
- graph exploration when relationship context matters

## Datasources

Use datasources to register external catalogs and inspect live metadata.

Typical datasource work:

- register or update an external catalog
- browse schemas, tables, and columns live
- preview bounded table rows
- choose `dataset.source` and `field.expression` values for semantic grounding

Key distinction:

- live catalog browse inspects the external system directly
- semantic model datasets and fields are the persisted physical grounding contract
- do not invent a sync/cache step before semantic authoring

## Engines, Mappings, And Semantic Grounding

### Engines

Use engines when:

- you are deciding where execution should happen
- you need to inspect or revise engine configuration including auth mode
- a session is failing because the data plane is not grounded correctly

### Mappings

Mappings govern **source-to-engine routing** and catalog projection. They replace the legacy `source_engine_binding.namespace` routing.

Use mappings when:

- you need to project authority locators (catalog/schema/table) from source to execution-side catalog names
- you need to inspect or revise which engine handles a given source's data
- a routing failure suggests the wrong execution backend was selected

Mapping structure:

- each mapping connects one `source_id` to one `engine_id`
- `catalog_mappings` list authority-to-execution catalog projections:
  - `authority_catalog`: source-side catalog name
  - `execution_catalog`: execution-side catalog name for routing/compile
  - `default_schema`: fallback schema when authority locator omits schema
- `priority`: routing priority (higher wins when multiple mappings match)
- `status`: `active`, `inactive`, or `deprecated`
- `readiness_status`: derived — `not_ready` or `ready`
- `failure_code`: stable blocker code when not ready (e.g., `mapping_inactive`, `mapping_incomplete`)

### Semantic Grounding

Dataset-native physical grounding belongs to the OSI semantic model:

- `dataset.custom_extensions[].data.datasource_id` selects the datasource
- `dataset.source` names the datasource-local relation FQN
- `field.expression` names physical columns or computed SQL expressions
- metrics, dimensions, predicates, and relationships reference datasets and fields

Use semantic grounding work when:

- the problem is semantic grounding rather than source-to-engine routing
- a dataset cannot resolve its datasource or relation
- a field cannot resolve to the expected source column or computed expression
- a downstream metric or relationship cannot consume data because its referenced dataset/field is missing

Key distinction:

- **mappings** = source-to-engine routing (which engine, which catalog)
- **dataset grounding** = semantic-to-relation grounding owned by OSI datasets and fields

## Execution Auth

Use execution auth when:

- the target engine requires authenticated access
- sessions must carry identity for engine routing
- auth failures block intent execution

Engine auth modes:

- `none`: no authentication (default for DuckDB)
- `username_only`: requires a username resolved from session or fixed config

Session execution identity:

- `session_user`: the authenticated user passed to engines
- `actor_ref`: the originating actor reference

See `http-contracts.md` for the full resolution rules and failure taxonomy.

## Query Routing

Treat routing as an operational capability, not an evidence surface.

Use it when:

- you need to understand how a dataset relation will map to an execution backend
- a source, engine, or mapping change may have altered routing behavior
- an execution failure suggests the wrong backend was selected

## Local Runtime

Marivo supports a local runtime CLI for development and testing:

- `marivo init-local`: bootstrap a local runtime with DuckDB
- `marivo status`: inspect local runtime status
- HTTP runtime status and health checks for local development

Use local runtime when:

- developing or testing without a remote engine
- validating semantic objects against DuckDB locally

## Jobs

Jobs are for asynchronous operational work such as background execution.

Use jobs when:

- a background task is still running
- an execution task failed and needs inspection
- you need operational progress, not analytical evidence

## Observability

Use observability and health surfaces when the task is service health, metrics, or operational readiness.

Operator surfaces help answer:

- is the service alive
- is a background process stuck
- is execution failing because of infrastructure or transport issues

They do **not** replace session state or proposition context.

## Read Next

- Read `semantic-layer.md` when the problem turns out to be missing semantic grounding rather than operational plumbing.
- Read `semantic-readiness.md` when an active semantic object is still unavailable for runtime consumption.
- Read `steps.md` when the real task is evidence interpretation rather than operations.
- Read `http-contracts.md` for execution auth resolution rules and failure taxonomy.
