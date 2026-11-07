---
name: marivo-service
description: Use when publishing, operating, or integrating a Marivo project HTTP service, including a saved report or a refreshable script endpoint for another application.
---

# marivo-service

## Purpose and ownership

Help another application read governed definitions or captured results from the
intended project. The host owns its connection, credentials, user authorization,
report storage and service lifecycle. Use the host-selected Marivo environment
and project; do not assume a service is running or that a local listener is
reachable by a remote client.

Start at `marivo help analysis`, then use `marivo help analysis.results.service`
for the installed service contract and its focused routes. Live Help owns
signatures, HTTP details, script requirements and examples. Structured errors own
concrete repairs. Use `marivo-analysis` for analytical judgment and
`marivo-semantic` for reusable business definitions when the task needs them.

## Choose and deliver the result

Distinguish an exact saved result from a named endpoint whose current result can
be refreshed. A one-time result can use its existing fixed identity. Create a
script endpoint when the requested service needs an explicitly repeatable data
production step; select the business scope and result type for the consumer.
Do not infer source freshness or completeness from publication time.

Retain the service connection identity and the returned result kind and fixed
reference for a report that must reopen the same version. Preserve the Store
identity required by Frame references. A named endpoint identifies its current
result, which can change; adopt a newer fixed reference only when updating the
consumer's report. Surface missing references and failed reads rather than
silently recalculating or substituting another result.

## Operate within the host and project

Use the host's established credential handling and never place tokens in chat,
endpoint scripts or saved report definitions. Treat installed endpoint scripts
as trusted project code. Choose their inputs and time scope deliberately, and
use the project's governed definitions for business meaning.

Read saved data when that answers the task. Refresh only when the task calls for
new production or an initial result. A refresh has effects even though its HTTP
method is GET: avoid prefetch or automatic retry. If the outcome is uncertain,
read the current endpoint before deciding whether another refresh is needed.
Honor the service's complete-result and error contracts; identify any preview
as partial rather than presenting it as the complete saved result.
