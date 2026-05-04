# specs/service/

Service runtime and operator design notes. These documents define system design, frozen contracts, and operator procedures — they are not HTTP API documentation.

## Topic Clusters

### [agent-runtime/](agent-runtime/)

Agent runtime target resolution: how `marivo-mcp` discovers and connects to a local or remote Marivo instance.

| File | Topic | Start here if you need to... |
|------|-------|------|
| [overview.md](agent-runtime/overview.md) | Full design: user model, target resolution, workspace layout, CLI | Understand the overall model |
| [scope-note.zh.md](agent-runtime/scope-note.zh.md) | v1 user-facing scope: "local auto-managed" vs "remote explicit" | Know what users should/shouldn't see |
| [config-contract.zh.md](agent-runtime/config-contract.zh.md) | `MARIVO_MODE`, `MARIVO_BASE_URL`, conflict matrix | Resolve config questions |
| [workspace-root.zh.md](agent-runtime/workspace-root.zh.md) | Workspace root resolution priority chain | Debug workspace discovery |
| [workspace-layout.zh.md](agent-runtime/workspace-layout.zh.md) | `.marivo/` directory structure, file lifecycle | Understand what lives in `.marivo/` |
| [manifest-schema.zh.md](agent-runtime/manifest-schema.zh.md) | `runtime.json` schema, producer/consumer obligations | Parse or validate `runtime.json` |
| [lifecycle.zh.md](agent-runtime/lifecycle.zh.md) | Local runtime state machine, restart, health checks | Implement or debug runtime startup |
| [cli-contract.zh.md](agent-runtime/cli-contract.zh.md) | `marivo serve-local`, `init-local`, `doctor`, `runtime status/stop` | Implement or call CLI commands |
| [error-taxonomy.zh.md](agent-runtime/error-taxonomy.zh.md) | `TargetResolutionError` codes and detail fields | Handle or display target-resolution errors |
| [http-mcp-boundary.zh.md](agent-runtime/http-mcp-boundary.zh.md) | Streamable HTTP MCP: workspace guard, local-Hosting restrictions | Deploy or debug HTTP MCP |
| [bootstrap-config.zh.md](agent-runtime/bootstrap-config.zh.md) | Minimal `marivo.yaml` content and rationale | Understand what `init-local` writes |
| [troubleshooting.zh.md](agent-runtime/troubleshooting.zh.md) | User/operator troubleshooting paths | Debug connection or startup failures |

### [data-plane/](data-plane/)

Source, execution engine, mapping, and execution auth contracts — the data-plane boundary.

| File | Topic |
|------|-------|
| [source-engine-mapping-contract.md](data-plane/source-engine-mapping-contract.md) | Three-object data-plane model: source (metadata authority), engine (execution authority), mapping (projection contract) |
| [source-engine-mapping-golden-cases.zh.md](data-plane/source-engine-mapping-golden-cases.zh.md) | Minimal regression test cases for source/engine/mapping routing |
| [execution-auth-contract.md](data-plane/execution-auth-contract.md) | Execution engine authentication: `session_user` injection for Trino, `auth.mode` for DuckDB |

### [mysql-metadata/](mysql-metadata/)

MySQL metadata backend: fresh-init v1, operator procedures, and design decisions.

| File | Topic |
|------|-------|
| [fresh-init-v1.zh.md](mysql-metadata/fresh-init-v1.zh.md) | MySQL metadata v1 contract: fresh-init only, fail-closed bootstrap, dialect layer, DDL, testing matrix |
| [operator-runbook.zh.md](mysql-metadata/operator-runbook.zh.md) | MySQL operator procedures: database creation, configuration, startup verification, common errors |
| [decision-record.zh.md](mysql-metadata/decision-record.zh.md) | v1 non-goals and future extension points (migration, PostgreSQL, online migration) |

### Standalone Documents

| File | Topic |
|------|-------|
| [causal-inference.md](causal-inference.md) | Causal inference guide: claim promotion L0→L2, observation windows, checker recipes |
| [marivo-skill.md](marivo-skill.md) | Agent skill design: when to use Marivo, surface routing, default investigation loop |

## Quick Lookup

| Question | Go to |
|----------|-------|
| How does `marivo-mcp` decide between local and remote? | [agent-runtime/config-contract.zh.md](agent-runtime/config-contract.zh.md) |
| What does `runtime.json` contain? | [agent-runtime/manifest-schema.zh.md](agent-runtime/manifest-schema.zh.md) |
| How is workspace root resolved? | [agent-runtime/workspace-root.zh.md](agent-runtime/workspace-root.zh.md) |
| What CLI commands manage local runtime? | [agent-runtime/cli-contract.zh.md](agent-runtime/cli-contract.zh.md) |
| What error codes does target resolution use? | [agent-runtime/error-taxonomy.zh.md](agent-runtime/error-taxonomy.zh.md) |
| Connection/startup troubleshooting? | [agent-runtime/troubleshooting.zh.md](agent-runtime/troubleshooting.zh.md) |
| How do source, engine, and mapping relate? | [data-plane/source-engine-mapping-contract.md](data-plane/source-engine-mapping-contract.md) |
| How does Trino `session_user` work? | [data-plane/execution-auth-contract.md](data-plane/execution-auth-contract.md) |
| How to set up MySQL metadata? | [mysql-metadata/operator-runbook.zh.md](mysql-metadata/operator-runbook.zh.md) |
| How do claims get promoted from L0 to L2? | [causal-inference.md](causal-inference.md) |
| When should an agent use Marivo? | [marivo-skill.md](marivo-skill.md) |
