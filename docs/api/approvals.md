# Approvals

The Approvals API provides a review workflow for high-risk recommendations generated during analysis. When `synthesize_findings` produces a recommendation with a `P0` priority or other high-risk signals, an approval request can be created to require human review before the recommendation is acted upon.

Approval requests can be created manually or triggered automatically via the auto-flag endpoint.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/approvals` | Create an approval request |
| `GET` | `/approvals` | List approval requests |
| `GET` | `/approvals/{request_id}` | Get an approval request |
| `POST` | `/approvals/{request_id}/approve` | Approve a request |
| `POST` | `/approvals/{request_id}/reject` | Reject a request |
| `POST` | `/sessions/{session_id}/approvals/auto-flag` | Auto-flag high-risk recommendations |

---

## Approval Request Lifecycle

```
pending → approved
       ↘ rejected
```

---

## Create Approval Request

```
POST /approvals
```

Creates a new approval request for a specific recommendation.

### Request Body

```json
{
  "session_id": "sess_...",
  "rec_id": "rec_..."
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `session_id` | string | yes | Session containing the recommendation |
| `rec_id` | string | yes | Recommendation ID to flag for review |

### Response

```json
{
  "request_id": "apr_a1b2c3d4e5f6",
  "session_id": "sess_...",
  "rec_id": "rec_...",
  "status": "pending",
  "reviewer": null,
  "reason": null,
  "submitted_at": "2024-01-15T10:00:00+00:00",
  "decided_at": null
}
```

---

## List Approval Requests

```
GET /approvals
```

### Query Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `session_id` | string | Filter by session |
| `status` | string | Filter by status: `pending`, `approved`, `rejected` |

### Response

Array of approval request objects.

---

## Get Approval Request

```
GET /approvals/{request_id}
```

---

## Approve Request

```
POST /approvals/{request_id}/approve
```

Approves a pending approval request. Records the reviewer identity and an optional reason.

### Request Body

```json
{
  "reviewer": "alice@example.com",
  "reason": "Reviewed query logic; impact analysis looks sound"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `reviewer` | string | yes | Identifier of the approver (email, username, etc.) |
| `reason` | string | no | Explanation for the approval decision |

### Response

```json
{
  "request_id": "apr_...",
  "session_id": "sess_...",
  "rec_id": "rec_...",
  "status": "approved",
  "reviewer": "alice@example.com",
  "reason": "Reviewed query logic; impact analysis looks sound",
  "submitted_at": "2024-01-15T10:00:00+00:00",
  "decided_at": "2024-01-15T11:30:00+00:00"
}
```

---

## Reject Request

```
POST /approvals/{request_id}/reject
```

Rejects a pending approval request.

### Request Body

```json
{
  "reviewer": "bob@example.com",
  "reason": "Sample size too small; need at least 7 days of data"
}
```

### Response

Approval request object with `status: "rejected"`.

---

## Auto-Flag Recommendations

```
POST /sessions/{session_id}/approvals/auto-flag
```

Scans all recommendations in a session and automatically creates approval requests for those meeting or exceeding the specified risk threshold. Recommendations that already have a pending or decided approval request are skipped.

### Request Body

```json
{
  "risk_threshold": "P0"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `risk_threshold` | string | no | Minimum risk level to flag: `"P0"` (default), `"P1"`, `"P2"` |

**Priority levels (highest to lowest risk):** `P0` → `P1` → `P2` → `P3`

Setting `risk_threshold: "P1"` flags recommendations with priority `P0` or `P1`.

### Response

Array of newly created approval request objects:

```json
[
  {
    "request_id": "apr_...",
    "session_id": "sess_...",
    "rec_id": "rec_...",
    "status": "pending",
    "submitted_at": "2024-01-15T11:00:00+00:00"
  }
]
```

Returns an empty array `[]` if no recommendations met the threshold or all already have approval requests.
