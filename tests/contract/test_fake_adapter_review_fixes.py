from __future__ import annotations

import pytest

from tests.contract.automation_adapter_contract import (
    execution_context,
    fake_target,
    runtime_environment,
)
from tests.contract.test_fake_adapter import supported_request
from universal_rpa.adapters.fake import FakeAutomationAdapter
from universal_rpa.domain.errors import ErrorCode
from universal_rpa.ports.automation import (
    ActionRequest,
    AdapterActionResult,
    CancellationToken,
)


@pytest.mark.parametrize(
    ("precheck", "expected_code"),
    (
        ("cancelled", ErrorCode.CANCELLED),
        ("unknown", ErrorCode.ACTION_UNSUPPORTED),
        ("invalid", ErrorCode.INVALID_SCHEMA),
        ("zero_matches", ErrorCode.TARGET_NOT_FOUND),
        ("two_matches", ErrorCode.TARGET_AMBIGUOUS),
    ),
)
def test_action_prechecks_preserve_fifo_and_record_no_call(
    precheck: str,
    expected_code: ErrorCode,
) -> None:
    adapter = FakeAutomationAdapter()
    sentinel = AdapterActionResult(output={"sentinel": True}, evidence={})
    adapter.script.append(sentinel)
    request = supported_request(adapter)
    parameters: dict[str, object] = {}
    action_type = request.action_type
    cancellation = CancellationToken()
    if precheck == "cancelled":
        cancellation.cancel()
    elif precheck == "unknown":
        action_type = "fake.unknown"
    elif precheck == "invalid":
        parameters["invalid"] = True
    elif precheck == "zero_matches":
        parameters["match_count"] = 0
    else:
        parameters["match_count"] = 2
    guarded_request = ActionRequest(
        action_type=action_type,
        target=request.target,
        parameters=parameters,  # type: ignore[arg-type]
        value=None,
        has_postcondition_or_assertion=True,
    )

    result = adapter.execute(guarded_request, execution_context(), cancellation)

    assert result.error_code is expected_code
    assert tuple(adapter.script) == (sentinel,)
    assert adapter.calls == ()


@pytest.mark.parametrize("match_count", (0, 2))
def test_deferred_target_validation_accepts_valid_non_live_match_counts(
    match_count: int,
) -> None:
    adapter = FakeAutomationAdapter()
    target = fake_target(adapter.adapter_id).model_copy(
        update={"payload": {"match_count": match_count}}
    )

    issues = adapter.validate_target(target, runtime_environment(), "deferred")

    assert issues == ()
    assert adapter.calls == ()


def test_deferred_target_validation_rejects_malformed_payload_without_live_lookup() -> None:
    adapter = FakeAutomationAdapter()
    malformed = fake_target(adapter.adapter_id).model_copy(
        update={"payload": {"match_count": "bad"}}
    )

    issues = adapter.validate_target(
        malformed,
        runtime_environment(),
        "deferred",
    )

    assert [issue.code for issue in issues] == [ErrorCode.INVALID_SCHEMA]
    assert adapter.calls == ()
