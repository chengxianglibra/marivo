"""Declare usage instrumentation on the callable that owns an operation."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable
from typing import ParamSpec, TypeVar, cast

from marivo._kernel.config import load_project_config
from marivo.telemetry.config import _LOAD_POLICY, CapturePolicy, _capture_policy, _mode
from marivo.telemetry.context import (
    _already_active,
    _Operation,
    _project_root,
    telemetry_stage,
)
from marivo.telemetry.sanitizer import (
    _input_attributes,
    _session_attributes,
    _session_creation_attributes,
)

_P = ParamSpec("_P")
_R = TypeVar("_R")


def staged(name: str) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Decorate an internal phase without creating a second operation."""

    def decorate(func: Callable[_P, _R]) -> Callable[_P, _R]:
        @functools.wraps(func)
        def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            with telemetry_stage(name):
                return func(*args, **kwargs)

        return wrapped

    return decorate


def _bind_arguments(
    signature: inspect.Signature, args: tuple[object, ...], kwargs: dict[str, object]
) -> dict[str, object]:
    try:
        bound = signature.bind_partial(*args, **kwargs)
    except TypeError:
        arguments = dict(kwargs)
        if args and next(iter(signature.parameters), None) == "self":
            arguments["self"] = args[0]
        return arguments
    bound.apply_defaults()
    return dict(bound.arguments)


def traced(
    *,
    surface: str,
    operation: str,
    kind: str = "callable",
    stage: str | None = None,
    capture: CapturePolicy | None = None,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Decorate one public capability with mode-aware operation telemetry."""
    analysis_dsl = surface == "analysis" and capture is not None
    policy = capture or _capture_policy(surface, operation, kind, default_stage=stage)

    def decorate(func: Callable[_P, _R]) -> Callable[_P, _R]:
        signature = inspect.signature(func)

        @functools.wraps(func)
        def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            if _already_active(surface, operation):
                return func(*args, **kwargs)
            arguments = _bind_arguments(
                signature,
                cast("tuple[object, ...]", args),
                cast("dict[str, object]", kwargs),
            )
            root = _project_root(arguments, captured_inputs=analysis_dsl)
            if surface == "analysis" and operation == "session.get_or_create":
                try:
                    load_project_config(root)
                except (OSError, ValueError):
                    # The owning preflight must fail before telemetry creates project state.
                    return func(*args, **kwargs)
            mode = _mode(root)
            if mode == "off":
                # Execution diagnostics remain active independently of usage telemetry.
                with _Operation(
                    surface=surface,
                    capability_id=operation,
                    capability_kind=kind,
                    root=root,
                    attributes=_session_attributes(arguments, include_question=False),
                    mode=mode,
                ) as active:
                    token = (
                        _LOAD_POLICY.set((active.root, mode))
                        if surface == "semantic" and operation == "load"
                        else None
                    )
                    try:
                        return func(*args, **kwargs)
                    finally:
                        if token is not None:
                            _LOAD_POLICY.reset(token)
            # DSL operands can contain literal business keys and values.
            attrs = (
                _session_attributes(arguments, include_question=False)
                if analysis_dsl
                else _input_attributes(operation, arguments)
            )
            attrs.update(_session_creation_attributes(root, operation, arguments))
            active = _Operation(
                surface=surface,
                capability_id=operation,
                capability_kind=kind,
                root=root,
                attributes=attrs,
                capture=policy,
                mode=mode,
                question_requested=(
                    arguments.get("question") is not None
                    and attrs.get("marivo.session.created") is True
                    if operation == "session.get_or_create"
                    else None
                ),
            )
            with active:
                if stage is None:
                    result = func(*args, **kwargs)
                else:
                    with telemetry_stage(stage):
                        result = func(*args, **kwargs)
                active.result = result
                return result

        wrapped.__dict__["__marivo_trace__"] = (surface, operation)
        return wrapped

    return decorate
