"""Tests for shared constraint disclosure."""

from marivo.introspection.constraints import ASTSpec, Constraint


def test_constraint_to_dict_accepts_plain_string_id() -> None:
    constraint = Constraint(
        id="example_rule",
        error_kind="example_error",
        phase="runtime",
        applies_to=("help",),
        title="Example rule.",
        why="Agents need stable rule metadata.",
        hint="Call help('example_rule') for details.",
        example="site/src/content/docs/docs/latest/guides/authoring.py",
        docs_ref="site/src/content/docs/docs/latest/concepts/semantic-layer.mdx",
        help_target="observe",
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
        "hint": "Call help('example_rule') for details.",
        "example": "site/src/content/docs/docs/latest/guides/authoring.py",
        "docs_ref": "site/src/content/docs/docs/latest/concepts/semantic-layer.mdx",
        "help_target": "observe",
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
            "shadowed_attributes": [],
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
        example="site/src/content/docs/docs/latest/guides/authoring.py",
        help_target="observe",
    )

    assert constraint.to_summary_dict() == {
        "id": "summary_rule",
        "title": "Summary rule.",
        "hint": "Use the supported frame method.",
        "example": "site/src/content/docs/docs/latest/guides/authoring.py",
        "help_target": "observe",
    }
