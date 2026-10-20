"""Pandas oracle labels that preserve time and ordinary coordinate keys."""

import pandas as pd

import marivo.analysis as mv


def ordinary_columns(frame: pd.DataFrame) -> tuple[tuple[str, str], ...]:
    """Select ordinary coordinates by their declared axis role, preserving order."""
    return tuple(
        ("coordinate", axis["token"])
        for axis in frame.attrs["axes"]
        if axis["role"] in ("identity", "field", "data")
    )


def plain(result: mv.AnalysisFrame) -> pd.DataFrame:
    """Retain every key, naming time keys separately from ordinary key_N labels."""
    frame = result.to_pandas()
    roles = {axis["token"]: axis["role"] for axis in frame.attrs["axes"]}
    names: list[str] = []
    ordinary = 0
    temporal = 0
    for label in frame.columns:
        assert isinstance(label, tuple) and len(label) == 2
        kind, name = label
        assert isinstance(kind, str) and isinstance(name, str)
        if kind == "coordinate":
            if roles[name] in ("interval", "calendar", "paired"):
                names.append(f"time_{temporal}")
                temporal += 1
            else:
                names.append(f"key_{ordinary}")
                ordinary += 1
        elif kind == "value":
            names.append(name)
        elif kind == "presence":
            names.append("presence." + name)
        else:
            raise AssertionError(f"Unexpected Frame export kind: {kind}")
    assert len(set(names)) == len(names) == len(frame.columns)
    frame.columns = names
    return frame
