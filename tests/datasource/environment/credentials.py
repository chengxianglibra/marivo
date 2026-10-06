"""Private credentials for the isolated multi-datasource test environment."""

from pathlib import Path


def password() -> str:
    """Read the shared fixture password without printing or persisting it."""
    for line in (Path.home() / ".cache/marivo-multisource/secrets.env").read_text().splitlines():
        if line.startswith("QUALIFICATION_PASSWORD="):
            return line.split("=", 1)[1]
    raise ValueError("Missing private qualification password")
