from __future__ import annotations

from pathlib import Path

import pytest

from universal_rpa.infrastructure.app_paths import default_recordings_root


def test_default_recording_root_is_local_app_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\operator\AppData\Local")
    assert default_recordings_root() == Path(
        r"C:\Users\operator\AppData\Local\UniversalRPAStudio\recordings"
    )


def test_default_recording_root_requires_local_app_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(RuntimeError, match="LOCALAPPDATA"):
        default_recordings_root()
