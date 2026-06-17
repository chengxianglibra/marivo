"""Pattern: create a session with system-derived report timezone.

When to use: calendar alignment or timestamp bucketing should use the process
timezone (the TZ environment variable or OS default).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _fixtures.tiny_semantic import ensure_loaded

os.environ["TZ"] = "Asia/Shanghai"
ensure_loaded(default_calendar="cn_holidays")

import marivo.analysis as mv  # noqa: E402

active = mv.session.current()

assert active.report_tz_name == "Asia/Shanghai"
assert active.default_calendar == "cn_holidays"

print(f"report_tz={active.report_tz_name!r}")
print(f"default_calendar={active.default_calendar!r}")
