"""Isolated fixture project builder for the analysis surface evaluation gate.

Each trial starts a fresh agent context in a temporary project.  The fixture
builder creates the required directory structure: checked-in Python semantic
files, a deterministic DuckDB database, the one-file boundary ``SKILL.md``,
prompt files, and virtual-environment placeholders.

For the skew case, the builder creates a separate ``help-venv`` directory so
that version/package/interpreter fingerprints differ and no matching
authoritative fingerprint can be established.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Fixture project descriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FixtureProject:
    """Descriptor for a built fixture project directory.

    Parameters
    ----------
    root:
        Root directory of the fixture project.
    case_id:
        Case identifier (``"clean_convergence"`` or ``"environment_skew"``).
    project_file:
        Path to ``marivo.toml``.
    datasource_file:
        Path to the checked-in Python datasource declaration.
    semantic_dir:
        Directory containing checked-in Python semantic files.
    duckdb_path:
        Path to the deterministic DuckDB database file.
    skill_file:
        Path to the copied one-file ``SKILL.md``.
    prompt_file:
        Path to the case prompt file.
    analysis_venv:
        Path to the analysis virtual environment directory (placeholder).
    help_venv:
        Path to the help virtual environment directory (skew only).
    is_skew:
        Whether this is a skew fixture (``True``) or convergence (``False``).
    """

    root: Path
    case_id: str
    project_file: Path
    datasource_file: Path
    semantic_dir: Path
    duckdb_path: Path
    skill_file: Path
    prompt_file: Path
    analysis_venv: Path
    help_venv: Path | None = None
    is_skew: bool = False

    def __repr__(self) -> str:
        return f"FixtureProject(case={self.case_id} root={self.root} skew={self.is_skew})"


# ---------------------------------------------------------------------------
# Semantic / datasource templates
# ---------------------------------------------------------------------------

_DATASOURCE_PY = """\
import marivo.datasource as md

md.duckdb(name='warehouse', path='{duckdb_path}')
"""

_METRIC_PY = """\
import marivo.semantic as ms

ms.metric(
    id='revenue',
    datasource='warehouse',
    expression=ms.from_sql(
        sql="SELECT order_date AS ts, region, amount FROM sales_orders",
        dialect="duckdb",
    ),
    grain="day",
)
"""

_DIMENSION_PY = """\
import marivo.semantic as ms

ms.dimension(
    id='sales.orders.region',
    datasource='warehouse',
    expression=ms.from_sql(
        sql="SELECT DISTINCT region FROM sales_orders",
        dialect="duckdb",
    ),
    grain="region",
)
"""

_PROJECT_TOML = """\
[project]
name = "eval-{case_id}"

[semantic]
models = ["models/metrics.py", "models/dimensions.py"]
"""

_SKILL_CONTENT = """\
---
name: marivo-analysis
description: Boundary protocol for Marivo analysis.
---

# marivo-analysis

Use mv.help() for the live analysis surface. Verify the environment
fingerprint before analysis. Do not use native reflection for contract
discovery.
"""

_DUCKDB_SEED_SQL = """\
CREATE TABLE sales_orders AS
SELECT * FROM (VALUES
    ('2024-10-01', 'North', 10000.0),
    ('2024-10-15', 'South', 8500.0),
    ('2024-11-01', 'North', 12000.0),
    ('2024-11-15', 'South', 9200.0),
    ('2024-12-01', 'North', 15000.0),
    ('2024-12-15', 'South', 11000.0),
    ('2024-10-01', 'East', 7000.0),
    ('2024-10-15', 'West', 6500.0),
    ('2024-11-01', 'East', 8000.0),
    ('2024-11-15', 'West', 7200.0),
    ('2024-12-01', 'East', 9500.0),
    ('2024-12-15', 'West', 8800.0)
) AS t(order_date, region, amount);
"""


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _create_duckdb(duckdb_path: Path) -> None:
    """Create a deterministic DuckDB database with seed data.

    Parameters
    ----------
    duckdb_path:
        Output path for the DuckDB file.
    """
    import duckdb  # local import: only needed when building fixtures

    con = duckdb.connect(str(duckdb_path))
    try:
        con.execute(_DUCKDB_SEED_SQL)
    finally:
        con.close()


def _copy_skill(dest: Path) -> None:
    """Write the one-file boundary SKILL.md into the fixture project.

    Parameters
    ----------
    dest:
        Destination path for ``SKILL.md``.
    """
    dest.write_text(_SKILL_CONTENT)


def _write_semantic_files(semantic_dir: Path, duckdb_path: Path) -> None:
    """Write checked-in Python semantic files.

    Parameters
    ----------
    semantic_dir:
        Directory for ``models/`` Python files.
    duckdb_path:
        Path to the DuckDB database (embedded in the datasource declaration).
    """
    datasource_dir = semantic_dir / "datasources"
    datasource_dir.mkdir(parents=True, exist_ok=True)
    (datasource_dir / "warehouse.py").write_text(
        _DATASOURCE_PY.format(duckdb_path=str(duckdb_path))
    )
    (semantic_dir / "metrics.py").write_text(_METRIC_PY)
    (semantic_dir / "dimensions.py").write_text(_DIMENSION_PY)


def _build_common_fixture(
    root: Path,
    case_id: str,
    prompt_source: Path,
    skill_source: Path | None = None,
) -> dict[str, Path]:
    """Build shared fixture structure common to convergence and skew cases.

    Creates the DuckDB database, semantic files, project TOML, SKILL.md,
    prompt copy, and analysis-venv directory.

    Parameters
    ----------
    root:
        Root directory for the fixture project.  Created if it does not exist.
    case_id:
        Case identifier used in the project TOML.
    prompt_source:
        Path to the checked-in prompt file.
    skill_source:
        Optional path to the candidate one-file ``SKILL.md``.  If ``None``,
        a deterministic placeholder is written.

    Returns
    -------
    dict[str, Path]
        Mapping of built path names to their resolved paths.  Keys:
        ``duckdb_path``, ``semantic_dir``, ``project_file``, ``skill_file``,
        ``prompt_file``, ``analysis_venv``, ``datasource_file``.
    """
    root.mkdir(parents=True, exist_ok=True)

    duckdb_path = root / "warehouse.duckdb"
    _create_duckdb(duckdb_path)

    semantic_dir = root / "models"
    semantic_dir.mkdir(exist_ok=True)
    _write_semantic_files(semantic_dir, duckdb_path)

    project_file = root / "marivo.toml"
    project_file.write_text(_PROJECT_TOML.format(case_id=case_id))

    skill_file = root / "SKILL.md"
    if skill_source is not None and skill_source.is_file():
        shutil.copy2(skill_source, skill_file)
    else:
        _copy_skill(skill_file)

    prompt_dest = root / "prompt.md"
    shutil.copy2(prompt_source, prompt_dest)

    analysis_venv = root / "analysis-venv"
    analysis_venv.mkdir(exist_ok=True)

    return {
        "duckdb_path": duckdb_path,
        "semantic_dir": semantic_dir,
        "project_file": project_file,
        "skill_file": skill_file,
        "prompt_file": prompt_dest,
        "analysis_venv": analysis_venv,
        "datasource_file": semantic_dir / "datasources" / "warehouse.py",
    }


def build_convergence_fixture(
    root: Path,
    *,
    prompt_file: Path,
    skill_source: Path | None = None,
) -> FixtureProject:
    """Build a convergence-case fixture project.

    Parameters
    ----------
    root:
        Root directory for the fixture project.  Created if it does not exist.
    prompt_file:
        Path to the checked-in ``clean_convergence.md`` prompt.
    skill_source:
        Optional path to the candidate one-file ``SKILL.md``.  If ``None``,
        a deterministic placeholder is written.

    Returns
    -------
    FixtureProject
        Descriptor for the built fixture project.

    Example:
        >>> from pathlib import Path
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as d:
        ...     fp = build_convergence_fixture(Path(d), prompt_file=Path("p.md"))
        ...     fp.case_id
        'clean_convergence'
    """
    paths = _build_common_fixture(root, "clean_convergence", prompt_file, skill_source)
    return FixtureProject(
        root=root,
        case_id="clean_convergence",
        project_file=paths["project_file"],
        datasource_file=paths["datasource_file"],
        semantic_dir=paths["semantic_dir"],
        duckdb_path=paths["duckdb_path"],
        skill_file=paths["skill_file"],
        prompt_file=paths["prompt_file"],
        analysis_venv=paths["analysis_venv"],
        is_skew=False,
    )


def build_skew_fixture(
    root: Path,
    *,
    prompt_file: Path,
    skill_source: Path | None = None,
) -> FixtureProject:
    """Build an environment-skew fixture project.

    The skew fixture is identical to the convergence fixture except that it
    also creates a ``help-venv`` directory with a distinct marker file so
    that the help fingerprint (from ``help-venv``) cannot match the
    execution fingerprint (from ``analysis-venv``).

    Parameters
    ----------
    root:
        Root directory for the fixture project.  Created if it does not exist.
    prompt_file:
        Path to the checked-in ``environment_skew.md`` prompt.
    skill_source:
        Optional path to the candidate one-file ``SKILL.md``.

    Returns
    -------
    FixtureProject
        Descriptor for the built fixture project, with ``is_skew=True`` and
        ``help_venv`` set.

    Example:
        >>> from pathlib import Path
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as d:
        ...     fp = build_skew_fixture(Path(d), prompt_file=Path("p.md"))
        ...     fp.is_skew
        True
    """
    paths = _build_common_fixture(root, "environment_skew", prompt_file, skill_source)
    analysis_venv = paths["analysis_venv"]

    # help-venv: deliberately distinct so fingerprints cannot match.
    help_venv = root / "help-venv"
    help_venv.mkdir(exist_ok=True)
    (help_venv / ".help_env_marker").write_text(
        "help-venv\nversion=0.0.0-skew\ninterpreter=help-python\n"
    )
    (analysis_venv / ".analysis_env_marker").write_text(
        "analysis-venv\nversion=0.3.2\ninterpreter=analysis-python\n"
    )

    return FixtureProject(
        root=root,
        case_id="environment_skew",
        project_file=paths["project_file"],
        datasource_file=paths["datasource_file"],
        semantic_dir=paths["semantic_dir"],
        duckdb_path=paths["duckdb_path"],
        skill_file=paths["skill_file"],
        prompt_file=paths["prompt_file"],
        analysis_venv=analysis_venv,
        help_venv=help_venv,
        is_skew=True,
    )
