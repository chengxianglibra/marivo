"""Pattern: create/attach an examples session with timezone and default calendar.

When to use: you need session-level timezone and default calendar for relative windows
and calendar-aware compare.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _fixtures.tiny_semantic import ensure_loaded

ensure_loaded(timezone="Asia/Shanghai", default_calendar="cn_holidays")

import marivo.analysis as mv  # noqa: E402

active = mv.session.active()

assert str(active.tz) == "Asia/Shanghai"
assert active.default_calendar == "cn_holidays"

print(f"session_tz={str(active.tz)!r}")
print(f"default_calendar={active.default_calendar!r}")
