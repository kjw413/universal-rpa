"""Launch, ready-wait, gating, and verified teardown for the harness process.

Interactive end-to-end coverage sends real native input to a real desktop.  That
is only meaningful on a logged-in, unlocked session, and it is actively harmful
anywhere else — on a headless or locked runner the input lands in whatever window
happens to hold focus.  ``require_interactive_desktop`` therefore refuses to run
unless the operator opted in with ``RPA_INTERACTIVE_DESKTOP=1``; a hosted CI job
must deselect ``windows_e2e`` rather than set the variable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from samples.test_harness.state import HarnessState, HarnessStateFile

#: Opt-in for tests that send native input to the real desktop.
INTERACTIVE_DESKTOP_ENV = "RPA_INTERACTIVE_DESKTOP"
READY_TIMEOUT_SECONDS = 30.0
SHUTDOWN_TIMEOUT_SECONDS = 10.0

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def require_interactive_desktop() -> None:
    """Skip unless an operator explicitly opted this session in."""

    if os.environ.get(INTERACTIVE_DESKTOP_ENV) != "1":
        pytest.skip(
            f"{INTERACTIVE_DESKTOP_ENV}=1 is required; interactive Windows E2E runs only on a "
            "logged-in, unlocked [self-hosted, windows, x64, rpa-interactive] session"
        )
    if sys.platform != "win32":
        pytest.skip("interactive Windows E2E requires a Windows desktop")


@dataclass(frozen=True, slots=True)
class HarnessOptions:
    delayed_control_ms: int = 500
    duplicate_selector: bool = False
    intentional_timeout: bool = False
    lock_output_path: Path | None = None


class HarnessProcess:
    """A launched harness, its published identity, and its observable state."""

    def __init__(self, root: Path, options: HarnessOptions) -> None:
        self._root = root
        self._options = options
        self._state_file = root / "harness-state.json"
        self._ready_file = root / "harness-ready.json"
        self._process: subprocess.Popen[bytes] | None = None
        self._identity: dict[str, object] = {}

    # -- lifecycle --------------------------------------------------------
    def launch(self) -> None:
        if self._process is not None:
            raise RuntimeError("harness is already running")
        command = [
            sys.executable,
            "-m",
            "samples.test_harness",
            "--state-file",
            str(self._state_file),
            "--ready-file",
            str(self._ready_file),
            "--delayed-control-ms",
            str(self._options.delayed_control_ms),
        ]
        if self._options.duplicate_selector:
            command.append("--duplicate-selector")
        if self._options.intentional_timeout:
            command.append("--intentional-timeout")
        if self._options.lock_output_path is not None:
            command.extend(
                ["--lock-output", "--lock-output-path", str(self._options.lock_output_path)]
            )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(REPOSITORY_ROOT)
        environment.pop("QT_QPA_PLATFORM", None)
        # Fixed argv assembled from this repository; nothing user-supplied.
        self._process = subprocess.Popen(
            command,
            cwd=str(REPOSITORY_ROOT),
            env=environment,
        )
        self._identity = self._await_ready()

    def _await_ready(self) -> dict[str, object]:
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            process = self._process
            if process is not None and process.poll() is not None:
                raise RuntimeError(f"harness exited early with code {process.returncode}")
            if self._ready_file.is_file():
                try:
                    payload = json.loads(self._ready_file.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    time.sleep(0.05)
                    continue
                if isinstance(payload, dict) and payload.get("top_level_hwnd"):
                    return payload
            time.sleep(0.05)
        raise TimeoutError("harness did not become ready in time")

    def terminate(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        if process.poll() is None:  # pragma: no cover - kill already waited
            raise RuntimeError("harness process could not be terminated")

    # -- observation ------------------------------------------------------
    @property
    def root(self) -> Path:
        return self._root

    @property
    def project_dir(self) -> Path:
        directory = self._root / "project"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    @property
    def output_dir(self) -> Path:
        directory = self._root / "output"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    @property
    def process_id(self) -> int:
        return int(self._identity["process_id"])  # type: ignore[arg-type]

    @property
    def top_level_hwnd(self) -> int:
        return int(self._identity["top_level_hwnd"])  # type: ignore[arg-type]

    @property
    def state_file(self) -> Path:
        return self._state_file

    @property
    def state(self) -> HarnessState:
        return HarnessStateFile.read(self._state_file)

    def await_state(self, predicate: object, timeout: float = 10.0) -> HarnessState:
        deadline = time.monotonic() + timeout
        observed = self.state
        while time.monotonic() < deadline:
            observed = self.state
            if predicate(observed):  # type: ignore[operator]
                return observed
            time.sleep(0.05)
        return observed

    def configure(self, **options: object) -> None:
        """Relaunch with different options; the harness is configured at start."""

        self.terminate()
        self._ready_file.unlink(missing_ok=True)
        object.__setattr__(self, "_options", HarnessOptions(**options))  # type: ignore[arg-type]
        self.launch()


def _window_rect(hwnd: int) -> tuple[int, int, int, int]:
    import win32gui  # type: ignore[import-untyped]

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    return left, top, right - left, bottom - top


def _move_window(hwnd: int, left: int, top: int, width: int, height: int) -> None:
    import win32gui  # type: ignore[import-untyped]

    win32gui.MoveWindow(hwnd, left, top, width, height, True)


@pytest.fixture
def harness_options() -> HarnessOptions:
    """Overridable per-test launch options."""

    return HarnessOptions()


@pytest.fixture
def move_harness_window() -> object:
    """Move the harness window without changing its client size."""

    def move(process: HarnessProcess, *, dx: int, dy: int) -> None:
        left, top, width, height = _window_rect(process.top_level_hwnd)
        _move_window(process.top_level_hwnd, left + dx, top + dy, width, height)
        time.sleep(0.2)

    return move


@pytest.fixture
def resize_harness_window() -> object:
    """Resize the harness window past the 2 % coordinate tolerance."""

    def resize(process: HarnessProcess, *, width: int, height: int) -> None:
        left, top, _, _ = _window_rect(process.top_level_hwnd)
        _move_window(process.top_level_hwnd, left, top, width, height)
        time.sleep(0.2)

    return resize


@pytest.fixture
def harness(tmp_path: Path, harness_options: HarnessOptions) -> Iterator[HarnessProcess]:
    require_interactive_desktop()
    process = HarnessProcess(tmp_path, harness_options)
    process.launch()
    try:
        yield process
    finally:
        process.terminate()


__all__ = [
    "INTERACTIVE_DESKTOP_ENV",
    "HarnessOptions",
    "HarnessProcess",
    "require_interactive_desktop",
]
