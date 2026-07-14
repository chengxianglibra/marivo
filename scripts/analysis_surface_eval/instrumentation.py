"""Instrumentation generator for the analysis surface evaluation gate.

Generates a temporary ``sitecustomize.py`` that wraps the allowed help
entries (``mv.help``, ``mv.help_text``, and the CLI ``help analysis``
subcommand) and registered analysis callables before the agent starts.

The probe writes append-only JSONL events to a configured path and never
changes return values or errors.  ``HelpTargetError`` is classified as both
a help invocation and an invalid-API error.  Ordinary ``AttributeError``
from ``describe``/``plot`` access on Marivo artifacts is observed and
recorded without being caught or changed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# sitecustomize.py template
# ---------------------------------------------------------------------------

_SITECUSTOMIZE_TEMPLATE = '''\
"""Auto-generated evaluation instrumentation probe.

This module is installed as ``sitecustomize.py`` in the fixture project's
analysis virtual environment.  It wraps the allowed help entries and
registered analysis callables, recording events as JSONL lines.

The probe never changes return values or errors.  It writes append-only
events to the configured events file path.
"""

from __future__ import annotations

import builtins
import functools
import inspect
import json
import sys
import time
from pathlib import Path

_EVENTS_FILE = Path({events_file_str!r})
_TRIAL = {trial!r}
_CASE_ID = {case_id!r}

# Flags for observe phase tracking.
_observe_phase = "before_observe"


def _now() -> float:
    return time.time()


def _append_event(event: dict) -> None:
    event["trial"] = _TRIAL
    event["case_id"] = _CASE_ID
    event.setdefault("timestamp", _now())
    with open(_EVENTS_FILE, "a") as f:
        f.write(json.dumps(event) + "\\n")


def _wrap_help(func, surface_name):
    """Wrap a help callable to record help invocations before resolution."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        target = args[0] if args else kwargs.get("target")
        target_str = str(target) if target is not None else None
        _append_event({{
            "kind": "help_invocation",
            "target": target_str,
            "observe_phase": _observe_phase,
            "is_help_target_error": False,
            "detail": surface_name,
        }})
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            exc_type_name = type(exc).__name__
            if exc_type_name == "HelpTargetError":
                _append_event({{
                    "kind": "invalid_api",
                    "target": target_str,
                    "observe_phase": _observe_phase,
                    "is_help_target_error": True,
                    "detail": f"HelpTargetError via {{surface_name}}",
                }})
            raise

    return wrapper


def _wrap_analysis_callable(func, callable_name, receiver_family=None):
    """Wrap a registered analysis callable to record API calls."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        _append_event({{
            "kind": "analysis_api_call",
            "target": callable_name,
            "receiver_family": receiver_family,
            "observe_phase": _observe_phase,
        }})
        result = func(*args, **kwargs)
        return result

    return wrapper


def _wrap_observe(func):
    """Wrap session.observe to detect the first correct observe."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        global _observe_phase
        _append_event({{
            "kind": "analysis_api_call",
            "target": "session.observe",
            "receiver_family": "Session",
            "observe_phase": _observe_phase,
        }})
        result = func(*args, **kwargs)
        _append_event({{
            "kind": "correct_observe",
            "observe_phase": _observe_phase,
        }})
        _observe_phase = "after_observe"
        return result

    return wrapper


def _install_help_wrappers():
    """Install wrappers on marivo.analysis help functions."""
    try:
        import marivo.analysis as mv
    except Exception:
        return
    if hasattr(mv, "help"):
        mv.help = _wrap_help(mv.help, "mv.help")
    if hasattr(mv, "help_text"):
        mv.help_text = _wrap_help(mv.help_text, "mv.help_text")


def _install_observe_wrapper():
    """Install wrapper on Session.observe and all analysis callables."""
    try:
        from marivo.analysis.session.core import Session
    except Exception:
        return
    if hasattr(Session, "observe"):
        Session.observe = _wrap_observe(Session.observe)

    # Wrap all registered analysis callables so that calls via compare,
    # attribute, correlate, etc. are detected as analysis API calls.
    _analysis_method_names = (
        "compare",
        "attribute",
        "correlate",
        "hypothesis_test",
        "forecast",
        "assess_quality",
        "derive_metric_frame",
    )
    for method_name in _analysis_method_names:
        if hasattr(Session, method_name):
            original = getattr(Session, method_name)
            wrapped = _wrap_analysis_callable(
                original, f"session.{{method_name}}", "Session"
            )
            setattr(Session, method_name, wrapped)


def _install_attribute_error_probe():
    """Install a probe that observes AttributeError from describe/plot.

    The probe does NOT catch or change the AttributeError.  It only records
    the event by hooking into __getattr__ on BaseFrame.  When the retired
    name is accessed, the event is emitted and then the AttributeError
    propagates normally.
    """
    try:
        from marivo.analysis.frames.base import BaseFrame
    except Exception:
        return

    original_getattr = getattr(BaseFrame, "__getattr__", None)
    _retired_names = {{"describe", "plot"}}

    if original_getattr is None:
        def _probed_getattr(self, name):
            if name in _retired_names:
                _append_event({{
                    "kind": "retired_name_attribute_error",
                    "target": name,
                    "receiver_family": type(self).__name__,
                    "observe_phase": _observe_phase,
                }})
            raise AttributeError(name)

        BaseFrame.__getattr__ = _probed_getattr
    else:
        def _probed_getattr(self, name):
            if name in _retired_names:
                _append_event({{
                    "kind": "retired_name_attribute_error",
                    "target": name,
                    "receiver_family": type(self).__name__,
                    "observe_phase": _observe_phase,
                }})
            return original_getattr(self, name)

        BaseFrame.__getattr__ = _probed_getattr


def _record_fingerprint():
    """Record the Marivo version and environment fingerprint."""
    try:
        import marivo
        version = getattr(marivo, "__version__", "unknown")
    except Exception:
        version = "unknown"
    _append_event({{
        "kind": "fingerprint",
        "fingerprint": f"version={{version}} executable={{sys.executable}}",
        "fingerprint_matched": None,
        "observe_phase": _observe_phase,
    }})


def _install_native_reflection_guard():
    """Detect native reflection used for Marivo contract discovery.

    Wraps builtins.dir and inspect.getmembers to flag when they are used
    on Marivo modules or objects.
    """
    _original_dir = builtins.dir

    @functools.wraps(builtins.dir)
    def _probed_dir(*args, **kwargs):
        result = _original_dir(*args, **kwargs)
        if args:
            obj = args[0]
            mod = getattr(obj, "__module__", "") or ""
            name = getattr(obj, "__name__", "") or ""
            if "marivo" in mod or "marivo" in name:
                _append_event({{
                    "kind": "invalid_api",
                    "target": "dir()",
                    "observe_phase": _observe_phase,
                    "is_help_target_error": False,
                    "detail": "native reflection via dir() on marivo object",
                }})
        return result

    builtins.dir = _probed_dir

    _original_getmembers = inspect.getmembers

    @functools.wraps(inspect.getmembers)
    def _probed_getmembers(*args, **kwargs):
        result = _original_getmembers(*args, **kwargs)
        if args:
            obj = args[0]
            mod = getattr(obj, "__module__", "") or ""
            name = getattr(obj, "__name__", "") or ""
            qualname = getattr(obj, "__qualname__", "") or ""
            if (
                "marivo" in mod
                or "marivo" in name
                or "marivo" in qualname
            ):
                _append_event({{
                    "kind": "invalid_api",
                    "target": "inspect.getmembers()",
                    "observe_phase": _observe_phase,
                    "is_help_target_error": False,
                    "detail": "native reflection via inspect.getmembers() on marivo object",
                }})
        return result

    inspect.getmembers = _probed_getmembers


_install_help_wrappers()
_install_observe_wrapper()
_install_attribute_error_probe()
_install_native_reflection_guard()
_record_fingerprint()
'''


@dataclass(frozen=True)
class InstrumentationConfig:
    """Configuration for the generated ``sitecustomize.py``.

    Parameters
    ----------
    events_file:
        Absolute path to the JSONL events file.
    trial:
        Zero-based trial index.
    case_id:
        Case identifier (``"clean_convergence"`` or ``"environment_skew"``).
    """

    events_file: Path
    trial: int
    case_id: str

    def __repr__(self) -> str:
        return (
            f"InstrumentationConfig(trial={self.trial} "
            f"case={self.case_id} events={self.events_file})"
        )


def generate_sitecustomize(
    dest: Path,
    config: InstrumentationConfig,
) -> Path:
    """Generate a ``sitecustomize.py`` probe file.

    Parameters
    ----------
    dest:
        Directory in which to write ``sitecustomize.py``.
    config:
        Instrumentation configuration.

    Returns
    -------
    Path
        The path to the generated ``sitecustomize.py``.

    Example:
        >>> from pathlib import Path
        >>> import tempfile
        >>> with tempfile.TemporaryDirectory() as d:
        ...     cfg = InstrumentationConfig(
        ...         events_file=Path(d) / "events.jsonl",
        ...         trial=0,
        ...         case_id="clean_convergence",
        ...     )
        ...     p = generate_sitecustomize(Path(d), cfg)
        ...     p.name
        'sitecustomize.py'
    """
    dest.mkdir(parents=True, exist_ok=True)
    content = _SITECUSTOMIZE_TEMPLATE.format(
        events_file_str=str(config.events_file),
        trial=config.trial,
        case_id=config.case_id,
    )
    target = dest / "sitecustomize.py"
    target.write_text(content)
    return target


def parse_event_line(line: str) -> dict[str, object]:
    """Parse a single JSONL event line into a dict.

    Parameters
    ----------
    line:
        A single line from the events JSONL file.

    Returns
    -------
    dict
        Parsed event dictionary.

    Raises
    ------
    json.JSONDecodeError
        If the line is not valid JSON.
    """
    return json.loads(line)
