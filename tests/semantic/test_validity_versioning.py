"""Validity versioning semantic contracts."""

from __future__ import annotations

import pytest

from marivo.semantic.catalog import SemanticCatalog
from marivo.semantic.errors import SemanticLoadFailed
from marivo.semantic.expressions import Entity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DOMAIN_FILE = "import marivo.datasource as md\nimport marivo.semantic as ms\nms.domain(name='sales', owner='Mina Zhang')\n"

# Typed temporal bounds are row coordinates, separate from stable Entity identity.
_DATASET_WITH_VALIDITY = """
import marivo.datasource as md
import marivo.semantic as ms
user_history_ref = ms.ref.entity('sales.user_history')
valid_from = ms.time_dimension_column(name='valid_from', entity=user_history_ref, column='valid_from')
valid_to = ms.time_dimension_column(name='valid_to', entity=user_history_ref, column='valid_to')
user_history = ms.entity(
    name='user_history', datasource=ms.ref.datasource('warehouse'),
    source=md.table('user_history', columns={'user_id': 'user_id', 'valid_from': 'valid_from', 'valid_to': 'valid_to'}),
    primary_key=['user_id'],
    versioning=ms.validity(valid_from=valid_from, valid_to=valid_to, interval='closed_open', open_end=(None,)),
)
"""


# ---------------------------------------------------------------------------
# Test 1: validity round-trip
# ---------------------------------------------------------------------------


def test_validity_versioning_round_trip(semantic_project_factory):
    """ValidityVersioningIR is stored on the dataset and readable after load."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_FILE,
            "sales/datasets.py": _DATASET_WITH_VALIDITY,
        }
    )

    dataset = SemanticCatalog(project).entities.get("sales.user_history")
    assert isinstance(dataset, Entity)
    versioning = project._registry.entities["sales.user_history"].versioning
    assert versioning is not None
    assert versioning.kind == "validity"
    assert versioning.valid_from == "sales.user_history.valid_from"
    assert versioning.valid_to == "sales.user_history.valid_to"
    assert versioning.interval == "closed_open"
    assert versioning.open_end == (None,)
    assert versioning.timezone is None


# ---------------------------------------------------------------------------
# Test 2: empty open_end is rejected
# ---------------------------------------------------------------------------


def test_validity_empty_open_end_rejected(semantic_project_factory):
    """ms.validity() with open_end=() raises invalid_entity_versioning at decorator time."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_FILE,
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "user_history = ms.entity(\n"
                "    name='user_history',\n"
                "    datasource=ms.ref.datasource('warehouse'),\n"
                "    source=md.table('user_history'),\n"
                "    primary_key=['user_id', 'valid_from'],\n"
                "    versioning=ms.validity(\n"
                "        valid_from=ms.ref.time_dimension('sales.user_history.valid_from'),\n"
                "        valid_to=ms.ref.time_dimension('sales.user_history.valid_to'),\n"
                "        interval='closed_open',\n"
                "        open_end=(),\n"
                "    ),\n"
                ")\n"
            ),
        },
        load=False,
    )
    project.load()

    with pytest.raises(SemanticLoadFailed) as exc_info:
        SemanticCatalog(project).entities.get("sales.user_history")

    errors = exc_info.value.errors
    assert len(errors) >= 1
    error = errors[0]
    assert error.kind == "invalid_entity_versioning"
    assert error.details.get("field") == "open_end"


# ---------------------------------------------------------------------------
# Test 3: invalid interval is rejected
# ---------------------------------------------------------------------------


def test_validity_invalid_interval_rejected(semantic_project_factory):
    """ms.validity() with interval='open_closed' raises invalid_ref at decorator time."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_FILE,
            "sales/datasets.py": (
                "import marivo.datasource as md\nimport marivo.semantic as ms\n"
                "user_history = ms.entity(\n"
                "    name='user_history',\n"
                "    datasource=ms.ref.datasource('warehouse'),\n"
                "    source=md.table('user_history'),\n"
                "    primary_key=['user_id', 'valid_from'],\n"
                "    versioning=ms.validity(\n"
                "        valid_from=ms.ref.time_dimension('sales.user_history.valid_from'),\n"
                "        valid_to=ms.ref.time_dimension('sales.user_history.valid_to'),\n"
                "        interval='open_closed',\n"
                "        open_end=(None,),\n"
                "    ),\n"
                ")\n"
            ),
        },
        load=False,
    )
    project.load()

    with pytest.raises(SemanticLoadFailed) as exc_info:
        SemanticCatalog(project).entities.get("sales.user_history")

    errors = exc_info.value.errors
    assert len(errors) >= 1
    error = errors[0]
    assert error.kind == "invalid_entity_versioning"
    assert error.details.get("field") == "interval"


# ---------------------------------------------------------------------------
# Test 4: valid_from in primary_key is rejected
# ---------------------------------------------------------------------------


def test_validity_valid_from_in_primary_key_rejected(semantic_project_factory):
    """Validity row coordinates cannot become stable Entity identity."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_FILE,
            "sales/datasets.py": _DATASET_WITH_VALIDITY.replace(
                "primary_key=['user_id']", "primary_key=['user_id', 'valid_from']"
            ),
        },
        load=False,
    )
    project.load()

    with pytest.raises(SemanticLoadFailed) as exc_info:
        SemanticCatalog(project).entities.get("sales.user_history")

    errors = exc_info.value.errors
    assert len(errors) >= 1
    error = errors[0]
    assert error.kind == "identity_version_overlap"
    assert error.expected == "stable identity K separate from validity coordinates"


# ---------------------------------------------------------------------------
# Test 5: unknown field ref is rejected
# ---------------------------------------------------------------------------


def test_validity_rejects_unknown_field_ref(semantic_project_factory):
    """Unknown temporal bounds fail semantic validation without source I/O."""
    project = semantic_project_factory(
        {
            "sales/_domain.py": _DOMAIN_FILE,
            "sales/datasets.py": _DATASET_WITH_VALIDITY.replace(
                "valid_to=valid_to,",
                "valid_to=ms.ref.time_dimension('sales.user_history.does_not_exist'),",
            ),
        },
        load=False,
    )
    project.load()
    with pytest.raises(SemanticLoadFailed) as exc_info:
        SemanticCatalog(project).entities.get("sales.user_history")
    error = exc_info.value.errors[0]
    assert error.kind == "invalid_target_semantics"
    assert "does_not_exist" in str(error)


@pytest.mark.parametrize("sentinel", ["20260802", "OPEN"])
def test_encoded_validity_open_end_matches_the_authored_source_sentinel(
    semantic_project_factory, tmp_path, monkeypatch, sentinel
):
    import duckdb

    import marivo.analysis as mv

    database = tmp_path / "validity.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE history(id BIGINT, beginning VARCHAR, ending VARCHAR)")
        connection.execute("INSERT INTO history VALUES (1,'20260801',?)", [sentinel])
    semantic_project_factory(
        {
            "datasources/warehouse.py": "import marivo.datasource as md\n"
            + f"md.duckdb(name='warehouse',path={str(database)!r})\n",
            "sales/_domain.py": _DOMAIN_FILE,
            "sales/models.py": "import marivo.datasource as md\nimport marivo.semantic as ms\n"
            + f"history=ms.entity(name='history',datasource=ms.ref.datasource('warehouse'),source=md.table('history'),primary_key=['id'],versioning=ms.validity(valid_from=ms.ref.time_dimension('sales.history.beginning'),valid_to=ms.ref.time_dimension('sales.history.ending'),interval='closed_open',open_end=({sentinel!r},),timezone='UTC'))\n"
            + "beginning=ms.time_dimension_column(name='beginning',entity=history,column='beginning',parse=ms.strptime('%Y%m%d'))\n"
            + "ending=ms.time_dimension_column(name='ending',entity=history,column='ending',parse=ms.strptime('%Y%m%d'))\n",
        }
    )
    monkeypatch.chdir(tmp_path)
    session = mv.session.create("encoded-open-end", timezone="UTC")
    members = session.members(
        session.catalog.entities.get("sales.history"), at=session.instant("2026-08-03")
    )
    assert session.frame(members).compute().to_pandas().iloc[:, 0].tolist() == [1]
