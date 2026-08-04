# Universal RPA Studio — MVP 수락 증거 매트릭스

master plan의 20개 수락 행 각각에 대해, 실행 가능한 명령 또는 산출물 하나를 지정한다.
증거가 없는 행은 `N/A`가 아니라 **하드 실패**로 취급한다.

## 이 문서의 상태

| 구분 | 상태 |
|---|---|
| 자동 게이트 (행 1–18) | **본 저장소에서 실행 가능** — 아래 명령으로 재현한다 |
| 대화형 harness (행 1–3, 7–9, 14, 17) | `[self-hosted, windows, x64, rpa-interactive]` 세션 필요 |
| 패키지 서명 (행 19) | Windows 10/11 실기 필요 — **미완료** |
| MIS 파일럿 (행 20) | 승인된 실제 MIS 접근 필요 — **미완료** |

> 행 19·20이 채워지기 전에는 MVP 완료를 선언하지 않는다.

## 게이트 명령

```powershell
# A. 비대화형 전체 (모든 호스트에서 실행 가능)
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest tests -m "not windows_e2e and not mis_pilot" -q

# B. 대화형 Windows E2E (잠금 해제된 로그인 세션에서만)
$env:RPA_INTERACTIVE_DESKTOP = "1"
.\.venv\Scripts\python.exe -m pytest tests/integration/windows -m windows_e2e -q

# C. 정적 게이트
.\.venv\Scripts\python.exe scripts/export_schema.py --check
.\.venv\Scripts\python.exe -m ruff check src tests samples scripts
.\.venv\Scripts\python.exe -m ruff format --check src tests samples scripts
.\.venv\Scripts\python.exe -m mypy src
.\.venv\Scripts\python.exe scripts/repository_hygiene.py

# D. 패키지 (Windows 실기)
.\scripts\build.ps1
.\scripts\smoke_packaged.ps1

# E. 저장소 분리
.\scripts\verify_repository_split.ps1
```

## 수락 매트릭스

| # | master 수락 행 | 증거 | 게이트 |
|---:|---|---|---|
| 1 | 한 앱 선택·기록·편집·테스트·실행·보고 | `tests/integration/windows/test_recorder_editor_roundtrip.py::test_full_flow_preflights_runs_and_projects_one_safe_report` | B |
| 2 | 의미 있는 마우스·키보드 단계 | `tests/unit/application/test_mouse_normalization.py`, `test_keyboard_normalization.py` + `tests/integration/windows/test_windows_runner_harness.py::test_drag_scroll_and_hotkey_each_have_an_observable_effect` | A, B |
| 3 | Ctrl+A → 날짜 → Enter 분리 | `tests/integration/windows/test_recorder_editor_roundtrip.py::test_complete_keyboard_roundtrip` (offscreen 대응: `tests/integration/windows/test_harness_app.py::test_ctrl_a_selects_all_in_the_focused_field_and_is_counted`) | B (A 일부) |
| 4 | 리터럴·변수·행·비밀 모드 | `tests/unit/domain/test_values.py`, `tests/unit/application/test_validation.py`, `tests/ui/test_property_panel.py` | A |
| 5 | 날짜 형식·화이트리스트 계산 | `tests/unit/application/test_date_rules.py`, `tests/ui/test_variable_dialog.py` | A |
| 6 | CSV/XLSX·깊이 2 반복 | `tests/unit/adapters/tabular/test_data_sources.py`, `tests/unit/adapters/tabular/test_output.py`, `tests/unit/application/test_execution.py` | A |
| 7 | UIA 우선·가드된 좌표 | `tests/unit/adapters/windows/test_target_resolver.py` + `test_windows_runner_harness.py::test_uia_survives_a_window_move`, `::test_coordinate_fallback_refuses_after_a_resize_beyond_tolerance` | A, B |
| 8 | 환경 불일치 시 클릭 전 중단 | `tests/unit/application/test_preflight.py::test_static_failure_stops_before_environment_validation` + `test_windows_runner_harness.py::test_duplicate_selector_fails_before_click` (`click_count == 0`) | A, B |
| 9 | 상태 대기·클립보드 단언 | `tests/unit/application/test_conditions.py`, `tests/unit/adapters/clipboard/test_adapter.py` + `test_output_lock_harness.py::test_clipboard_table_is_extracted_and_committed` | A, B |
| 10 | 성공한 iteration 이후 재개 | `tests/unit/application/test_resume_execution.py::test_resume_starts_after_exact_matching_loop_cursor`, `tests/unit/application/test_resume_discovery.py` | A |
| 11 | 비멱등 작업 자동 재실행 금지 | `tests/unit/application/test_resume_discovery.py::test_interrupted_non_idempotent_iteration_is_unsafe_and_not_a_mismatch`, `tests/ui/test_runner_page.py::test_unsafe_resume_is_disabled_with_manual_recovery_message` | A |
| 12 | 산출물에 비밀 없음 | `tests/unit/infrastructure/test_redaction.py`, `tests/unit/application/test_keyboard_secret_redaction.py`, `tests/unit/test_no_customer_artifacts.py`, `tests/ui/test_runner_page.py::test_runner_displays_only_workflow_configured_credential_reference` | A, C |
| 13 | 중첩 데이터 불변 | `tests/unit/domain/test_immutable_json.py`, `tests/unit/application/test_reports.py` | A |
| 14 | 포커스·큐 독립 `Ctrl+Shift+F12` | `tests/unit/adapters/windows/test_capture.py`, `tests/ui/test_execution_worker.py::test_control_listener_requires_ctrl_shift_chord`, `::test_cancel_reaches_a_blocked_run_without_the_worker_event_loop` | A |
| 15 | 실패 시 기존 산출물 보존 | `tests/unit/adapters/tabular/test_output.py` (취소·검증·flush 경로) + `test_output_lock_harness.py::test_a_locked_destination_preserves_the_previous_bytes` | A, B |
| 16 | 산출물은 선택한 root 아래 | `tests/unit/adapters/tabular/test_output.py` (containment), `tests/ui/test_runner_page.py::test_output_directory_selection_is_required`, `scripts/verify_mis_pilot_report.py` `output_root_containment` | A |
| 17 | 필수 마스크 제거 불가 | `tests/unit/infrastructure/test_screenshots.py` (세 마스크 출처 합집합), `tests/unit/adapters/windows/test_context.py`, `tests/ui/test_target_picker.py` | A |
| 18 | 부모 모듈 import 없음 | `tests/unit/test_project_isolation.py` + `scripts\smoke_packaged.ps1` (빈 CWD·`PYTHONPATH` 제거) | A, D |
| 19 | Windows 10/11 x64 패키지 | `docs/validation/mis-read-only-pilot-windows-10-x64.md`, `docs/validation/mis-read-only-pilot-windows-11-x64.md` 의 self-check·smoke 절 | D |
| 20 | LLM 없는 read-only MIS 파일럿 | 위 두 문서의 5문서 게이트 결과 (`pilot gate: PASS`) | 런북 1.10 |

## 미완료 항목과 그 이유

**행 19 (패키지 서명)** — 이 저장소에서 `scripts\build.ps1`을 실행했으나 Nuitka가
standalone 빌드를 완료하지 못했다. 대상 머신에 MSVC도 gcc도 없어 Nuitka가
dependency walker와 C 툴체인을 내려받아야 하는데, `pysidedeploy.spec`에
`--assume-yes-for-downloads`를 넣어 무인 실행이 프롬프트에서 멈추지 않도록
고쳤지만 실제 컴파일은 검증하지 못했다. `dist/`와 EXE가 없으므로
`smoke_packaged.ps1`도 미실행이다. Windows 10과 11 각각에서 D 게이트를 실행하고
그 출력을 아래 두 문서에 기록해야 한다.

**행 20 (MIS 파일럿)** — 승인된 read-only MIS 접근과 승인된 워크플로가 필요하다.
`docs/pilot/mis-read-only-pilot-runbook.md`의 0장 전제 조건부터 따르고,
`pilot-policy.json`을 번들과 **독립적으로** 준비한다(번들이 스스로의 정확성을
주장하지 못하게 하는 것이 이 게이트의 핵심이다). 1.10의 두 명령이 `--summary`로
아래 두 문서를 자동 생성한다.

- `docs/validation/mis-read-only-pilot-windows-10-x64.md`
- `docs/validation/mis-read-only-pilot-windows-11-x64.md`

두 문서가 모두 `PASS`로 생성되고 행 19의 D 게이트가 두 OS에서 통과한 뒤에만
MVP 완료를 선언한다.
