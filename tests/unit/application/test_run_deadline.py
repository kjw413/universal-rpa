from __future__ import annotations

import pytest

from universal_rpa.application.run_control import RunControl
from universal_rpa.domain.errors import ErrorCode, RpaError


def test_monotonic_deadline_blocks_further_work() -> None:
    now = [10.0]
    control = RunControl(lambda: now[0])
    control.configure_deadline(5)
    now[0] = 15.0

    with pytest.raises(RpaError) as caught:
        control.raise_if_cancelled()

    assert caught.value.code is ErrorCode.CONDITION_TIMEOUT
