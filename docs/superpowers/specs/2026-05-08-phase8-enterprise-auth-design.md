# Phase 8: Enterprise Authentication and Authorization — Design Spec

**Date:** 2026-05-08
**Status:** Draft
**Parent spec:** `docs/superpowers/specs/2026-05-06-marivo-platform-architecture-design.md`
**Phase:** 8 of 8 (execution order 8)

---

## 1. Overview

Phase 8 replaces Phase 6's non-production `AlwaysAllowAuthZ` server stub with a trusted enterprise identity boundary and role-based authorization. It hardens both enterprise HTTP API and HTTP MCP traffic while leaving local stdio mode simple and single-user.

Local profile remains unchanged: `NoopAuthZ`, no external auth, no network service. Server profile gains authentication, canonical actor resolution, role mapping, Runtime-level `AuthZ` checks, and audit records that include both actor and authorization decision context.

---

## 2. Scope

| In scope | Out of scope |
|----------|-------------|
| Trusted HTTP/MCP edge that strips untrusted identity headers | Local stdio authentication |
| Bearer/OIDC token validation or pluggable token introspection | Building an identity provider |
| Canonical actor + roles propagated to Runtime context | Data warehouse impersonation semantics beyond existing datasource config |
| `OidcRbacAuthZ` replacing `AlwaysAllowAuthZ` in server profile | Fine-grained row/column security inside warehouses |
| Role policy for admin / semantic modeler / analyst / service actor | UI permission editor |
| Audit hardening for auth decisions and denied requests | Long-term audit retention policy |
| Tests for HTTP API and HTTP MCP auth behavior | Public internet deployment guidance |

---

## 3. Roles and Capabilities

| Role | Capabilities |
|------|--------------|
| `admin` | Manage datasources, runtime config, user/role mappings, and all semantic models |
| `semantic_modeler` | Create, update, approve, and publish semantic models within allowed domains |
| `analyst` | Create sessions, run intents, read allowed public models, read own private sessions |
| `service` | Server-to-server automation with explicitly scoped actions |

The first implementation uses static role mapping from server config or token claims. A database-backed role admin UI is deferred.

---

## 4. Sub-phase Sequence

| Sub-phase | Name | Deliverable | Gate |
|-----------|------|-------------|------|
| 8a | Identity contract | Add `ActorContext`, role names, auth error codes, and request context helpers | Unit tests cover anonymous, user, service actor, and role parsing |
| 8b | Trusted edge middleware | Pure-ASGI middleware validates token, strips inbound `X-Marivo-User` / role headers, injects canonical actor context | Unauthenticated enterprise HTTP API and HTTP MCP requests fail closed |
| 8c | `OidcRbacAuthZ` | Server profile wires `OidcRbacAuthZ`; `AlwaysAllowAuthZ` remains test/dev only | `MARIVO_ENV=production` cannot construct server runtime with `AlwaysAllowAuthZ` |
| 8d | Runtime authorization checks | Runtime checks AuthZ for semantic model, datasource, session, intent, and admin operations | Denied operations return standard domain errors through HTTP and MCP envelopes |
| 8e | Audit hardening | Audit log records actor, action, resource, decision, denial reason, and transport | Audit tests cover allowed and denied requests |
| 8f | Role policy tests | End-to-end tests for admin, semantic modeler, analyst, and service actor paths | HTTP API and HTTP MCP role matrices pass in CI |

---

## 5. Identity Boundary

Enterprise requests must not trust caller-supplied identity propagation headers. The edge middleware must:

1. Remove inbound `X-Marivo-User`, `X-Marivo-Roles`, and similar propagation headers before any Runtime code sees them.
2. Validate the configured credential source, initially Bearer token with OIDC/JWKS or a pluggable introspection function.
3. Resolve a canonical actor id and roles.
4. Set Runtime identity context from the canonical actor, not from raw headers.
5. Fail closed before business logic when identity cannot be established.

For deployments that still need a reverse proxy to perform token validation, Marivo must require an explicit trusted-proxy mode and a shared trust signal. Trusted-proxy mode is disabled by default.

---

## 6. Runtime Authorization

Authorization belongs in Runtime use cases, not in transports and not inside storage adapters. Transports translate protocol inputs into Runtime calls; Runtime derives `(actor, action, resource)` and calls `ports.authz.check(...)`.

Minimum action/resource coverage:

| Runtime area | Actions |
|--------------|---------|
| Session | `session:create`, `session:read`, `session:run_intent`, `session:terminate` |
| Semantic model | `model:create`, `model:update`, `model:approve`, `model:publish`, `model:read_private`, `model:read_public` |
| Datasource | `datasource:create`, `datasource:update`, `datasource:read`, `datasource:delete`, `datasource:preview` |
| Admin | `admin:read_runtime`, `admin:update_runtime`, `admin:manage_roles` |

Local `NoopAuthZ` returns allowed for all actions. Server `OidcRbacAuthZ` implements the role matrix above and must return structured denial codes.

---

## 7. Testing Strategy

- Middleware unit tests: header stripping, token missing, token invalid, token valid.
- HTTP API tests: every protected route rejects anonymous enterprise requests.
- HTTP MCP tests: tool calls reject anonymous requests and pass with valid actor roles.
- Runtime tests: direct Runtime calls enforce AuthZ even when bypassing transports.
- Audit tests: allowed and denied decisions both create audit entries with canonical actor and action/resource.
- Production guard tests: server runtime construction fails when `MARIVO_ENV=production` and `AlwaysAllowAuthZ` is selected.

---

## 8. Acceptance Criteria

1. Enterprise HTTP API and HTTP MCP fail closed without valid identity.
2. Caller-supplied `X-Marivo-User` cannot spoof actor identity.
3. `OidcRbacAuthZ` replaces `AlwaysAllowAuthZ` in server profile for production-like environments.
4. Admin, semantic modeler, analyst, and service roles have tested allow/deny matrices.
5. Audit log records actor, action, resource, allow/deny decision, and reason.
6. Local stdio behavior remains unchanged and does not require authentication.
