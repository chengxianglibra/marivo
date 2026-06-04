"""Tests for the shared introspection internals."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from marivo.introspection.constraints import ASTSpec, Constraint
from marivo.introspection.describe import describe_object, own_doc, resolve_method_descriptor
from marivo.introspection.render import render_json
from marivo.introspection.schema import Descriptor, MethodInfo
from marivo.introspection.surface import Surface, render


class _BaseDoc:
    """Inherited doc that must not leak."""


class _OwnDoc(_BaseDoc):
    """Own doc line."""

    def sample(self, value: int) -> int:
        """Return the supplied value."""
        return value


class _PydanticNoDoc(BaseModel):
    value: int


@dataclass
class _FrameLike:
    """Frame-like class."""

    _NEXT_INTENTS = ("compare", "discover")

    def to_pandas(self) -> object:
        """Return a defensive copy."""
        return object()


class _BaseFrameLike:
    def to_pandas(self) -> object:
        """Return a defensive copy."""
        return object()


class _InheritedFrameLike(_BaseFrameLike):
    """Frame-like class with inherited frame API."""

    _NEXT_INTENTS = ("compare",)


def test_constraint_to_dict_accepts_plain_string_id() -> None:
    constraint = Constraint(
        id="example_rule",
        error_kind="example_error",
        phase="runtime",
        applies_to=("help",),
        title="Example rule.",
        why="Agents need stable rule metadata.",
        hint="Call help('example_rule', format='json') for details.",
        example="marivo-skills/marivo-analysis/references/examples/01_observe_single_window.py",
        docs_ref="marivo-skills/marivo-analysis/references/cheatsheet.md",
        ast_spec=ASTSpec(
            name="single_return",
            single_return=True,
            forbidden_statements=("Assign",),
            allowed_calls=("ms.ref",),
        ),
    )

    assert constraint.to_dict() == {
        "id": "example_rule",
        "error_kind": "example_error",
        "phase": "runtime",
        "applies_to": ["help"],
        "title": "Example rule.",
        "why": "Agents need stable rule metadata.",
        "hint": "Call help('example_rule', format='json') for details.",
        "example": "marivo-skills/marivo-analysis/references/examples/01_observe_single_window.py",
        "docs_ref": "marivo-skills/marivo-analysis/references/cheatsheet.md",
        "ast_spec": {
            "name": "single_return",
            "single_return": True,
            "forbidden_statements": ["Assign"],
            "forbidden_attributes": [],
            "forbidden_calls": [],
            "allowed_calls": ["ms.ref"],
            "allowed_binops": [],
            "allowed_unary_ops": [],
            "component_call_only": False,
        },
    }


def test_constraint_summary_is_l1_bounded() -> None:
    constraint = Constraint(
        id="summary_rule",
        error_kind="summary_error",
        phase="runtime",
        applies_to=("MetricFrame",),
        title="Summary rule.",
        why="This rationale is intentionally excluded from L1.",
        hint="Use the supported frame method.",
        example="marivo-skills/marivo-analysis/references/examples/compare_panel.py",
    )

    assert constraint.to_summary_dict() == {
        "id": "summary_rule",
        "title": "Summary rule.",
        "hint": "Use the supported frame method.",
        "example": "marivo-skills/marivo-analysis/references/examples/compare_panel.py",
    }


def test_own_doc_does_not_walk_mro_or_module_doc() -> None:
    assert own_doc(_OwnDoc) == "Own doc line."
    assert own_doc(_BaseDoc) == "Inherited doc that must not leak."
    assert own_doc(_PydanticNoDoc) == ""


def test_describe_class_lists_public_methods_as_l1_summaries() -> None:
    descriptor = describe_object(
        surface="test.surface",
        symbol="_OwnDoc",
        obj=_OwnDoc,
        summary="class summary",
        constraints=(),
        examples=(),
        see_also=("help('other')",),
    )

    assert descriptor.kind == "class"
    assert descriptor.signature == "class _OwnDoc"
    assert descriptor.doc == "Own doc line."
    assert descriptor.methods == (MethodInfo(name="sample", summary="Return the supplied value."),)
    assert render_json(descriptor)["methods"] == [
        {"name": "sample", "summary": "Return the supplied value."}
    ]


def test_signature_for_falls_back_when_annotation_eval_fails() -> None:
    namespace: dict[str, object] = {}
    exec(
        "from __future__ import annotations\n"
        "\n"
        "def broken(value: MissingType) -> MissingReturn:\n"
        "    return value\n",
        namespace,
    )

    descriptor = describe_object(
        surface="test.surface",
        symbol="broken",
        obj=namespace["broken"],
        summary="broken signature",
        constraints=(),
        examples=(),
        see_also=(),
    )

    assert descriptor.signature == "broken(...)"


def test_describe_frame_suppresses_dataclass_init_and_exposes_next_intents() -> None:
    descriptor = describe_object(
        surface="test.surface",
        symbol="_FrameLike",
        obj=_FrameLike,
        summary="frame summary",
        constraints=(),
        examples=(),
        see_also=(),
        frame_symbols={"_FrameLike"},
        constructed_by={"_FrameLike": "session.observe(...)"},
    )

    data = render_json(descriptor)
    assert data["kind"] == "frame"
    assert "signature" not in data
    assert data["constructed_by"] == "session.observe(...)"
    assert data["next_intents"] == ["compare", "discover"]
    assert data["methods"] == [{"name": "to_pandas", "summary": "Return a defensive copy."}]


def test_frame_descriptors_include_inherited_frame_methods() -> None:
    descriptor = describe_object(
        surface="test.surface",
        symbol="_InheritedFrameLike",
        obj=_InheritedFrameLike,
        summary="frame summary",
        constraints=(),
        examples=(),
        see_also=(),
        frame_symbols={"_InheritedFrameLike"},
        constructed_by={"_InheritedFrameLike": "session.observe(...)"},
    )

    data = render_json(descriptor)
    assert data["kind"] == "frame"
    assert data["methods"] == [{"name": "to_pandas", "summary": "Return a defensive copy."}]


def test_method_drilldown_returns_signature_and_own_doc() -> None:
    descriptor = resolve_method_descriptor(
        surface="test.surface",
        dotted_path="_OwnDoc.sample",
        owner=_OwnDoc,
        summary="Return the supplied value.",
    )

    data = render_json(descriptor)
    assert data["kind"] == "callable"
    assert data["symbol"] == "_OwnDoc.sample"
    assert data["signature"] == "_OwnDoc.sample(self, value: int) -> int"
    assert data["doc"] == "Return the supplied value."


def test_method_drilldown_rejects_inherited_public_methods() -> None:
    def resolve(symbol: str) -> object | None:
        return _PydanticNoDoc if symbol == "_PydanticNoDoc" else None

    surface = Surface(
        name="test.surface",
        all_names=("_PydanticNoDoc",),
        summaries={"_PydanticNoDoc": "pydantic model"},
        resolve=resolve,
        catalog={},
        topics={},
    )

    data = render(surface, "_PydanticNoDoc.model_dump", "json")
    assert isinstance(data, dict)
    assert data["kind"] == "unknown"


def test_method_drilldown_allows_inherited_frame_methods() -> None:
    def resolve(symbol: str) -> object | None:
        return _InheritedFrameLike if symbol == "_InheritedFrameLike" else None

    surface = Surface(
        name="test.surface",
        all_names=("_InheritedFrameLike",),
        summaries={"_InheritedFrameLike": "frame summary"},
        resolve=resolve,
        catalog={},
        topics={},
        frame_symbols={"_InheritedFrameLike"},
    )

    data = render(surface, "_InheritedFrameLike.to_pandas", "json")
    assert isinstance(data, dict)
    assert data["kind"] == "callable"
    assert data["symbol"] == "_InheritedFrameLike.to_pandas"
    assert data["doc"] == "Return a defensive copy."


def test_surface_topics_accept_prebuilt_descriptors() -> None:
    topic = Descriptor(
        surface="test.surface",
        kind="topic",
        symbol="topic",
        summary="prebuilt topic",
        content={"items": ["one"]},
    )
    surface = Surface(
        name="test.surface",
        all_names=("topic",),
        summaries={"topic": "prebuilt topic"},
        resolve=lambda symbol: None,
        catalog={},
        topics={"topic": topic},
    )

    data = render(surface, "topic", "json")
    assert isinstance(data, dict)
    assert data["kind"] == "topic"
    assert data["content"] == {"items": ["one"]}


def test_surface_render_handles_unknown_with_did_you_mean() -> None:
    def resolve(symbol: str) -> object | None:
        return _OwnDoc if symbol == "_OwnDoc" else None

    surface = Surface(
        name="test.surface",
        all_names=("_OwnDoc",),
        summaries={"_OwnDoc": "class summary"},
        resolve=resolve,
        catalog={},
        topics={},
    )

    data = render(surface, "_OwnDc", "json")
    assert isinstance(data, dict)
    assert data["kind"] == "unknown"
    assert data["did_you_mean"] == ["_OwnDoc"]
    assert "help()" in data["summary"]


def test_surface_top_level_entries_are_derived_from_all_names() -> None:
    def resolve(symbol: str) -> object | None:
        return _OwnDoc if symbol == "_OwnDoc" else None

    surface = Surface(
        name="test.surface",
        all_names=("_OwnDoc",),
        summaries={"_OwnDoc": "class summary"},
        resolve=resolve,
        catalog={},
        topics={},
    )

    data = render(surface, None, "json")
    assert isinstance(data, dict)
    assert data["kind"] == "surface"
    assert data["entries"] == [{"name": "_OwnDoc", "kind": "class", "summary": "class summary"}]
    assert "test.surface" in render(surface, None, "text")
