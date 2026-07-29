# Universal RPA M2 Recorder and Normalizer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 선택한 Windows 창의 mouse·keyboard 입력을 비차단 방식으로 안전하게 JSONL에 기록하고, 명령키·문자열·double-click·drag·scroll을 편집 가능한 의미 단계 후보로 결정론적으로 변환한다.

**Architecture:** 별도 WinEvent/UIA focus watcher가 불변 context token을 미리 갱신하고, pynput callback은 timestamp·입력 필드와 그 token을 메모리 복사해 bounded queue에 넣는다. 키 문자는 직렬화 불가능한 메모리 전용 토큰이다. `Ctrl+Shift+F11/F12`는 별도 priority control sink로 즉시 전달한다. `RecordingService` worker는 짧은 focus-confirmation barrier 뒤 이벤트 시점 token의 Windows context/UIA/environment만 보강하고 fail-closed sanitization 후 append-only store에 기록한다. 별도 `NormalizationService`는 finalized raw audit을 실행 불가능한 `StepCandidate` 목록으로 변환한다. Password·불확실 context·범위 밖·pause 중 키 payload와 recorder control hotkey는 persistence boundary 전에 제거한다.

**Tech Stack:** M1 Domain Core, pynput, pywinauto UIA, pywin32, Pydantic 2, Python queue/threading, pytest.

## Global Constraints

- M1 completion gate와 commit review가 먼저 통과해야 한다.
- raw session root는 `%LOCALAPPDATA%\UniversalRPAStudio\recordings\<session_id>\`이고 repository/project 경로를 받을 수 없다.
- listener callback은 timestamp·입력 필드 생성, 미리 게시된 immutable context token의 메모리 복사, queue/priority-control memory operation만 수행한다. Win32/UIA, filesystem, logging, normalization은 호출하지 않으며 latency budget은 20ms다.
- keyboard 문자/키 label은 UIA가 이벤트 시점 target을 확인할 때까지 repr/직렬화가
  redacted된 메모리 전용 토큰이다. context가 사라지거나 달라지면 영구 삭제한다.
- `Ctrl+Shift+F11/F12`는 bounded event queue와 raw JSONL을 통과하지 않는다. 특히 `Ctrl+Shift+F12` stop은
  queue가 가득 찬 경우에도 priority control sink에서 즉시 설정된다.
- 원본은 append-only JSON Lines이며 실행기가 직접 실행하지 않는다.
- mouse move 단독은 workflow 후보가 아니지만 drag 판단을 위해 raw event에는 존재할 수 있다.
- `Ctrl+Shift+F11`과 `Ctrl+Shift+F12`는 recording event가 아닌 control command이며 raw/candidate 어디에도 저장하지 않는다.
- 일반 text의 기본 mode는 `literal`; date/number/path detection은 suggestion만 생성한다.
- password control뿐 아니라 paused/out-of-scope/context-uncertain keyboard event의 key/text payload도 모든 raw/candidate serialization에서 평문 0건이어야 한다.
- 녹화 session은 finalized이고 incomplete가 아니어야만 정규화할 수 있다.

---

### Task 1: Raw event, session, and sanitization models

**Files:**

- Create: `src/universal_rpa/domain/recording.py`
- Create: `tests/unit/domain/test_recording.py`

**Interfaces:**

- Produces: `NativeInputEvent`, `RawInputEvent`, `RecordingTarget`,
  `RecordingSession`, `RecordingSessionSummary`
- Produces: `WindowContextSnapshot`, `TargetSnapshot`,
  `RecordingEnvironmentSnapshot`
- Consumes: M1 `UiaSelector`, `FrozenJsonObject`, `deep_freeze_json`

- [ ] **Step 1: Write failing timezone, scope, and password tests**

```python
def test_raw_event_requires_utc_wall_time() -> None:
    with pytest.raises(ValidationError):
        raw_event(wall_time_utc=datetime(2026, 7, 27, 9, 0))


def test_out_of_scope_event_keeps_audit_metadata_but_redacts_key_payload() -> None:
    event = raw_event(
        event_type=RawEventType.KEY_DOWN,
        payload={"key": "SENTINEL_OUTSIDE"},
        in_scope=False,
    )
    assert event.in_scope is False
    assert event.event_id is not None
    assert event.payload == {"redacted": True}
    assert "SENTINEL_OUTSIDE" not in event.model_dump_json()


def test_paused_key_payload_is_always_redacted() -> None:
    event = raw_event(
        event_type=RawEventType.KEY_DOWN,
        payload={"key": "SENTINEL_PAUSED"},
        capture_state="paused",
    )
    assert event.payload == {"redacted": True}
    assert "SENTINEL_PAUSED" not in event.model_dump_json()


def test_raw_payload_is_defensively_copied_before_it_can_cross_threads() -> None:
    source = {"delta": {"axes": [0, 120]}}
    event = raw_event(event_type=RawEventType.MOUSE_WHEEL, payload=source)
    source["delta"]["axes"][1] = 999
    assert thaw_json(event.payload) == {"delta": {"axes": [0, 120]}}
    with pytest.raises(TypeError):
        event.payload["delta"]["axes"][1] = 999


def test_password_target_removes_key_and_value_payload() -> None:
    event = enrich_and_sanitize_event(
        native_key_event(key="s", text="secret-letter"),
        session_id=uuid4(),
        context=window_context(),
        target=target_snapshot(is_password=True, observed_value="secret-letter"),
        environment=environment_snapshot(),
        in_scope=True,
    )
    encoded = event.model_dump_json()
    assert "secret-letter" not in encoded
    assert event.payload == {"redacted": True}
    assert event.target_snapshot is not None
    assert event.target_snapshot.observed_value is None
```

- [ ] **Step 2: Run the model test and verify it fails**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/domain/test_recording.py -q
```

Expected: FAIL with missing recording models.

- [ ] **Step 3: Implement raw models with the exact persisted fields**

```python
class RawEventType(StrEnum):
    MOUSE_DOWN = "mouse_down"
    MOUSE_UP = "mouse_up"
    MOUSE_MOVE = "mouse_move"
    MOUSE_WHEEL = "mouse_wheel"
    KEY_DOWN = "key_down"
    KEY_UP = "key_up"

@dataclass(frozen=True, slots=True)
class EventFocusSnapshot:
    foreground_hwnd: int
    focused_hwnd: int | None
    foreground_process_id: int
    cached_uia_runtime_id: tuple[int, ...] | None
    focus_event_time_ms: int
    cache_generation: int
    cache_confirmed: bool

@dataclass(frozen=True, slots=True)
class KeyChord:
    key: str
    modifiers: frozenset[str] = frozenset()

class SensitiveKeyToken:
    @classmethod
    def create(cls, *, key: str, text: str | None) -> "SensitiveKeyToken": ...
    def reveal_once(self) -> tuple[str, str | None]: ...
    def discard(self) -> None: ...
    def __repr__(self) -> str:
        return "SensitiveKeyToken(<redacted>)"

@dataclass(frozen=True, slots=True)
class NativeInputEvent:
    monotonic_ns: int
    wall_time_utc: datetime
    hook_time_ms: int
    event_type: RawEventType
    focus: EventFocusSnapshot
    payload: FrozenJsonObject
    key_token: SensitiveKeyToken | None = field(default=None, repr=False, compare=False)

class RecordingTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    process_id: int = Field(gt=0)
    process_executable: str = Field(min_length=1)
    top_level_hwnd: int
    window_title: str
    window_class: str = Field(min_length=1)

class WindowContextSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    foreground_hwnd: int
    focused_hwnd: int | None
    process_id: int = Field(gt=0)
    process_executable: str = Field(min_length=1)
    top_level_hwnd: int
    window_title: str
    window_class: str = Field(min_length=1)
    focused_runtime_id: tuple[int, ...] | None
    selected_top_level_hwnd: int
    owned_by_selected_window: bool
    context_confident: bool

class TargetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    selector_candidates: tuple[UiaSelector, ...]
    focused_runtime_id: tuple[int, ...] | None
    editable: bool
    is_password: bool
    observed_value: str | None
    bounds: NormalizedRect | None

class RecordingEnvironmentSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    client_left: int
    client_top: int
    client_width: int = Field(gt=0)
    client_height: int = Field(gt=0)
    dpi_x: int = Field(gt=0)
    dpi_y: int = Field(gt=0)
    monitor_scale: float = Field(gt=0)
    monitor_id: str
    double_click_time_ms: int = Field(gt=0)
    drag_width_px: int = Field(gt=0)
    drag_height_px: int = Field(gt=0)

class RecordingSession(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: UUID
    target: RecordingTarget
    started_at: datetime
    retained: bool = False

class RecordingSessionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: UUID
    finalized: bool
    incomplete: bool
    retained: bool
    event_count: int = Field(ge=0)
    dropped_event_count: int = Field(ge=0)
    started_at: datetime
    finished_at: datetime | None
class RawInputEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1"] = "1"
    session_id: UUID
    event_id: UUID
    monotonic_ns: int = Field(ge=0)
    wall_time_utc: datetime
    event_type: RawEventType
    payload: FrozenJsonObject
    in_scope: bool
    capture_state: Literal["recording", "paused"]
    window_context: WindowContextSnapshot
    target_snapshot: TargetSnapshot | None
    environment_snapshot: RecordingEnvironmentSnapshot
```

`WindowContextSnapshot` contains the event-time foreground/focused HWND copied
from `EventFocusSnapshot`, process ID, executable name, top-level HWND/title/class,
focused UIA runtime ID, ownership relation to the selected window, and
`context_confident`. UIA lookup resolves the exact captured handles; it must not
substitute whichever element is focused when the worker eventually runs.
`TargetSnapshot` contains selector candidates, editable flag, password flag,
committed observed value, and normalized bounding rectangle.
`RecordingEnvironmentSnapshot` contains client rectangle, DPI X/Y, monitor
scale, monitor ID, Windows double-click milliseconds, and drag width/height.

All datetimes must have UTC offset zero. `enrich_and_sanitize_event` creates a
new UUID event ID. The worker reveals `SensitiveKeyToken` exactly once only when
all of these hold: recording state, selected-window scope, exact event-time
handle/runtime identity resolved, non-password target. Otherwise it discards the
token and persists only `{"redacted": True}`. Password observed value is never
read. A `RawInputEvent` before-validator independently replaces every keyboard
payload with `{"redacted": True}` when password, paused, out-of-scope, or
`context_confident=False`, and removes observed value. Direct model construction
therefore cannot bypass redaction. Both `NativeInputEvent` and `RawInputEvent`
deep-freeze a defensive copy before crossing the queue/storage boundary; later
caller mutation and nested item assignment cannot change serialized bytes.
`SensitiveKeyToken` is never accepted by the JSONL store or Pydantic serialization.

- [ ] **Step 4: Run recording-model and M1 regressions**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/domain/test_recording.py tests/unit/domain -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass; naive/local datetimes fail and recursive serialization scans find zero password, paused, out-of-scope, or uncertain-context sentinels.

- [ ] **Step 5: Commit raw recording models**

```powershell
git add src/universal_rpa/domain/recording.py tests/unit/domain/test_recording.py
git commit -m "feat(universal-rpa): define safe raw recording events"
```

---

### Task 2: Append-only JSONL recording store and seven-day retention

**Files:**

- Create: `src/universal_rpa/infrastructure/app_paths.py`
- Create: `src/universal_rpa/infrastructure/recording_store.py`
- Create: `src/universal_rpa/bootstrap.py`
- Create: `tests/unit/infrastructure/test_app_paths.py`
- Create: `tests/unit/infrastructure/test_recording_store.py`
- Create: `tests/unit/test_bootstrap_recording_store.py`

**Interfaces:**

- Produces: `default_recordings_root(local_app_data=None) -> Path`
- Produces: `JsonlRecordingStore.open_default`, test-only `for_test`,
  `create_session`, `append`, `finalize`, `load_summary`, `iter_events`,
  `list_sessions`, `delete_session`, `purge_expired`
- Produces: bootstrap wiring that can construct only the default app-data store
- Consumes: M2 Task 1 raw/session models

- [ ] **Step 1: Write failing path, append, corruption, and retention tests**

```python
def test_default_recording_root_is_local_app_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\operator\AppData\Local")
    assert default_recordings_root() == Path(
        r"C:\Users\operator\AppData\Local\UniversalRPAStudio\recordings"
    )


def test_production_store_has_no_arbitrary_root_parameter() -> None:
    assert tuple(inspect.signature(JsonlRecordingStore.open_default).parameters) == (
        "local_app_data",
        "forbidden_roots",
    )


def test_bootstrap_uses_default_recording_store_not_project_directory(
    tmp_path: Path,
) -> None:
    services = build_services(active_project_dir=tmp_path / "project")
    assert not services.recording_store.root.is_relative_to(tmp_path / "project")


def test_append_writes_one_json_object_per_line(tmp_path: Path) -> None:
    store = JsonlRecordingStore.for_test(tmp_path)
    session = recording_session()
    store.create_session(session)
    store.append(raw_event(session_id=session.session_id))
    store.append(raw_event(session_id=session.session_id))
    lines = (tmp_path / str(session.session_id) / "events.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["schema_version"] == "1" for line in lines)


def test_retained_session_survives_default_retention(tmp_path: Path) -> None:
    store = JsonlRecordingStore.for_test(tmp_path)
    old = finalized_session(store, age_days=8, retained=True)
    assert store.purge_expired(now=datetime.now(UTC)).deleted == ()
    assert (tmp_path / str(old.session_id)).exists()


def test_explicit_sensitive_reclassification_deletes_source_session(tmp_path: Path) -> None:
    store = JsonlRecordingStore.for_test(tmp_path)
    session = finalized_session(store, age_days=0, retained=True)
    store.delete_session(session.session_id, reason="reclassified_as_secret")
    assert not (tmp_path / str(session.session_id)).exists()
```

- [ ] **Step 2: Run store tests and confirm failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/infrastructure/test_app_paths.py tests/unit/infrastructure/test_recording_store.py tests/unit/test_bootstrap_recording_store.py -q
```

Expected: FAIL because storage modules are absent.

- [ ] **Step 3: Implement constrained session paths and append-only writes**

```python
class JsonlRecordingStore:
    def __init__(self, root: Path, *, _factory_token: object) -> None:
        if _factory_token not in {_PRODUCTION_STORE, _TEST_STORE}:
            raise TypeError("use open_default() or for_test()")
        self._root = root.resolve()

    @classmethod
    def open_default(
        cls,
        local_app_data: Path | None = None,
        forbidden_roots: tuple[Path, ...] = (),
    ) -> "JsonlRecordingStore": ...

    @classmethod
    def for_test(cls, root: Path) -> "JsonlRecordingStore": ...

    @property
    def root(self) -> Path: ...
    def create_session(self, session: RecordingSession) -> None: ...
    def append(self, event: RawInputEvent) -> None: ...
    def finalize(
        self,
        session_id: UUID,
        *,
        retained: bool,
        incomplete: bool,
    ) -> RecordingSessionSummary: ...
    def load_summary(self, session_id: UUID) -> RecordingSessionSummary: ...
    def iter_events(self, session_id: UUID) -> Iterator[RawInputEvent]: ...
    def list_sessions(self) -> tuple[RecordingSessionSummary, ...]: ...
    def delete_session(self, session_id: UUID, *, reason: str) -> None: ...
    def purge_expired(
        self,
        *,
        now: datetime,
        retention: timedelta = timedelta(days=7),
    ) -> RetentionSummary: ...
```

`open_default` is the only production factory. It derives its root from
`default_recordings_root`, resolves it, and rejects equality/containment with any
active project or source-repository root supplied by bootstrap. `for_test` is
explicitly test-only; production dependency wiring never accepts a caller path.
The only valid child directory is `root / str(UUID)`. Write `manifest.json`
atomically, open `events.jsonl` in UTF-8 append mode, write one compact JSON
object plus newline, flush after each event, and never rewrite it on finalize.
`finalize` atomically writes `RecordingSessionSummary` with `finalized=True`,
`incomplete`, event/drop counts, and final timestamp. `load_summary` rejects a
missing/unfinalized manifest. `iter_events` raises `CorruptRecordingError` with
line number on malformed JSON.
Retention skips `retained=True`, reports locked-directory failures, and never
follows symlinks or deletes outside the resolved root. `delete_session` uses the same
resolved-root and UUID checks and exists so a later user reclassification to secret can
purge the source raw session before the workflow change is accepted.

- [ ] **Step 4: Run store tests and scan fixture bytes for password text**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/infrastructure/test_app_paths.py tests/unit/infrastructure/test_recording_store.py tests/unit/test_bootstrap_recording_store.py -q
.\.venv\Scripts\python.exe -m pytest tests/unit -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass; seven-day deletion and explicit retention are deterministic
under a fake clock.

- [ ] **Step 5: Commit the raw-session store**

```powershell
git add src/universal_rpa/infrastructure/app_paths.py src/universal_rpa/infrastructure/recording_store.py src/universal_rpa/bootstrap.py tests/unit/infrastructure tests/unit/test_bootstrap_recording_store.py
git commit -m "feat(universal-rpa): persist append-only recording sessions"
```

---

### Task 3: Capture/context ports and non-blocking RecordingService

**Files:**

- Create: `src/universal_rpa/ports/capture.py`
- Create: `src/universal_rpa/ports/context.py`
- Modify: `src/universal_rpa/ports/repositories.py`
- Create: `src/universal_rpa/application/recording.py`
- Create: `tests/helpers/recording_fakes.py`
- Create: `tests/unit/application/test_recording.py`

**Interfaces:**

- Produces: `InputCapturePort`, `WindowContextPort`, `ControlCommand`, `ControlHotkeys`
- Produces: `RecordingService.start`, `pause`, `resume`, `stop`
- Consumes: `JsonlRecordingStore` through `RecordingStorePort`

- [ ] **Step 1: Write failing non-blocking and state-transition tests**

```python
def test_listener_submission_never_waits_for_slow_context() -> None:
    context = BlockingWindowContext()
    service = recording_service(context=context, queue_size=4)
    service.start(recording_target())
    started = time.perf_counter()
    service.submit_native_event(native_key_event(key="a"))
    elapsed = time.perf_counter() - started
    assert elapsed < 0.02


def test_queue_overflow_marks_session_incomplete() -> None:
    service = recording_service(context=BlockingWindowContext(), queue_size=1)
    service.start(recording_target())
    for key in ("a", "b", "c"):
        service.submit_native_event(native_key_event(key=key))
    summary = service.stop(timeout_seconds=0.1)
    assert summary.incomplete is True
    assert summary.dropped_event_count > 0


def test_priority_stop_is_not_blocked_or_dropped_when_event_queue_is_full() -> None:
    service = recording_service(context=BlockingWindowContext(), queue_size=1)
    service.start(recording_target())
    service.submit_native_event(native_key_event(key="a"))
    started = time.perf_counter()
    service.submit_control(ControlCommand.STOP)
    assert service.stop_requested is True
    assert time.perf_counter() - started < 0.02


def test_control_hotkeys_never_reach_raw_store() -> None:
    service, store = recording_service_with_store()
    service.start(recording_target())
    service.submit_control(ControlCommand.TOGGLE_PAUSE)
    service.submit_control(ControlCommand.TOGGLE_PAUSE)
    service.submit_control(ControlCommand.STOP)
    service.await_stopped(timeout_seconds=1.0)
    assert all(event.event_type.value != "control" for event in store.events)
    assert not any("f11" in event.model_dump_json().lower() for event in store.events)
    assert not any("f12" in event.model_dump_json().lower() for event in store.events)


def test_uncertain_event_context_discards_memory_key_token() -> None:
    service, store = recording_service_with_store(context=uncertain_event_context())
    token = SensitiveKeyToken.create(key="x", text="SENTINEL_RACE")
    service.start(recording_target())
    service.submit_native_event(native_key_event(key_token=token))
    service.stop()
    assert "SENTINEL_RACE" not in store.serialized_bytes().decode("utf-8")


def test_invalid_state_transition_is_rejected() -> None:
    service = recording_service()
    with pytest.raises(RecordingStateError):
        service.resume()
```

- [ ] **Step 2: Run service tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/application/test_recording.py -q
```

Expected: FAIL with missing ports/service.

- [ ] **Step 3: Implement the queue-only callback boundary**

```python
InputEventSink = Callable[[NativeInputEvent], None]

class ControlCommand(StrEnum):
    TOGGLE_PAUSE = "toggle_pause"
    STOP = "stop"

ControlSink = Callable[[ControlCommand], None]

class InputCapturePort(Protocol):
    def start(self, event_sink: InputEventSink, control_sink: ControlSink) -> None: ...
    def stop(self) -> None: ...

class WindowContextPort(Protocol):
    def list_recordable_windows(self) -> tuple[RecordingTarget, ...]: ...
    def capture_context(
        self,
        event: NativeInputEvent,
        selected: RecordingTarget,
    ) -> CapturedEventContext: ...
    def capture_target(
        self,
        request: TargetCaptureRequest,
        cancellation: CancellationToken,
    ) -> TargetCaptureResult: ...

@dataclass(frozen=True, slots=True)
class ControlHotkeys:
    toggle_pause: KeyChord = KeyChord("f11", frozenset({"ctrl", "shift"}))
    stop: KeyChord = KeyChord("f12", frozenset({"ctrl", "shift"}))

class RecordingService:
    def start(self, target: RecordingTarget) -> RecordingSession: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def stop(
        self,
        *,
        keep: bool = False,
        timeout_seconds: float = 5.0,
    ) -> RecordingSessionSummary: ...
    def submit_native_event(self, event: NativeInputEvent) -> None: ...
    def submit_control(self, command: ControlCommand) -> None: ...
    @property
    def stop_requested(self) -> bool: ...
```

`submit_native_event` may only call `Queue.put_nowait` and set in-memory
`threading.Event`/integer state on `queue.Full`. `submit_control` never touches
that queue: it atomically toggles pause or sets the stop event and wakes the
coordinator. It performs no disk/UI work and returns within the same 20ms budget.
`Ctrl+Shift+F12` remains effective under queue overflow, blocked UIA, and append failure; a
coordinator thread performs capture shutdown so the listener callback never
joins itself. Control commands never become `NativeInputEvent` or `RawInputEvent`.
A named worker thread enriches,
sanitizes, and appends events. `stop` first stops the capture port, signals the
worker, drains queued events, joins up to the supplied timeout, and finalizes.
Write failure or overflow makes the summary incomplete and prevents normalizer
approval. Scope and pause state are audit fields; they do not drop raw events,
but the persistence sanitizer strips all keyboard payload from those audit-only
events. An uncertain/missing event-time context discards its key token before
append and can never be recovered by querying the later live focus.

- [ ] **Step 4: Verify callback latency and cumulative unit tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/application/test_recording.py -q
.\.venv\Scripts\python.exe -m pytest tests/unit -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass, including blocked context, full queue, immediate `Ctrl+Shift+F12`, zero persisted control chords, context-race redaction, and worker-write failure cases.

- [ ] **Step 5: Commit the recording service boundary**

```powershell
git add src/universal_rpa/ports src/universal_rpa/application/recording.py tests/helpers/recording_fakes.py tests/unit/application/test_recording.py
git commit -m "feat(universal-rpa): add nonblocking recording service"
```

---

### Task 4: Windows DPI, window catalog, context capture, and pynput adapter

**Files:**

- Create: `src/universal_rpa/adapters/windows/__init__.py`
- Create: `src/universal_rpa/adapters/windows/dpi.py`
- Create: `src/universal_rpa/adapters/windows/window_catalog.py`
- Create: `src/universal_rpa/adapters/windows/context.py`
- Create: `src/universal_rpa/adapters/windows/capture.py`
- Create: `tests/unit/adapters/windows/test_dpi.py`
- Create: `tests/unit/adapters/windows/test_window_catalog.py`
- Create: `tests/unit/adapters/windows/test_context.py`
- Create: `tests/unit/adapters/windows/test_capture.py`

**Interfaces:**

- Produces: `enable_per_monitor_v2_dpi_awareness()`
- Produces: `Win32WindowCatalog`, `FocusContextWatcher`, `UiaFocusCache`, `WindowsWindowContext`, `PynputInputCapture`
- Consumes: M2 Task 3 ports

- [ ] **Step 1: Write failing Windows-boundary tests using patched Win32/UIA calls**

```python
def test_capture_callback_only_copies_cached_context_and_memory_sinks() -> None:
    event_sink = Mock()
    control_sink = Mock()
    context_cache = fake_context_cache(runtime_id=(42, 7))
    win32 = Mock()
    ui_automation = Mock()
    capture = PynputInputCapture(
        listener_factory=fake_listener_factory(),
        context_cache=context_cache,
        forbidden_native_dependencies=(win32, ui_automation),
    )
    capture.start(event_sink, control_sink)
    capture._on_press(FakeKey("a"))
    event = event_sink.call_args.args[0]
    assert event.focus.cached_uia_runtime_id == (42, 7)
    assert "a" not in repr(event)
    control_sink.assert_not_called()
    win32.assert_not_called()
    ui_automation.assert_not_called()


def test_f12_uses_priority_control_sink_and_never_event_sink() -> None:
    event_sink = Mock()
    control_sink = Mock()
    capture = capture_with_pressed_modifiers("ctrl", "shift")
    capture.start(event_sink, control_sink)
    capture._on_press(FakeKey("f12"))
    control_sink.assert_called_once_with(ControlCommand.STOP)
    event_sink.assert_not_called()


def test_worker_resolves_captured_runtime_id_not_later_live_focus() -> None:
    context = WindowsWindowContext(
        uia=fake_uia(captured={(42, 7): non_password_edit()}, live=password_edit()),
        win32=fake_win32(),
    )
    captured = context.capture_context(
        native_key_event(focus=event_focus(runtime_id=(42, 7))),
        selected_window(hwnd=101),
    )
    assert captured.target_snapshot.is_password is False
    assert captured.window_context.focused_runtime_id == (42, 7)


def test_missing_captured_runtime_id_is_uncertain_not_live_substitution() -> None:
    context = WindowsWindowContext(uia=fake_uia(live=non_password_edit()), win32=fake_win32())
    captured = context.capture_context(
        native_key_event(focus=event_focus(runtime_id=None, cache_confirmed=False)),
        selected_window(hwnd=101),
    )
    assert captured.window_context.context_confident is False
    assert captured.target_snapshot is None


def test_owned_modal_is_inside_selected_window_scope() -> None:
    context = WindowsWindowContext(win32=fake_win32(owner_of={202: 101}))
    captured = context.capture_context(native_key_event(), selected_window(hwnd=101))
    assert captured.in_scope is True


def test_password_control_value_is_never_read() -> None:
    value_pattern = Mock()
    element = fake_uia_element(is_password=True, value_pattern=value_pattern)
    snapshot = capture_target_snapshot(element)
    value_pattern.get_value.assert_not_called()
    assert snapshot.observed_value is None


def test_explicit_target_capture_keeps_region_metadata_in_each_candidate() -> None:
    request = target_capture_request(
        screen_x=100,
        screen_y=200,
        client_size=(1200, 800),
    )
    context = WindowsWindowContext(
        uia=fake_uia(point_target=password_edit()),
        win32=fake_win32(),
        screenshot=fake_png_capture(width=1200, height=800),
    )
    result = context.capture_target(request, CancellationToken())
    assert result.target is not None
    assert result.target.adapter_id == "windows"
    assert result.target in result.candidates
    target = WindowsTarget.model_validate(result.target.payload)
    assert target.target_region is not None
    assert target.mandatory_sensitive_regions
    assert target.user_sensitive_regions == ()
    assert result.preview_png is not None and result.preview_png.startswith(b"\x89PNG")
    assert decoded_png_size(result.preview_png) == (1200, 800)
```

- [ ] **Step 2: Run adapter tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/adapters/windows -q
```

Expected: FAIL because Windows adapters do not exist.

- [ ] **Step 3: Implement adapters behind injected native facades**

`enable_per_monitor_v2_dpi_awareness` calls
`SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)` once
before any listener/window. Treat `ERROR_ACCESS_DENIED` as “already set” only
after querying the process awareness.

`Win32WindowCatalog.list_recordable_windows()` returns visible, titled,
non-cloaked top-level windows with HWND, PID, executable, title, class, client
rectangle, and ownership. Sort by casefolded app/title then HWND.

`FocusContextWatcher` receives WinEvent foreground/focus notifications outside
pynput callbacks and performs Win32/UIA resolution there. `UiaFocusCache`
publishes immutable generation tokens containing foreground/focused HWND,
process, focused UIA runtime ID, OS event time, and transition confidence. The
listener only copies the current token. Before persistence, a bounded
`ContextConfirmationBarrier` allows later-delivered focus notifications to catch
up and confirms that no transition with OS event time at or before the input was
missed. Stale, transitioning, missing, or mismatched tokens are uncertain and
therefore redacted; the barrier never runs in the callback.

`WindowsWindowContext.capture_context()` resolves only the event's captured
foreground/focused HWND and cached runtime ID. It records selected-window
ownership, client rectangle, DPI, monitor scale, relative point, and every
selector candidate. It never substitutes later live focus or chooses among
duplicates. Missing, stale, mismatched, or disappeared identities set
`context_confident=False`. `IsPassword=True` skips ValuePattern access.
`capture_target(TargetCaptureRequest)` supports explicit target picking. Every
Windows candidate payload contains its own `target_region`, mandatory password
regions, initially empty user regions, and a complete coordinate fallback.
`TargetCaptureResult` carries no duplicate region fields. Its PNG remains
memory-only and encodes exactly the request runtime top-level client at
`client_width × client_height`; any capture/dimension mismatch fails closed.
Recording context capture does not take screenshots.

`PynputInputCapture` translates mouse down/up/move/wheel and key down/up to
`NativeInputEvent` with `time.monotonic_ns()`, `datetime.now(UTC)`, the hook OS
event time, and a memory copy of the current focus context token. Keyboard events carry only a redacted `SensitiveKeyToken` until
the worker validates context. Its stateful chord detector routes `Ctrl+Shift+F11/F12` only to
`ControlSink` and does not enqueue them. Callback modules cannot import the
recording store or pywinauto.

- [ ] **Step 4: Run Windows adapter tests and quality checks**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/adapters/windows tests/unit/application/test_recording.py -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all native tests pass through injected fakes; focus changes cannot relabel an earlier key, `Ctrl+Shift+F12` bypasses the event sink, previews stay in memory, and no real desktop input is sent.

- [ ] **Step 5: Commit Windows recording adapters**

```powershell
git add src/universal_rpa/adapters/windows tests/unit/adapters/windows
git commit -m "feat(universal-rpa): capture Windows input context"
```

---

### Task 5: Mouse gesture normalizer

**Files:**

- Create: `src/universal_rpa/application/normalization.py`
- Create: `tests/unit/application/test_mouse_normalization.py`

**Interfaces:**

- Produces: `StepCandidate`, `NormalizationWarning`, `NormalizationResult`
- Produces: `normalize_mouse_events(events, thresholds)`
- Consumes: raw events and OS-recorded double-click/drag thresholds

- [ ] **Step 1: Write failing click, double-click, drag, and wheel tests**

```python
def test_two_os_qualified_clicks_become_one_double_click() -> None:
    events = two_clicks(gap_ms=250, distance_px=2, same_target=True)
    result = normalize_mouse_events(events, thresholds=os_thresholds(500, 4, 4))
    assert [item.action_type for item in result.candidates] == ["windows.double_click"]
    assert len(result.candidates[0].source_event_ids) == 4


def test_move_without_drag_creates_no_candidate() -> None:
    assert normalize_mouse_events(mouse_moves_only(), thresholds=os_thresholds()).candidates == ()


def test_drag_uses_canonical_typed_end_point() -> None:
    candidate = normalize_mouse_events(
        completed_drag(start=(0.1, 0.2), end=(0.8, 0.7), button="left"),
        thresholds=os_thresholds(),
    ).candidates[0]
    params = DragParameters.model_validate(candidate.parameters)
    assert (params.end_point.x, params.end_point.y, params.button) == (0.8, 0.7, "left")


def test_wheel_preserves_signed_horizontal_and_vertical_deltas() -> None:
    candidate = normalize_mouse_events(
        wheel(horizontal_delta=-120, vertical_delta=240),
        thresholds=os_thresholds(),
    ).candidates[0]
    params = ScrollParameters.model_validate(candidate.parameters)
    assert (params.horizontal_delta, params.vertical_delta) == (-120, 240)


def test_missing_mouse_up_warns_instead_of_guessing_drag() -> None:
    result = normalize_mouse_events(mouse_down_and_moves_without_up(), thresholds=os_thresholds())
    assert result.candidates == ()
    assert result.warnings[0].code == "incomplete_mouse_gesture"


def test_click_candidate_preserves_executable_class_dpi_size_and_relative_point() -> None:
    candidate = normalize_mouse_events(
        one_click(
            relative_point=(0.25, 0.75),
            executable="mis.exe",
            window_class="MainFrame",
            dpi=(120, 120),
            client_size=(1600, 900),
        ),
        thresholds=os_thresholds(),
    ).candidates[0]
    assert candidate.target is not None
    target = WindowsTarget.model_validate(candidate.target.payload)
    fallback = target.coordinate_fallback
    assert fallback.recorded_process_executable == "mis.exe"
    assert fallback.recorded_window_class == "MainFrame"
    assert (fallback.recorded_dpi_x, fallback.recorded_dpi_y) == (120, 120)
    assert (fallback.recorded_client_width, fallback.recorded_client_height) == (1600, 900)
    assert (fallback.point.x, fallback.point.y) == (0.25, 0.75)
    assert target.target_region == recorded_target_region()
```

- [ ] **Step 2: Run mouse tests and confirm failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/application/test_mouse_normalization.py -q
```

Expected: FAIL with missing normalizer types.

- [ ] **Step 3: Implement deterministic gesture grouping**

```python
class CandidateLiteralValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["literal"] = "literal"
    display_value: str | None

class CandidateSecretValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["secret_ref"] = "secret_ref"
    display_value: None = None
    credential_ref_required: Literal[True] = True

CandidateValue = Annotated[
    CandidateLiteralValue | CandidateSecretValue,
    Field(discriminator="mode"),
]

class CandidateSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["date_variable", "number_variable", "path_variable", "wait_candidate"]
    source_event_ids: tuple[UUID, ...]
    details: FrozenJsonObject = Field(default_factory=FrozenMapping.empty)

class NormalizationWarning(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    source_event_ids: tuple[UUID, ...]
    safe_message: str

class StepCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidate_id: UUID
    session_id: UUID
    action_type: str
    source_event_ids: tuple[UUID, ...]
    first_monotonic_ns: int
    target: TargetSpec | None
    target_snapshot: TargetSnapshot | None
    value: CandidateValue | None = None
    parameters: FrozenJsonObject = Field(default_factory=FrozenMapping.empty)
    suggestions: tuple[CandidateSuggestion, ...] = ()
    requires_confirmation: bool = False

class NormalizationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    session_id: UUID
    candidates: tuple[StepCandidate, ...]
    warnings: tuple[NormalizationWarning, ...] = ()
    suggestions: tuple[CandidateSuggestion, ...] = ()

def normalize_mouse_events(
    events: Sequence[RawInputEvent],
    *,
    thresholds: MouseThresholds,
) -> NormalizationResult: ...
```

Pair down/up on the same button. Classify as drag only when movement exceeds
recorded Windows width or height threshold. Combine two complete left clicks
only when target identity, OS time, and OS distance constraints all match.
Wheel becomes `windows.scroll` with signed horizontal/vertical deltas.
For every actionable mouse candidate, materialize a `TargetSpec(adapter_id="windows")`
from the captured event—not from later live state—through the shared
`materialize_windows_target(event, action_point)` builder. All observed selector
candidates remain in `target_snapshot` for editor review; `WindowsTarget.selector`
contains only one selector proven unique at recording time, otherwise `None` and
`requires_confirmation=True`. The builder also copies `TargetSnapshot` bounds to
`WindowsTarget.target_region`; password/editable targets include that region in
`mandatory_sensitive_regions`; user regions start empty. M3 preview and M5
evidence masking use the union of both tuples. Its complete `CoordinateFallback`
retains recorded executable basename,
window class, DPI X/Y, client width/height, and normalized client-relative action
point. Drag, scroll, click, press-key, and hotkey parameters are emitted only through
M1 `validate_builtin_action_parameters`, so recorder and runner field names
cannot drift. Absolute screen coordinates remain diagnostic-only. If any required
fallback field is missing, set `target=None`, `requires_confirmation=True`, and
never invent a coordinate target.
Ignore out-of-scope and paused events; warn on unmatched down/up and never infer
an executable action from incomplete input.

- [ ] **Step 4: Run mouse normalization and cumulative tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/application/test_mouse_normalization.py -q
.\.venv\Scripts\python.exe -m pytest tests/unit -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass with stable candidate ordering.

- [ ] **Step 5: Commit the mouse normalizer**

```powershell
git add src/universal_rpa/application/normalization.py tests/unit/application/test_mouse_normalization.py
git commit -m "feat(universal-rpa): normalize mouse gestures"
```

---

### Task 6: Keyboard command, text, IME, and secret state machine

**Files:**

- Modify: `src/universal_rpa/application/normalization.py`
- Create: `tests/unit/application/test_keyboard_normalization.py`
- Create: `tests/unit/application/test_keyboard_secret_redaction.py`

**Interfaces:**

- Produces: `normalize_keyboard_events(events, config)`
- Produces: `suggest_variable_types(text)`
- Consumes: `StepCandidate`, target snapshots, source event IDs

- [ ] **Step 1: Write the required keyboard acceptance test first**

```python
def test_ctrl_a_date_enter_becomes_three_actions() -> None:
    events = recorded_keys(
        chord("ctrl", "a"),
        text("2026-07-27", observed_value="2026-07-27"),
        special("enter"),
    )
    result = normalize_keyboard_events(events)
    assert [candidate.action_type for candidate in result.candidates] == [
        "windows.hotkey",
        "windows.set_text",
        "windows.press_key",
    ]
    hotkey = HotkeyParameters.model_validate(result.candidates[0].parameters)
    press_key = PressKeyParameters.model_validate(result.candidates[2].parameters)
    assert (hotkey.key, hotkey.modifiers) == ("a", ("ctrl",))
    assert press_key.key == "enter"
    assert result.candidates[1].parameters == FrozenMapping.empty()
    assert result.candidates[1].value.mode == "literal"
    assert result.candidates[1].value.display_value == "2026-07-27"
    assert [suggestion.kind for suggestion in result.candidates[1].suggestions] == ["date_variable"]


def test_korean_uses_committed_uia_value_not_physical_reconstruction() -> None:
    events = korean_physical_keys(observed_value="생산실적")
    candidate = normalize_keyboard_events(events).candidates[0]
    assert candidate.value.display_value == "생산실적"


def test_password_candidate_contains_only_unassigned_secret_reference() -> None:
    encoded = normalize_keyboard_events(password_key_events("hunter2")).model_dump_json()
    assert "hunter2" not in encoded
    assert '"mode":"secret_ref"' in encoded


def test_keyboard_candidate_preserves_event_time_target_environment() -> None:
    result = normalize_keyboard_events(
        recorded_keys(
            text("abc", observed_value="abc"),
            executable="mis.exe",
            window_class="MainFrame",
            dpi=(144, 144),
            client_size=(1200, 800),
            target_point=(0.4, 0.3),
        )
    )
    target = WindowsTarget.model_validate(result.candidates[0].target.payload)
    fallback = target.coordinate_fallback
    assert fallback.recorded_process_executable == "mis.exe"
    assert fallback.recorded_window_class == "MainFrame"
    assert (fallback.recorded_dpi_x, fallback.recorded_dpi_y) == (144, 144)
    assert (fallback.recorded_client_width, fallback.recorded_client_height) == (1200, 800)
    assert target.target_region == recorded_target_region()
```

- [ ] **Step 2: Run keyboard tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/application/test_keyboard_normalization.py tests/unit/application/test_keyboard_secret_redaction.py -q
```

Expected: FAIL because keyboard normalization is not implemented.

- [ ] **Step 3: Implement an explicit key state machine**

```python
class KeyboardNormalizationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text_commit_gap_ns: int = 1_000_000_000
    toggle_hotkey: KeyChord = KeyChord("f11", frozenset({"ctrl", "shift"}))
    stop_hotkey: KeyChord = KeyChord("f12", frozenset({"ctrl", "shift"}))

COMMAND_KEYS = frozenset(
    {"enter", "tab", "esc", "left", "right", "up", "down"}
    | {f"f{number}" for number in range(1, 25)}
)

def normalize_keyboard_events(
    events: Sequence[RawInputEvent],
    *,
    config: KeyboardNormalizationConfig = KeyboardNormalizationConfig(),
) -> NormalizationResult: ...
```

Track modifier down/up separately; a bare modifier emits nothing. A modifier
chord emits `windows.hotkey`; command keys emit `windows.press_key`. Group
characters only while the focused editable target identity stays the same and
the gap is less than one second. Commit before Enter, Tab, focus change, or
one-second gap.

For editable UIA controls, use the final non-password `observed_value` as the
committed string. If no value is available for a probable IME sequence, set
`CandidateValue.display_value=None`, retain source event IDs, and set
`requires_confirmation=True`; never invent Hangul from physical keys.
Password candidates contain `mode="secret_ref"` and
`credential_ref_required=True`, with no key labels or value. Hotkey and
press-key parameters are created only through M1
`validate_builtin_action_parameters`; Ctrl+A therefore persists exactly
`{"key": "a", "modifiers": ["ctrl"]}` after JSON thaw, Enter persists exactly
`{"key": "enter"}`, and set-text has the canonical empty parameter object.
Every set-text, hotkey, and press-key candidate uses the same Task 5 event-time target builder;
focus changes within a text group split candidates instead of borrowing a later
target. Date/number/path recognizers append badges only; all normal text remains literal.
Remove both recorder control chords from candidates.

- [ ] **Step 4: Run keyboard, redaction, and full M2 tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/application/test_keyboard_normalization.py tests/unit/application/test_keyboard_secret_redaction.py -q
.\.venv\Scripts\python.exe -m pytest tests/unit -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass; Enter/Tab are never absorbed into text and control hotkeys
never become candidates.

- [ ] **Step 5: Commit keyboard normalization**

```powershell
git add src/universal_rpa/application/normalization.py tests/unit/application/test_keyboard_normalization.py tests/unit/application/test_keyboard_secret_redaction.py
git commit -m "feat(universal-rpa): normalize keyboard text and commands"
```

---

### Task 7: NormalizationService integration, merge/split, and golden session

**Files:**

- Modify: `src/universal_rpa/application/normalization.py`
- Modify: `src/universal_rpa/application/recording.py`
- Create: `tests/fixtures/recordings/ctrl-a-date-enter/events.jsonl`
- Create: `tests/fixtures/recordings/ctrl-a-date-enter/manifest.json`
- Create: `tests/unit/application/test_normalization.py`
- Create: `tests/integration/test_recording_normalization_roundtrip.py`

**Interfaces:**

- Produces: `NormalizationService.normalize_session`, `merge`, `split`
- Produces: control-hotkey transition from RecordingService
- Consumes: raw store, mouse and keyboard normalization

- [ ] **Step 1: Write failing stable-order, wait-suggestion, and round-trip tests**

```python
def test_mixed_candidates_are_sorted_by_first_monotonic_time() -> None:
    store, session_id = finalized_store(mixed_mouse_and_keyboard_events())
    result = NormalizationService().normalize_session(store, session_id)
    assert [item.first_monotonic_ns for item in result.candidates] == sorted(
        item.first_monotonic_ns for item in result.candidates
    )


def test_long_gap_is_only_a_wait_suggestion() -> None:
    store, session_id = finalized_store(two_clicks_with_gap(seconds=12))
    result = NormalizationService().normalize_session(store, session_id)
    assert "windows.wait" not in [item.action_type for item in result.candidates]
    assert result.suggestions[0].kind == "wait_candidate"


@pytest.mark.parametrize("finalized,incomplete", [(False, False), (True, True)])
def test_unfinalized_or_incomplete_session_is_rejected(
    finalized: bool,
    incomplete: bool,
) -> None:
    store, session_id = session_store(finalized=finalized, incomplete=incomplete)
    with pytest.raises(RecordingNotNormalizable):
        NormalizationService().normalize_session(store, session_id)


def test_same_jsonl_and_manifest_normalize_byte_identically() -> None:
    store = JsonlRecordingStore.for_test(fixture_root())
    service = NormalizationService()
    first = service.normalize_session(store, FIXTURE_SESSION_ID).model_dump_json()
    second = service.normalize_session(store, FIXTURE_SESSION_ID).model_dump_json()
    assert first == second
```

- [ ] **Step 2: Run integration test and confirm failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/application/test_normalization.py tests/integration/test_recording_normalization_roundtrip.py -q
```

Expected: FAIL until the integration API and fixture are present.

- [ ] **Step 3: Integrate normalization without creating workflow steps**

```python
class NormalizationService:
    def normalize_session(
        self,
        store: RecordingStorePort,
        session_id: UUID,
    ) -> NormalizationResult: ...
    def merge(
        self,
        candidates: Sequence[StepCandidate],
        indices: Sequence[int],
    ) -> StepCandidate: ...
    def split(
        self,
        candidate: StepCandidate,
        at_event_id: UUID,
    ) -> tuple[StepCandidate, StepCandidate]: ...
```

`normalize_session` first loads the canonical manifest through the store. Reject
unless `finalized=True`, `incomplete=False`, and every event has exactly the
requested session ID; no overload accepts a free event iterable without its
manifest. Filter out-of-scope and paused events from candidates while retaining
their redacted audit metadata. Merge only adjacent compatible
text/click candidates on the same target. Split only at a source event boundary.
Use deterministic UUID5 derived from session ID plus source event IDs so repeated
normalization is byte-identical. A recording gap produces
`wait_candidate` metadata, never a fixed wait action.

Connect the control chord detector to `RecordingService` only through the
priority `ControlSink`: `Ctrl+Shift+F11` toggles recording/paused and `Ctrl+Shift+F12` requests stop.
Neither chord creates a native/raw event, so there is no control audit payload to
normalize or persist. State transitions are reported separately as safe UI
telemetry without key labels.

- [ ] **Step 4: Run the complete M2 gate**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit tests/contract tests/integration/test_recording_normalization_roundtrip.py -q
.\.venv\Scripts\python.exe scripts/export_schema.py --check
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m ruff format --check src tests scripts
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass; the golden session yields exactly
`windows.hotkey`, `windows.set_text`, `windows.press_key`.

- [ ] **Step 5: Commit and stop at the M2 review gate**

```powershell
git add src/universal_rpa/application tests/fixtures/recordings tests/unit/application tests/integration/test_recording_normalization_roundtrip.py
git commit -m "feat(universal-rpa): integrate deterministic normalization"
git status --short
```

## M2 Completion Gate

Do not start M3 until a reviewer confirms:

- callback latency test proves no filesystem/UIA work occurs in listener callbacks;
- raw scope audit and candidate exclusion both work;
- Windows-recorded thresholds drive double-click and drag;
- the required keyboard sequence splits into three actions;
- Korean uses committed UIA text or requires confirmation;
- a recursive byte search finds no password plaintext;
- raw retention is seven days unless explicitly retained.
