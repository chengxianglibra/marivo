---
status: approved
created: 2026-05-11
---

# Remove Legacy Semantic Contract Tables

**Goal:** Completely remove unused legacy semantic contract tables and their dependencies from the codebase. This is a breaking change with no backwards compatibility.

**Context:** The OSI alignment v2 work (completed 2026-05-01) replaced the old contract-based semantic layer with a simpler dataset-native architecture. The old contract tables remain in the schema but are empty (0 rows) and barely used in the code.

**Approach:** Surgical removal - drop tables from schema, remove code dependencies, update tests. No migration needed since tables are empty.

---

## 1. Scope and Impact

### Tables to Remove

**Contract tables (all empty, 0 rows):**
- `semantic_metric_contracts` - 1 query usage in semantic_repository.py (has fallback)
- `semantic_dimension_contracts` - 0 runtime usage
- `semantic_process_objects` - 0 runtime usage
- `semantic_process_exported_dimension_refs` - 0 runtime usage
- `semantic_time_objects` - 0 runtime usage
- `semantic_predicate_contracts` - 1 query usage in semantic_ops.py
- `compiler_compatibility_profiles` - 0 runtime usage
- `semantic_enum_sets` - 0 runtime usage
- `semantic_enum_set_versions` - 0 runtime usage
- `semantic_enum_set_values` - 0 runtime usage
- `semantic_domain_catalog` - 0 runtime usage

**Total:** 11 tables to drop

### Tables to Keep

**OSI-aligned tables (actively used):**
- `semantic_models` - Model definitions
- `semantic_datasets` - Dataset (table) definitions
- `semantic_fields` - Field (column) definitions
- `semantic_metrics` - Metric definitions
- `semantic_relationships` - Relationship definitions
- `semantic_readiness_status` - Model readiness tracking

### Breaking Changes

1. **predicate_ref removed:** All APIs that accepted `predicate_ref` parameter will have it removed. Users must use `predicate` (raw SQL filter string) instead.
2. **Contract table queries fail:** Any code attempting to query contract tables will fail immediately with table-not-found errors.
3. **No migration path:** This is a destructive update. Old contract-based semantic models cannot be imported.

---

## 2. Schema Changes

### Files to Modify

**`marivo/adapters/schema.py`:**
- Remove all `CREATE TABLE` statements for the 11 contract tables
- Remove all associated indexes:
  - `idx_semantic_metric_contracts_*`
  - `idx_semantic_dimension_contracts_*`
  - `idx_semantic_predicate_contracts_*`
  - `idx_compiler_compatibility_profiles_*`
- Remove all triggers related to contract tables (e.g., `BEFORE UPDATE ON compiler_compatibility_profiles`)
- Bump metadata schema version to indicate breaking change

**`marivo/adapters/local/sqlite_metadata.py`:**
- Remove migration entries for contract table columns
- Specifically remove the `catalog_metadata_json` column additions for:
  - `semantic_metric_contracts`
  - `semantic_process_objects`
  - `semantic_dimension_contracts`
  - `semantic_predicate_contracts`
  - `compiler_compatibility_profiles`

### Migration Strategy

Since this is a breaking change with no backwards compatibility:

1. **Schema version bump:** Increment metadata schema version (e.g., v2 → v3)
2. **Automatic cleanup:** On startup, if old tables exist, drop them automatically
3. **No data migration:** Tables are empty, no data to preserve
4. **Fresh start:** Users with existing metadata.sqlite will get tables dropped on next startup

### Verification

After schema changes, running `sqlite3 .marivo/metadata.sqlite ".tables"` should show:

**Analysis engine tables:**
- sessions, session_events, propositions, assessments, findings, evidence_gaps, inference_records, action_proposals, plans, steps, step_metadata, artifacts

**New semantic tables:**
- semantic_models, semantic_datasets, semantic_fields, semantic_metrics, semantic_relationships, semantic_readiness_status

**System tables:**
- metadata_schema_marker, datasources, calendar, sqlite_sequence

**Should NOT show:**
- Any tables with "contract", "enum_set", "domain_catalog", "process_object", or "compatibility_profile" in the name

---

## 3. Code Removal and Replacement

### File: `marivo/runtime/evidence/semantic_repository.py`

**Lines 60-113 (`resolve_metric_ref` method):**

Current code queries `semantic_metric_contracts` table, then falls back to other resolution methods. Since the table is empty and unused:

**Change:**
- Remove the entire `semantic_metric_contracts` query block (lines 60-79)
- The method will fail fast with `SemanticRuntimeNotFoundError` instead of querying a non-existent table
- This is acceptable because the new architecture doesn't use metric contracts

**Rationale:** The contract query never returns results (table is empty), so removing it has no functional impact.

---

### File: `marivo/runtime/semantic_ops.py`

**Lines 1214-1303 (predicate-related functions):**

These functions resolve `predicate_ref` to SQL by querying `semantic_predicate_contracts`:

**Delete entirely:**
- `_resolve_predicate_ref_to_filter()` (lines 1214-1240) - queries semantic_predicate_contracts
- `_resolve_predicate_target_column()` (lines 1243-1262) - helper for predicate resolution
- `_predicate_expression_to_sql()` (lines 1265-1302) - converts predicate expression to SQL

**Update:**
- `build_scoped_query()` (line 1305) - remove `predicate_ref` handling, keep only `predicate` (raw SQL)
- `_resolved_scope_filter()` (line 1556) - remove `predicate_ref` handling

**Before (lines 1347-1356):**
```python
"scope_predicate_filter": (
    _resolve_predicate_ref_to_filter(
        runtime,
        request.scope.predicate_ref,
        metric_ref=metric_ref,
        table_name=request.table,
    )
    if request.scope.predicate_ref is not None
    else request.scope.predicate
),
```

**After:**
```python
"scope_predicate_filter": request.scope.predicate,
```

**Rationale:** Users will pass raw SQL filters via `predicate` instead of referencing stored predicates via `predicate_ref`.

---

### Contract/Model Files

**Search and remove:**

1. **Pydantic models with `predicate_ref` fields:**
   - Search: `grep -r "predicate_ref" --include="*.py" marivo/contracts/ marivo/transports/`
   - Remove `predicate_ref: str | None` fields from Scope models
   - Keep `predicate: str | None` (raw SQL filter)

2. **Contract definitions:**
   - Remove any imports or references to contract table models
   - Remove type definitions for MetricContract, DimensionContract, PredicateContract, etc.

3. **Generated contract models:**
   - Check `marivo/contracts/generated/` for contract-related models
   - Remove if present (these should have been removed in OSI alignment v2)

---

### Test Files

**`tests/shared_fixtures.py`:**

Remove contract table references:
- Remove PRAGMA queries for contract tables (lines checking table_info)
- Remove contract table names from fixture setup lists
- Remove any seeded contract data

**Test files to update or delete:**
- `tests/test_osi_models.py` - remove contract model tests
- `tests/test_osi_storage_roundtrip.py` - remove contract table assertions
- `tests/test_semantic_v2_service.py` - remove contract CRUD tests
- Any test files specifically for contracts, predicates, compatibility profiles

---

## 4. API Changes

### Breaking Changes

All endpoints that accept `predicate_ref` parameter will have it removed.

**Affected endpoints:**
1. `POST /sessions/{session_id}/observe` - remove `predicate_ref` from scope
2. `POST /sessions/{session_id}/compare` - remove `predicate_ref` from scope
3. `POST /sessions/{session_id}/detect` - remove `predicate_ref` from scope
4. `POST /sessions/{session_id}/diagnose` - remove `predicate_ref` from scope
5. Any other analysis endpoints with scope parameters

### Request/Response Model Changes

**Update `Scope` model:**

**Before:**
```python
class Scope(BaseModel):
    constraints: dict[str, Any] = {}
    predicate: str | None = None
    predicate_ref: str | None = None  # REMOVE THIS
```

**After:**
```python
class Scope(BaseModel):
    constraints: dict[str, Any] = {}
    predicate: str | None = None
```

**Validation:** Add validation to reject requests with `predicate_ref` field (return 400 Bad Request with clear error message).

### Migration Guide for Users

**Before (with predicate_ref):**
```json
{
  "metric": "metric.active_users",
  "time_scope": {...},
  "scope": {
    "predicate_ref": "predicate.active_users"
  }
}
```

**After (raw SQL):**
```json
{
  "metric": "metric.active_users",
  "time_scope": {...},
  "scope": {
    "predicate": "user_status = 'active' AND last_login > '2026-01-01'"
  }
}
```

### Documentation Updates

**API documentation:**
- Remove all references to `predicate_ref` parameter
- Update examples to show only `predicate` (SQL string) option
- Add migration note: "Breaking change in v3: predicate_ref removed - use predicate with raw SQL instead"

**OpenAPI schema:**
- Update schema definitions to remove `predicate_ref` field
- Update descriptions to clarify `predicate` accepts raw SQL WHERE clause

---

## 5. Testing Strategy

### Test Updates Required

**1. Schema tests (`test_openapi_schema_quality.py`):**
- Add assertion: contract tables must NOT exist in schema
- Verify metadata schema version is bumped correctly
- Test that fresh metadata.sqlite doesn't contain dropped tables

**2. Repository tests (`test_osi_storage_roundtrip.py`):**
- Remove any assertions about contract tables
- Remove tests that query contract tables
- Verify `semantic_repository.py` works without contract tables
- Test that metric resolution fails gracefully when contracts don't exist

**3. API tests (`test_semantic_v2_api.py`):**
- Remove `predicate_ref` from all test requests
- Add test: requests with `predicate_ref` are rejected with 400 error
- Update all analysis endpoint tests to use `predicate` instead
- Verify error messages are clear when `predicate_ref` is used

**4. Integration tests (`test_e2e_osi_aoi.py`):**
- Remove contract table setup from fixtures
- Verify end-to-end flows work with only dataset-native tables
- Test that analysis workflows complete without contract tables

**5. Fixture cleanup (`tests/shared_fixtures.py`):**
- Remove contract table references from `EXPECTED_TABLES` lists
- Remove seeded contract data from test fixtures
- Update table count assertions

### Verification Checklist

After all changes:
- [ ] `make test` passes with 0 failures
- [ ] `make typecheck` passes with 0 errors
- [ ] `make lint` passes
- [ ] No references to contract tables in test output
- [ ] API requests with `predicate_ref` return 400 Bad Request
- [ ] `sqlite3 .marivo/metadata.sqlite ".tables"` shows no contract tables
- [ ] All analysis endpoints work with `predicate` (raw SQL)

---

## 6. Implementation Order

Execute in this order to minimize breakage:

1. **Schema changes** - Drop tables from DDL, bump version
2. **Code removal** - Remove contract queries and predicate_ref handling
3. **API changes** - Remove predicate_ref from models and endpoints
4. **Test updates** - Update all tests to match new behavior
5. **Verification** - Run full test suite and manual smoke tests

Each step should be verified independently before proceeding to the next.

---

## 7. Rollback Plan

Since this is a breaking change with no backwards compatibility:

**If issues are found:**
1. Revert the commit(s)
2. Old schema and code will be restored
3. Empty contract tables will be recreated

**No data loss risk:** All contract tables are empty (0 rows), so dropping them loses no data.

**User impact:** Users will need to update API calls to remove `predicate_ref` and use `predicate` instead. This is a one-time migration.

---

## 8. Success Criteria

**Schema:**
- [ ] All 11 contract tables removed from schema DDL
- [ ] Metadata schema version bumped
- [ ] Fresh metadata.sqlite contains no contract tables

**Code:**
- [ ] No queries to contract tables in runtime code
- [ ] No `predicate_ref` handling in semantic_ops.py
- [ ] No contract model imports in active code

**API:**
- [ ] `predicate_ref` removed from all request models
- [ ] API returns 400 for requests with `predicate_ref`
- [ ] Documentation updated to show only `predicate` option

**Tests:**
- [ ] All tests pass (`make test`)
- [ ] Type checking passes (`make typecheck`)
- [ ] No contract table references in test output

**Verification:**
- [ ] Manual smoke test of analysis endpoints with `predicate` (raw SQL)
- [ ] Confirm error message when `predicate_ref` is used
- [ ] Verify `.marivo/metadata.sqlite` has no contract tables
