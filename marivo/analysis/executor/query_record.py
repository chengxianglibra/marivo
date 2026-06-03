"""Query execution record for the executed-SQL audit trail."""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import asdict, dataclass
from typing import Any


def gen_query_ref() -> str:
    return f"query_{secrets.token_hex(4)}"


_DIGEST_HEX_CHARS = 16  # 8 bytes of SHA-256, enough for dedup/correlation


def compute_sql_digest(normalized_sql: str) -> str:
    return hashlib.sha256(normalized_sql.encode("utf-8")).hexdigest()[:_DIGEST_HEX_CHARS]


_SESSION_COMMENT_RE = re.compile(r"^/\* from=marivo,session=[^*]* \*/\n?")


def _strip_session_comment(sql: str) -> str:
    return _SESSION_COMMENT_RE.sub("", sql)


def normalize_sql(sql: str, dialect: str) -> tuple[str, tuple[Any, ...]]:
    """Parameterize SQL by replacing literals with ``?`` placeholders.

    Returns ``(normalized_sql, bind_params)``.  If sqlglot parsing fails,
    falls back to ``(sql_without_comment, ())`` so the analysis is never
    blocked by audit capture.
    """
    import sqlglot
    from sqlglot import exp

    sql_without_comment = _strip_session_comment(sql)

    try:
        parsed = sqlglot.parse_one(sql_without_comment, dialect=dialect)
    except Exception:
        return sql_without_comment, ()

    bind_params: list[Any] = []
    for literal in list(parsed.find_all(exp.Literal)):
        value: Any = literal.this
        if literal.is_string:
            bind_params.append(str(value))
        else:
            try:
                bind_params.append(int(value))
            except (ValueError, TypeError):
                try:
                    bind_params.append(float(value))
                except (ValueError, TypeError):
                    bind_params.append(value)
        literal.replace(exp.Placeholder())

    normalized = parsed.sql(dialect=dialect)
    return normalized, tuple(bind_params)


@dataclass(frozen=True)
class QueryExecution:
    query_id: str
    datasource: str
    dialect: str
    sql: str
    normalized_sql: str
    sql_digest: str
    bind_params: tuple[Any, ...]
    row_count: int
    duration_ms: int
    started_at: str
    finished_at: str
    status: str
    output_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["bind_params"] = list(self.bind_params)
        return d
