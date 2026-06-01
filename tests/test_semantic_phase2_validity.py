"""Phase 2 validity versioning semantic contracts.

Tests cover:
- ValidityVersioningIR round-trip through reader/loader
- Empty open_end rejection at load time
- Invalid interval rejection at load time
- valid_from not in primary_key rejection at load time
- Unknown field ref rejection at load time
"""

from __future__ import annotations

import pytest

from marivo.semantic.errors import SemanticLoadFailed

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MODEL_FILE = "import marivo.semantic as ms\nms.model(name='sales')\n"

# Dataset with actual @ms.field declarations so FieldRef objects exist in the
# registry and the field-existence check in _validate_validity_versioning passes.
# Fields are declared on the versioned dataset itself using a string dataset ref.
_DATASET_WITH_VALIDITY = (
    "import marivo.semantic as ms\n"
    "\n"
    "@ms.field(dataset='sales.user_history')\n"
    "def valid_from(t):\n"
    "    return t.valid_from\n"
    "\n"
    "@ms.field(dataset='sales.user_history')\n"
    "def valid_to(t):\n"
    "    return t.valid_to\n"
    "\n"
    "user_history = ms.dataset(\n"
    "    name='user_history',\n"
    "    datasource='warehouse',\n"
    "    source=ms.table('user_history'),\n"
    "    primary_key=['user_id', 'valid_from'],\n"
    "    versioning=ms.validity(\n"
    "        valid_from=valid_from,\n"
    "        valid_to=valid_to,\n"
    "        interval='closed_open',\n"
    "        open_end=(None,),\n"
    "    ),\n"
    ")\n"
)


# ---------------------------------------------------------------------------
# Test 1: validity round-trip
# ---------------------------------------------------------------------------


def test_validity_versioning_round_trip(semantic_project_factory):
    """ValidityVersioningIR is stored on the dataset and readable after load."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_FILE,
            "sales/datasets.py": _DATASET_WITH_VALIDITY,
        }
    )

    dataset = project.get_dataset("sales.user_history")
    assert dataset is not None
    versioning = dataset.versioning
    assert versioning is not None
    assert versioning.kind == "validity"
    assert versioning.valid_from == "sales.valid_from"
    assert versioning.valid_to == "sales.valid_to"
    assert versioning.interval == "closed_open"
    assert versioning.open_end == (None,)
    assert versioning.timezone is None


# ---------------------------------------------------------------------------
# Test 2: empty open_end is rejected
# ---------------------------------------------------------------------------


def test_validity_empty_open_end_rejected(semantic_project_factory):
    """ms.validity() with open_end=() raises invalid_dataset_versioning at decorator time."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_FILE,
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "user_history = ms.dataset(\n"
                "    name='user_history',\n"
                "    datasource='warehouse',\n"
                "    source=ms.table('user_history'),\n"
                "    primary_key=['user_id', 'valid_from'],\n"
                "    versioning=ms.validity(\n"
                "        valid_from='valid_from',\n"
                "        valid_to='valid_to',\n"
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
        project.get_dataset("sales.user_history")

    errors = exc_info.value.errors
    assert len(errors) >= 1
    error = errors[0]
    assert error.kind == "invalid_dataset_versioning"
    assert error.details.get("field") == "open_end"


# ---------------------------------------------------------------------------
# Test 3: invalid interval is rejected
# ---------------------------------------------------------------------------


def test_validity_invalid_interval_rejected(semantic_project_factory):
    """ms.validity() with interval='open_closed' raises invalid_ref at decorator time."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_FILE,
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "user_history = ms.dataset(\n"
                "    name='user_history',\n"
                "    datasource='warehouse',\n"
                "    source=ms.table('user_history'),\n"
                "    primary_key=['user_id', 'valid_from'],\n"
                "    versioning=ms.validity(\n"
                "        valid_from='valid_from',\n"
                "        valid_to='valid_to',\n"
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
        project.get_dataset("sales.user_history")

    errors = exc_info.value.errors
    assert len(errors) >= 1
    error = errors[0]
    assert error.kind == "invalid_dataset_versioning"
    assert error.details.get("field") == "interval"


# ---------------------------------------------------------------------------
# Test 4: valid_from not in primary_key is rejected
# ---------------------------------------------------------------------------


def test_validity_valid_from_not_in_primary_key_rejected(semantic_project_factory):
    """ms.validity() where valid_from is not in primary_key raises INVALID_DATASET_VERSIONING."""
    project = semantic_project_factory(
        {
            "sales/_model.py": _MODEL_FILE,
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "user_history = ms.dataset(\n"
                "    name='user_history',\n"
                "    datasource='warehouse',\n"
                "    source=ms.table('user_history'),\n"
                "    primary_key=['user_id'],\n"
                "    versioning=ms.validity(\n"
                "        valid_from='valid_from',\n"
                "        valid_to='valid_to',\n"
                "        interval='closed_open',\n"
                "        open_end=(None,),\n"
                "    ),\n"
                ")\n"
            ),
        },
        load=False,
    )
    project.load()

    with pytest.raises(SemanticLoadFailed) as exc_info:
        project.get_dataset("sales.user_history")

    errors = exc_info.value.errors
    assert len(errors) >= 1
    error = errors[0]
    assert error.kind == "invalid_dataset_versioning"
    assert error.details.get("field") == "valid_from"


# ---------------------------------------------------------------------------
# Test 5: unknown field ref is rejected
# ---------------------------------------------------------------------------


def test_validity_rejects_unknown_field_ref(semantic_project_factory):
    """ms.validity() with a valid_to that does not exist raises INVALID_DATASET_VERSIONING."""
    project = semantic_project_factory(
        {
            "sales/_model.py": "import marivo.semantic as ms\nms.model(name='sales')\n",
            "sales/datasets.py": (
                "import marivo.semantic as ms\n"
                "\n"
                "# Declare valid_from field on user_history (string dataset ref)\n"
                "@ms.field(dataset='sales.user_history')\n"
                "def valid_from(t):\n"
                "    return t.valid_from\n"
                "\n"
                "# valid_to is intentionally NOT declared\n"
                "user_history = ms.dataset(\n"
                "    name='user_history',\n"
                "    datasource='warehouse',\n"
                "    source=ms.table('user_history'),\n"
                "    primary_key=['user_id', 'valid_from'],\n"
                "    versioning=ms.validity(\n"
                "        valid_from=valid_from,\n"
                "        valid_to='sales.does_not_exist',\n"
                "        interval='closed_open',\n"
                "        open_end=(None,),\n"
                "    ),\n"
                ")\n"
            ),
        },
        load=False,
    )
    project.load()
    with pytest.raises(SemanticLoadFailed) as exc_info:
        project.get_dataset("sales.user_history")
    error = exc_info.value.errors[0]
    assert error.kind == "invalid_dataset_versioning"
    assert error.details["field"] == "valid_to"
