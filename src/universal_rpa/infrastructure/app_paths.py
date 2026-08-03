from __future__ import annotations

import os
from pathlib import Path


def default_recordings_root(local_app_data: Path | None = None) -> Path:
    """Return the private application-data directory for raw recordings."""

    if local_app_data is None:
        configured = os.environ.get("LOCALAPPDATA")
        if not configured:
            raise RuntimeError("LOCALAPPDATA is required to store recording sessions")
        base = Path(configured)
    else:
        base = Path(local_app_data)
    return base / "UniversalRPAStudio" / "recordings"


__all__ = ["default_recordings_root"]
