# TODOS

## OSI/AOI Static Cutover

### Phase A
- [ ] Add import-linter contract: `contracts/generated/` must not import `runtime/`, `adapters/`, `transports/`. (D1 from eng review)

### Phase B
- [ ] Extract metric extension enrichment into shared helper in `semantic_service.py` before replacing 5 old fields with `additive_dimensions`. DRY violation at lines 250-254, 882-886, and `import_osi_document`. (D-TODO2 from eng review: build now)

### Phase F
- [ ] Add import-linter contract: `runtime/` must not import from `transports/http/models/osi` or `transports/http/models/marivo_extensions` (only from `contracts/generated/`). Cannot be enforced until old paths are deleted. (D1 from eng review)

### Post-cutover
- [ ] Add 'User sees' line to each error scenario in `docs/superpowers/specs/2026-05-10-osi-aoi-cutover-error-registry.md` (E1-E10). Currently covers root cause and rescue but not user-visible impact.
