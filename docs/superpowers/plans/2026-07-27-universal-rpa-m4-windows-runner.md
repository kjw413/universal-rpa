# Universal RPA M4 Windows Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 검증된 workflow를 fail-closed로 실행하는 Windows runner를 완성해 UIA-first target resolution, guarded input, state wait, assertion, limited retry, depth-two loops, cancellation, checkpoint, resume를 제공한다.

**Architecture:** `PreflightService`가 모든 입력 전 readiness를 검사한다. `WorkflowExecutor`는 target resolve → foreground guard → precondition → action → postcondition/assertion → result 순서를 고정하고, 모든 native input 직전에 guard를 다시 호출한다. `RunControl`과 monotonic clock을 주입해 pause/cancel/wait/retry를 결정론적으로 테스트한다.

**Tech Stack:** M1–M3, pywinauto UIA, pywin32, Windows Credential Manager, pytest fakes, Python threading/time.

## Global Constraints

- M1–M3 completion gate 및 review가 먼저 통과해야 한다.
- preflight 오류 하나라도 있으면 adapter `execute`와 input driver call count는 0이다.
- global input 직전 foreground executable/top-level HWND를 다시 검사한다.
- UIA selector는 정확히 한 요소일 때만 성공이다.
- coordinate fallback은 여섯 범주—기록 창 identity(process executable + window class), exact DPI, client size ±2%, foreground, in-bounds, finite postcondition 또는 compatible assertion—가 모두 참이어야 한다.
- absolute recording coordinate는 진단용이며 resolver가 읽어 실행 위치로 사용할 수 없다.
- wait는 monotonic finite timeout; retry 기본 0, 최대 3이며 registry가 고정한 descriptor의 idempotent action + 해당 retryable error 조합에만 허용한다.
- `Ctrl+Shift+F12` cancellation은 `Ctrl+Shift+F11` pause보다 우선하며 입력·poll·backoff 사이마다 확인한다.
- UAC secure desktop, locked/logged-off/noninteractive desktop, MFA/CAPTCHA를 조작하지 않는다.

---

### Task 1: Run request, RunControl, and zero-input preflight

**Files:**

- Create: `src/universal_rpa/domain/execution.py`
- Create: `src/universal_rpa/application/run_control.py`
- Create: `src/universal_rpa/application/preflight.py`
- Create: `tests/unit/application/test_run_control.py`
- Create: `tests/unit/application/test_preflight.py`

**Interfaces:**

- Produces: `RunInputs`, `RunRequest`, `ResumeRequest`, `ValidationReport`
- Consumes: M1 `RuntimeEnvironment`
- Produces: `RunControl.pause/resume/cancel/wait_if_paused/raise_if_cancelled`
- Produces: `PreflightService.check`
- Consumes: M3 ValidationService and all M1 ports

- [ ] **Step 1: Write failing zero-input and cancellation-priority tests**

```python
@pytest.mark.parametrize(
    "failure",
    ["schema", "adapter", "window", "target", "variable", "secret", "output", "lock"],
)
def test_any_preflight_failure_sends_zero_input(failure: str) -> None:
    spies = preflight_spies(failure=failure)
    report = PreflightService(spies.dependencies).check(spies.request)
    assert not report.is_valid
    assert spies.input_driver.calls == []
    assert spies.adapter.execute_calls == 0


def test_cancel_has_priority_over_pause() -> None:
    control = RunControl()
    control.pause()
    control.cancel()
    with pytest.raises(RunCancelled):
        control.wait_if_paused()


def test_validation_only_never_executes() -> None:
    request = valid_run_request(validation_only=True)
    dependencies = fake_dependencies()
    outcome = PreflightService(dependencies).check(request)
    assert outcome.is_valid
    assert dependencies.adapter.execute_calls == 0
```

- [ ] **Step 2: Run focused tests and confirm failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/application/test_run_control.py tests/unit/application/test_preflight.py -q
```

Expected: FAIL because runtime models/control/preflight are absent.

- [ ] **Step 3: Implement immutable request models and cooperative control**

```python
class RunInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    variable_values: FrozenMapping[str, DataCell] = Field(
        default_factory=FrozenMapping.empty
    )
    output_directory: Path


class ResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: UUID

class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    workflow: Workflow
    project_dir: Path
    inputs: RunInputs
    resume: ResumeRequest | None = None
    validation_only: bool = False

class RunControl(CancellationToken):
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def cancel(self) -> None: ...
    def wait_if_paused(self) -> None: ...
    def raise_if_cancelled(self) -> None: ...
```

`RunInputs.variable_values` contains only raw, strictly parsed non-secret scalar
run selections; a field validator defensively deep-freezes it at construction.
Secret variables resolve from workflow credential references and never enter this
model. Resolve `output_directory` to a user-selected existing root, reject device/
reparse ambiguity, and pass it as `ExecutionContext.output_root`. Use `threading.Event` plus a `Condition`; cancellation wakes paused workers and
always raises first. M3 `ValidationService` remains the only owner of the design's
eight static/environment checks. `PreflightService.check` calls `validate_static`,
stops if it has errors, then calls `validate_environment`, concatenates issues in
stable path order, and returns the canonical `ValidationReport`. It adds no duplicate
validation rules and cannot depend on input driver or adapter `execute`.

- [ ] **Step 4: Run preflight, validation, and quality checks**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/application/test_run_control.py tests/unit/application/test_preflight.py tests/unit/application/test_validation.py -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass and every parameterized preflight failure has zero side
effects.

- [ ] **Step 5: Commit run control and preflight**

```powershell
git add src/universal_rpa/domain/execution.py src/universal_rpa/application/run_control.py src/universal_rpa/application/preflight.py tests/unit/application/test_run_control.py tests/unit/application/test_preflight.py
git commit -m "feat(universal-rpa): add fail-closed run preflight"
```

---

### Task 2: Interactive environment, foreground guard, and target resolver

**Files:**

- Create: `src/universal_rpa/adapters/windows/environment.py`
- Create: `src/universal_rpa/adapters/windows/foreground.py`
- Create: `src/universal_rpa/adapters/windows/target_resolver.py`
- Create: `tests/unit/adapters/windows/test_environment.py`
- Create: `tests/unit/adapters/windows/test_foreground.py`
- Create: `tests/unit/adapters/windows/test_target_resolver.py`

**Interfaces:**

- Produces: `WindowsEnvironmentProbe.snapshot/require_interactive_desktop`
- Produces: `ForegroundGuard.verify`
- Produces: `WindowsTargetResolver.resolve`
- Produces: `ResolvedUiaTarget | ResolvedCoordinateTarget`

- [ ] **Step 1: Write seven failure cases covering the six coordinate-guard categories**

```python
@pytest.mark.parametrize(
    "mismatch",
    [
        "process_executable",
        "window_class",
        "dpi",
        "client_size_over_two_percent",
        "not_foreground",
        "point_outside_client",
        "verification_missing",
    ],
)
def test_each_coordinate_guard_blocks_resolution(mismatch: str) -> None:
    resolver, target, runtime = coordinate_case(mismatch=mismatch)
    with pytest.raises(RpaError) as caught:
        resolver.resolve(
            target,
            runtime,
            has_postcondition_or_assertion=mismatch != "verification_missing",
        )
    assert caught.value.code in {
        ErrorCode.ENVIRONMENT_MISMATCH,
        ErrorCode.FOREGROUND_MISMATCH,
    }
    assert resolver.native_click_calls == 0


def test_unique_uia_match_wins_even_after_window_move() -> None:
    resolver, target, runtime = uia_case(matches=1, moved=True)
    resolved = resolver.resolve(
        target, runtime, has_postcondition_or_assertion=True
    )
    assert isinstance(resolved, ResolvedUiaTarget)


def test_absolute_diagnostic_point_is_never_read() -> None:
    target = windows_target(diagnostic_absolute_x=9999, diagnostic_absolute_y=9999)
    resolved = resolver_with_no_uia().resolve(
        target, matching_runtime(), has_postcondition_or_assertion=True
    )
    assert resolved.screen_point != (9999, 9999)
```

- [ ] **Step 2: Run resolver tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/adapters/windows/test_environment.py tests/unit/adapters/windows/test_foreground.py tests/unit/adapters/windows/test_target_resolver.py -q
```

Expected: FAIL because resolver/guard are absent.

- [ ] **Step 3: Implement UIA-first resolution and exact fallback arithmetic**

```python
@dataclass(frozen=True, slots=True)
class WindowIdentity:
    process_id: int
    process_executable: str
    top_level_hwnd: int
    window_class: str

@dataclass(frozen=True, slots=True)
class ResolvedUiaTarget:
    window: WindowIdentity
    element: object

@dataclass(frozen=True, slots=True)
class ResolvedCoordinateTarget:
    window: WindowIdentity
    client_point: tuple[int, int]
    screen_point: tuple[int, int]

ResolvedTarget = ResolvedUiaTarget | ResolvedCoordinateTarget

class WindowsEnvironmentProbe:
    def snapshot(self, hwnd: int) -> RuntimeEnvironment: ...
    def require_interactive_desktop(self) -> None: ...

class ForegroundGuard:
    def verify(self, expected: WindowIdentity) -> None: ...

class WindowsTargetResolver:
    def resolve(
        self,
        target: TargetSpec,
        runtime: RuntimeEnvironment,
        *,
        has_postcondition_or_assertion: bool,
    ) -> ResolvedTarget: ...
```

Parse `WindowsTarget` from `TargetSpec.payload`. Query UIA using
automation ID, control type, name, class, and stable ancestor path; return only
for exactly one match. For 0 or 2+ matches, evaluate fallback guards.

Use exact executable basename and window class comparisons, exact integer DPI
X/Y, and:

```python
width_ok = abs(current_width - recorded_width) / recorded_width <= 0.02
height_ok = abs(current_height - recorded_height) / recorded_height <= 0.02
client_x = round(relative_x * (current_width - 1))
client_y = round(relative_y * (current_height - 1))
```

Require foreground HWND to equal expected top-level window, point inside client
bounds, and a finite postcondition or compatible assertion. Convert client to screen coordinates
only after all checks. `require_interactive_desktop` fails on locked,
disconnected, secure, or non-input desktops.

- [ ] **Step 4: Run resolver and existing Windows context tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/adapters/windows -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass; changing any one guard yields zero click calls.

- [ ] **Step 5: Commit the target safety boundary**

```powershell
git add src/universal_rpa/adapters/windows/environment.py src/universal_rpa/adapters/windows/foreground.py src/universal_rpa/adapters/windows/target_resolver.py tests/unit/adapters/windows
git commit -m "feat(universal-rpa): add guarded Windows target resolver"
```

---

### Task 3: Foreground-guarded Windows actions, text/IME fallback, and credentials

**Files:**

- Create: `src/universal_rpa/adapters/windows/input_driver.py`
- Create: `src/universal_rpa/adapters/windows/text_input.py`
- Create: `src/universal_rpa/adapters/windows/credentials.py`
- Create: `src/universal_rpa/adapters/windows/adapter.py`
- Create: `tests/unit/adapters/windows/test_input_driver.py`
- Create: `tests/unit/adapters/windows/test_text_input.py`
- Create: `tests/unit/adapters/windows/test_credentials.py`
- Create: `tests/contract/test_windows_adapter.py`

**Interfaces:**

- Produces: `WindowsAutomationAdapter` with adapter ID `windows`
- Produces: `TextInputStrategy.set_text`
- Produces: `WindowsCredentialStore` implementing `SecretStorePort`
- Consumes: resolver, foreground guard, M1 contract suite

- [ ] **Step 1: Write failing focus-theft, IME-order, and secret tests**

```python
def test_focus_theft_between_resolution_and_click_blocks_native_input() -> None:
    driver = input_driver(foreground_sequence=[EXPECTED_WINDOW, OTHER_WINDOW])
    adapter = windows_adapter(driver=driver)
    result = adapter.execute(click_request(), execution_context(), CancellationToken())
    assert result.error_code is ErrorCode.FOREGROUND_MISMATCH
    assert driver.native_calls == []


def test_text_strategy_order_is_value_set_edit_paste_then_keys() -> None:
    strategies = failing_text_strategies(success_at="paste")
    result = TextInputStrategy(strategies).set_text(
        editable_target(), "생산실적", verify=True
    )
    assert strategies.calls == ["uia_value", "set_edit_text", "paste"]
    assert result.verified is True


def test_secret_value_has_redacted_repr_and_no_string_conversion() -> None:
    secret = SecretValue.from_text("never-log-this")
    assert "never-log-this" not in repr(secret)
    with pytest.raises(TypeError):
        str(secret)

def test_windows_descriptor_locks_verification_retry_and_assertion_metadata() -> None:
    descriptor = windows_adapter().descriptor()
    assert descriptor.implementation_version == WINDOWS_ADAPTER_VERSION
    assert descriptor.supports_target_capture is True
    assert descriptor.verification_by_action["windows.click"] == "postcondition_or_assertion"
    assert descriptor.verification_by_action["windows.wait"] == "intrinsic"
    assert "windows.click" not in descriptor.idempotent_actions
    assert descriptor.retryable_errors_by_action["windows.set_text"] == frozenset(
        {ErrorCode.ACTION_FAILED}
    )
    assert descriptor.assertions_by_action["windows.set_text"] == frozenset(
        {"windows.value_equals", "windows.value_contains"}
    )


def test_windows_validation_is_pure_and_uses_canonical_parameter_models() -> None:
    adapter = windows_adapter()
    adapter.validate_action_spec(action_step("windows.drag", DragParameters(...)))
    adapter.validate_action_spec(action_step("windows.scroll", ScrollParameters(...)))
    adapter.validate_action_spec(action_step("windows.press_key", PressKeyParameters(...)))
    adapter.validate_action_spec(action_step("windows.hotkey", HotkeyParameters(...)))
    assert adapter.native_calls == []
    with pytest.raises(RpaError) as caught:
        adapter.validate_action_spec(action_step("windows.drag", NoParameters()))
    assert caught.value.code is ErrorCode.INVALID_SCHEMA
```

- [ ] **Step 2: Run action/credential tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/adapters/windows/test_input_driver.py tests/unit/adapters/windows/test_text_input.py tests/unit/adapters/windows/test_credentials.py tests/contract/test_windows_adapter.py -q
```

Expected: FAIL because action adapter and secret wrapper do not exist.

- [ ] **Step 3: Implement guarded actions and non-serializable secret values**

```python
WINDOWS_ADAPTER_VERSION = "1.0.0"

class WindowsAutomationAdapter(AutomationAdapter):
    @property
    def adapter_id(self) -> str:
        return "windows"

    def capture_target(
        self,
        request: TargetCaptureRequest,
        cancellation: CancellationToken,
    ) -> TargetCaptureResult:
        return self._target_capture.capture_target(request, cancellation)

@dataclass(frozen=True, slots=True)
class TextInputResult:
    strategy: Literal["uia_value", "set_edit_text", "paste", "direct_keys"]
    verified: bool
    evidence: FrozenJsonObject

class TextInputStrategy:
    def set_text(
        self,
        target: ResolvedTarget,
        value: str | SecretValue,
        *,
        verify: bool,
    ) -> TextInputResult: ...

class WindowsCredentialStore(SecretStorePort):
    def exists(self, reference: str) -> bool: ...
    def read(self, reference: str) -> SecretValue: ...
    def write(self, reference: str, value: SecretValue) -> None: ...
    def delete(self, reference: str) -> None: ...
```

Support exactly:

- `windows.activate_window`
- `windows.click`
- `windows.double_click`
- `windows.drag`
- `windows.scroll`
- `windows.set_text`
- `windows.press_key`
- `windows.hotkey`
- `windows.wait`

The Windows descriptor uses `implementation_version="1.0.0"`, declares
`supports_target_capture=True`, and declares
conditions `windows.element_exists`, `windows.element_visible`,
`windows.element_enabled`, `windows.window_exists`, `windows.value_equals`,
`windows.value_contains`, and `windows.fixed_delay`, plus assertions
`windows.value_equals` and `windows.value_contains`. All mouse/key/text/activate
actions require a postcondition or compatible assertion; `windows.wait` is
intrinsically verified and sends no input. Only `windows.activate_window` and
`windows.set_text` are idempotent; only `ErrorCode.ACTION_FAILED` is retryable for
`windows.set_text`. Both Windows assertions have input kind `json` and are
compatible only with descriptor-listed actions. `evaluate_assertion` re-reads
the resolved target and returns redacted evidence. The adapter delegates
`capture_target` to M2's Windows target-capture component so M3 and runtime share
one contract. Its pure `validate_action_spec`, `validate_condition_spec`, and
`validate_assertion_spec` methods perform no UIA/native work and validate every
built-in action against the canonical M1 parameter model and exact target/value
requirements. ExecutionService evaluates `WaitSpec` through ConditionPoller.

`WindowsInputDriver` calls `ForegroundGuard.verify` immediately before every
native mouse/keyboard operation, including each fallback strategy. Text order is
UIA ValuePattern, pywinauto `set_edit_text`, guarded clipboard paste, guarded
direct keys. Re-read editable value when available; mismatch is failure.
Preserve and restore prior clipboard without logging it. Password text is exposed only inside the bounded `SecretValue.reveal()` context; its
bytearray is zeroed best-effort on exit and it never supports repr/string/JSON. Do not
claim Python or Windows can guarantee complete in-memory erasure.
Use Windows Credential Manager generic credentials with a product-prefixed
target name; workflow stores only the reference.

- [ ] **Step 4: Run shared adapter contract and secret scan tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/adapters/windows tests/contract/test_windows_adapter.py tests/contract/test_fake_adapter.py -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass; error/evidence/repr/model dumps contain no test secret.

- [ ] **Step 5: Commit Windows actions and credentials**

```powershell
git add src/universal_rpa/adapters/windows tests/unit/adapters/windows tests/contract/test_windows_adapter.py
git commit -m "feat(universal-rpa): execute guarded Windows actions"
```

---

### Task 4: Clipboard change detection, table parsing, and safe evidence

**Files:**

- Create: `src/universal_rpa/adapters/clipboard/__init__.py`
- Create: `src/universal_rpa/adapters/clipboard/adapter.py`
- Create: `src/universal_rpa/adapters/clipboard/table_parser.py`
- Modify: `src/universal_rpa/bootstrap.py`
- Create: `tests/unit/test_bootstrap_registry.py`
- Create: `tests/unit/adapters/clipboard/test_adapter.py`
- Create: `tests/unit/adapters/clipboard/test_table_parser.py`
- Create: `tests/contract/test_clipboard_adapter.py`

**Interfaces:**

- Produces: `ClipboardAutomationAdapter` with adapter ID `clipboard`
- Produces: `ClipboardSnapshot`, `parse_clipboard_table`
- Consumes: M1 `TableData`
- Produces: bootstrap registry containing exactly `windows` and `clipboard` at M4
- Supports actions `clipboard.read_clipboard`, `clipboard.extract_table`; condition
  `clipboard.clipboard_changed`; assertion `clipboard.table`

- [ ] **Step 1: Write failing stale-data and evidence tests**

```python
def test_unchanged_clipboard_never_returns_stale_text_as_success() -> None:
    adapter = clipboard_adapter(sequence_numbers=[10, 10, 10])
    observation = adapter.evaluate_condition(
        clipboard_changed_condition(previous_sequence=10),
        execution_context(),
        CancellationToken(),
    )
    assert observation.satisfied is False
    assert observation.evidence["sequence_number"] == 10


def test_extraction_evidence_has_shape_and_hash_but_no_body() -> None:
    result = clipboard_adapter(text="공장\t수량\nA\t3").execute(
        extract_table_request(), execution_context(), CancellationToken()
    )
    assert result.evidence["headers"] == ["공장", "수량"]
    assert result.evidence["row_count"] == 1
    assert "공장\t수량" not in json.dumps(result.evidence, ensure_ascii=False)


def test_malformed_rows_are_rejected() -> None:
    with pytest.raises(RpaError) as caught:
        parse_clipboard_table("a\tb\n1")
    assert caught.value.code is ErrorCode.DATA_SOURCE_INVALID


def test_bootstrap_registers_m4_builtin_adapters() -> None:
    assert set(build_services().adapter_registry.adapter_ids()) == {"windows", "clipboard"}

def test_clipboard_descriptor_routes_table_assertion_only_to_extraction() -> None:
    descriptor = clipboard_adapter().descriptor()
    assert descriptor.implementation_version == CLIPBOARD_ADAPTER_VERSION
    assert descriptor.assertions_by_action["clipboard.extract_table"] == frozenset(
        {"clipboard.table"}
    )
    assert descriptor.assertions_by_action["clipboard.read_clipboard"] == frozenset()
    assert descriptor.assertion_input_kind["clipboard.table"] == "table"
```

- [ ] **Step 2: Run clipboard tests and confirm failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/adapters/clipboard tests/unit/test_bootstrap_registry.py tests/contract/test_clipboard_adapter.py -q
```

Expected: FAIL with missing adapter/parser.

- [ ] **Step 3: Implement sequence-based reads and immutable table data**

```python
@dataclass(frozen=True, slots=True)
class ClipboardSnapshot:
    sequence_number: int
    text_length: int
    sha256: str
    formats: tuple[str, ...]

def parse_clipboard_table(
    text: str,
    *,
    delimiter: Literal["auto", "tab", "comma"] = "auto",
) -> TableData: ...
```

Use Win32 clipboard sequence number and bounded open retries. Auto delimiter
chooses tab when the first nonempty line contains tab, otherwise CSV comma
parsing. Require nonempty unique headers and equal row widths. Strip only line
terminators, not field whitespace. Evidence includes sequence, length, SHA-256,
formats, headers, row count; never body/cells. Map clipboard lock and format
errors to stable safe errors. The descriptor version is `1.0.0`; both read
operations are idempotent but retry only declared clipboard-lock
`ACTION_FAILED`. Both require explicit verification, and `clipboard.table`
(input kind `table`) is compatible only with `clipboard.extract_table`.
`evaluate_assertion` owns table checks through the namespaced adapter contract.
Update `bootstrap.py` to register one Windows adapter and
one clipboard adapter through the same `AdapterRegistry`; fail startup on duplicate IDs.

- [ ] **Step 4: Run clipboard and adapter contracts**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/adapters/clipboard tests/contract -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass and unchanged sequence cannot return old text as fresh.

- [ ] **Step 5: Commit the clipboard adapter**

```powershell
git add src/universal_rpa/adapters/clipboard src/universal_rpa/bootstrap.py tests/unit/adapters/clipboard tests/unit/test_bootstrap_registry.py tests/contract/test_clipboard_adapter.py
git commit -m "feat(universal-rpa): add safe clipboard extraction"
```

---

### Task 5: Finite condition polling, assertions, and retry

**Files:**

- Create: `src/universal_rpa/application/conditions.py`
- Create: `tests/unit/application/test_condition_poller.py`
- Create: `tests/unit/application/test_assertions.py`
- Create: `tests/unit/application/test_retry.py`

**Interfaces:**

- Produces: `ConditionPoller.wait`, `AssertionEvaluator.evaluate`,
  `RetryExecutor.execute`
- Consumes: adapter registry, fake clock, RunControl, domain policies

- [ ] **Step 1: Write failing timeout, cancellation, and retry tests**

```python
def test_wait_uses_monotonic_deadline() -> None:
    clock = FakeClock()
    poller = ConditionPoller(registry=fake_registry(always_false=True), clock=clock)
    with pytest.raises(RpaError) as caught:
        poller.wait(wait_spec(timeout_ms=300, poll_interval_ms=100), context(), RunControl())
    assert caught.value.code is ErrorCode.CONDITION_TIMEOUT
    assert clock.monotonic_seconds == pytest.approx(0.3)


def test_cancel_stops_before_next_poll() -> None:
    control = RunControl()
    registry = registry_that_cancels_after_first_poll(control)
    with pytest.raises(RunCancelled):
        ConditionPoller(registry, FakeClock()).wait(wait_spec(), context(), control)
    assert registry.poll_count == 1


def test_retry_policy_on_non_idempotent_capability_fails_before_operation() -> None:
    operation = Mock()
    with pytest.raises(RpaError) as caught:
        RetryExecutor(FakeClock()).execute(
            retry_policy(1),
            action_capability(idempotent=False),
            operation,
            RunControl(),
        )
    assert caught.value.code is ErrorCode.INVALID_SCHEMA
    operation.assert_not_called()


def test_idempotent_action_does_not_retry_undeclared_error() -> None:
    operation = Mock(return_value=failed_adapter_result(ErrorCode.TARGET_NOT_FOUND))
    outcome = RetryExecutor(FakeClock()).execute(
        retry_policy(3),
        action_capability(
            idempotent=True,
            retryable_errors=frozenset({ErrorCode.ACTION_FAILED}),
        ),
        operation,
        RunControl(),
    )
    assert outcome.attempt_count == 1
    operation.assert_called_once()


def test_cancellation_during_backoff_prevents_next_attempt() -> None:
    control = RunControl()
    clock = cancelling_clock(control)
    operation = Mock(return_value=failed_adapter_result(ErrorCode.ACTION_FAILED))
    with pytest.raises(RunCancelled):
        RetryExecutor(clock).execute(
            retry_policy(3),
            action_capability(
                idempotent=True,
                retryable_errors=frozenset({ErrorCode.ACTION_FAILED}),
            ),
            operation,
            control,
        )
    operation.assert_called_once()


def test_retry_count_three_means_four_total_attempts() -> None:
    operation = Mock(return_value=failed_adapter_result(ErrorCode.ACTION_FAILED))
    outcome = RetryExecutor(FakeClock()).execute(
        retry_policy(3),
        action_capability(
            idempotent=True,
            retryable_errors=frozenset({ErrorCode.ACTION_FAILED}),
        ),
        operation,
        RunControl(),
    )
    assert outcome.attempt_count == 4
    assert operation.call_count == 4


def test_assertion_dispatches_to_namespace_owner_with_declared_subject_kind() -> None:
    adapter = assertion_spy(input_kind="table")
    evaluator = AssertionEvaluator(registry_with(adapter))
    outcome = evaluator.evaluate(
        "clipboard.extract_table",
        table_assertion(),
        table_data(),
        target=None,
        context=execution_context(),
        control=RunControl(),
    )
    assert outcome.passed
    assert adapter.assertion_calls == [("clipboard.table", "table")]
```

- [ ] **Step 2: Run condition tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/application/test_condition_poller.py tests/unit/application/test_assertions.py tests/unit/application/test_retry.py -q
```

Expected: FAIL with missing services.

- [ ] **Step 3: Implement polling and table assertions**

```python
@dataclass(frozen=True, slots=True)
class AssertionOutcome:
    passed: bool
    error_code: ErrorCode | None
    safe_message: str
    evidence: FrozenJsonObject

@dataclass(frozen=True, slots=True)
class AttemptOutcome:
    result: AdapterActionResult
    attempt_count: int

@dataclass(frozen=True, slots=True)
class AdapterActionCapability:
    action_type: str
    idempotent: bool
    retryable_errors: frozenset[ErrorCode]

class ConditionPoller:
    def wait(
        self,
        wait: WaitSpec,
        context: ExecutionContext,
        control: RunControl,
    ) -> ConditionObservation: ...

class AssertionEvaluator:
    def evaluate(
        self,
        action_type: str,
        assertion: AssertionSpec | TableAssertionSpec,
        subject: FrozenJsonValue | TableData | OutputCommit | None,
        target: TargetSpec | None,
        context: ExecutionContext,
        control: RunControl,
    ) -> AssertionOutcome: ...

class RetryExecutor:
    def execute(
        self,
        policy: FailurePolicy,
        capability: AdapterActionCapability,
        operation: Callable[[], AdapterActionResult],
        control: RunControl,
    ) -> AttemptOutcome: ...
```

Use injected `monotonic()` and cancellable sleep sliced to at most 100 ms.
Dispatch condition namespace through registry. Implement design conditions:
element exists/visible/enabled, window exists, value equals/contains, clipboard
changed, file exists/stable, and fixed delay. `AssertionEvaluator` resolves the
assertion namespace through registry, verifies it is compatible with the action
and that the runtime subject matches descriptor `assertion_input_kind`, then
calls owner `evaluate_assertion`; it never hard-codes extension assertions.

Build `AdapterActionCapability` only from the immutable registered descriptor,
never workflow JSON. A retry policy on a non-idempotent action is a defensive
`INVALID_SCHEMA`; an idempotent action repeats only when the actual common
`ErrorCode` is in its declared retryable set. Undeclared errors run once.
Backoff is cancellable in at most 100ms slices. `retry_count` is the number of
additional retries: it is bounded at three, so total attempts never exceed four.

- [ ] **Step 4: Run focused and cumulative runner tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/application/test_condition_poller.py tests/unit/application/test_assertions.py tests/unit/application/test_retry.py -q
.\.venv\Scripts\python.exe -m pytest tests/unit/adapters tests/contract -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass using fake time with no wall-clock sleeps.

- [ ] **Step 5: Commit waits, assertions, and retry**

```powershell
git add src/universal_rpa/application/conditions.py tests/unit/application/test_condition_poller.py tests/unit/application/test_assertions.py tests/unit/application/test_retry.py
git commit -m "feat(universal-rpa): add finite waits and safe retry"
```

---

### Task 6: Variable preparation, value resolution, and depth-two loop planning

**Files:**

- Create: `src/universal_rpa/application/variable_preparation.py`
- Create: `src/universal_rpa/application/value_resolution.py`
- Create: `src/universal_rpa/application/loops.py`
- Create: `tests/unit/application/test_variable_preparation.py`
- Create: `tests/unit/application/test_value_resolution.py`
- Create: `tests/unit/application/test_loops.py`

**Interfaces:**

- Produces: `PreparedVariables`, `VariablePreparationService.prepare`
- Produces: `ValueResolver.resolve`
- Produces: `DataSourceSnapshot`, `IterationFrame`,
  `LoopPlanner.materialize_snapshots`, `LoopPlanner.iter_workflow_frames`
- Consumes: M1 `PreparedValue`, `FrozenMapping`, `DateContext`, `LoopCursor`,
  variable-source/value-spec unions, `ExecutionContext`; M3 `DataSourcePort`;
  M4 `RunInputs`; `SecretStorePort`

- [ ] **Step 1: Write failing source-matrix, row-scope, and hard-limit tests**

```python
@pytest.mark.parametrize(
    ("definition", "raw_input", "expected"),
    [
        (run_input_variable("name", "text"), "  공장 A  ", "공장 A"),
        (run_input_variable("count", "integer"), "42", 42),
        (run_input_variable("ratio", "decimal"), "1.25", Decimal("1.25")),
        (run_input_variable("day", "date"), "2026-07-27", date(2026, 7, 27)),
        (run_input_variable("folder", "path"), "inputs", Path("inputs")),
        (fixed_default_variable("prefix", "text", "LOT-"), None, "LOT-"),
        (inline_choice_variable("shift", ("주간", "야간")), "야간", "야간"),
    ],
)
def test_prepare_supports_typed_run_fixed_and_inline_sources(
    definition: VariableDefinition,
    raw_input: DataCell | None,
    expected: PreparedValue,
    tmp_path: Path,
) -> None:
    inputs = run_inputs({definition.variable_id: raw_input} if raw_input is not None else {})
    prepared = preparation_service().prepare(
        workflow_with_variables(definition),
        inputs,
        tmp_path,
        DateContext(today=date(2026, 7, 27), run_date=date(2026, 7, 27)),
        FrozenMapping.empty(),
        spy_secret_store(),
    )
    assert prepared.values[definition.variable_id] == expected


def test_date_rule_and_credential_reference_are_prepared_without_secret_read(
    tmp_path: Path,
) -> None:
    secrets = spy_secret_store(existing={"universal-rpa/pilot"})
    prepared = preparation_service().prepare(
        workflow_with_variables(
            date_rule_variable("month_end", operation="month_end"),
            credential_variable("password", "universal-rpa/pilot"),
        ),
        run_inputs({}),
        tmp_path,
        DateContext(today=date(2026, 7, 27), run_date=date(2026, 7, 27)),
        FrozenMapping.empty(),
        secrets,
    )
    assert prepared.values["month_end"] == date(2026, 7, 31)
    assert prepared.credential_refs["password"] == "universal-rpa/pilot"
    assert secrets.exists_calls == ["universal-rpa/pilot"]
    assert secrets.read_calls == []


def test_data_column_choice_uses_the_same_materialized_snapshot(
    tmp_path: Path,
) -> None:
    source = mutable_csv_source(tmp_path, rows=(row("A"), row("B")))
    workflow = workflow_with_data_choice(source, variable_id="factory", column="factory")
    snapshots = loop_planner(source).materialize_snapshots(tmp_path, workflow)
    source.rewrite(rows=(row("C"),))
    prepared = preparation_service().prepare(
        workflow,
        run_inputs({"factory": "B"}),
        tmp_path,
        fixed_date_context(),
        snapshots,
        spy_secret_store(),
    )
    assert prepared.values["factory"] == "B"


@pytest.mark.parametrize(
    "case",
    [
        "missing_required_run_input",
        "unexpected_run_input",
        "wrong_scalar_type",
        "choice_not_in_options",
        "data_choice_not_in_snapshot",
        "missing_credential_reference",
    ],
)
def test_variable_preparation_fails_closed_before_input(case: str, tmp_path: Path) -> None:
    service, workflow, inputs, snapshots, secrets = invalid_variable_case(case, tmp_path)
    with pytest.raises(RpaError) as caught:
        service.prepare(workflow, inputs, tmp_path, fixed_date_context(), snapshots, secrets)
    assert caught.value.code in {
        ErrorCode.INVALID_SCHEMA,
        ErrorCode.DATA_SOURCE_INVALID,
        ErrorCode.SECRET_MISSING,
    }
    assert secrets.read_calls == []


def test_missing_row_column_fails_instead_of_becoming_empty_text() -> None:
    resolver = ValueResolver(secret_store=fake_secret_store())
    with pytest.raises(RpaError) as caught:
        resolver.resolve(
            RowBindingValue(template="{{ row.factory }}"),
            context(rows=(frozen_mapping({"date": "x"}),)),
        )
    assert caught.value.code is ErrorCode.DATA_SOURCE_INVALID


def test_inner_row_shadows_same_outer_column_but_keeps_other_outer_columns() -> None:
    context = execution_context(
        rows=(
            frozen_mapping({"factory": "outer", "period": "202607"}),
            frozen_mapping({"factory": "inner"}),
        )
    )
    resolver = ValueResolver(fake_secret_store())
    assert resolver.resolve(row_value("factory"), context) == "inner"
    assert resolver.resolve(row_value("period"), context) == "202607"


def test_cross_product_over_hard_limit_is_rejected_before_iteration(tmp_path: Path) -> None:
    workflow = workflow_with_nested_loops(outer_rows=101, inner_rows=100)
    planner = loop_planner()
    snapshots = planner.materialize_snapshots(tmp_path, workflow)
    with pytest.raises(RpaError) as caught:
        tuple(
            planner.iter_workflow_frames(
                workflow,
                execution_context(),
                run_policy(max_iterations=10_000),
                snapshots,
            )
        )
    assert caught.value.code is ErrorCode.DATA_SOURCE_INVALID


def test_parsed_data_snapshot_hash_changes_when_row_order_changes(tmp_path: Path) -> None:
    planner = loop_planner()
    first = planner.materialize_snapshots(
        tmp_path, workflow_with_inline_rows((row("A"), row("B")))
    )["source"]
    second = planner.materialize_snapshots(
        tmp_path, workflow_with_inline_rows((row("B"), row("A")))
    )["source"]
    assert first.content_sha256 != second.content_sha256


def test_one_workflow_api_handles_sequential_and_nested_typed_cursors(
    tmp_path: Path,
) -> None:
    workflow = workflow_with_sequential_and_nested_loops()
    planner = loop_planner()
    snapshots = planner.materialize_snapshots(tmp_path, workflow)
    frames = tuple(
        planner.iter_workflow_frames(
            workflow,
            execution_context(),
            run_policy(max_iterations=10_000),
            snapshots,
        )
    )
    assert all(isinstance(cursor, LoopCursor) for frame in frames for cursor in frame.cursor)
    assert frames[0].cursor[0].loop_step_id != frames[-1].cursor[0].loop_step_id
    assert max(len(frame.cursor) for frame in frames) == 2
```

- [ ] **Step 2: Run variable/loop tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/application/test_variable_preparation.py tests/unit/application/test_value_resolution.py tests/unit/application/test_loops.py -q
```

Expected: FAIL because preparation/resolver/planner are absent.

- [ ] **Step 3: Implement one preparation pass and one workflow loop API**

```python
@dataclass(frozen=True, slots=True)
class PreparedVariables:
    values: FrozenMapping[str, PreparedValue]
    credential_refs: FrozenMapping[str, str]


class VariablePreparationService:
    def prepare(
        self,
        workflow: Workflow,
        run_inputs: RunInputs,
        project_dir: Path,
        date_context: DateContext,
        snapshots: FrozenMapping[str, "DataSourceSnapshot"],
        secret_store: SecretStorePort,
    ) -> PreparedVariables: ...


class ValueResolver:
    def resolve(
        self,
        value: ValueSpec,
        context: ExecutionContext,
    ) -> FrozenJsonValue | PreparedValue | TableData | OutputCommit | SecretValue: ...


@dataclass(frozen=True, slots=True)
class DataSourceSnapshot:
    data_source_id: str
    source_type: Literal["inline", "csv", "xlsx"]
    headers: tuple[str, ...]
    rows: tuple[tuple[DataCell, ...], ...]
    content_sha256: str


@dataclass(frozen=True, slots=True)
class IterationFrame:
    iteration_path: tuple[int, ...]
    cursor: tuple[LoopCursor, ...]
    row_stack: tuple[FrozenMapping[str, DataCell], ...]


class LoopPlanner:
    def materialize_snapshots(
        self,
        project_dir: Path,
        workflow: Workflow,
    ) -> FrozenMapping[str, DataSourceSnapshot]: ...

    def iter_workflow_frames(
        self,
        workflow: Workflow,
        context: ExecutionContext,
        run_policy: RunPolicy,
        snapshots: FrozenMapping[str, DataSourceSnapshot],
    ) -> Iterator[IterationFrame]: ...
```

`VariablePreparationService.prepare` is the sole runtime-input preparation pass
and runs after immutable data snapshots are materialized but before preflight can
permit adapter execution. It iterates every `VariableDefinition` exactly once and
implements the exhaustive M1 source/type matrix:

- `run_input`: require exactly one user value and strictly parse text/date/integer/
  decimal/path; reject Boolean-as-integer, locale-ambiguous dates, NaN/infinity,
  blank text, absolute/device paths, and undeclared input IDs;
- `fixed_default`: strictly parse the persisted default without consulting
  `RunInputs`;
- `inline_options`: require the selected scalar in the persisted option tuple;
- `csv_column`/`xlsx_column`: require the user selection in the named column of
  the already-materialized matching source snapshot; never reopen the file;
- `date_rule`: evaluate the M1 whitelist using the one canonical `DateContext`;
- `credential_ref`: call only `SecretStorePort.exists`, put variable ID → reference
  in `credential_refs`, and never read, hash, measure, log, or serialize plaintext.

It returns only defensively deep-frozen `FrozenMapping` instances. `ExecutionService`
builds each `ExecutionContext` from `PreparedVariables.values`,
`PreparedVariables.credential_refs`, the same `DateContext`, normalized
`output_root`, current frozen row stack, and current-iteration frozen action
outputs. `ValueResolver` does no expression evaluation: literal returns itself;
variable lookup uses `variables` or, for a credential variable, reads the mapped
reference only at action resolution; row binding searches the frozen row stack
from inner to outer; date values are already prepared. It never formats or
interpolates a `SecretValue`.

Pass `RunRequest.project_dir` to every `DataSourcePort.preview/iter_rows` call.
Before any input, `materialize_snapshots` reads every source exactly once into a
frozen parsed `DataSourceSnapshot`, validates required columns, scalar cells and
limits, and hashes canonical UTF-8 JSON of source type + headers + row order +
cell values. `iter_workflow_frames` is the only public iteration API: it traverses
all enabled sequential top-level and nested loops depth-first from the supplied
snapshots and never reopens a source. It uses the canonical M1 `LoopCursor`
(no M4 duplicate), preserves loop-step UUID at each nesting level, keeps
`iteration_path` display-only, enforces maximum depth two, computes the full
cross-product bound before yielding, then rechecks total count and monotonic
runtime while iterating.

- [ ] **Step 4: Run variable/value/loop and domain regressions**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/application/test_variable_preparation.py tests/unit/application/test_value_resolution.py tests/unit/application/test_loops.py tests/unit/domain/test_workflow.py -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass; every source kind is prepared, secrets remain unread until
an action resolves them, and deterministic frames use only M1 cursors.

- [ ] **Step 5: Commit variable preparation, value resolution, and loops**

```powershell
git add src/universal_rpa/application/variable_preparation.py src/universal_rpa/application/value_resolution.py src/universal_rpa/application/loops.py tests/unit/application/test_variable_preparation.py tests/unit/application/test_value_resolution.py tests/unit/application/test_loops.py
git commit -m "feat(universal-rpa): prepare variables and plan loops"
```
---

### Task 7: Step lifecycle, step test, durable checkpoint/journal, resume, and run status

**Files:**

- Modify: `src/universal_rpa/domain/execution.py`
- Modify: `src/universal_rpa/ports/artifacts.py`
- Create: `src/universal_rpa/infrastructure/checkpoint_store.py`
- Create: `src/universal_rpa/infrastructure/execution_journal.py`
- Create: `src/universal_rpa/application/resume.py`
- Create: `src/universal_rpa/application/execution.py`
- Create: `tests/unit/infrastructure/test_checkpoint_store.py`
- Create: `tests/unit/infrastructure/test_execution_journal.py`
- Create: `tests/unit/application/test_resume.py`
- Create: `tests/unit/application/test_step_test.py`
- Create: `tests/unit/application/test_execution.py`
- Create: `tests/integration/test_fake_workflow_execution.py`

**Interfaces:**

- Produces: `Checkpoint`, `TerminalRunRecord`, `ResumeFingerprint`,
  `DataSourceFingerprint`, `AdapterFingerprint`
- Produces: `InProgressAction`, `InProgressIterationJournal`
- Produces: `RunStarted`, `RunActionObserved`, `RunObserver`
- Produces: `StepTestRequest`, `StepTestEligibility`
- Produces: `ResumeFingerprintBuilder.build`, `ResumeValidator.validate`
- Produces: `JsonCheckpointStore.load_active/save_active/mark_terminal/discover_active`
- Produces: `JsonExecutionJournalStore.load/save/clear`
- Produces: `ExecutionService.preflight/run/step_test_eligibility/test_step`
- Produces: ordered M1 `ActionResult` and final M1 `RunReport`, including run-level
  `error_code`/`safe_message` for failures before an action result exists
- Consumes: all M4 services, M1 `DateContext`/`LoopCursor`, and adapter registry

- [ ] **Step 1: Write failing lifecycle, status, step-test, journal, and resume tests**

```python
def test_action_lifecycle_order_is_fixed() -> None:
    trace: list[str] = []
    service = traced_execution_service(trace)
    service.test_step(
        StepTestRequest(
            run_request=valid_run_request(),
            step_id=ACTION_ID,
            cursor=(),
            date_context=fixed_date_context(),
        ),
        RunControl(),
    )
    assert trace == [
        "resolve_target",
        "foreground_guard",
        "precondition",
        "execute_action",
        "postcondition",
        "assertions",
        "record_result",
    ]


def test_absent_if_present_group_does_not_make_run_partial_or_execute_children() -> None:
    service = execution_service(if_present=False)
    report = service.run(valid_run_request(), RunControl())
    assert report.status == "success"
    assert any(result.skip_reason == "if_present_absent" for result in report.results)
    assert service.if_present_child_execute_calls == 0


def test_if_present_ambiguity_is_failure_not_optional_absence() -> None:
    report = execution_service(if_present_matches=2).run(valid_run_request(), RunControl())
    assert report.status == "failed"
    assert report.results[-1].error_code is ErrorCode.TARGET_AMBIGUOUS


@pytest.mark.parametrize(
    ("policy", "expected"),
    [("stop", "failed"), ("skip_iteration", "partial")],
)
def test_iteration_failure_maps_run_status(policy: str, expected: str) -> None:
    report = failing_execution_service(policy).run(valid_run_request(), RunControl())
    assert report.status == expected


def test_row_bound_step_test_reconstructs_exact_snapshot_and_row_stack(
    tmp_path: Path,
) -> None:
    request = row_bound_step_test_request(
        tmp_path,
        cursor=(LoopCursor(loop_step_id=LOOP_ID, row_index=4),),
    )
    service = execution_service()
    result = service.test_step(request, RunControl())
    assert result.iteration_cursor == request.cursor
    assert service.adapter.last_context.row_stack[-1]["factory"] == "F-005"
    assert service.loop_planner.open_count == 1


def test_row_bound_step_test_without_cursor_is_rejected_before_adapter() -> None:
    service = execution_service()
    request = row_bound_step_test_request(cursor=())
    with pytest.raises(RpaError) as caught:
        service.test_step(request, RunControl())
    assert caught.value.code is ErrorCode.INVALID_SCHEMA
    assert service.adapter.execute_calls == 0


@pytest.mark.parametrize("dependency", ["input_step_id", "prior_action_output"])
def test_step_test_with_unreconstructable_dependency_is_disabled(dependency: str) -> None:
    service = execution_service(workflow=workflow_with_dependency(dependency))
    eligibility = service.step_test_eligibility(
        step_test_request(step_id=DEPENDENT_STEP_ID, cursor=())
    )
    assert eligibility.enabled is False
    assert eligibility.reason_code == "requires_prior_action_output"
    assert service.adapter.execute_calls == 0


def test_resume_starts_after_matching_last_successful_loop_cursor() -> None:
    store = checkpoint_store_with_completed(
        cursor=(LoopCursor(loop_step_id=LOOP_ID, row_index=4),),
        fingerprint=matching_resume_fingerprint(),
    )
    report = execution_service(checkpoints=store).run(
        run_request(resume=resume_request()),
        RunControl(),
    )
    assert report.results[0].iteration_path == (5,)


@pytest.mark.parametrize(
    "mismatch",
    [
        "workflow_bytes_same_revision",
        "run_inputs",
        "output_root",
        "csv_row_order",
        "xlsx_snapshot",
        "adapter_version",
        "adapter_descriptor",
        "coordinate_environment",
        "output_missing",
        "output_hash",
        "output_row_count",
        "output_headers",
    ],
)
def test_resume_mismatch_is_run_level_failure_before_any_action(mismatch: str) -> None:
    service, spies = resume_execution_case(mismatch=mismatch)
    report = service.run(resume_run_request(), RunControl())
    assert report.status == "failed"
    assert report.error_code is ErrorCode.RESUME_MISMATCH
    assert report.results == ()
    assert spies.input_driver.calls == []
    assert spies.adapter.execute_calls == 0


def test_descriptor_order_does_not_change_canonical_fingerprint() -> None:
    assert fingerprint_for_registry(registry_order("windows", "clipboard")) == fingerprint_for_registry(
        registry_order("clipboard", "windows")
    )


def test_secret_plaintext_or_digest_never_enters_resume_fingerprint() -> None:
    secret_store = spy_secret_store(value="NEVER_FINGERPRINT")
    fingerprint = build_resume_fingerprint(secret_store=secret_store)
    encoded = fingerprint.model_dump_json()
    assert secret_store.read_calls == 0
    assert "NEVER_FINGERPRINT" not in encoded
    assert sha256(b"NEVER_FINGERPRINT").hexdigest() not in encoded


def test_resume_reuses_original_m1_date_context_after_midnight() -> None:
    checkpoint = matching_checkpoint(
        date_context=DateContext(
            today=date(2026, 7, 27),
            run_date=date(2026, 7, 27),
        )
    )
    service = execution_service(checkpoints=checkpoint, clock_date=date(2026, 7, 28))
    service.run(resume_run_request(), RunControl())
    assert service.value_resolver.date_context.run_date == date(2026, 7, 27)


def test_corrupt_or_unknown_checkpoint_is_run_level_failure() -> None:
    service, spies = corrupt_checkpoint_case(schema_version="999")
    report = service.run(resume_run_request(), RunControl())
    assert report.error_code is ErrorCode.CHECKPOINT_INVALID
    assert report.results == ()
    assert spies.input_driver.calls == []


@pytest.mark.parametrize("action_state", ["inflight", "succeeded"])
def test_non_idempotent_partial_iteration_resume_is_unsafe_before_input(
    action_state: str,
) -> None:
    journal = journal_with_action(
        cursor=(LoopCursor(loop_step_id=LOOP_ID, row_index=5),),
        step_id=CLICK_ID,
        action_type="windows.click",
        idempotent=False,
        state=action_state,
    )
    service, spies = resume_service_with_journal(journal)
    report = service.run(resume_run_request(), RunControl())
    assert report.status == "failed"
    assert report.error_code is ErrorCode.RESUME_UNSAFE
    assert report.results == ()
    assert spies.input_driver.calls == []


def test_idempotent_partial_iteration_replays_the_whole_iteration() -> None:
    journal = journal_with_action(
        cursor=(LoopCursor(loop_step_id=LOOP_ID, row_index=5),),
        step_id=SET_TEXT_ID,
        action_type="windows.set_text",
        idempotent=True,
        state="succeeded",
    )
    service = resume_service_with_journal(journal)[0]
    report = service.run(resume_run_request(), RunControl())
    replayed = [result for result in report.results if result.iteration_cursor == journal.cursor]
    assert replayed[0].step_id == FIRST_STEP_IN_ITERATION
    assert SET_TEXT_ID in {result.step_id for result in replayed}


def test_journal_flushes_inflight_before_action_and_success_after_action() -> None:
    trace: list[str] = []
    service = execution_service(trace=trace)
    service.run(one_action_request(), RunControl())
    assert trace.index("journal_flush_inflight") < trace.index("execute_action")
    assert trace.index("execute_action") < trace.index("journal_flush_succeeded")
    assert trace.index("checkpoint_flush") < trace.index("journal_clear")


def test_journal_serialization_contains_no_values_targets_or_secrets() -> None:
    journal = in_progress_journal()
    encoded = journal.model_dump_json()
    assert set(json.loads(encoded)) == {
        "journal_schema_version",
        "workflow_id",
        "run_id",
        "cursor",
        "actions",
        "started_at",
        "updated_at",
    }
    assert "password" not in encoded.casefold()
    assert "target" not in encoded.casefold()
    assert "variable" not in encoded.casefold()


def test_latest_output_commit_per_normalized_destination_is_checkpointed() -> None:
    old, latest = commits_for_same_destination_with_different_case()
    checkpoint = build_checkpoint(output_commits=(old, latest))
    assert checkpoint.output_commits == (latest,)


@pytest.mark.parametrize("status", ["success", "partial"])
def test_completed_run_marks_terminal_before_clearing_and_discovery_ignores_terminal(
    status: str,
) -> None:
    store, trace = tracing_checkpoint_store()
    report = execution_service(checkpoints=store, terminal_status=status).run(
        valid_run_request(), RunControl()
    )
    assert report.status == status
    assert trace[-2:] == ["mark_terminal", "journal_clear"]
    assert report.last_checkpoint_cursor is not None
    assert store.discover_active(report.workflow_id) == ()
    assert store.terminal_exists(report.workflow_id, report.run_id)


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_failure_or_cancellation_preserves_last_safe_checkpoint(status: str) -> None:
    store = checkpoint_store_with_completed(cursor=(LoopCursor(loop_step_id=LOOP_ID, row_index=3),))
    report = execution_service(checkpoints=store, terminal_status=status).run(
        valid_run_request(), RunControl()
    )
    assert report.status == status
    assert store.load_active(report.workflow_id, report.run_id).completed_cursor[-1].row_index == 3


def test_extraction_output_is_passed_to_save_table_in_same_iteration() -> None:
    adapters = scripted_extract_then_save()
    report = execution_service(adapters=adapters).run(extract_save_request(), RunControl())
    assert report.status == "success"
    assert adapters.tabular.saved_table == adapters.clipboard.extracted_table


def test_observer_receives_exact_action_runtime_and_frozen_boundaries() -> None:
    observer = SpyRunObserver()
    report = execution_service().run(valid_run_request(), RunControl(), observers=(observer,))
    action_events = [event for event in observer.events if isinstance(event, RunActionObserved)]
    assert [event.result for event in action_events] == list(report.results)
    assert action_events[0].expected_runtime.process_id == EXECUTED_PID
    assert action_events[0].expected_runtime.window_handle == EXECUTED_HWND
    assert action_events[0].target == executed_target_spec()
    with pytest.raises(TypeError):
        action_events[0].result.evidence["new"] = "mutation"
```

- [ ] **Step 2: Run execution tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/infrastructure/test_checkpoint_store.py tests/unit/infrastructure/test_execution_journal.py tests/unit/application/test_resume.py tests/unit/application/test_step_test.py tests/unit/application/test_execution.py tests/integration/test_fake_workflow_execution.py -q
```

Expected: FAIL because checkpoint, journal, typed step test, and executor are absent.

- [ ] **Step 3: Implement deterministic execution with two-layer durable state**

```python
class DataSourceFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    data_source_id: str
    source_type: Literal["inline", "csv", "xlsx"]
    row_count: int = Field(ge=0)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AdapterFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    adapter_id: str
    implementation_version: str
    descriptor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ResumeFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal["1"] = "1"
    workflow_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resolved_inputs_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    data_sources: tuple[DataSourceFingerprint, ...]
    adapters: tuple[AdapterFingerprint, ...]
    environment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    checkpoint_schema_version: Literal["1"] = "1"
    workflow_id: UUID
    workflow_revision: int
    run_id: UUID
    date_context: DateContext
    fingerprint: ResumeFingerprint
    completed_cursor: tuple[LoopCursor, ...]
    completed_at: datetime
    output_commits: tuple[OutputCommit, ...]


class TerminalRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    terminal_schema_version: Literal["1"] = "1"
    workflow_id: UUID
    run_id: UUID
    status: Literal["success", "partial"]
    finished_at: datetime
    last_checkpoint_cursor: tuple[LoopCursor, ...] | None


class InProgressAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    step_id: UUID
    action_type: str
    idempotent: bool
    state: Literal["inflight", "succeeded"]


class InProgressIterationJournal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    journal_schema_version: Literal["1"] = "1"
    workflow_id: UUID
    run_id: UUID
    cursor: tuple[LoopCursor, ...]
    actions: tuple[InProgressAction, ...]
    started_at: datetime
    updated_at: datetime


class StepTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_request: RunRequest
    step_id: UUID
    cursor: tuple[LoopCursor, ...]
    date_context: DateContext


class StepTestEligibility(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool
    reason_code: Literal[
        "ready",
        "requires_loop_cursor",
        "requires_prior_action_output",
        "disabled_step",
    ]


@dataclass(frozen=True, slots=True)
class RunStarted:
    run_id: UUID
    workflow_id: UUID
    workflow_name: str
    workflow_revision: int
    step_labels: FrozenMapping[UUID, str]
    started_at: datetime
    runtime: RuntimeEnvironment


@dataclass(frozen=True, slots=True)
class RunActionObserved:
    result: ActionResult
    target: TargetSpec | None
    expected_runtime: RuntimeEnvironment


class RunObserver(Protocol):
    def on_run_started(self, event: RunStarted) -> None: ...
    def on_action_result(self, event: RunActionObserved) -> None: ...
    def on_run_finished(self, report: RunReport) -> None: ...


class ResumeFingerprintBuilder:
    def build(
        self,
        request: RunRequest,
        prepared: PreparedVariables,
        snapshots: FrozenMapping[str, DataSourceSnapshot],
        registry: AdapterRegistry,
        runtime: RuntimeEnvironment,
        date_context: DateContext,
    ) -> ResumeFingerprint: ...


class ResumeValidator:
    def validate(
        self,
        checkpoint: Checkpoint,
        journal: InProgressIterationJournal | None,
        current: ResumeFingerprint,
    ) -> tuple[DateContext, tuple[LoopCursor, ...] | None]: ...


class ExecutionService:
    def preflight(self, request: RunRequest) -> ValidationReport: ...

    def run(
        self,
        request: RunRequest,
        control: RunControl,
        observers: tuple[RunObserver, ...] = (),
    ) -> RunReport: ...

    def step_test_eligibility(
        self,
        request: StepTestRequest,
    ) -> StepTestEligibility: ...

    def test_step(
        self,
        request: StepTestRequest,
        control: RunControl,
    ) -> ActionResult: ...


class JsonCheckpointStore:
    def load_active(self, workflow_id: UUID, run_id: UUID) -> Checkpoint | None: ...
    def save_active(self, checkpoint: Checkpoint) -> None: ...
    def mark_terminal(self, checkpoint: Checkpoint, report: RunReport) -> None: ...
    def discover_active(self, workflow_id: UUID) -> tuple[Checkpoint, ...]: ...


class JsonExecutionJournalStore:
    def load(
        self,
        workflow_id: UUID,
        run_id: UUID,
    ) -> InProgressIterationJournal | None: ...
    def save(self, journal: InProgressIterationJournal) -> None: ...
    def clear(self, workflow_id: UUID, run_id: UUID) -> None: ...
```

Both stores are rooted only under per-user app data and use validated UUID-derived
children. They reject symlink/reparse escape and distinguish missing from malformed
or unknown-version files. Every `save` writes a same-directory temporary file,
flushes Python buffers, calls `os.fsync`, atomically replaces the destination, and
flushes the containing directory where Windows permits; no method reports success
before that sequence completes. All tuple/mapping/JSON boundaries are canonical M1
immutable types, and constructors defensively deep-freeze caller-owned input.

`RunControl` extends `CancellationToken`, so one instance controls adapter
cancellation, pause, and emergency stop. `Ctrl+Shift+F12` cancellation wins over
`Ctrl+Shift+F11` pause and is checked before and after target resolution, before
every input, during poll/backoff, and between steps. Notify observers in
start/result/finish order. For every completed action, emit `RunActionObserved`
with the exact `TargetSpec` used and the exact post-resolution
`RuntimeEnvironment` containing the PID/HWND that received or was inspected for
the action. Never re-resolve a selector for observer evidence. Observer targets
remain in memory only; `RunReport`, checkpoints, and journals never serialize
them. Observer failure stops before the next input, becomes a safe internal
failure, and remaining observers still receive the final report.

`run` returns immediately after successful validation when
`validation_only=True`. For a real run it materializes Task 6 snapshots once,
prepares every variable once, builds the fingerprint, and durably saves a baseline
active checkpoint with `completed_cursor=()` before permitting the first input. Execute
enabled steps depth-first using only `LoopPlanner.iter_workflow_frames`. Build each
`ExecutionContext` with frozen prepared values/reference IDs, the one canonical
`DateContext`, normalized output root, exact frozen row stack, and current-
iteration frozen action outputs. Clear inner outputs when that frame ends. Before
`tabular.save_table`, resolve `input_step_id` in the same iteration, require
`TableData`, and pass it in `ActionRequest.value`. `if_present` skips children only
for a normal zero-match timeout; ambiguity/environment/adapter/cancellation fail.
Apply stop/retry/skip-iteration rules exactly and record every retry in one
`ActionResult.attempt_count` (1–4).

`step_test_eligibility` is pure and consumes the typed request. It disables disabled
steps, row-bound steps until a complete canonical cursor is supplied, and any step that consumes
`input_step_id` or another prior action output. `test_step` never replays preceding
side-effecting actions. It rematerializes immutable sources, prepares variables,
rebuilds the exact nested row stack by validating every cursor loop UUID/index,
rechecks preflight/fingerprint/environment, then runs the same target → guard →
precondition → action → postcondition/assertion lifecycle. Missing/stale cursor or
unreconstructable dependency fails before adapter/native input. Step tests do not
write checkpoint, resume journal, or terminal state.

Before each normal-run action, derive idempotency only from the immutable adapter
descriptor, append an `InProgressAction(state="inflight")` for the current cursor,
and durably save the journal before calling the adapter. After success, replace
that record with `state="succeeded"` and durably save again. The journal contains
only schema/version IDs, cursor, step ID, action type, descriptor-derived
idempotency, state, and timestamps—never prepared/raw values, credential
references/plaintext, hashes of plaintext, action output, selectors, target
identity, screenshots, or evidence.

After every fully successful iteration, require every output commit to be durable,
reduce commits to one latest entry per case-normalized resolved destination, then
durably `save_active` the checkpoint. Only after that save succeeds may the
executor clear the current journal. `Checkpoint.output_commits` therefore contains
only current possible state: committed flag, destination below output root,
format/sheet, file SHA-256, headers SHA-256, row count, producer step, and producer
cursor. Resume revalidates each of those latest unique commits; it does not demand
obsolete hashes for overwritten destinations.

Hash rules are exact:

- workflow: SHA-256 of M1 `dump_workflow()` UTF-8 bytes, regardless of revision;
- inputs: canonical typed `PreparedVariables.values`, credential reference IDs,
  and `exists()` booleans; never secret plaintext, plaintext hash, or length;
- output root: normalized resolved user-selected output directory;
- data: canonical parsed headers, row order, and scalar snapshot actually consumed;
- adapters: canonical full immutable descriptor plus implementation version,
  sorted by adapter ID;
- environment: executable/window class and, for coordinate fallback, DPI/client
  geometry; exclude PID/HWND and transient title.

On resume, load the active checkpoint and journal by `(workflow_id, run_id)`,
restore its M1 `DateContext`, rebuild current snapshots/prepared variables/
fingerprint, and reject missing/corrupt/unknown state with `CHECKPOINT_INVALID` or
any fingerprint/output mismatch with `RESUME_MISMATCH`. These are run-level
failures: `RunReport.results == ()`, `RunReport.error_code` carries the cause, and
no adapter/native input occurs. If the incomplete iteration journal contains any
non-idempotent action in either `inflight` or `succeeded` state, return run-level
`RESUME_UNSAFE` before input and instruct manual reconciliation. If every recorded
action is idempotent, replay that journal cursor from the first step of the whole
iteration; never continue after the last journal action. With no journal, locate
`completed_cursor` by loop-step UUID and start at the next valid frame—never compare
display integer paths lexicographically. Never turn invalid resume into a new run.

When the workflow reaches a completed `success` or `partial` outcome, copy the
last durable cursor into the in-memory `RunReport`, then `mark_terminal` by
atomically replacing active state
with a minimal `TerminalRunRecord`; only then clear any journal. Active discovery
matches active filenames only and ignores terminal records, so a crash after the
terminal rename cannot offer a completed run for resume. A normal failed or
cancelled run preserves the last safe active checkpoint plus any partial journal
needed for the next safety decision; it does not mark terminal or erase that state.
Cancellation yields `cancelled`, explicit skipped rows yield `partial`, and an
unhandled stop yields `failed`.

- [ ] **Step 4: Run the complete M4 gate**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit tests/contract tests/integration/test_recording_normalization_roundtrip.py tests/integration/test_fake_workflow_execution.py -q
.\.venv\Scripts\python.exe scripts/export_schema.py --check
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m ruff format --check src tests scripts
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass; fake traces prove lifecycle order, exact PID/HWND observer
context, journal-before-action durability, and zero input before successful
preflight/resume validation.

- [ ] **Step 5: Commit and stop at the M4 review gate**

```powershell
git add src/universal_rpa/domain/execution.py src/universal_rpa/ports/artifacts.py src/universal_rpa/infrastructure/checkpoint_store.py src/universal_rpa/infrastructure/execution_journal.py src/universal_rpa/application/resume.py src/universal_rpa/application/execution.py tests/unit/infrastructure/test_checkpoint_store.py tests/unit/infrastructure/test_execution_journal.py tests/unit/application/test_resume.py tests/unit/application/test_step_test.py tests/unit/application/test_execution.py tests/integration/test_fake_workflow_execution.py
git commit -m "feat(universal-rpa): add safely resumable execution"
git status --short
```
## M4 Completion Gate

Before M5, review recorded fake traces for preflight, UIA unique match,
every coordinate guard, focus theft, timeout, retry, cancellation, skip-row,
checkpoint, and resume. Do not use the real MIS yet; M5's deterministic harness
must pass before the read-only pilot.
