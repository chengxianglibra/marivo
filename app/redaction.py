from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

SENSITIVE_FIELD_RE = re.compile(r"(password|passwd|pwd|secret|token|key|dsn)", re.I)
_URL_WITH_AUTH_RE = re.compile(r"\b([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^@\s]+)@")
_ASSIGNMENT_RE = re.compile(
    r"(?P<key>password|passwd|pwd|secret|token|dsn)(?P<sep>\s*[:=]\s*)(?P<value>[^\s,;]+)",
    re.I,
)
_DICT_REPR_RE = re.compile(
    r"(?P<quote>['\"])(?P<key>password|passwd|pwd|secret|token|dsn)(?P=quote)"
    r"(?P<sep>\s*:\s*)(?P<value>['\"][^'\"]*['\"])",
    re.I,
)


def redact_sensitive_text(value: object) -> str:
    text = str(value)
    text = _redact_url_passwords(text)
    text = _DICT_REPR_RE.sub(
        lambda match: (
            f"{match.group('quote')}{match.group('key')}"
            f"{match.group('quote')}{match.group('sep')}***"
        ),
        text,
    )
    text = _ASSIGNMENT_RE.sub(lambda match: f"{match.group('key')}{match.group('sep')}***", text)
    return text


def redact_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in values.items():
        if SENSITIVE_FIELD_RE.search(key):
            redacted[key] = "***" if value is not None else None
        elif isinstance(value, Mapping):
            redacted[key] = redact_mapping(value)
        else:
            redacted[key] = value
    return redacted


def redact_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.password is None:
        return value
    username = parsed.username or ""
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port is not None else ""
    netloc = f"{username}:***@{host}{port}" if username else f"***@{host}{port}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _redact_url_passwords(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return f"{match.group(1)}{match.group(2)}:***@"

    return _URL_WITH_AUTH_RE.sub(replace, text)
