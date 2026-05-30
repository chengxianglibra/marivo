import runpy
from pathlib import Path


def test_v1_2_examples_execute():
    root = Path("marivo-skills/marivo-analysis/references/examples")
    for name in ["observe_timescope.py", "session_timezone.py", "compare_calendar.py"]:
        runpy.run_path(str(root / name), run_name="__main__")
