from __future__ import annotations

import pytest

from universal_rpa.application.run_control import RunControl
from universal_rpa.domain.errors import ErrorCode, RpaError


def test_cancellation_has_priority_over_pause() -> None:
    control = RunControl()
    control.pause()
    control.cancel()

    with pytest.raises(RpaError) as caught:
        control.wait_if_paused()

    assert caught.value.code is ErrorCode.CANCELLED


def test_resume_releases_pause_without_cancelling() -> None:
    control = RunControl()
    control.pause()
    control.resume()

    control.wait_if_paused()
    assert not control.is_cancelled()
