"""Small pandas oracle naming helpers over readable Frame exports."""

import pandas as pd

import marivo.analysis as mv


def ordinary_columns(frame: mv.AnalysisFrame) -> tuple[str, ...]:
    """Select ordinary coordinates by their declared axis role, preserving order."""
    return tuple(
        axis.name.rsplit(".", 1)[-1] for axis in frame.axes if axis.role in ("identity", "field")
    )


def plain(result: mv.AnalysisFrame) -> pd.DataFrame:
    """Give shared numerical oracles short coordinate and presence names."""
    frame = result.to_pandas()
    names: dict[str, str] = {}
    ordinary = temporal = 0
    for index, axis in enumerate(result.axes):
        if axis.role in ("identity", "field"):
            names[axis.name.rsplit(".", 1)[-1]] = f"key_{ordinary}"
            ordinary += 1
        else:
            column = axis.name.rsplit(".", 1)[-1]
            if axis.role == "interval":
                column += ".start"
            if axis.role == "paired":
                column += ".left"
                schema = result.schema.axes[index]
                assert isinstance(schema, mv.FramePairedAxisSchema)
                if schema.left.kind == "interval":
                    column += ".start"
            names[column] = f"time_{temporal}"
            temporal += 1
    names.update((name, "presence." + name) for name in result.presence_names)
    return frame.rename(columns=names)
