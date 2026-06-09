from __future__ import annotations

import dataclasses

import pytest

from marivo.semantic.discovery import DiscoveryResult, SelectionError
from marivo.semantic.errors import SemanticError


@dataclasses.dataclass(frozen=True)
class _FakeItem:
    semantic_id: str
    label: str


def _make(items: list[_FakeItem], *, has_ids: bool = True) -> DiscoveryResult[_FakeItem]:
    return DiscoveryResult(items, item_type_name="_FakeItem", has_ids=has_ids)


# --- repr ---


def test_repr_is_one_line_hint():
    dr = _make([_FakeItem("a.b", "B"), _FakeItem("a.c", "C")])
    r = repr(dr)
    assert r.count("\n") == 0
    assert "DiscoveryResult[_FakeItem]" in r
    assert "items=2" in r
    assert "call .show() to inspect" in r


def test_repr_writes_no_stdout(capsys):
    dr = _make([_FakeItem("a.b", "B")])
    repr(dr)
    assert capsys.readouterr().out == ""


# --- render / show ---


def test_render_returns_str_no_stdout(capsys):
    dr = _make([_FakeItem("a.b", "B")])
    result = dr.render()
    assert isinstance(result, str)
    assert capsys.readouterr().out == ""


def test_render_does_not_end_with_newline():
    dr = _make([_FakeItem("a.b", "B")])
    assert not dr.render().endswith("\n")


def test_render_contains_identity_and_available():
    dr = _make([_FakeItem("a.b", "B"), _FakeItem("a.c", "C")])
    rendered = dr.render()
    assert "DiscoveryResult[_FakeItem]" in rendered
    assert "available:" in rendered


def test_render_available_includes_ids_first_require_one(capsys):
    dr = _make([_FakeItem("a.b", "B")])
    rendered = dr.render()
    assert ".ids()" in rendered
    assert ".first()" in rendered
    assert ".require_one()" in rendered


def test_render_available_omits_ids_when_not_applicable():
    dr = _make([_FakeItem("a.b", "B")], has_ids=False)
    rendered = dr.render()
    assert ".ids()" not in rendered
    assert ".first()" in rendered


def test_render_available_never_empty():
    dr = _make([])
    rendered = dr.render()
    lines = rendered.splitlines()
    avail_idx = next(i for i, ln in enumerate(lines) if ln == "available:")
    assert avail_idx < len(lines) - 1


def test_show_prints_render_plus_newline(capsys):
    dr = _make([_FakeItem("a.b", "B")])
    dr.show()
    captured = capsys.readouterr()
    assert captured.out == dr.render() + "\n"


# --- summary ---


def test_summary_returns_typed_object():
    from marivo.semantic.discovery import DiscoveryResultSummary

    dr = _make([_FakeItem("a.b", "B"), _FakeItem("a.c", "C")])
    s = dr.summary()
    assert isinstance(s, DiscoveryResultSummary)
    assert s.item_count == 2
    assert s.item_type == "_FakeItem"


def test_summary_writes_no_stdout(capsys):
    dr = _make([_FakeItem("a.b", "B")])
    dr.summary()
    assert capsys.readouterr().out == ""


# --- preview ---


def test_preview_returns_bounded_list():
    items = [_FakeItem(f"a.{i}", str(i)) for i in range(20)]
    dr = _make(items)
    result = dr.preview(limit=5)
    assert len(result) == 5
    assert result[0] == items[0]


def test_preview_default_limit_is_ten():
    items = [_FakeItem(f"a.{i}", str(i)) for i in range(20)]
    dr = _make(items)
    assert len(dr.preview()) == 10


def test_preview_writes_no_stdout(capsys):
    dr = _make([_FakeItem("a.b", "B")])
    dr.preview()
    assert capsys.readouterr().out == ""


# --- .items ---


def test_items_returns_full_typed_list():
    items = [_FakeItem("a.b", "B"), _FakeItem("a.c", "C")]
    dr = _make(items)
    assert dr.items == items


def test_items_returns_copy():
    items = [_FakeItem("a.b", "B")]
    dr = _make(items)
    assert dr.items is not dr.items  # new list each call


# --- .ids() ---


def test_ids_returns_semantic_id_list():
    dr = _make([_FakeItem("a.b", "B"), _FakeItem("a.c", "C")])
    assert dr.ids() == ["a.b", "a.c"]


def test_ids_writes_no_stdout(capsys):
    dr = _make([_FakeItem("a.b", "B")])
    dr.ids()
    assert capsys.readouterr().out == ""


def test_ids_raises_selection_error_when_has_ids_false():
    dr = _make([_FakeItem("a.b", "B")], has_ids=False)
    with pytest.raises(SelectionError):
        dr.ids()


# --- .first() ---


def test_first_returns_first_item():
    dr = _make([_FakeItem("a.b", "B"), _FakeItem("a.c", "C")])
    assert dr.first() == _FakeItem("a.b", "B")


def test_first_returns_none_when_empty():
    dr = _make([])
    assert dr.first() is None


# --- .require_one() ---


def test_require_one_returns_item_when_exactly_one():
    dr = _make([_FakeItem("a.b", "B")])
    assert dr.require_one() == _FakeItem("a.b", "B")


def test_require_one_raises_selection_error_for_zero():
    dr = _make([])
    with pytest.raises(SelectionError) as exc_info:
        dr.require_one()
    assert "no results" in str(exc_info.value.message).lower()


def test_require_one_raises_selection_error_for_multiple():
    dr = _make([_FakeItem("a.b", "B"), _FakeItem("a.c", "C")])
    with pytest.raises(SelectionError) as exc_info:
        dr.require_one()
    assert "2" in str(exc_info.value.message)


def test_selection_error_is_semantic_error():
    assert issubclass(SelectionError, SemanticError)


# --- list-like protocol ---


def test_len():
    dr = _make([_FakeItem("a.b", "B"), _FakeItem("a.c", "C")])
    assert len(dr) == 2


def test_iter():
    items = [_FakeItem("a.b", "B"), _FakeItem("a.c", "C")]
    dr = _make(items)
    assert list(dr) == items


def test_getitem():
    items = [_FakeItem("a.b", "B"), _FakeItem("a.c", "C")]
    dr = _make(items)
    assert dr[0] == items[0]
    assert dr[1] == items[1]
