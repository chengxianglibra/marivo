import os
import runpy
import sys
from pathlib import Path

RETAINED_ANALYSIS_EXAMPLES = {
    "00_real_project_template.py",
    "01_observe_single_window.py",
    "02_compare_yoy.py",
    "03_attribute_attribution.py",
    "04_discover_point_anomaly.py",
    "14_derive_metric_frame.py",
    "99_pitfall_pass_delta_to_compare.py",
}

TEMPLATE_ONLY_EXAMPLES = {"00_real_project_template.py"}


def test_v1_2_examples_execute():
    root = Path("marivo/skills/marivo-analysis/references/examples").resolve()
    names = {path.name for path in root.glob("*.py")}
    assert names == RETAINED_ANALYSIS_EXAMPLES

    old_cwd = Path.cwd()
    old_path = list(sys.path)
    try:
        os.chdir(root)
        sys.path.insert(0, str(root.resolve()))
        for name in sorted(RETAINED_ANALYSIS_EXAMPLES - TEMPLATE_ONLY_EXAMPLES):
            runpy.run_path(name, run_name="__main__")
    finally:
        os.chdir(old_cwd)
        sys.path[:] = old_path
