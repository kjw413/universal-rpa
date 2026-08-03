from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer

from tests.helpers.recording_fakes import (
    FakeInputCapture,
    InMemoryRecordingStore,
    StaticWindowContext,
    recording_target,
)
from universal_rpa.application.normalization import NormalizationService
from universal_rpa.application.recording import RecordingService, RecordingState
from universal_rpa.ui.recorder_page import RecorderPage


class SlowFinalizeStore(InMemoryRecordingStore):
    def finalize(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        time.sleep(0.25)
        return super().finalize(*args, **kwargs)  # type: ignore[arg-type]


def test_slow_recording_stop_does_not_block_qt_timer(qtbot: object, tmp_path: Path) -> None:
    del tmp_path
    store = SlowFinalizeStore()
    capture = FakeInputCapture()
    context = StaticWindowContext()
    service = RecordingService(capture=capture, context=context, store=store)
    page = RecorderPage(context, service, NormalizationService(), store)
    qtbot.addWidget(page)  # type: ignore[attr-defined]
    page.show()
    page.set_targets((recording_target(),))
    page.target_combo.setCurrentIndex(0)
    completed: list[object] = []
    page.recording_completed.connect(completed.append)
    ticks: list[int] = []
    timer = QTimer(page)
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()

    qtbot.mouseClick(page.start_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    qtbot.waitUntil(lambda: service.state is RecordingState.RECORDING, timeout=2_000)  # type: ignore[attr-defined]
    qtbot.mouseClick(page.stop_button, Qt.MouseButton.LeftButton)  # type: ignore[attr-defined]
    qtbot.waitUntil(lambda: bool(completed), timeout=3_000)  # type: ignore[attr-defined]

    assert len(ticks) >= 5
    page.close()
