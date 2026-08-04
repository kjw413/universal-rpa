"""Launchable entry point for the deterministic Windows test harness.

``python -m samples.test_harness --state-file <path> --ready-file <path>`` starts
one window from the source checkout.  The ready file is written only after the
window is mapped and its native handle exists, so a fixture that waits for it can
rely on the HWND it publishes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtWidgets import QApplication

from samples.test_harness.main_window import HarnessWindow
from samples.test_harness.state import HarnessStateFile


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    state_file: Path
    ready_file: Path
    delayed_control_ms: int = 500
    duplicate_selector: bool = False
    intentional_timeout: bool = False
    lock_output: bool = False
    #: The file kept open with an exclusive share mode when ``lock_output`` is set.
    lock_output_path: Path | None = None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="samples.test_harness")
    parser.add_argument("--state-file", required=True, type=Path)
    parser.add_argument("--ready-file", required=True, type=Path)
    parser.add_argument("--delayed-control-ms", type=int, default=500)
    parser.add_argument("--duplicate-selector", action="store_true")
    parser.add_argument("--intentional-timeout", action="store_true")
    parser.add_argument("--lock-output", action="store_true")
    parser.add_argument("--lock-output-path", type=Path, default=None)
    return parser


def parse_config(argv: Sequence[str] | None = None) -> HarnessConfig:
    parsed = _build_parser().parse_args(list(argv) if argv is not None else None)
    return HarnessConfig(
        state_file=parsed.state_file,
        ready_file=parsed.ready_file,
        delayed_control_ms=parsed.delayed_control_ms,
        duplicate_selector=parsed.duplicate_selector,
        intentional_timeout=parsed.intentional_timeout,
        lock_output=parsed.lock_output,
        lock_output_path=parsed.lock_output_path,
    )


def create_harness_window(config: HarnessConfig) -> HarnessWindow:
    """Build the window and its state file without starting an event loop."""

    state = HarnessStateFile(config.state_file)
    window = HarnessWindow(config, state)
    window.seed_korean_text()
    return window


def _open_exclusive_lock(path: Path) -> object | None:
    """Hold *path* open so a concurrent writer observes a real Windows lock."""

    try:
        import msvcrt

        handle = path.open("a+b")
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except (ImportError, OSError):
        return None
    return handle


def _write_ready_file(config: HarnessConfig, window: HarnessWindow) -> None:
    payload = {
        "process_id": os.getpid(),
        "top_level_hwnd": int(window.winId()),
        "state_file": str(config.state_file),
        "duplicate_selector": config.duplicate_selector,
        "intentional_timeout": config.intentional_timeout,
        "lock_output": config.lock_output,
    }
    temporary = config.ready_file.with_suffix(config.ready_file.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, config.ready_file)


def main(argv: Sequence[str] | None = None) -> int:
    config = parse_config(argv)
    application = QApplication.instance() or QApplication(sys.argv[:1])
    window = create_harness_window(config)
    lock: object | None = None
    if config.lock_output and config.lock_output_path is not None:
        lock = _open_exclusive_lock(config.lock_output_path)
    window.ready.connect(lambda: _write_ready_file(config, window))
    window.show()
    window.raise_()
    window.activateWindow()
    window.start()
    try:
        return int(application.exec())  # type: ignore[union-attr]
    finally:
        if lock is not None:
            with suppress(OSError):
                lock.close()  # type: ignore[attr-defined]


__all__ = ["HarnessConfig", "create_harness_window", "main", "parse_config"]
