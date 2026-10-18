"""Read operation-owned documentation; reflection only checks its public shape."""

from __future__ import annotations

import re
from inspect import getdoc


def section(value: object, heading: str) -> str:
    doc = getdoc(value) or ""
    match = re.search(
        r"(?:^|\n)\s*" + heading + r":\s*(.*?)(?=\n\s*(?:Args|Returns|Example|Constraints):|\Z)",
        doc,
        re.S,
    )
    return " ".join(match.group(1).split()) if match else ""
