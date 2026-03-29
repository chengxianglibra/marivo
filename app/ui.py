from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def register_ui(app: FastAPI, *, static_dir: Path, admin_enabled: bool, user_enabled: bool) -> None:
    if not (admin_enabled or user_enabled):
        return

    if admin_enabled:

        @app.get("/admin")
        def admin_index() -> FileResponse:
            return FileResponse(static_dir / "admin.html")

    if user_enabled:

        @app.get("/ui")
        def ui_index() -> FileResponse:
            return FileResponse(static_dir / "user.html")

    app.mount("/static", StaticFiles(directory=static_dir), name="static")
