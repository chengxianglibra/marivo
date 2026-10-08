"""Read operation-owned documentation; reflection only checks its public shape."""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from inspect import getdoc, signature

from marivo.analysis._capabilities.dataset_model import ExampleInput, invalid


def section(value: object, heading: str) -> str:
    doc = getdoc(value) or ""
    match = re.search(
        r"(?:^|\n)\s*" + heading + r":\s*(.*?)(?=\n\s*(?:Args|Returns|Example|Constraints):|\Z)",
        doc,
        re.S,
    )
    return " ".join(match.group(1).split()) if match else ""


def parameter_guidance(value: Callable[..., object]) -> dict[str, str]:
    names = tuple(name for name in signature(value).parameters if name not in ("self", "cls"))
    if not names:
        return {}
    args = section(value, "Args")
    pattern = r"(?:^|\s)(" + "|".join(re.escape(name) for name in names) + r"): "
    matches = list(re.finditer(pattern, args))
    guidance = {
        match.group(1): args[
            match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(args)
        ].strip()
        for index, match in enumerate(matches)
    }
    if set(guidance) != set(names) or not all(guidance.values()):
        raise invalid("operation-owned Args for every parameter", value.__qualname__)
    return guidance


def free_names(code: str) -> tuple[str, ...]:
    tree = ast.parse(code)
    defined = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    defined.update(
        alias.asname or alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    )
    return tuple(
        dict.fromkeys(
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id not in defined | {"mv", "ms"}
        )
    )


def method_example(value: object) -> ExampleInput:
    snippets = re.findall(r"``(.*?)``", section(value, "Example"))
    if len(snippets) != 1:
        raise invalid("one operation-owned Python example", str(value))
    code = snippets[0]
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        raise invalid(
            "an executable operation-owned Python example", section(value, "Example")
        ) from error
    if len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr):
        code = "result = " + code
    assignments = [
        node.id
        for node in ast.walk(ast.parse(code))
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    ]
    return ExampleInput(
        code,
        free_names(code),
        assignments[-1] if assignments else "result",
        "The receiver-bound result.",
        True,
    )
