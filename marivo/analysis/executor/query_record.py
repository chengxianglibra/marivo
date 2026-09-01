"""Query execution record for the executed-SQL audit trail."""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from typing import Literal


def gen_query_ref() -> str:
    return f"query_{secrets.token_hex(4)}"


_DIGEST_HEX_CHARS = 16  # 8 bytes of SHA-256, enough for dedup/correlation


def compute_sql_digest(normalized_sql: str) -> str:
    return hashlib.sha256(normalized_sql.encode("utf-8")).hexdigest()[:_DIGEST_HEX_CHARS]


_SESSION_COMMENT_RE = re.compile(r"^/\* from=marivo,session=[^*]* \*/\n?")


def _strip_session_comment(sql: str) -> str:
    return _SESSION_COMMENT_RE.sub("", sql)


def normalize_sql(sql: str, dialect: str) -> str:
    """Normalize SQL by replacing literals with ``?`` placeholders.

    If sqlglot parsing fails, token-level literal normalization is attempted;
    the SQL without the Session comment is the final non-blocking fallback.
    The normalized text is transient input to :func:`compute_sql_digest`; it is
    not an executed query or a source of database-driver bind parameters.
    """
    from sqlglot import Tokenizer, TokenType, exp, parse_one

    sql_without_comment = _strip_session_comment(sql)

    try:
        parsed = parse_one(sql_without_comment, dialect=dialect)
    except Exception:
        try:
            literal_tokens = {
                TokenType.BIT_STRING,
                TokenType.BOOLEAN,
                TokenType.BYTE_STRING,
                TokenType.FALSE,
                TokenType.HEREDOC_STRING,
                TokenType.HEX_STRING,
                TokenType.NATIONAL_STRING,
                TokenType.NUMBER,
                TokenType.RAW_STRING,
                TokenType.STRING,
                TokenType.TRUE,
                TokenType.UNICODE_STRING,
            }
            return " ".join(
                "?" if token.token_type in literal_tokens else token.text
                for token in Tokenizer(dialect=dialect).tokenize(sql_without_comment)
            )
        except Exception:
            return sql_without_comment

    literals = [*parsed.find_all(exp.Literal), *parsed.find_all(exp.Boolean)]
    for literal in literals:
        literal.replace(exp.Placeholder())

    return parsed.sql(dialect=dialect)


@dataclass(frozen=True, slots=True)
class QueryExecution:
    """Private execution-time query facts captured for the active Run."""

    query_id: str
    datasource: str
    dialect: str
    sql: str
    sql_digest: str
    row_count: int
    duration_ms: int
    started_at: str
    finished_at: str
    status: Literal["succeeded", "failed"]
