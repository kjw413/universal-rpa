# Universal RPA M3 Studio Editor and Validator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 비개발자가 한 한국어 Windows 앱에서 project를 만들고 대상 창을 선택해 녹화한 뒤, 3영역 편집기에서 단계·값·변수·반복·대기·검증·target을 수정하고 실행 전 오류를 확인할 수 있게 한다.

**Architecture:** PySide6 widget은 frozen domain model을 직접 변경하지 않고 typed `EditCommand` signal만 발행한다. `ProjectService`가 optimistic revision의 `workflow.json`을 관리하고, `ValidationService`는 adapter의 validate/observe 기능만 사용하여 UI 입력 없이 schema·reference·environment readiness를 보고한다.

**Tech Stack:** M1/M2, PySide6 Qt Widgets, pytest-qt, Pydantic 2, atomic JSON persistence.

## Global Constraints

- M1과 M2 completion gate 및 review가 먼저 통과해야 한다.
- 사용자 project는 source repository 밖의 사용자가 선택한 디렉터리이며 raw recording과 secret을 포함하지 않는다.
- 화면 구성은 한 `QMainWindow` 안의 Project, Recorder, Editor, Runner, Report page다.
- Editor는 left step tree, center target preview, right properties의 3영역이다.
- JSON inspector는 read-only이며 정상 사용 흐름에서 JSON 편집을 요구하지 않는다.
- 모든 사용자 오류는 한국어 안전 메시지로 표시하고 raw exception/secret을 노출하지 않는다.
- validation-only와 정적 검증은 adapter action이나 global input을 한 번도 실행하지 않는다.

---

### Task 1: Atomic project repository and immutable edit commands

**Files:**

- Create: `src/universal_rpa/infrastructure/json_repository.py`
- Create: `src/universal_rpa/application/projects.py`
- Create: `src/universal_rpa/application/editing.py`
- Create: `tests/unit/infrastructure/test_json_repository.py`
- Create: `tests/unit/application/test_projects.py`
- Create: `tests/unit/application/test_editing.py`

**Interfaces:**

- Produces: `JsonWorkflowRepository` implementing M1 `WorkflowRepositoryPort`
- Produces: `ProjectSession`, `ProjectService.create/open/save/import_input_file`
- Produces: `EditCommand` union and `WorkflowEditingService.apply`
- Consumes: M1 Workflow and M2 StepCandidate

- [ ] **Step 1: Write failing revision and immutable-edit tests**

```python
def test_stale_revision_never_overwrites_newer_workflow(tmp_path: Path) -> None:
    repository = JsonWorkflowRepository()
    original = repository.save(tmp_path, workflow(revision=1), expected_revision=0)
    repository.save(tmp_path, renamed(original, "newer"), expected_revision=original.revision)
    with pytest.raises(RevisionConflict):
        repository.save(tmp_path, renamed(original, "stale"), expected_revision=original.revision)
    assert repository.load(tmp_path).name == "newer"


def test_empty_label_is_rejected_without_mutating_workflow() -> None:
    before = workflow_with_action()
    command = RenameStep(step_id=before.steps[0].step_id, label="")
    with pytest.raises(EditRejected):
        WorkflowEditingService().apply(before, command)
    assert before.steps[0].label == "클릭"


def test_import_input_file_copies_atomically_under_project_inputs(tmp_path: Path) -> None:
    source = tmp_path / "external.csv"
    source.write_bytes(b"factory\nA\n")
    session = ProjectService().create(tmp_path / "project", "테스트")
    relative = ProjectService().import_input_file(session, source)
    assert relative.root.startswith("inputs/")
    assert (session.project_dir / Path(relative.root)).read_bytes() == source.read_bytes()


def test_import_input_rejects_symlink_or_junction_escape(tmp_path: Path) -> None:
    session = project_session(tmp_path / "project")
    with pytest.raises(ProjectBoundaryError):
        ProjectService().resolve_input(session, unsafe_inputs_link(tmp_path))


def test_full_secret_value_replaces_previous_literal_atomically() -> None:
    before = workflow_with_text(LiteralValue(value="visible"))
    after = WorkflowEditingService().apply(
        before,
        SetStepValue(
            step_id=before.steps[0].step_id,
            value=SecretRefValue(credential_ref="mis/query-password"),
        ),
    )
    encoded = after.model_dump_json()
    assert "visible" not in encoded
    assert after.steps[0].value == SecretRefValue(credential_ref="mis/query-password")


def test_import_candidates_requires_confirmation_and_creates_actions() -> None:
    command = ImportCandidates.from_review(
        ctrl_a_date_enter_candidates(),
        labels=("전체 선택", "조회일 입력", "확인"),
        confirmed_values={DATE_CANDIDATE_ID: LiteralValue(value="2026-07-27")},
    )
    after = WorkflowEditingService().apply(empty_workflow(), command)
    assert [step.action_type for step in after.steps] == [
        "windows.hotkey",
        "windows.set_text",
        "windows.press_key",
    ]
    assert all(step.failure_policy.mode == "stop" for step in after.steps)
    assert all(step.target == candidate.target for step, candidate in zip(after.steps, ctrl_a_date_enter_candidates(), strict=True))
```

- [ ] **Step 2: Run repository/edit tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/infrastructure/test_json_repository.py tests/unit/application/test_projects.py tests/unit/application/test_editing.py -q
```

Expected: FAIL because repository and edit commands do not exist.

- [ ] **Step 3: Implement atomic revisions and explicit edit commands**

```python
@dataclass(frozen=True, slots=True)
class ProjectSession:
    project_dir: Path
    workflow: Workflow
    loaded_revision: int
    dirty: bool

class ProjectService:
    def create(self, project_dir: Path, name: str) -> ProjectSession: ...
    def open(self, project_dir: Path) -> ProjectSession: ...
    def save(self, session: ProjectSession) -> ProjectSession: ...
    def import_input_file(
        self,
        session: ProjectSession,
        source: Path,
    ) -> ProjectRelativePath: ...

@dataclass(frozen=True, slots=True)
class ImportCandidates:
    candidates: tuple[StepCandidate, ...]
    labels: FrozenMapping[UUID, str]
    confirmed_values: FrozenMapping[UUID, ValueSpec | None]
    credential_refs: FrozenMapping[UUID, str]

    @classmethod
    def from_review(
        cls,
        candidates: Sequence[StepCandidate],
        labels: Sequence[str],
        confirmed_values: Mapping[UUID, ValueSpec | None],
        credential_refs: Mapping[UUID, str] | None = None,
    ) -> "ImportCandidates": ...

EditCommand = (
    RenameStep
    | PatchActionStep
    | MoveStep
    | WrapInLoop
    | MergeSteps
    | SplitStep
    | ReplaceTarget
    | SetStepValue
    | UpsertVariable
    | UpsertDataSource
    | ImportCandidates
)

class WorkflowEditingService:
    def apply(self, workflow: Workflow, command: EditCommand) -> Workflow: ...
```

`JsonWorkflowRepository` writes `workflow.json.tmp` in the project directory,
flushes and `os.fsync`s, then `os.replace`s `workflow.json`. It compares
`expected_revision` to the on-disk revision and increments exactly once.
Create only `workflow.json`, `targets/`, and `inputs/`; never create
recording/artifact folders inside the project. `import_input_file` resolves a
regular CSV/XLSX source without following a link, copies to a unique temp file in
`inputs/`, verifies SHA-256/size, atomically renames to
`inputs/<sha256-prefix>-<safe-name>`, and returns only `ProjectRelativePath`.
Existing committed bytes are never overwritten by a failed copy.

`ImportCandidates` accepts only user-reviewed candidates with nonempty labels, a
confirmed value for every `requires_confirmation` candidate, and a credential reference
for every secret candidate. Use each M2 candidate's already materialized event-time
`TargetSpec`; never rebuild it from the current desktop or silently choose among
`target_snapshot` selector candidates. A missing target requires explicit retargeting
before import. Use `stop` as failure policy and never convert wait suggestions into
actions automatically.

Implement edits with recursive pure functions and `model_copy`, deep-freeze a
defensive copy of every command mapping in its constructor, then revalidate the
entire Workflow. Value-mode selection is UI draft state only; persistence emits
one `SetStepValue(value: ValueSpec | None)` containing the complete literal,
variable ID, row template, or credential reference. Reject missing IDs, non-adjacent/incompatible merge,
invalid split boundary, move/wrap that creates depth three, empty labels, and
secret mode with a literal payload. No failed command mutates its input.

- [ ] **Step 4: Run project/edit tests and M1/M2 regression**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/infrastructure/test_json_repository.py tests/unit/application/test_projects.py tests/unit/application/test_editing.py -q
.\.venv\Scripts\python.exe -m pytest tests/unit tests/contract tests/integration/test_recording_normalization_roundtrip.py -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass and the old workflow bytes remain intact on revision conflict.

- [ ] **Step 5: Commit project persistence and editing**

```powershell
git add src/universal_rpa/infrastructure/json_repository.py src/universal_rpa/application/projects.py src/universal_rpa/application/editing.py tests/unit/infrastructure/test_json_repository.py tests/unit/application/test_projects.py tests/unit/application/test_editing.py
git commit -m "feat(universal-rpa): add project editing service"
```

---

### Task 2: Static and environment ValidationService

**Files:**

- Create: `src/universal_rpa/application/validation.py`
- Create: `tests/helpers/validation_fakes.py`
- Create: `tests/unit/application/test_validation.py`

**Interfaces:**

- Produces: `ValidationContext`, `ValidationService.validate_static`,
  `validate_environment`
- Consumes: `Workflow`, `AdapterRegistry`, `DataSourcePort`,
  `SecretStorePort`, runtime environment provider

- [ ] **Step 1: Write failing fail-closed validation tests**

```python
def test_missing_adapter_is_an_error() -> None:
    report = validation_service(registry=AdapterRegistry()).validate_static(
        workflow_with_action("windows.click")
    )
    assert issue_codes(report) == {ErrorCode.ADAPTER_MISSING}


def test_validation_never_executes_an_action() -> None:
    adapter = SpyAdapter()
    registry = registry_with(adapter)
    report = validation_service(registry=registry).validate_environment(
        valid_workflow(),
        validation_context(),
    )
    assert report.is_valid
    assert adapter.execute_calls == 0


@pytest.mark.parametrize(
    ("action_type", "parameters"),
    [
        ("windows.click", {"button": "left"}),
        ("windows.double_click", {"button": "right"}),
        ("windows.drag", {"button": "left", "end_point": {"x": 0.8, "y": 0.4}}),
        ("windows.scroll", {"horizontal_delta": -120, "vertical_delta": 240}),
        ("windows.press_key", {"key": "enter"}),
        ("windows.hotkey", {"key": "a", "modifiers": ["ctrl"]}),
        ("windows.activate_window", {}),
        ("windows.set_text", {}),
        ("windows.wait", {}),
    ],
)
def test_every_builtin_windows_action_uses_the_canonical_m1_parameter_model(
    action_type: str,
    parameters: Mapping[str, JsonValue],
) -> None:
    adapter = SpyAdapter(descriptor=windows_descriptor())
    step = valid_windows_step(action_type, parameters=parameters)
    report = validation_service(registry=registry_with(adapter)).validate_static(
        workflow_with_steps(step)
    )
    assert report.is_valid
    assert adapter.validated_action_specs == (step,)
    assert step.parameters == validate_builtin_action_parameters(action_type, parameters)


def test_if_present_zero_match_is_optional_but_ambiguity_is_error() -> None:
    adapter = scripted_target_adapter(matches=0)
    report = validation_service(registry=registry_with(adapter)).validate_environment(
        workflow_with_if_present(),
        validation_context(),
    )
    assert report.is_valid
    adapter.script(matches=2)
    report = validation_service(registry=registry_with(adapter)).validate_environment(
        workflow_with_if_present(),
        validation_context(),
    )
    assert ErrorCode.TARGET_AMBIGUOUS in issue_codes(report)


def test_wait_and_postcondition_targets_may_be_absent_before_execution() -> None:
    adapter = scripted_target_adapter(matches=0)
    report = validation_service(registry=registry_with(adapter)).validate_environment(
        workflow_with_delayed_wait_target(),
        validation_context(),
    )
    assert report.is_valid
    assert adapter.validation_modes == ["deferred", "may_be_absent_now"]


def test_row_binding_outside_loop_is_rejected() -> None:
    report = validation_service().validate_static(workflow_with_unscoped_row_binding())
    assert any(issue.path.endswith(".value") for issue in report.errors)


@pytest.mark.parametrize(
    "action_type",
    [
        "windows.click",
        "windows.double_click",
        "windows.drag",
        "windows.scroll",
        "windows.press_key",
        "windows.hotkey",
        "windows.activate_window",
        "windows.set_text",
        "clipboard.read_clipboard",
        "clipboard.extract_table",
    ],
)
def test_mvp_input_query_and_copy_action_requires_declared_verification(
    action_type: str,
) -> None:
    registry = registry_with(mvp_descriptor_for(action_type))
    workflow = workflow_with_action(action_type, postcondition=None, assertions=())
    report = validation_service(registry=registry).validate_static(workflow)
    assert any(issue.code is ErrorCode.INVALID_SCHEMA for issue in report.errors)


def test_retry_authority_comes_only_from_adapter_descriptor() -> None:
    adapter = fake_adapter(
        action="fake.submit",
        idempotent_actions=frozenset(),
        retryable_errors_by_action={},
    )
    workflow = workflow_with_action(
        "fake.submit",
        failure_policy=retry_policy(1),
        postcondition=successful_postcondition(),
    )
    report = validation_service(registry=registry_with(adapter)).validate_static(workflow)
    assert any("재시도" in issue.safe_message for issue in report.errors)


def test_intrinsically_verified_action_does_not_require_user_assertion() -> None:
    adapter = fake_adapter(
        action="fake.atomic_save",
        verification_by_action={"fake.atomic_save": "intrinsic"},
    )
    report = validation_service(registry=registry_with(adapter)).validate_static(
        workflow_with_action("fake.atomic_save", postcondition=None, assertions=())
    )
    assert report.is_valid

def test_assertion_must_be_compatible_with_action_and_subject_kind() -> None:
    adapter = fake_adapter(
        action="fake.read",
        assertions=frozenset({"fake.table"}),
        assertions_by_action={"fake.read": frozenset()},
        assertion_input_kind={"fake.table": "table"},
    )
    report = validation_service(registry=registry_with(adapter)).validate_static(
        workflow_with_action("fake.read", assertions=(assertion("fake.table"),))
    )
    assert any(issue.code is ErrorCode.INVALID_SCHEMA for issue in report.errors)
```

- [ ] **Step 2: Run validation tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/application/test_validation.py -q
```

Expected: FAIL with missing service.

- [ ] **Step 3: Implement ordered, side-effect-free validation**

```python
@dataclass(frozen=True, slots=True)
class ValidationContext:
    project_dir: Path
    runtime: RuntimeEnvironment | None
    variable_values: FrozenMapping[str, DataCell]
    output_root: Path

class ValidationService:
    def validate_static(self, workflow: Workflow) -> ValidationReport: ...
    def validate_environment(
        self,
        workflow: Workflow,
        context: ValidationContext,
    ) -> ValidationReport: ...
```

Static validation collects all errors in stable path order: schema/references,
installed namespaces, action capability and exact parameter model, value mode,
row-binding scope,
loop depth/limits, descriptor-owned retry policy, verification mode, and finite
wait. For every action, read `AdapterDescriptor.verification_by_action`:
`postcondition_or_assertion` requires a finite postcondition or at least one
assertion; `intrinsic` delegates proof to the adapter result/output commit; and
`none` is permitted only when the descriptor explicitly declares it. Validate
each attached assertion against `assertions_by_action` and require its declared
`assertion_input_kind`; unknown/incompatible assertion namespaces fail before
execution. It calls the namespace owner's pure `validate_action_spec`,
`validate_condition_spec`, and `validate_assertion_spec` for every applicable
spec; these methods cannot perform native/UIA I/O. All MVP input/query/copy actions use
`postcondition_or_assertion`; `windows.wait` and atomic `tabular.save_table` use
`intrinsic`. A retry policy is valid only when the action appears in descriptor
`idempotent_actions`; workflow JSON has no field that can override this.
Environment validation performs exactly: readable schema/revision, installed
adapters, unique process/window, valid target/environment, prepared required
variables/data sources, existing secret references, writable resolved output
root, and unlocked resolved outputs. Output specs are `OutputRelativePath` values
resolved beneath `ValidationContext.output_root` with absolute/parent/device/
reparse escape rejection. It calls only adapter `validate_target`, data-source
`preview`, secret `exists`, output-root writability/lock probes, process/window
uniqueness, and interactive-desktop probe. Target validation mode follows
workflow role: an immediate action target is `must_exist_now`; an
`if_present`/presence target is `may_be_absent_now`; wait/pre/post targets and
children gated by optional presence are `deferred` or
`may_be_absent_now`. Normal zero-match is accepted only in the latter modes;
ambiguity, environment mismatch, adapter error, and cancellation always remain
errors. It never calls `AutomationAdapter.execute`. It also verifies that every
variable column source resolves to the matching CSV/XLSX data-source kind and
that preview headers contain the named column.

`ValidationContext` defensively copies `variable_values` to `FrozenMapping` and
normalizes both roots when it is created; raw construction may accept `Mapping`,
but validation never exposes or retains a caller-owned mutable mapping.
`ValidationReport.is_valid` is true only with no error severity; warnings do not
enable unsafe fallback. Convert every caught exception to a known `ErrorCode`
and Korean `safe_message`.

- [ ] **Step 4: Run validation and complete non-UI regression**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/application/test_validation.py -q
.\.venv\Scripts\python.exe -m pytest tests/unit tests/contract tests/integration/test_recording_normalization_roundtrip.py -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass; any preflight error has zero adapter action calls.

- [ ] **Step 5: Commit the validation service**

```powershell
git add src/universal_rpa/application/validation.py tests/helpers/validation_fakes.py tests/unit/application/test_validation.py
git commit -m "feat(universal-rpa): add fail-closed validation"
```

---

### Task 3: QApplication, composition root, main window, and project home

**Files:**

- Create: `src/universal_rpa/__main__.py`
- Modify: `src/universal_rpa/bootstrap.py`
- Create: `src/universal_rpa/ui/__init__.py`
- Create: `src/universal_rpa/ui/app.py`
- Create: `src/universal_rpa/ui/main_window.py`
- Create: `src/universal_rpa/ui/project_home.py`
- Create: `src/universal_rpa/ui/workers.py`
- Modify: `pyproject.toml`
- Create: `tests/unit/test_bootstrap_retention.py`
- Create: `tests/ui/test_app.py`
- Create: `tests/ui/test_main_window.py`
- Create: `tests/ui/test_project_home.py`

**Interfaces:**

- Produces: console entry point `universal-rpa-studio`
- Produces: `create_application`, `build_main_window`, `main`
- Produces: `MainWindow.open_session`, `show_validation`
- Consumes: application services only through `AppServices`

- [ ] **Step 1: Write failing one-window and cancellation tests**

```python
def test_bootstrap_runs_recording_retention_once() -> None:
    store = SpyRecordingStore()
    build_services(recording_store=store, now=fixed_utc_now())
    assert store.purge_calls == [(fixed_utc_now(), timedelta(days=7))]


def test_all_pages_live_in_one_main_window(qtbot: QtBot, services: AppServices) -> None:
    window = MainWindow(services)
    qtbot.addWidget(window)
    assert [window.page_name(index) for index in range(window.page_count())] == [
        "project",
        "recorder",
        "editor",
        "runner",
        "report",
    ]


def test_cancelled_new_project_creates_nothing(
    qtbot: QtBot, tmp_path: Path, services: AppServices
) -> None:
    page = ProjectHome(services.project_service)
    qtbot.addWidget(page)
    page.request_project_directory = lambda: None
    qtbot.mouseClick(page.new_project_button, Qt.MouseButton.LeftButton)
    assert list(tmp_path.iterdir()) == []


def test_corrupt_project_shows_safe_korean_error(qtbot: QtBot, services: AppServices) -> None:
    window = MainWindow(services)
    qtbot.addWidget(window)
    window.open_project(corrupt_project_dir())
    assert "워크플로를 열 수 없습니다" in window.status_message()
```

- [ ] **Step 2: Run UI tests offscreen and verify failure**

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest tests/unit/test_bootstrap_retention.py tests/ui/test_app.py tests/ui/test_main_window.py tests/ui/test_project_home.py -q
```

Expected: FAIL with missing UI modules.

- [ ] **Step 3: Implement one application shell with injected services**

```python
@dataclass(frozen=True, slots=True)
class AppServices:
    project_service: ProjectService
    recording_service: RecordingService
    normalization_service: NormalizationService
    editing_service: WorkflowEditingService
    validation_service: ValidationService
    adapter_registry: AdapterRegistry

def create_application(argv: Sequence[str]) -> QApplication: ...
def build_main_window(services: AppServices) -> MainWindow: ...
def main(argv: Sequence[str] | None = None) -> int: ...
```

Call DPI awareness before QApplication. Set Korean locale and application name.
`MainWindow` owns one `QStackedWidget` with five named pages and a persistent
navigation sidebar; runner/report may show a disabled Korean readiness message
until M4/M5. Prompt Save/Discard/Cancel before project switch when session is
dirty. `bootstrap.py` is the only production code that constructs concrete
repositories/adapters; tests inject fakes. During service construction, call recording
`purge_expired(now=UTC, retention=timedelta(days=7))` once. Report a safe warning if a
locked session cannot be removed; do not prevent Studio startup.

Add:

```toml
[project.scripts]
universal-rpa-studio = "universal_rpa.__main__:main"
```

- [ ] **Step 4: Run UI and non-UI quality gates**

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest tests/unit/test_bootstrap_retention.py tests/ui/test_app.py tests/ui/test_main_window.py tests/ui/test_project_home.py -q
.\.venv\Scripts\python.exe -m pytest tests/unit tests/contract tests/integration/test_recording_normalization_roundtrip.py -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass and the Qt event loop is not started by imports.

- [ ] **Step 5: Commit the integrated app shell**

```powershell
git add src/universal_rpa/__main__.py src/universal_rpa/bootstrap.py src/universal_rpa/ui pyproject.toml tests/unit/test_bootstrap_retention.py tests/ui
git commit -m "feat(universal-rpa): add integrated Studio shell"
```

---

### Task 4: Recorder page, target-window chooser, and always-visible banner

**Files:**

- Create: `src/universal_rpa/ui/recorder_page.py`
- Modify: `src/universal_rpa/ui/workers.py`
- Modify: `src/universal_rpa/ui/main_window.py`
- Create: `tests/ui/test_recorder_page.py`
- Create: `tests/ui/test_recording_worker.py`

**Interfaces:**

- Produces: `RecorderPage.recording_completed(NormalizationResult)` and reviewed
  `ImportCandidates`
- Produces: `RecordingWorker` lifecycle signals
- Consumes: Windows window catalog, RecordingService, NormalizationService

- [ ] **Step 1: Write failing recorder-state UI tests**

```python
def test_start_requires_an_explicit_target(qtbot: QtBot, page: RecorderPage) -> None:
    qtbot.mouseClick(page.start_button, Qt.MouseButton.LeftButton)
    assert page.recording_worker_start_count == 0
    assert "대상 창을 선택" in page.validation_text.text()


def test_recording_banner_never_disappears_while_active(
    qtbot: QtBot, page: RecorderPage
) -> None:
    page.set_targets([recording_target()])
    page.target_combo.setCurrentIndex(0)
    qtbot.mouseClick(page.start_button, Qt.MouseButton.LeftButton)
    assert page.banner.isVisible()
    assert "Ctrl+Shift+F12" in page.banner.text()


def test_paused_events_do_not_enter_preview_candidates(
    qtbot: QtBot, page: RecorderPage
) -> None:
    page.on_state_changed("paused")
    assert page.banner.property("state") == "paused"
    assert page.pause_button.text() == "계속"
```

- [ ] **Step 2: Run recorder UI tests and verify failure**

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest tests/ui/test_recorder_page.py tests/ui/test_recording_worker.py -q
```

Expected: FAIL because the recorder page is absent.

- [ ] **Step 3: Implement worker-backed recording controls**

```python
class RecordingWorker(QObject):
    state_changed = Signal(str)
    failed = Signal(object)
    completed = Signal(object)

    @Slot(object)
    def start(self, target: RecordingTarget) -> None: ...
    @Slot()
    def pause(self) -> None: ...
    @Slot()
    def resume(self) -> None: ...
    @Slot()
    def stop(self) -> None: ...

class RecorderPage(QWidget):
    recording_completed = Signal(object)

    def refresh_targets(self) -> None: ...
    def set_targets(self, targets: Sequence[RecordingTarget]) -> None: ...
```

Move `RecordingWorker` to a dedicated `QThread`; never block the UI thread on
worker drain. Disable target change while active. Banner shows target app/window,
recording/paused state, elapsed time, `Ctrl+Shift+F11` toggle, and `Ctrl+Shift+F12` stop at all times.
On completion, pass the returned session ID to `NormalizationService.normalize_session(recording_store, session_id)` so the canonical finalized/incomplete manifest is checked before showing a review table. Never call a free-event normalization path.
Require labels and confirmations for IME/variable/secret candidates, then create one
`ImportCandidates` command and emit the result. Do not import a wait suggestion as an
action. Recording intentionally contains no screenshot, so an imported target shows
`미리보기 없음`; the user may explicitly invoke “대상/미리보기 다시 캡처”, which uses
Task 6 `TargetCapturePort` and never silently recaptures from changed live state. A
candidate conversion failure leaves the workflow unchanged. On failure show only safe
message and keep the raw incomplete session unavailable for workflow import.

- [ ] **Step 4: Run recorder UI and service regression**

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest tests/ui/test_recorder_page.py tests/ui/test_recording_worker.py tests/unit/application/test_recording.py -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass and a slow stop does not freeze a Qt timer probe.

- [ ] **Step 5: Commit the recorder UI**

```powershell
git add src/universal_rpa/ui/recorder_page.py src/universal_rpa/ui/workers.py src/universal_rpa/ui/main_window.py tests/ui/test_recorder_page.py tests/ui/test_recording_worker.py
git commit -m "feat(universal-rpa): add Studio recorder page"
```

---

### Task 5: Three-pane editor, recursive tree model, preview, and JSON inspector

**Files:**

- Create: `src/universal_rpa/ui/editor_page.py`
- Create: `src/universal_rpa/ui/step_tree_model.py`
- Create: `src/universal_rpa/ui/target_preview.py`
- Create: `src/universal_rpa/ui/json_inspector.py`
- Create: `tests/ui/test_editor_page.py`
- Create: `tests/ui/test_step_tree_model.py`
- Create: `tests/ui/test_target_preview.py`

**Interfaces:**

- Produces: `WorkflowEditor.edit_requested`, `step_test_requested`,
  `retarget_requested`
- Produces: `WorkflowTreeModel.set_workflow`, `step_id`
- Produces: `TargetPreviewResolver.resolve`
- Consumes: `ProjectSession`, `WorkflowEditingService`

- [ ] **Step 1: Write failing selection and read-only tests**

```python
def test_selection_updates_all_three_panes(qtbot: QtBot, editor: WorkflowEditor) -> None:
    editor.set_session(project_session_with_two_steps())
    selected = editor.tree_model.index_for_step(SECOND_STEP_ID)
    editor.tree_view.setCurrentIndex(selected)
    assert editor.target_preview.step_id == SECOND_STEP_ID
    assert editor.property_panel.step_id == SECOND_STEP_ID


def test_json_inspector_is_read_only(qtbot: QtBot, editor: WorkflowEditor) -> None:
    editor.show_json_inspector()
    assert editor.json_inspector.text_edit.isReadOnly()
    before = editor.json_inspector.text_edit.toPlainText()
    qtbot.keyClicks(editor.json_inspector.text_edit, "malicious")
    assert editor.json_inspector.text_edit.toPlainText() == before


def test_drag_drop_rejects_loop_depth_three(editor: WorkflowEditor) -> None:
    editor.set_session(project_session_with_depth_two())
    assert editor.tree_model.can_move(INNER_LOOP_ID, under=INNER_ACTION_ID) is False
```

- [ ] **Step 2: Run editor-frame tests and verify failure**

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest tests/ui/test_editor_page.py tests/ui/test_step_tree_model.py tests/ui/test_target_preview.py -q
```

Expected: FAIL with missing widgets/models.

- [ ] **Step 3: Implement the 3:5:3 splitter and command-only mutation path**

```python
class WorkflowEditor(QWidget):
    edit_requested = Signal(object)
    step_test_requested = Signal(object)  # UUID
    retarget_requested = Signal(object)   # UUID

    def set_session(self, session: ProjectSession) -> None: ...

class WorkflowTreeModel(QAbstractItemModel):
    def set_workflow(self, workflow: Workflow) -> None: ...
    def step_id(self, index: QModelIndex) -> UUID | None: ...
    def index_for_step(self, step_id: UUID) -> QModelIndex: ...

class TargetPreviewResolver(Protocol):
    def resolve(
        self,
        project_dir: Path,
        step_id: UUID,
        target: TargetSpec,
    ) -> Path | None: ...

class TargetPreview(QWidget):
    def __init__(self, resolver: TargetPreviewResolver) -> None: ...
    def set_target(
        self,
        project_dir: Path,
        step_id: UUID,
        target: TargetSpec | None,
    ) -> None: ...
```

Use a horizontal splitter with step tree, scaled preview, and property host.
Tree roles contain only step ID, label, kind, enabled, and validation severity;
never secret values. All rename/move/wrap/merge/split gestures emit an
`EditCommand`; only the page coordinator calls the editing service and replaces
the session. `TargetPreview` asks the injected `TargetPreviewResolver` protocol to resolve a
hash-derived path under the current project; Task 6 supplies the production
`TargetPreviewStore`, while Task 5 tests inject a fake. It never accepts an arbitrary path.
Missing/unreadable shows the Korean message `미리보기 없음`. Draw
`WindowsTarget.target_region` as an overlay; saved pixels are already masked.
JSON inspector refreshes from `dump_workflow` and has no paste/edit action.

- [ ] **Step 4: Run editor-frame and project tests**

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest tests/ui/test_editor_page.py tests/ui/test_step_tree_model.py tests/ui/test_target_preview.py tests/unit/application/test_editing.py -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass; every mutation is observable as one typed command.

- [ ] **Step 5: Commit the editor frame**

```powershell
git add src/universal_rpa/ui/editor_page.py src/universal_rpa/ui/step_tree_model.py src/universal_rpa/ui/target_preview.py src/universal_rpa/ui/json_inspector.py tests/ui/test_editor_page.py tests/ui/test_step_tree_model.py tests/ui/test_target_preview.py
git commit -m "feat(universal-rpa): add three-pane workflow editor"
```

---

### Task 6: Property forms, variable/loop/wait/assertion editors, retarget, and validation

**Files:**

- Create: `src/universal_rpa/adapters/tabular/__init__.py`
- Create: `src/universal_rpa/adapters/tabular/data_sources.py`
- Create: `src/universal_rpa/application/recording_privacy.py`
- Create: `src/universal_rpa/infrastructure/target_preview_store.py`
- Modify: `src/universal_rpa/bootstrap.py`
- Create: `src/universal_rpa/ui/property_panel.py`
- Create: `src/universal_rpa/ui/action_parameter_editor.py`
- Create: `src/universal_rpa/ui/variable_dialog.py`
- Create: `src/universal_rpa/ui/loop_dialog.py`
- Create: `src/universal_rpa/ui/wait_assertion_editor.py`
- Create: `src/universal_rpa/ui/target_picker.py`
- Modify: `src/universal_rpa/ui/editor_page.py`
- Modify: `src/universal_rpa/ui/target_preview.py`
- Modify: `src/universal_rpa/ui/main_window.py`
- Create: `tests/unit/adapters/tabular/test_data_sources.py`
- Create: `tests/unit/application/test_recording_privacy.py`
- Create: `tests/unit/infrastructure/test_target_preview_store.py`
- Create: `tests/ui/test_property_panel.py`
- Create: `tests/ui/test_action_parameter_editor.py`
- Create: `tests/ui/test_variable_dialog.py`
- Create: `tests/ui/test_loop_dialog.py`
- Create: `tests/ui/test_target_picker.py`
- Create: `tests/ui/test_editor_validation.py`

**Interfaces:**

- Produces: `TabularDataSourceProvider` implementing M1 `DataSourcePort`
- Produces: `RecordingPrivacyService.purge_before_secret_mode`
- Produces: `TargetPreviewStore` implementing Task 5 `TargetPreviewResolver`,
  plus `stage_masked`, `resolve`, `commit_variant`, `discard_variant`
- Produces: complete edit commands for value modes, variables, loops, waits,
  assertions, failure policy, target replacement
- Produces: `ActionParameterEditor` backed by M1 canonical parameter models
- Produces: `WorkflowEditor.validate_requested`
- Consumes: `ValidationService`, M1 `TargetCapturePort`

- [ ] **Step 1: Write failing value-mode, loop-limit, and retarget tests**

```python
def test_csv_preview_uses_explicit_encoding_without_guessing(tmp_path: Path) -> None:
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    path = input_dir / "rows.csv"
    path.write_bytes("공장\nA".encode("cp949"))
    spec = csv_source("inputs/rows.csv", encoding="utf-8")
    with pytest.raises(RpaError):
        TabularDataSourceProvider().preview(tmp_path, spec)

@pytest.mark.parametrize(
    ("action_type", "draft", "expected"),
    [
        ("windows.click", {"button": "right"}, MouseButtonParameters(button="right")),
        ("windows.double_click", {"button": "left"}, MouseButtonParameters()),
        (
            "windows.drag",
            {"button": "left", "end_point": {"x": 0.8, "y": 0.4}},
            DragParameters(button="left", end_point=RelativePoint(x=0.8, y=0.4)),
        ),
        (
            "windows.scroll",
            {"horizontal_delta": -120, "vertical_delta": 240},
            ScrollParameters(horizontal_delta=-120, vertical_delta=240),
        ),
        ("windows.press_key", {"key": "enter"}, PressKeyParameters(key="enter")),
        (
            "windows.hotkey",
            {"key": "a", "modifiers": ["ctrl"]},
            HotkeyParameters(key="a", modifiers=("ctrl",)),
        ),
        ("windows.activate_window", {}, NoParameters()),
        ("windows.set_text", {}, NoParameters()),
        ("windows.wait", {}, NoParameters()),
    ],
)
def test_action_parameter_editor_emits_only_canonical_m1_parameters(
    qtbot: QtBot,
    action_parameter_editor: ActionParameterEditor,
    action_type: str,
    draft: Mapping[str, JsonValue],
    expected: BaseModel,
) -> None:
    action_parameter_editor.set_action(action_type, FrozenMapping.empty())
    set_parameter_controls(action_parameter_editor, draft)
    assert action_parameter_editor.pending_parameters() == deep_freeze_json(
        expected.model_dump(mode="json")
    )


def test_invalid_action_parameter_draft_emits_no_edit_command(
    qtbot: QtBot, panel: PropertyPanel
) -> None:
    panel.set_step(action_step("windows.scroll"))
    set_parameter_controls(
        panel.action_parameter_editor,
        {"horizontal_delta": 0, "vertical_delta": 0},
    )
    assert panel.pending_command() is None
    assert panel.action_parameter_editor.error_text


def test_secret_mode_stays_draft_until_complete_reference_is_selected(
    qtbot: QtBot, panel: PropertyPanel
) -> None:
    panel.set_step(text_step(LiteralValue(value="sensitive")))
    panel.mode_combo.setCurrentText("비밀값")
    assert panel.pending_command() is None
    assert "sensitive" not in all_model_role_text(panel)
    panel.select_credential_reference("mis/query-password")
    command = panel.pending_command()
    assert isinstance(command, SetStepValue)
    assert command.value == SecretRefValue(credential_ref="mis/query-password")
    assert "sensitive" not in repr(command)


def test_loop_dialog_shows_defaults_and_hard_limits(qtbot: QtBot) -> None:
    dialog = LoopDialog()
    qtbot.addWidget(dialog)
    assert dialog.max_iterations.value() == 1_000
    assert dialog.max_iterations.maximum() == 10_000
    assert dialog.max_runtime_seconds.value() == 7_200
    assert dialog.max_runtime_seconds.maximum() == 86_400


def test_cancelled_retarget_preserves_old_target_and_preview(
    qtbot: QtBot,
    editor: WorkflowEditor,
) -> None:
    old_target = editor.selected_step().target
    old_preview = editor.target_preview.preview_path
    editor.target_picker_factory = cancelled_target_picker
    editor.retarget_selected_step()
    assert editor.selected_step().target == old_target
    assert editor.target_preview.preview_path == old_preview


def test_unmasked_capture_bytes_never_reach_project_disk(tmp_path: Path) -> None:
    sentinel = b"UNMASKED_SENTINEL"
    store = TargetPreviewStore()
    captured = capture_result_with_png(sentinel=sentinel, target_region=full_region())
    staged = store.stage_masked(tmp_path, STEP_ID, captured)
    assert staged.path.is_relative_to((tmp_path / "targets").resolve())
    disk_bytes = b"".join(path.read_bytes() for path in (tmp_path / "targets").iterdir())
    assert sentinel not in disk_bytes
    assert image_pixel(staged.path, SENSITIVE_POINT) == BLACK


def test_project_save_failure_discards_new_masked_variant_and_keeps_old(
    qtbot: QtBot,
    editor_with_failing_save: WorkflowEditor,
) -> None:
    old_target = editor_with_failing_save.selected_step().target
    old_preview = editor_with_failing_save.target_preview.preview_path
    with pytest.raises(ProjectSaveFailed):
        editor_with_failing_save.apply_capture(capture_result())
    assert editor_with_failing_save.selected_step().target == old_target
    assert editor_with_failing_save.target_preview.preview_path == old_preview
    assert editor_with_failing_save.preview_store.staged_variants == ()


def test_user_sensitive_region_is_persisted_and_masked(
    qtbot: QtBot,
    picker: TargetPicker,
) -> None:
    picker.set_capture_result(capture_result(target_region=target_region()))
    picker.region_editor.add_region(user_region())
    picker.accept()
    result = picker.captured_result()
    assert result is not None and result.target is not None
    target = WindowsTarget.model_validate(result.target.payload)
    assert user_region() in target.user_sensitive_regions


def test_mandatory_password_region_cannot_be_removed(qtbot: QtBot, picker: TargetPicker) -> None:
    mandatory = password_region()
    picker.set_capture_result(capture_result(mandatory_regions=(mandatory,)))
    assert picker.region_editor.remove_region(mandatory) is False
    target = WindowsTarget.model_validate(picker.captured_result().target.payload)
    assert mandatory in target.mandatory_sensitive_regions


def test_secret_mode_is_rejected_when_source_raw_session_cannot_be_purged() -> None:
    store = failing_delete_store()
    privacy = RecordingPrivacyService(recording_store=store)
    with pytest.raises(SensitiveSourcePurgeFailed):
        privacy.purge_before_secret_mode([SOURCE_SESSION_ID], allow_retained=True)
    assert store.delete_attempts == 1


def test_secret_mode_masks_the_saved_target_preview(qtbot: QtBot, editor: WorkflowEditor) -> None:
    editor.set_session(session_with_literal_and_red_preview())
    editor.change_selected_value_mode("secret_ref")
    image = QImage(str(editor.target_preview.preview_path))
    assert image.pixelColor(SENSITIVE_POINT) == QColor(Qt.GlobalColor.black)
```

- [ ] **Step 2: Run property/form tests and verify failure**

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest tests/unit/adapters/tabular/test_data_sources.py tests/unit/application/test_recording_privacy.py tests/unit/infrastructure/test_target_preview_store.py tests/ui/test_property_panel.py tests/ui/test_action_parameter_editor.py tests/ui/test_variable_dialog.py tests/ui/test_loop_dialog.py tests/ui/test_target_picker.py tests/ui/test_editor_validation.py -q
```

Expected: FAIL because forms, target capture flow, and masked preview store are absent.

- [ ] **Step 3: Implement nondeveloper forms with domain validation at submit**

`PropertyPanel` shows label, enabled, action, target summary, value mode, wait,
assertions, failure policy, and an injected `ActionParameterEditor`. The editor
has explicit Korean controls for click/double-click button, drag button and
normalized end point, signed horizontal/vertical scroll deltas, whitelisted
press key, ordered hotkey modifiers plus primary key, and no-parameter
activate-window/set-text/wait actions. On every change it calls M1
`validate_builtin_action_parameters`; incomplete/invalid drafts remain UI state
and emit no `PatchActionStep`, while a valid submit carries only the returned
`FrozenJsonObject`. Mode mapping is exactly:

| Korean label | model |
|---|---|
| 고정값 | `LiteralValue` |
| 실행 변수 | `VariableValue` |
| 반복 열 | `RowBindingValue` |
| 비밀값 | `SecretRefValue` |

`TabularDataSourceProvider` resolves every `ProjectRelativePath` against the
explicit `project_dir`, rejects absolute/parent/device/link/junction escape, and
defensively builds a new `FrozenMapping` for every preview and iteration row;
raw CSV/XLSX parser mappings never cross the port boundary. It supports
immutable inline scalar rows, CSV with the exact selected
`utf-8`/`utf-8-sig`/`cp949` encoding, and XLSX
`read_only=True, data_only=True` with an existing named sheet. It rejects
blank/duplicate headers, width drift, nested cells, and missing required columns.
Register this provider as the production `DataSourcePort` in bootstrap; it is not
yet an AutomationAdapter.

`VariableDialog` exposes the exact M1 source/type matrix: direct run input,
strict fixed default, inline or CSV/XLSX-backed choice, date rule, and Credential
Manager reference. It supports only approved date operations. `LoopDialog`
passes the active project directory to `DataSourcePort`, shows required columns,
limits depth to two, and exposes skip-row only inside a loop.
`WaitAssertionEditor` defaults timeout to 30,000 ms and filters assertions through
`AdapterDescriptor.assertions_by_action` and `assertion_input_kind`.

`TargetPicker` invokes the injected M1 `TargetCapturePort.capture_target` on a
worker with cancellation. It displays every returned candidate and issue;
ambiguity requires an explicit candidate selection. It overlays
`target_region` from the selected candidate payload. Mandatory password/secret
regions are visibly locked and non-removable; users may add/remove only
`user_sensitive_regions`. It updates the selected immutable `TargetSpec`
atomically and returns the complete capture result—PNG included in memory—rather
than only `TargetSpec`.

```python
class TabularDataSourceProvider(DataSourcePort):
    def preview(
        self,
        project_dir: Path,
        spec: DataSourceSpec,
        max_rows: int = 20,
    ) -> DataPreview: ...
    def iter_rows(
        self,
        project_dir: Path,
        spec: DataSourceSpec,
        required_columns: frozenset[str],
    ) -> Iterator[FrozenMapping[str, DataCell]]: ...

class RecordingPrivacyService:
    def purge_before_secret_mode(
        self,
        source_session_ids: Sequence[UUID] | None,
        *,
        allow_retained: bool,
    ) -> None: ...

class ActionParameterEditor(QWidget):
    parameters_changed = Signal(object)
    def set_action(
        self,
        action_type: str,
        parameters: FrozenJsonObject,
    ) -> None: ...
    def pending_parameters(self) -> FrozenJsonObject | None: ...

class PropertyPanel(QWidget):
    command_ready = Signal(object)
    action_parameter_editor: ActionParameterEditor
    def set_step(self, step: Step) -> None: ...
    def pending_command(self) -> EditCommand | None: ...

@dataclass(frozen=True, slots=True)
class MaskedPreviewVariant:
    path: Path
    target_sha256: str

class TargetPreviewStore:
    def stage_masked(
        self,
        project_dir: Path,
        step_id: UUID,
        capture: TargetCaptureResult,
    ) -> MaskedPreviewVariant: ...
    def resolve(
        self,
        project_dir: Path,
        step_id: UUID,
        target: TargetSpec,
    ) -> Path | None: ...
    def commit_variant(self, variant: MaskedPreviewVariant) -> None: ...
    def discard_variant(self, variant: MaskedPreviewVariant) -> None: ...

class TargetPicker(QDialog):
    def captured_result(self) -> TargetCaptureResult | None: ...
```

`TargetPreviewStore` derives
`targets/<step_uuid>-<canonical-target-sha256-prefix>.png`, resolves every path
under the real project `targets/` directory without following reparse-point
escapes, decodes PNG only in memory, paints the complete persisted
union of `WindowsTarget.mandatory_sensitive_regions` and
`user_sensitive_regions`, and only then writes a masked same-directory temp file
plus `os.replace`. It decodes and requires the PNG dimensions to equal the selected Windows
candidate coordinate fallback client dimensions. If a preview has no trustworthy
target region, dimension basis, or mandatory-mask metadata, fail closed and store
no image. Unmasked bytes are never written,
even to temp/orphan files.

Retarget order is: capture and user confirmation → stage masked variant → apply
`ReplaceTarget` to a copy → atomically save workflow → commit variant and delete
other variants for that step. Cancellation or any edit/save failure discards the
new masked variant and preserves old target/workflow/preview. Secret mode first purges source raw sessions, adds the target region to
`mandatory_sensitive_regions`, re-masks or deletes every old variant for the
step, and only then saves
the workflow; any failure rejects the change.

WorkflowEditor keeps the normalization result session ID only in application
state, never in workflow JSON. Before emitting a complete
`SetStepValue(SecretRefValue(...))`, call `RecordingPrivacyService`: delete the
known source sessions; if a reopened workflow has no mapping, ask the user to
delete all local raw sessions and pass them explicitly. A deletion or masking
failure rejects the mode change.

After each successful command, run static validation and display step badges.
An explicit “환경 검사” action runs `validate_environment` in a worker and
shows the complete report. Step-test signal stays disabled until M4
`ExecutionService.test_step` is connected.

- [ ] **Step 4: Run the complete M3 gate**

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest tests/ui tests/unit tests/contract tests/integration/test_recording_normalization_roundtrip.py -q
.\.venv\Scripts\python.exe scripts/export_schema.py --check
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m ruff format --check src tests scripts
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass; changing to secret removes plaintext from raw source session,
Workflow, command repr, Qt model roles, inspector JSON, and target preview pixels.

- [ ] **Step 5: Commit and stop at the M3 review gate**

```powershell
git add src/universal_rpa/adapters/tabular src/universal_rpa/application/recording_privacy.py src/universal_rpa/infrastructure/target_preview_store.py src/universal_rpa/bootstrap.py src/universal_rpa/ui tests/unit/adapters/tabular/test_data_sources.py tests/unit/application/test_recording_privacy.py tests/unit/infrastructure/test_target_preview_store.py tests/ui
git commit -m "feat(universal-rpa): complete editor forms and validation"
git status --short
```

## M3 Completion Gate

Before M4, manually launch `universal-rpa-studio` in an unlocked test session
and confirm one window can create a temporary project, select a new blank Windows
Notepad window containing test-only text, record a short session, edit labels/value modes,
save/reopen, and run environment validation without sending any action input.
Record only pass/fail in the task review; do not commit the temporary project.
