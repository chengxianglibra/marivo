"""Shared assertions for datasource and semantic terminal result protocols."""

from marivo.render import _DEFAULT_MAX_OUTPUT_BYTES, AgentResult

REPR_MAX_LEN = 200
RENDER_MAX_LINES = 1000
RENDER_MAX_CHARS = _DEFAULT_MAX_OUTPUT_BYTES


def assert_conforms(obj: object) -> None:
    assert isinstance(obj, AgentResult)

    r = repr(obj)
    assert "\n" not in r, f"repr must be single-line: {r!r}"
    assert len(r) <= REPR_MAX_LEN, f"repr too long ({len(r)}): {r!r}"
    assert type(obj).__name__ in r, f"repr must name the type: {r!r}"

    rendered = obj.render()
    assert isinstance(rendered, str)
    assert not rendered.endswith("\n"), "render() must not end with newline"
    assert len(rendered.splitlines()) <= RENDER_MAX_LINES
    assert len(rendered.encode("utf-8")) <= RENDER_MAX_CHARS, (
        f"render() too large ({len(rendered.encode('utf-8'))} bytes)"
    )

    assert obj.show() is None
