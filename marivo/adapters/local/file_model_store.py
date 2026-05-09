from __future__ import annotations

import contextlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

import yaml

from marivo.contracts.errors import ErrorCode, ValidationError
from marivo.contracts.ids import ModelId, RevisionId, UserId
from marivo.contracts.semantic import ModelSummary, SemanticModel
from marivo.ports.model_store import ModelListQuery, ModelSelector


class _Selector:
    """Concrete ModelSelector implementation for FileModelStore."""

    def __init__(
        self,
        *,
        model_id: ModelId | None = None,
        name: str | None = None,
        revision: str | None = None,
    ) -> None:
        self.model_id = model_id
        self.name = name
        self.revision = revision


class _ListQuery:
    """Concrete ModelListQuery implementation for FileModelStore."""

    def __init__(
        self,
        *,
        owner: UserId | None = None,
        visibility: str | None = None,
        include_public: bool = True,
        include_private: bool = False,
    ) -> None:
        self.owner = owner
        self.visibility = visibility
        self.include_public = include_public
        self.include_private = include_private


class FileModelStore:
    """File-backed ModelStore using .marivo/models/ directory.

    - Auto-detects YAML (.yaml/.yml) vs JSON (.json) by extension
    - mtime-based cache: get() checks st_mtime on each call
    - Atomic write via tmp-<uuid> temp file then os.rename
    - Single-user: no owner/visibility filtering
    - Auto-assigns incrementing ModelId integers on save
    """

    def __init__(self, models_dir: Path) -> None:
        self._dir = models_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, tuple[float, SemanticModel]] = {}
        self._next_id: int = 1
        self._name_to_id: dict[str, ModelId] = {}

    def get(self, selector: ModelSelector) -> SemanticModel | None:
        name = getattr(selector, "name", None)
        if name is None:
            # Try model_id lookup
            model_id = getattr(selector, "model_id", None)
            if model_id is None:
                return None
            name = self._id_to_name(model_id)
            if name is None:
                return None

        path = self._find_file(name)
        if path is None:
            return None
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        cached = self._cache.get(name)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        try:
            content = self._read_file(path)
        except Exception as e:
            raise ValidationError(
                code=ErrorCode.VALIDATION,
                message=f"Model file '{path}' is invalid: {e}",
            ) from e
        model = SemanticModel(**content)
        self._cache[name] = (mtime, model)
        return model

    def save(
        self,
        model: SemanticModel,
        *,
        actor: UserId,
        expected_revision: RevisionId | None = None,
    ) -> ModelId:
        name = model.name
        existing = self._find_file(name)

        if existing is not None:
            # Reuse existing ModelId
            model_id = self._name_to_id.get(name, ModelId(self._next_id))
            if name not in self._name_to_id:
                self._name_to_id[name] = model_id
                self._next_id += 1
        else:
            # Assign new ModelId
            model_id = ModelId(self._next_id)
            self._name_to_id[name] = model_id
            self._next_id += 1

        content = model.model_dump(mode="json")
        # Inject model_id into persisted content
        content["model_id"] = model_id
        path = self._dir / f"{name}.yaml"
        self._atomic_write(path, yaml.dump(content, default_flow_style=False, sort_keys=False))

        # Invalidate cache
        self._cache.pop(name, None)
        return model_id

    def list(self, query: ModelListQuery) -> list[ModelSummary]:
        results: list[ModelSummary] = []
        for path in sorted(self._dir.iterdir()):
            if path.suffix not in (".yaml", ".yml", ".json"):
                continue
            try:
                content = self._read_file(path)
                model = SemanticModel(**content)
                model_id = self._name_to_id.get(model.name, ModelId(content.get("model_id", 0)))
                results.append(
                    ModelSummary(
                        model_id=model_id,
                        name=model.name,
                        revision=model.revision,
                        description=model.description,
                        visibility=model.visibility,
                        owner=model.owner,
                    )
                )
            except Exception:
                continue  # skip malformed files in listing
        return results

    # --- Private helpers ---

    def _id_to_name(self, model_id: ModelId) -> str | None:
        for name, mid in self._name_to_id.items():
            if mid == model_id:
                return name
        return None

    def _find_file(self, name: str) -> Path | None:
        for ext in (".yaml", ".yml", ".json"):
            path = self._dir / f"{name}{ext}"
            if path.is_file():
                return path
        return None

    def _read_file(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            data: dict[str, Any] = json.loads(text)
            return data
        result: dict[str, Any] = yaml.safe_load(text)
        return result

    def _atomic_write(self, path: Path, content: str) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp_path = self._dir / f"tmp-{uuid.uuid4().hex[:12]}"
        try:
            tmp_path.write_text(content, encoding="utf-8")
            os.replace(str(tmp_path), str(path))
        except BaseException:
            with contextlib.suppress(OSError):
                tmp_path.unlink()
            raise
