# Universal RPA M5 Extraction, Report, and Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** CSV/XLSX 반복 입력과 atomic 출력, redacted report/artifact, Runner/Report UI, deterministic Windows harness, packaged executable, 실제 MIS read-only pilot을 완성해 MVP acceptance를 닫는다.

**Architecture:** M3 `DataSourcePort`가 CSV/XLSX input을 담당하고 tabular adapter는 GUI와 분리된 direct output/file-condition I/O만 처리하며, durability가 확인된 `OutputCommit` 뒤에만 checkpoint를 허용한다. Run observer가 안전한 evidence와 실패 screenshot만 per-user app-data에 저장한다. PySide6 harness가 전체 recorder/editor/runner/report 흐름을 재현한 뒤에만 실제 MIS pilot과 package release gate를 연다.

**Tech Stack:** M1–M4, standard `csv`, openpyxl, PySide6/pytest-qt, pywin32 lock probe, pyside6-deploy, GitHub Actions Windows runners.

## Global Constraints

- M1–M4 completion gate 및 review가 먼저 통과해야 한다.
- CSV input encoding은 사용자가 `utf-8`, `utf-8-sig`, `cp949` 중 명시 선택하고 자동 추측하지 않는다.
- CSV output은 UTF-8-SIG, XLSX output은 openpyxl workbook이다.
- output 임시 파일은 destination과 같은 directory에 두고 검증 뒤 `os.replace`한다.
- 실패·취소·lock·schema mismatch 시 기존 정상 output bytes를 보존한다.
- 성공 step screenshot은 금지; 실패 screenshot만 password/사용자 민감 영역을 마스킹한다.
- run artifact 기본 보존기간은 30일이며 symlink를 따라 app-data root 밖을 삭제하지 않는다.
- 실제 MIS workflow, raw recording, input/output, secret, full report는 repository에 commit하지 않는다.
- Windows 10/11 package sign-off와 read-only pilot 전까지 MVP 완료를 선언하지 않는다.

---

### Task 1: Tabular output adapter and durable atomic commit

**Files:**

- Create: `src/universal_rpa/adapters/tabular/output.py`
- Create: `src/universal_rpa/adapters/tabular/adapter.py`
- Modify: `src/universal_rpa/bootstrap.py`
- Modify: `tests/unit/test_bootstrap_registry.py`
- Create: `tests/unit/adapters/tabular/test_output.py`
- Create: `tests/contract/test_tabular_adapter.py`

**Interfaces:**

- Consumes: M1 `OutputRelativePath`, `OutputCommit`, `LoopCursor`, `TableData`, and `CancellationToken`
- Consumes: M3 `DataSourcePort` for every CSV/XLSX input; this adapter does not duplicate input loading
- Produces: `AtomicTableWriter.save(table, spec, output_root, cancellation, producer_step_id, producer_cursor) -> OutputCommit`
- Produces: `TabularAutomationAdapter` ID `tabular`, action `tabular.save_table`, and conditions `tabular.file_exists`/`tabular.file_stable`
- Produces: final bootstrap registry IDs exactly `windows`, `clipboard`, `tabular`

- [ ] **Step 1: Write failing containment, cancellation, durability, and descriptor tests**

```python
def test_relative_output_is_resolved_beneath_runtime_root(tmp_path: Path) -> None:
    root = tmp_path / "selected-output"
    commit = AtomicTableWriter().save(
        table_data(),
        csv_output(OutputRelativePath("exports/out.csv")),
        output_root=root,
        cancellation=never_cancelled(),
        producer_step_id=STEP_ID,
        producer_cursor=(LoopCursor(loop_step_id=LOOP_ID, row_index=2),),
    )
    assert commit.destination == root / "exports" / "out.csv"
    assert commit.producer_step_id == STEP_ID
    assert commit.producer_cursor == (LoopCursor(loop_step_id=LOOP_ID, row_index=2),)


@pytest.mark.parametrize(
    "relative_path",
    ["../escape.csv", "C:/escape.csv", "//server/share/out.csv", "con.csv"],
)
def test_output_rejects_escape_absolute_unc_and_device_names(
    tmp_path: Path, relative_path: str
) -> None:
    with pytest.raises(RpaError) as error:
        save_csv(tmp_path, OutputRelativePath.model_construct(root=relative_path))
    assert error.value.code == ErrorCode.INVALID_SCHEMA


def test_cancel_during_serialization_preserves_old_bytes_and_removes_temp(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "out.csv"
    destination.write_bytes(b"known-good")
    cancellation = cancel_after_row(2)
    with pytest.raises(RpaError) as error:
        save_csv(tmp_path, "out.csv", cancellation=cancellation)
    assert error.value.code == ErrorCode.CANCELLED
    assert destination.read_bytes() == b"known-good"
    assert list(tmp_path.glob("*.universal-rpa.tmp")) == []


@pytest.mark.parametrize("cancel_point", ["before_validation", "before_replace"])
def test_cancel_at_commit_boundaries_preserves_destination(
    tmp_path: Path, cancel_point: str
) -> None:
    destination = tmp_path / "out.xlsx"
    destination.write_bytes(b"known-good")
    with pytest.raises(RpaError) as error:
        writer(cancel_at=cancel_point).save(
            table_data(), xlsx_output("out.xlsx"), tmp_path, token(), STEP_ID, ()
        )
    assert error.value.code == ErrorCode.CANCELLED
    assert destination.read_bytes() == b"known-good"


def test_destination_flush_failure_restores_previous_bytes_and_never_commits(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "out.csv"
    destination.write_bytes(b"known-good")
    with pytest.raises(RpaError) as error:
        writer(flush_destination=failing_flush()).save(
            table_data(), csv_output("out.csv"), tmp_path, token(), STEP_ID, ()
        )
    assert error.value.code == ErrorCode.OUTPUT_UNAVAILABLE
    assert destination.read_bytes() == b"known-good"
    assert checkpoint_spy().commits == []


def test_output_commit_contains_durable_hashes_and_producer_identity(
    tmp_path: Path,
) -> None:
    commit = save_csv(tmp_path, "out.csv", producer_cursor=(LoopCursor(loop_step_id=LOOP_ID, row_index=4),))
    assert commit.committed is True
    assert commit.format == "csv"
    assert commit.sheet_name is None
    assert commit.sha256 == sha256(commit.destination.read_bytes()).hexdigest()
    assert commit.headers_sha256 == canonical_header_hash(table_data().headers)
    assert commit.producer_step_id == STEP_ID
    assert commit.producer_cursor == (LoopCursor(loop_step_id=LOOP_ID, row_index=4),)


def test_checkpoint_keeps_only_latest_commit_per_normalized_destination() -> None:
    checkpoint = checkpoint_with_commits(old_commit("out.csv"), new_commit("OUT.csv"))
    assert checkpoint.output_commits == (new_commit("OUT.csv"),)


def test_tabular_descriptor_and_final_registry_are_exact() -> None:
    descriptor = tabular_adapter().descriptor()
    assert descriptor.implementation_version == "1.0.0"
    assert descriptor.supports_target_capture is False
    assert descriptor.verification_by_action["tabular.save_table"] == "intrinsic"
    assert descriptor.idempotent_actions == frozenset({"tabular.save_table"})
    assert descriptor.retryable_errors_by_action["tabular.save_table"] == frozenset(
        {ErrorCode.OUTPUT_UNAVAILABLE}
    )
    assert set(build_services().adapter_registry.adapter_ids()) == {
        "windows", "clipboard", "tabular"
    }
```

- [ ] **Step 2: Run tabular tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/adapters/tabular tests/unit/test_bootstrap_registry.py tests/contract/test_tabular_adapter.py -q
```

Expected: FAIL because relative output resolution, cancellation-aware durable commit, and `TabularAutomationAdapter` are absent.

- [ ] **Step 3: Implement output-only tabular I/O and durable commit**

```python
class TableOutputSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["csv", "xlsx"]
    relative_path: OutputRelativePath
    required_headers: frozenset[str] = frozenset()
    sheet_name: str | None = None


class AtomicTableWriter:
    def save(
        self,
        table: TableData,
        spec: TableOutputSpec,
        output_root: Path,
        cancellation: CancellationToken,
        producer_step_id: UUID,
        producer_cursor: tuple[LoopCursor, ...],
    ) -> OutputCommit: ...
```

Resolve `spec.relative_path` with the shared M1/M4 containment helper beneath the selected, normalized `output_root`. Reject absolute, parent, UNC/device paths, reparse traversal, and any resolved destination outside that root before opening a file. Serialize CSV as UTF-8-SIG or XLSX with openpyxl to a unique verified temp in `destination.parent`; call `cancellation.raise_if_cancelled()` at least once per serialized row, before reopen/validation, and immediately before replacement. Flush the temp stream and call `os.fsync`, close it, reopen it, and validate exact headers and row count.

Keep a same-directory, fsynced rollback copy when a destination exists. After the final cancellation check, replace with `os.replace`, open the destination with Win32 write-through semantics, call `FlushFileBuffers`, then calculate hashes from the durable destination. Only after that succeeds may `OutputCommit.committed=True` be returned and M4 checkpointing be notified. A cancellation or exception before replace removes only the verified temp and preserves the old bytes; a destination-flush failure restores and flushes the rollback copy (or removes a newly-created destination), returns `OUTPUT_UNAVAILABLE`, and creates no commit/checkpoint. Never include table cells in `OutputCommit`.

M4 stores only the latest commit per case-normalized resolved destination. Resume compatibility revalidates that latest commit's destination, body/header hashes, format, sheet name, row count, producer step, and producer cursor; overwritten historical commits are not independently required. `tabular.file_stable` requires identical size and last-write time on two polls. The adapter has no input loader or assertions; all input remains behind M3 `DataSourcePort`.

- [ ] **Step 4: Run tabular, loop, resume, and output regressions**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/adapters/tabular tests/contract/test_tabular_adapter.py tests/unit/application/test_loops.py tests/unit/application/test_preflight.py tests/unit/application/test_checkpoint.py -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass; injected cancellation/validation/replace/flush failures preserve the prior destination and never emit a committed output.

- [ ] **Step 5: Commit tabular output**

```powershell
git add src/universal_rpa/adapters/tabular src/universal_rpa/bootstrap.py tests/unit/adapters/tabular tests/unit/test_bootstrap_registry.py tests/contract/test_tabular_adapter.py
git commit -m "feat(universal-rpa): add durable tabular output"
```

---

### Task 2: Deep-frozen reports, exact-window screenshots, and retention

**Files:**

- Create: `src/universal_rpa/application/reports.py`
- Create: `src/universal_rpa/infrastructure/artifact_store.py`
- Create: `src/universal_rpa/infrastructure/screenshots.py`
- Create: `src/universal_rpa/infrastructure/sensitive_regions.py`
- Modify: `src/universal_rpa/bootstrap.py`
- Modify: `tests/unit/test_bootstrap_retention.py`
- Create: `tests/unit/application/test_reports.py`
- Create: `tests/unit/infrastructure/test_artifact_store.py`
- Create: `tests/unit/infrastructure/test_screenshots.py`
- Create: `tests/unit/infrastructure/test_artifact_retention.py`

**Interfaces:**

- Consumes: M1 `FrozenJsonObject`, `deep_freeze_json`, `sanitize_evidence`, `RuntimeEnvironment`
- Consumes: M4 `RunObserver` and `RunActionObserved(result, target, runtime)`
- Produces: `SafeRunReportDocument`, `ReportProjector.project`, and `RunArtifactStore`
- Produces: `FailureScreenshotService.capture_failure(target, expected_runtime, destination)`
- Produces: `SensitiveRegionProvider.resolve` and `ArtifactRetentionService.prune`

- [ ] **Step 1: Write failing deep-freeze and exact-window masking tests**

```python
def test_safe_report_recursively_freezes_source_mappings() -> None:
    environment = {"safe": {"dpi": 144}}
    document = safe_report(environment=environment)
    environment["safe"]["dpi"] = 96
    with pytest.raises(TypeError):
        document.environment["safe"]["dpi"] = 120
    assert document.environment["safe"]["dpi"] == 144


def test_nested_evidence_removes_text_clipboard_and_secret() -> None:
    source = {
        "safe": {"row_count": 3},
        "payload": {"text": "typed-value", "clipboard_text": "table-body"},
        "token": "credential",
    }
    sanitized = sanitize_evidence(source)
    encoded = json.dumps(thaw_json(sanitized), ensure_ascii=False)
    assert all(value not in encoded for value in ("typed-value", "table-body", "credential"))
    assert sanitized["safe"]["row_count"] == 3


def test_success_result_never_captures_screenshot(tmp_path: Path) -> None:
    capture = SpyScreenCapture()
    store = run_artifact_store(tmp_path, capture=capture)
    store.on_action_result(observed_action(result=success_result()))
    assert capture.calls == []


def test_failure_masks_mandatory_user_and_live_password_regions(
    tmp_path: Path,
) -> None:
    runtime = runtime_environment(process_id=41, top_level_hwnd=901)
    service = FailureScreenshotService(
        capture=solid_exact_client_capture(runtime, width=200, height=100),
        regions=SensitiveRegionProvider(
            password_probe=fake_password_regions(hwnd=901, screen_rect=(50, 20, 20, 10))
        ),
    )
    target = windows_target(
        mandatory_sensitive_regions=(NormalizedRect(0.10, 0.10, 0.10, 0.20),),
        user_sensitive_regions=(NormalizedRect(0.70, 0.10, 0.10, 0.20),),
    )
    path = service.capture_failure(target, runtime, tmp_path / "failure.png")
    image = QImage(str(path))
    assert image.pixelColor(25, 15) == QColor(Qt.GlobalColor.black)
    assert image.pixelColor(145, 15) == QColor(Qt.GlobalColor.black)
    assert image.pixelColor(55, 25) == QColor(Qt.GlobalColor.black)


def test_selector_only_target_uses_observed_pid_hwnd_without_reresolution(
    tmp_path: Path,
) -> None:
    runtime = runtime_environment(process_id=41, top_level_hwnd=901)
    capture = SpyExactWindowCapture()
    service = screenshot_service(capture=capture)
    service.capture_failure(selector_only_target(), runtime, tmp_path / "failure.png")
    assert capture.calls == [(41, 901)]
    assert capture.executable_or_class_queries == []


@pytest.mark.parametrize("field", ["process_id", "top_level_hwnd", "client_size"])
def test_runtime_identity_or_client_basis_mismatch_fails_closed(
    tmp_path: Path, field: str
) -> None:
    runtime = runtime_environment(process_id=41, top_level_hwnd=901)
    service = screenshot_service(capture=mismatched_capture(field))
    destination = tmp_path / "failure.png"
    assert service.capture_failure(windows_target(), runtime, destination) is None
    assert not destination.exists()
    assert tuple(tmp_path.iterdir()) == ()


def test_bootstrap_prunes_artifacts_once_without_following_reparse_points() -> None:
    retention = SpyArtifactRetentionService()
    build_services(artifact_retention=retention, now=fixed_utc_now())
    assert retention.prune_calls == [(fixed_utc_now(), timedelta(days=30))]
```

- [ ] **Step 2: Run artifact tests and verify failure**

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest tests/unit/test_bootstrap_retention.py tests/unit/application/test_reports.py tests/unit/infrastructure/test_artifact_store.py tests/unit/infrastructure/test_screenshots.py tests/unit/infrastructure/test_artifact_retention.py -q
```

Expected: FAIL with missing deep-frozen report projection and exact-runtime screenshot infrastructure.

- [ ] **Step 3: Implement safe per-run storage and fail-closed screenshots**

```python
class SafeRunReportDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: UUID
    workflow_name: str
    workflow_revision: int
    status: RunStatus
    environment: FrozenJsonObject
    total_iterations: int
    successful_iterations: int
    failed_iterations: int
    skipped_iterations: int
    outputs: tuple[FrozenJsonObject, ...]
    failures: tuple[FrozenJsonObject, ...]
    last_checkpoint: str | None


class ReportProjector:
    def project(self, started: RunStarted, report: RunReport) -> SafeRunReportDocument: ...


class RunArtifactStore(RunObserver):
    def on_run_started(self, event: RunStarted) -> None: ...
    def on_action_result(self, event: RunActionObserved) -> None: ...
    def on_run_finished(self, report: RunReport) -> None: ...
    def report_path(self, run_id: UUID) -> Path: ...


@dataclass(frozen=True, slots=True)
class ClientCapture:
    process_id: int
    hwnd: int
    client_screen_x: int
    client_screen_y: int
    width: int
    height: int
    image: QImage


class SensitiveRegionProvider:
    def resolve(
        self,
        target: TargetSpec,
        capture: ClientCapture,
        expected_runtime: RuntimeEnvironment,
    ) -> tuple[PixelRegion, ...]: ...


class FailureScreenshotService:
    def capture_failure(
        self,
        target: TargetSpec,
        expected_runtime: RuntimeEnvironment,
        destination: Path,
    ) -> Path | None: ...
```

Every constructor/projector calls `deep_freeze_json`; Pydantic `frozen=True` alone is insufficient. Store only thawed safe JSON encodings under `%LOCALAPPDATA%\UniversalRPAStudio\runs\<workflow_id>\<run_id>\`. The projector includes IDs, counts, safe output metadata, failed cursor/step label, error/attempts, and last checkpoint; it excludes selectors, input text, clipboard cells, credentials, and raw exception strings.

`RunArtifactStore` receives the exact target and `RuntimeEnvironment` that M4 used for the failed action through `RunActionObserved`; it never recovers a target from `RunStarted.step_targets` and never re-resolves by executable, title, or class. Production capture requests exactly `(process_id, top_level_hwnd)`, confirms both plus current client width/height against the event runtime, and captures only that HWND client area. This works for selector-only targets. Zero/ambiguous/mismatched/reused HWND, PID mismatch, or client-basis mismatch is a fail-closed no-screenshot result.

Mask the union of `WindowsTarget.mandatory_sensitive_regions`, `WindowsTarget.user_sensitive_regions`, and every live UIA descendant of the exact HWND with `IsPassword=True`. Convert all three sets to the captured client-pixel basis and clip to bounds. Any probe/conversion/mask/encode/write failure deletes the verified temp and writes no image. Only fully masked in-memory bytes may be flushed and atomically replaced. Capture failures only; successes never call capture. Retention defaults to 30 days and rejects symlink/reparse traversal outside its verified root.

- [ ] **Step 4: Run artifacts, observer contract, and recursive secret scan**

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest tests/unit/test_bootstrap_retention.py tests/unit/application/test_reports.py tests/unit/infrastructure tests/contract/test_runner_observer.py -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass; nested mutation fails, success has no screenshot, and every failure screenshot is tied to the observed PID/HWND and contains all three mask sources.

- [ ] **Step 5: Commit safe artifacts and reports**

```powershell
git add src/universal_rpa/application/reports.py src/universal_rpa/infrastructure/artifact_store.py src/universal_rpa/infrastructure/screenshots.py src/universal_rpa/infrastructure/sensitive_regions.py src/universal_rpa/bootstrap.py tests/unit/test_bootstrap_retention.py tests/unit/application/test_reports.py tests/unit/infrastructure
git commit -m "feat(universal-rpa): add deep-frozen redacted reports"
```

---

### Task 3: Runner/Report UI, guarded step retest, and safe resume controls

**Files:**

- Create: `src/universal_rpa/ui/runner_page.py`
- Create: `src/universal_rpa/ui/report_page.py`
- Modify: `src/universal_rpa/ui/workers.py`
- Modify: `src/universal_rpa/ui/main_window.py`
- Modify: `src/universal_rpa/bootstrap.py`
- Create: `tests/ui/test_runner_page.py`
- Create: `tests/ui/test_report_page.py`
- Create: `tests/ui/test_execution_worker.py`

**Interfaces:**

- Produces: `ExecutionWorker` signals and control slots
- Produces: `RunnerPage.set_session`, `set_output_root`, `preflight`, `start`, `pause`, `resume`, `cancel`
- Produces: `ReportPage.set_report`
- Consumes: M4 `RunRequest`, `StepTestRequest`, `ExecutionService`, resume compatibility, and `RESUME_UNSAFE`
- Consumes: M5 `RunArtifactStore` and `ReportProjector`

- [ ] **Step 1: Write failing credential, output-root, hotkey, retest, and resume tests**

```python
def test_preflight_error_disables_run_without_starting_worker(page: RunnerPage) -> None:
    page.set_preflight_report(invalid_preflight())
    assert not page.run_button.isEnabled()
    assert page.worker_start_count == 0


def test_runner_displays_only_workflow_configured_credential_reference(
    page: RunnerPage,
) -> None:
    page.set_session(session_with_secret_reference("erp/password"))
    assert page.credential_reference_label.text() == "erp/password"
    assert page.findChildren(QComboBox, "credentialReferenceChooser") == []
    assert page.secret_store.exists_calls == ["erp/password"]
    assert "actual-password" not in all_widget_and_model_text(page)


def test_missing_configured_credential_disables_run_and_links_manager(page: RunnerPage) -> None:
    page.set_session(session_with_missing_credential("erp/password"))
    assert not page.run_button.isEnabled()
    assert page.open_credential_manager_button.isVisible()


def test_output_directory_selection_is_required(page: RunnerPage, tmp_path: Path) -> None:
    page.set_preflight_report(valid_preflight())
    assert not page.run_button.isEnabled()
    page.set_output_root(tmp_path / "selected-output")
    assert page.run_button.isEnabled()
    assert page.build_request().run_inputs.output_root == tmp_path / "selected-output"


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        ((Key.f11,), None),
        ((Key.f12,), None),
        ((Key.ctrl_l, Key.shift, Key.f11), "toggle_pause"),
        ((Key.ctrl_l, Key.shift, Key.f12), "cancel"),
    ],
)
def test_control_listener_requires_ctrl_shift_chord(keys, expected) -> None:
    assert feed_control_keys(keys) == expected


@pytest.mark.parametrize("reason", ["input_step", "row_cursor_missing", "dependency_missing"])
def test_failure_step_retest_is_disabled_when_context_cannot_be_rebuilt(
    page: RunnerPage, reason: str
) -> None:
    page.set_report(report_with_retest_block(reason))
    assert not page.retest_button.isEnabled()


def test_row_bound_retest_sends_exact_cursor_and_rebuilds_snapshot(page: RunnerPage) -> None:
    page.set_report(report_with_retest_cursor((LoopCursor(loop_step_id=LOOP_ID, row_index=3),)))
    page.retest_button.click()
    request = page.execution_service.test_step_calls[0]
    assert isinstance(request, StepTestRequest)
    assert request.cursor == (LoopCursor(loop_step_id=LOOP_ID, row_index=3),)


def test_unsafe_resume_is_disabled_with_manual_recovery_message(page: RunnerPage) -> None:
    page.set_resume_compatibility(resume_unsafe_non_idempotent_iteration())
    assert not page.resume_button.isEnabled()
    assert page.resume_error.property("errorCode") == ErrorCode.RESUME_UNSAFE
    assert "수동" in page.resume_error.text()


@pytest.mark.parametrize(
    "mismatch", ["workflow", "inputs", "data", "adapter", "environment", "output"]
)
def test_resume_disabled_for_every_fingerprint_mismatch(
    page: RunnerPage, mismatch: str
) -> None:
    page.set_resume_compatibility(resume_compatibility(mismatch=mismatch))
    assert not page.resume_button.isEnabled()
    assert page.resume_error.property("errorCode") == ErrorCode.RESUME_MISMATCH
```

- [ ] **Step 2: Run Runner/Report UI tests and verify failure**

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest tests/ui/test_runner_page.py tests/ui/test_report_page.py tests/ui/test_execution_worker.py -q
```

Expected: FAIL because the final pages, guarded retest flow, output chooser, and chord listener are absent.

- [ ] **Step 3: Implement worker-backed execution and non-editable run bindings**

```python
class ExecutionWorker(QObject):
    progress = Signal(object)
    completed = Signal(object)
    failed = Signal(object)

    @Slot(object)
    def start(self, request: RunRequest) -> None: ...
    @Slot()
    def pause(self) -> None: ...
    @Slot()
    def resume(self) -> None: ...
    @Slot()
    def cancel(self) -> None: ...


class RunnerPage(QWidget):
    def set_session(self, session: ProjectSession) -> None: ...
    def set_output_root(self, root: Path) -> None: ...
    def set_preflight_report(self, report: ValidationReport) -> None: ...


class ReportPage(QWidget):
    def set_report(self, report: SafeRunReportDocument) -> None: ...
```

Build typed run forms for text/date/integer/decimal/path/choice. For a `CredentialRefValue`, render the workflow-configured reference as read-only text and call only `SecretStore.exists(reference)` during preflight; the Runner cannot select, substitute, read, or display a secret/reference. Credential creation/update is a separate Credential Manager action and returning to Runner reruns preflight. Require the user to choose an output directory; normalize it once into `RunInputs.output_root`, display the safe path, and keep Run disabled if it is missing/unwritable/reparse-unsafe.

Run `ExecutionService` in a `QThread`; wire `RunArtifactStore` plus a Qt progress observer. Use a control-only listener for exactly `Ctrl+Shift+F11` pause/resume and `Ctrl+Shift+F12` cancel. Bare F11/F12 or partial chords do nothing, no raw event is retained, and the listener stops on every terminal outcome.

Retest calls `ExecutionService.test_step(StepTestRequest(...))`, never a raw step. Supply the failed cursor so M4 can rebuild fingerprinted snapshots, `row_stack`, prepared variables, and action outputs. Disable retest for data input steps, row-bound failures without a cursor, and steps with missing/failed dependencies. Resume discovery uses M4's current snapshot builder and validator in a worker. `RESUME_UNSAFE` means an interrupted non-idempotent iteration and must disable automatic resume with a manual-recovery message; it is distinct from corrupt `CHECKPOINT_INVALID` and fingerprint `RESUME_MISMATCH`.

Report page shows safe totals, output paths/counts, failed cursor/step, attempts, terminal checkpoint state, and safe message. Export only `SafeRunReportDocument`.

- [ ] **Step 4: Run UI, execution, retest, and resume regressions**

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest tests/ui tests/unit/application/test_execution.py tests/unit/application/test_step_test.py tests/unit/application/test_resume.py tests/unit/application/test_reports.py -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass; the Qt timer advances during execution, only the two chords control a run, unsafe resume cannot start, and output-root/credential prerequisites are enforced.

- [ ] **Step 5: Commit final Studio pages**

```powershell
git add src/universal_rpa/ui src/universal_rpa/bootstrap.py tests/ui
git commit -m "feat(universal-rpa): connect guarded runner and report UI"
```

---

### Task 4: Deterministic source-mode PySide6 Windows harness and E2E workflow

**Files:**

- Create: `samples/test_harness/__init__.py`
- Create: `samples/test_harness/__main__.py`
- Create: `samples/test_harness/app.py`
- Create: `samples/test_harness/main_window.py`
- Create: `samples/test_harness/state.py`
- Create: `samples/test_harness/README.md`
- Create: `tests/integration/windows/conftest.py`
- Create: `tests/integration/windows/helpers.py`
- Create: `tests/integration/windows/test_windows_runner_harness.py`
- Create: `tests/integration/windows/test_recorder_editor_roundtrip.py`
- Create: `tests/integration/windows/test_output_lock_harness.py`

**Interfaces:**

- Produces: `HarnessConfig`, `create_harness_window`, `python -m samples.test_harness`
- Produces: `HarnessProcess`, `run_harness_workflow`, and `record_edit_run` integration helpers
- Consumes: source-tree production bootstrap, registry, and adapters through public interfaces; packaging is not required until Task 5

- [ ] **Step 1: Write failing source-harness and interactive-session tests**

```python
@pytest.mark.windows_e2e
def test_duplicate_selector_fails_before_click(harness: HarnessProcess) -> None:
    harness.configure(duplicate_selector=True)
    report = run_harness_workflow("duplicate-selector", harness)
    assert report.status == RunStatus.FAILED
    assert report.results[-1].error_code == ErrorCode.TARGET_AMBIGUOUS
    assert harness.state.click_count == 0


@pytest.mark.windows_e2e
def test_move_resize_and_dpi_rules(harness: HarnessProcess) -> None:
    assert run_harness_workflow("uia-after-move", harness).status == RunStatus.SUCCESS
    harness.resize_beyond_two_percent()
    assert run_harness_workflow("coordinate-fallback", harness).status == RunStatus.FAILED


@pytest.mark.windows_e2e
def test_complete_keyboard_roundtrip(harness: HarnessProcess) -> None:
    result = record_edit_run("ctrl-a-date-enter", harness)
    assert result.normalized_actions == [
        "windows.hotkey", "windows.set_text", "windows.press_key"
    ]
    assert harness.state.date_text == "2026-07-27"


def test_windows_e2e_fixture_requires_interactive_self_hosted_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RPA_INTERACTIVE_DESKTOP", raising=False)
    with pytest.raises(pytest.skip.Exception):
        require_interactive_desktop()
```

- [ ] **Step 2: Run marked tests and verify missing harness failure**

```powershell
$env:RPA_INTERACTIVE_DESKTOP = "1"
.\.venv\Scripts\python.exe -m pytest tests/integration/windows -m windows_e2e -q
```

Expected: FAIL because the launchable harness and shared fixtures/helpers are absent.

- [ ] **Step 3: Implement a deterministic accessible source target**

```python
@dataclass(frozen=True, slots=True)
class HarnessConfig:
    delayed_control_ms: int = 500
    duplicate_selector: bool = False
    intentional_timeout: bool = False
    lock_output: bool = False
    state_file: Path
    ready_file: Path


def create_harness_window(config: HarnessConfig) -> HarnessWindow: ...
def main(argv: Sequence[str] | None = None) -> int: ...
```

`__main__.py` calls `main()` so fixtures launch `python -m samples.test_harness --state-file <temp> --ready-file <temp>` from the source checkout. Expose stable automation/accessibility names for normal/date/Korean/password text, click/double-click, drag, scroll, hotkey indicator, delayed control, owned modal, duplicate selectors, and clipboard-table button. The state file records only counters and fixed synthetic values.

`conftest.py` owns launch, ready wait, verified termination, desktop/focus cleanup, temp roots, and `RPA_INTERACTIVE_DESKTOP=1` gating. `helpers.py` owns source bootstrap/session/workflow builders and calls the same production recorder, editor, validator, Windows/clipboard/tabular adapters, execution service, and report projector used by Studio. Cover stale clipboard, focus theft, move/resize/DPI, delayed element, modal, Korean verification, mandatory password masking, drag/scroll/hotkey, locked output, cancellation, checkpoint, safe/unsafe resume, and full recorder→editor→preflight→runner→report.

Interactive E2E runs only in a logged-in unlocked desktop job labelled exactly `[self-hosted, windows, x64, rpa-interactive]`. A GitHub-hosted `windows-latest` job may run unit, contract, offscreen UI, and noninteractive integration tests, but it must deselect `windows_e2e` and must never claim native input/focus coverage.

- [ ] **Step 4: Run harness and every non-pilot source test**

```powershell
$env:RPA_INTERACTIVE_DESKTOP = "1"
.\.venv\Scripts\python.exe -m pytest tests/integration/windows -m windows_e2e -q
.\.venv\Scripts\python.exe -m pytest tests/unit tests/contract tests/ui tests/integration -m "not windows_e2e and not mis_pilot" -q
.\.venv\Scripts\python.exe -m ruff check src tests samples scripts
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass in an unlocked self-hosted Windows session; hosted CI runs only the second command.

- [ ] **Step 5: Commit deterministic source harness coverage**

```powershell
git add samples/test_harness tests/integration/windows
git commit -m "test(universal-rpa): add deterministic source Windows harness"
```

---

### Task 5: Pinned package, real packaged-GUI smoke, CI, and safe repository split

**Files:**

- Modify: `pyproject.toml`
- Create: `src/universal_rpa/self_check.py`
- Create: `src/universal_rpa/packaged_smoke.py`
- Modify: `src/universal_rpa/__main__.py`
- Create: `tests/unit/test_self_check.py`
- Create: `tests/unit/test_packaged_smoke.py`
- Create: `pysidedeploy.spec`
- Create: `scripts/build.ps1`
- Create: `scripts/smoke_packaged.ps1`
- Create: `scripts/verify_repository_split.ps1`
- Modify: `.github/workflows/windows.yml`
- Create: `.github/workflows/package-windows.yml`
- Create: `docs/architecture/repository-split.md`

**Interfaces:**

- Produces: `run_self_check(app_data_root=None) -> SelfCheckReport`
- Produces: `run_packaged_smoke(root: Path) -> PackagedSmokeReport`
- Produces: `UniversalRPAStudio.exe --self-check` and `--packaged-smoke <empty-temp-root>`
- Produces: disposable-clone-only repository extraction verification

- [ ] **Step 1: Write failing self-check, real-GUI smoke, manifest, and split-safety tests**

```python
def test_self_check_verifies_schema_adapters_appdata_and_dpi(tmp_path: Path) -> None:
    report = run_self_check(app_data_root=tmp_path)
    assert report.ok
    assert {item.name for item in report.checks} == {
        "workflow_schema_v1", "builtin_adapters", "app_data_write", "dpi_awareness"
    }


def test_packaged_smoke_builds_window_validates_runs_and_reports(tmp_path: Path) -> None:
    report = run_packaged_smoke(tmp_path)
    assert report.ok
    assert report.main_window_created
    assert report.workflow_action == "windows.wait"
    assert report.validation_error_count == 0
    assert report.run_status == RunStatus.SUCCESS
    assert report.safe_report_created


def test_distribution_manifest_excludes_sensitive_roots() -> None:
    forbidden = {"recordings", "artifacts", "projects", ".superpowers"}
    assert forbidden.isdisjoint(distribution_file_names())


def test_split_script_refuses_current_checkout_and_requires_disposable_clone() -> None:
    result = inspect_split_script(Path("scripts/verify_repository_split.ps1"))
    assert result.clones_before_filter_repo
    assert result.filter_repo_target_is_temporary_clone
    assert result.mutates_source_checkout is False
```

- [ ] **Step 2: Run package tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_self_check.py tests/unit/test_packaged_smoke.py -q
```

Expected: FAIL because self-check, packaged smoke, pinned deployment config, and safe split script are absent.

- [ ] **Step 3: Pin toolchain and implement actual packaged-process smoke**

Pin the tested Windows build toolchain exactly in `pyproject.toml`: `PySide6==6.11.1` and build dependency `Nuitka==4.1.3`; retain the project Python range 3.12/3.13. The pins correspond to the release records on [PySide6 PyPI](https://pypi.org/project/PySide6/) and [Nuitka PyPI](https://pypi.org/project/Nuitka/). Initialize and inspect the checked-in config using the [official Qt for Python deployment commands](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html):

```powershell
.\.venv\Scripts\pyside6-deploy.exe src\universal_rpa\__main__.py --init
.\.venv\Scripts\pyside6-deploy.exe -c pysidedeploy.spec --dry-run
```

Configure `Universal RPA Studio`, project `.`, input `src/universal_rpa/__main__.py`, output `dist`, Windows standalone mode, and Qt `Core,Gui,Widgets`. `--self-check` verifies schema v1, exactly three adapter IDs, app-data atomic write/delete, and DPI initialization with path-free JSON.

`--packaged-smoke <root>` must reject a nonempty or reparse root, create a real `QApplication` and production `MainWindow`, bootstrap real Windows/clipboard/tabular adapters, persist/load a synthetic workflow containing one short `windows.wait`, run production validation and `ExecutionService`, project the safe report, close the window, and return 0 only when every stage succeeds. It may not replace adapters with test doubles or merely import modules.

`scripts/build.ps1` runs non-pilot automated gates, schema check, deploy dry-run, and the forced build. `scripts/smoke_packaged.ps1` creates a fresh empty temp directory, changes CWD to it, removes `PYTHONPATH` from the child process environment, launches the single expected EXE first with `--self-check` and then `--packaged-smoke <temp>`, requires both exit 0, and scans the actual distribution manifest for forbidden roots and parent-repository modules.

CI has two explicit classes: GitHub-hosted `windows-latest` runs unit/contract/offscreen UI/noninteractive tests with `-m "not windows_e2e and not mis_pilot"`; native input/focus harness and packaged-GUI smoke run only on `[self-hosted, windows, x64, rpa-interactive]`. The package workflow pins Python 3.13 and the exact locked dependencies.

`verify_repository_split.ps1` performs history rewriting only in a newly-created disposable clone:

```powershell
$sourceRepo = (git rev-parse --show-toplevel).Trim()
$splitRoot = Join-Path ([IO.Path]::GetTempPath()) ("universal-rpa-split-" + [guid]::NewGuid())
git clone --no-local $sourceRepo $splitRoot
Push-Location $splitRoot
git filter-repo --path universal_rpa/ --path-rename universal_rpa/: --force
git remote remove origin
Pop-Location
```

The script resolves and proves `$splitRoot` is a new descendant of `[IO.Path]::GetTempPath()`, refuses the source worktree or any nonempty target, leaves the clone for inspection, and prints its path. It verifies `pyproject.toml`, `.github`, `src`, `tests`, `docs`, `samples`, and `scripts` at the new root, runs schema/unit checks there, and never invokes `filter-repo` in the user's current repository.

- [ ] **Step 4: Build and smoke from an empty working directory**

```powershell
.\scripts\build.ps1
.\scripts\smoke_packaged.ps1
.\scripts\verify_repository_split.ps1
```

Expected: build and both EXE modes exit 0 with empty CWD/PYTHONPATH isolation; the disposable split clone passes its checks and the source checkout remains byte-for-byte untouched.

- [ ] **Step 5: Commit packaging, CI, and split verification**

```powershell
git add pyproject.toml src/universal_rpa/self_check.py src/universal_rpa/packaged_smoke.py src/universal_rpa/__main__.py tests/unit/test_self_check.py tests/unit/test_packaged_smoke.py pysidedeploy.spec scripts .github docs/architecture/repository-split.md
git commit -m "build(universal-rpa): package and verify Windows app"
```

---

### Task 6: Multi-document read-only MIS pilot and exact 20-row acceptance audit

**Files:**

- Create: `docs/pilot/mis-read-only-pilot-runbook.md`
- Create: `scripts/verify_mis_pilot_report.py`
- Create: `tests/unit/scripts/test_verify_mis_pilot_report.py`
- Create: `tests/unit/test_no_customer_artifacts.py`
- Create: `tests/fixtures/recordings/synthetic-manifest.json`
- Create after successful pilot: `docs/validation/mis-read-only-pilot-windows-10-x64.md`
- Create after successful pilot: `docs/validation/mis-read-only-pilot-windows-11-x64.md`
- Create after all gates: `docs/validation/mvp-acceptance-evidence.md`

**Interfaces:**

- Produces: `PilotEvidenceBundle` with five required relative evidence paths
- Produces: `verify_pilot_bundle(bundle_path, policy, expected_os) -> PilotGateResult`
- Produces: two redacted OS summaries plus one 20-row acceptance evidence matrix
- Consumes: packaged app and safe validation/step-test/multi-run/resume/self-check JSON documents

- [ ] **Step 1: Write failing bundle, cross-evidence, path, and hygiene tests**

```python
@dataclass(frozen=True, slots=True)
class PilotEvidenceBundle:
    validation_report: Path
    step_test_report: Path
    multi_run_report: Path
    resume_report: Path
    self_check_report: Path


def test_bundle_requires_all_five_distinct_documents(tmp_path: Path) -> None:
    bundle = write_bundle(tmp_path, omit="resume_report")
    result = verify_pilot_bundle(bundle, pilot_policy(), "windows-11-x64")
    assert not result.ok
    assert "resume_report_missing" in result.failures


@pytest.mark.parametrize(
    ("mutation", "failure"),
    [
        ("header_hash", "required_headers"),
        ("token_hash", "required_token"),
        ("row_count_zero", "minimum_rows"),
        ("validation_action_count", "validation_only"),
        ("self_check_false", "package_self_check"),
    ],
)
def test_bundle_checks_header_token_rows_and_each_evidence(
    tmp_path: Path, mutation: str, failure: str
) -> None:
    bundle = write_complete_bundle(tmp_path, mutation=mutation)
    result = verify_pilot_bundle(bundle, pilot_policy(), "windows-11-x64")
    assert not result.ok
    assert failure in result.failures


@pytest.mark.parametrize("escape", ["../report.json", "C:/other/report.json", "link/report.json"])
def test_bundle_paths_must_be_regular_files_beneath_evidence_root(
    tmp_path: Path, escape: str
) -> None:
    result = verify_pilot_bundle(write_bundle_with_path(tmp_path, escape), pilot_policy(), "windows-10-x64")
    assert not result.ok
    assert "unsafe_evidence_path" in result.failures


def test_repository_hygiene_allows_only_manifested_synthetic_recording_jsonl(
    synthetic_repo_root: Path,
) -> None:
    assert scan_repository_for_runtime_artifacts(synthetic_repo_root) == ()
    unlisted = synthetic_repo_root / "tests/fixtures/recordings/unlisted.jsonl"
    unlisted.write_text('{"text":"customer"}', encoding="utf-8")
    assert unlisted in scan_repository_for_runtime_artifacts(synthetic_repo_root)


@pytest.mark.parametrize(
    "ignored_root",
    [".git", ".venv", "build", "dist", ".pytest_cache", ".mypy_cache", ".ruff_cache"],
)
def test_repository_hygiene_prunes_vcs_build_and_environment_roots(
    synthetic_repo_root: Path,
    ignored_root: str,
) -> None:
    ignored = synthetic_repo_root / ignored_root / "should-not-be-scanned.jsonl"
    ignored.parent.mkdir(parents=True, exist_ok=True)
    ignored.write_text('{"synthetic":"outside source tree"}', encoding="utf-8")
    assert scan_repository_for_runtime_artifacts(synthetic_repo_root) == ()
```

- [ ] **Step 2: Run pilot verifier tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/scripts/test_verify_mis_pilot_report.py tests/unit/test_no_customer_artifacts.py -q
```

Expected: FAIL because the five-document verifier, manifest allowlist, and runbook are absent.

- [ ] **Step 3: Implement path-safe cross-document verification and supervised runbook**

```python
@dataclass(frozen=True, slots=True)
class PilotPolicy:
    required_headers: frozenset[str]
    required_token_sha256: str
    minimum_rows: int
    allowed_output_root: Path
    workflow_revision: int


@dataclass(frozen=True, slots=True)
class PilotEvidenceBundle:
    validation_report: Path
    step_test_report: Path
    multi_run_report: Path
    resume_report: Path
    self_check_report: Path


@dataclass(frozen=True, slots=True)
class PilotGateResult:
    ok: bool
    failures: tuple[str, ...]


def verify_pilot_bundle(
    bundle_path: Path,
    policy: PilotPolicy,
    expected_os: Literal["windows-10-x64", "windows-11-x64"],
) -> PilotGateResult: ...
```

The bundle manifest contains only relative paths. Resolve every path under the manifest directory, require five distinct regular non-reparse files, reject absolute/parent/UNC/device escapes, cap each JSON file at 10 MiB, parse with strict safe schemas, and never follow links. Cross-check one workflow ID/revision, app/schema version, anonymous environment fingerprint, expected OS, and chronological run IDs across all documents.

Require: validation-only status success and `action_count==0`; a one-factory/one-period step-test success; canonical required-header SHA-256 and approved-token SHA-256 match the external policy without copying either observed value into the report; positive row count; multi-row run success with all output paths beneath the resolved allowed root and every latest output commit hash valid; resume starts only after the last completed cursor and has no duplicate completed cursor; package self-check has all four checks true. Reject any secret, clipboard body, selector, raw message, or absolute customer path field.

The runbook's exact supervised order is: create an approved read-only workflow outside the repo; choose a separate empty output root; validation-only with zero actions; one factory/period step test; verify header/token hash and positive row count; several approved rows; stop after a completed iteration and resume once; verify production RPA/output baseline hashes unchanged except the approved test output; export all five safe documents; run both commands below; apply the seven-day raw-recording policy.

```powershell
.\.venv\Scripts\python.exe scripts\verify_mis_pilot_report.py --bundle C:\UniversalRPA-Pilot\win10-x64\pilot-bundle.json --policy C:\UniversalRPA-Pilot\pilot-policy.json --expected-os windows-10-x64 --summary docs\validation\mis-read-only-pilot-windows-10-x64.md
.\.venv\Scripts\python.exe scripts\verify_mis_pilot_report.py --bundle C:\UniversalRPA-Pilot\win11-x64\pilot-bundle.json --policy C:\UniversalRPA-Pilot\pilot-policy.json --expected-os windows-11-x64 --summary docs\validation\mis-read-only-pilot-windows-11-x64.md
```

`tests/fixtures/recordings/synthetic-manifest.json` has `synthetic_only: true` and exact relative file/SHA-256 entries. Resolve and verify the standalone project root, then prune `.git`, `.venv`, `build`, `dist`, and tool cache directories before scanning source-owned paths. Hygiene rejects every `.jsonl` except a listed hash-matching file under that one directory, and rejects workflow/preview/credential/run-artifact/CSV/XLSX anywhere except separately manifested synthetic fixtures. Generated validation summaries contain only versions, OS, counts, hashes, and pass/fail.

- [ ] **Step 4: Execute all gates and generate the exact acceptance matrix**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit tests/contract tests/ui tests/integration -m "not windows_e2e and not mis_pilot" -q
$env:RPA_INTERACTIVE_DESKTOP = "1"
.\.venv\Scripts\python.exe -m pytest tests/integration/windows -m windows_e2e -q
.\.venv\Scripts\python.exe scripts/export_schema.py --check
.\.venv\Scripts\python.exe -m ruff check src tests samples scripts
.\.venv\Scripts\python.exe -m ruff format --check src tests samples scripts
.\.venv\Scripts\python.exe -m mypy src
.\scripts\build.ps1
.\scripts\smoke_packaged.ps1
```

After both exact verifier commands pass, generate `mvp-acceptance-evidence.md` with one concrete artifact/test command per master row:

| # | Master acceptance row | Required evidence |
|---:|---|---|
| 1 | one app select/record/edit/test/run/report | `test_recorder_editor_roundtrip.py` full-flow report |
| 2 | meaningful mouse and keyboard steps | M2 normalization suite + harness action trace |
| 3 | Ctrl+A → date → Enter split | `test_complete_keyboard_roundtrip` |
| 4 | literal/variable/row/secret modes | M1 schema + M3 property-panel tests |
| 5 | date form/whitelist calculations | M1 date resolver + M3 editor tests |
| 6 | CSV/XLSX/depth-two loop | M4 loop + M5 tabular output tests |
| 7 | UIA first/guarded coordinates | M4 target guard + harness move/DPI tests |
| 8 | environment mismatch stops before click | M4 preflight click-count test |
| 9 | state wait/clipboard assertion | M4 adapter contracts + harness |
| 10 | resume after successful iteration | M4 resume suite + pilot resume report |
| 11 | no automatic non-idempotent replay | M4 journal `RESUME_UNSAFE` + Runner UI test |
| 12 | no secret in artifacts | recursive redaction + repository hygiene tests |
| 13 | nested data immutable | M1/M2 immutable JSON + M5 report mutation tests |
| 14 | event focus/queue-independent `Ctrl+Shift+F12` | M2 listener tests |
| 15 | preserve existing output on failure | M5 cancellation/validation/flush tests |
| 16 | output beneath selected root | M3/M4 containment + M5 path tests |
| 17 | mandatory masks cannot be removed | M2/M3 lock tests + M5 three-source mask test |
| 18 | no parent import | M1 import isolation + packaged empty-CWD smoke |
| 19 | Windows 10/11 x64 package | two packaged smoke/self-check summaries |
| 20 | read-only MIS pilot without LLM | two five-document pilot gate summaries |

Expected: every row has a passing command/artifact link; missing evidence is a hard failure, not `N/A`.

- [ ] **Step 5: Commit only verifier, synthetic manifest, and redacted evidence**

```powershell
git add docs/pilot/mis-read-only-pilot-runbook.md scripts/verify_mis_pilot_report.py tests/unit/scripts/test_verify_mis_pilot_report.py tests/unit/test_no_customer_artifacts.py tests/fixtures/recordings/synthetic-manifest.json docs/validation/mis-read-only-pilot-windows-10-x64.md docs/validation/mis-read-only-pilot-windows-11-x64.md docs/validation/mvp-acceptance-evidence.md
git commit -m "docs(universal-rpa): record read-only pilot acceptance"
git status --short
```

Expected: no workflow, raw unmanifested recording, preview, customer CSV/XLSX, credential, full report, screenshot, or customer path is staged.

## M5 and MVP Completion Gate

MVP is complete only when:

- all M1–M5 automated commands exit 0;
- the harness covers recorder → editor → preflight → runner → report;
- packaged app self-check passes without parent modules;
- Windows 10 and Windows 11 sign-off records exist;
- real MIS read-only pilot passes without runtime LLM or extra LLM input;
- repository hygiene scan reports zero customer/runtime artifacts;
- all 20 acceptance rows in the master plan have exact passing evidence.
