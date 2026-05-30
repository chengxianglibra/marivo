"""Pattern: observe relative windows with system-derived timezone.

When to use: you want v1.2 relative-window inputs using the system timezone
(the TZ environment variable or OS default) rather than an explicit parameter.
Output shape: scalar frame for no grain, time_series frame for day grain.
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

active = mv.session.active()

assert str(active.tz) == "Asia/Shanghai"
assert active.default_calendar == "cn_holidays"

print(f"session_tz={str(active.tz)!r}")
print(f"default_calendar={active.default_calendar!r}")
