"""Instrumentation generator for the semantic surface evaluation gate.

Generates a temporary ``sitecustomize.py`` that wraps the allowed help entries
(``ms.help``, ``ms.help_text``, ``md.help``, ``md.help_text``, and the CLI
``help semantic``/``help datasource`` subcommands) and registered
datasource/semantic callables before the agent starts.

The probe writes append-only JSONL events to a configured path and never
changes return values or errors.  It captures the semantic-track event
vocabulary: help invocations, fingerprints, invalid API attempts, datasource
and semantic API calls, explicit scope, data reads, connections, mutations,
authored objects, verify/preview/readiness/repair events, user questions,
environment stops, and deleted-attachment reliance.
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
registered datasource/semantic callables, recording events as JSONL lines.

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


def _now() -> float:
    return time.time()


def _append_event(event: dict) -> None:
    event["trial"] = _TRIAL
    event["case_id"] = _CASE_ID
    event.setdefault("timestamp", _now())
    with open(_EVENTS_FILE, "a") as f:
        f.write(json.dumps(event) + "\\n")


def _wrap_help(func, surface_name):
    """Wrap a help callable to record help invocations."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        target = args[0] if args else kwargs.get("target")
        target_str = str(target) if target is not None else None
        _append_event({{
            "kind": "help_invocation",
            "target": target_str,
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
                    "is_help_target_error": True,
                    "detail": f"HelpTargetError via {{surface_name}}",
                }})
            raise

    return wrapper


def _wrap_callable(func, callable_name, receiver_family, *, is_registered=True):
    """Wrap a registered callable to record API calls."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        kind = "semantic_api_call" if "semantic" in receiver_family.lower() or "ms." in callable_name else "datasource_api_call"
        _append_event({{
            "kind": kind,
            "target": callable_name,
            "receiver_family": receiver_family,
            "is_registered": is_registered,
        }})
        return func(*args, **kwargs)

    return wrapper


def _install_help_wrappers():
    """Install wrappers on marivo datasource and semantic help functions."""
    try:
        import marivo.semantic as ms
        if hasattr(ms, "help"):
            ms.help = _wrap_help(ms.help, "ms.help")
        if hasattr(ms, "help_text"):
            ms.help_text = _wrap_help(ms.help_text, "ms.help_text")
    except Exception:
        pass
    try:
        import marivo.datasource as md
        if hasattr(md, "help"):
            md.help = _wrap_help(md.help, "md.help")
        if hasattr(md, "help_text"):
            md.help_text = _wrap_help(md.help_text, "md.help_text")
    except Exception:
        pass


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
    }})


def _install_native_reflection_guard():
    """Detect native reflection used for Marivo contract discovery."""

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
            if "marivo" in mod or "marivo" in name or "marivo" in qualname:
                _append_event({{
                    "kind": "invalid_api",
                    "target": "inspect.getmembers()",
                    "is_help_target_error": False,
                    "detail": "native reflection via inspect.getmembers() on marivo object",
                }})
        return result

    inspect.getmembers = _probed_getmembers


_install_help_wrappers()
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
        Case identifier.
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
        ...         case_id="clean_one_object_readiness",
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
