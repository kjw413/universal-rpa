# Universal RPA Studio MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 비개발 업무 담당자가 한 Windows 데스크톱 앱에서 업무를 녹화·편집·검증·반복 실행하고, 클립보드 표를 안전하게 CSV/XLSX로 저장하며 결과 보고서를 확인할 수 있는 런타임 LLM 없는 Universal RPA Studio MVP를 구축한다.

**Architecture:** `universal_rpa/` 안의 독립 `src/` 패키지에 Domain Core, Application Service, Port, Adapter, PySide6 UI를 분리한다. 워크플로는 schema major version 1의 Pydantic 모델과 JSON으로 저장하며, 동기식 어댑터를 실행 worker가 호출하고 UI thread에는 signal로 상태만 전달한다. Windows UIA selector를 우선 사용하고 여섯 guard 범주(기록 창 identity, DPI, client 크기, foreground, client bounds, postcondition/assertion)를 모두 통과한 경우에만 client-relative 좌표로 fallback한다.

**Tech Stack:** Windows 10/11 x64, Python `>=3.12,<3.14`, PySide6 Qt Widgets, pywinauto UIA backend, pywin32, pynput, Pydantic 2, JSON Schema, openpyxl, 표준 `csv`, pytest, pytest-qt, Ruff, mypy, `pyside6-deploy`.

## Global Constraints

- 모든 제품·테스트·문서·CI 파일은 `universal_rpa/` 아래에만 만들고 부모 프로젝트 모듈·설정·fixture를 import하거나 참조하지 않는다.
- 현재 저장소 안에는 중첩 `.git`을 만들지 않으며, 모든 경로와 명령은 `universal_rpa/`가 별도 저장소 루트가 되어도 그대로 동작해야 한다.
- 지원 OS는 Windows 10 x64와 Windows 11 x64, Python은 `>=3.12,<3.14`, UI 언어는 한국어다.
- workflow schema major version은 `1`이며 알 수 없는 major version은 migration 없이 거부한다.
- 런타임 LLM, 네트워크 AI 호출, 임의 Python·PowerShell·배치·shell 실행은 추가하지 않는다.
- raw recording은 `%LOCALAPPDATA%\UniversalRPAStudio\recordings\<session_id>\`에 append-only JSONL로 저장하고 기본 7일 뒤 삭제한다.
- 실행 artifact는 기본 30일 보존하며 workflow, raw recording, 로그, 보고서, screenshot 어디에도 평문 secret을 남기지 않는다.
- `frozen=True`만으로 중첩 불변성을 가정하지 않는다. 검증·저장·fingerprint 경계의 모든 JSON/mapping은 M1 `FrozenMapping`/`FrozenJsonValue`로 방어 복사·deep-freeze하고 JSON 출력 때만 새 mutable 사본으로 thaw한다.
- 별도 WinEvent/UIA watcher가 event-context token을 갱신하고 listener callback은 timestamp·입력과 token의 메모리 복사 및 queue/priority-control 동작만 한다. UIA·Win32 조회, 정규화, 디스크 쓰기는 callback 밖에서 수행한다.
- UIA selector는 정확히 한 요소에 일치해야 하며, 좌표 fallback은 명세의 환경·foreground·assertion 조건을 모두 만족할 때만 허용한다.
- 모든 global mouse·keyboard 입력 직전에 foreground process와 top-level window를 재검증하고 불일치 시 입력 없이 실패한다.
- 모든 wait는 유한 timeout을 가지며 편집기 기본값은 30초다.
- `retry_count` 기본값은 0, 최대 3이며 총 시도 횟수는 `1 + retry_count`(최대 4)다. 어댑터 descriptor가 idempotent 및 해당 error를 retryable로 선언한 action에만 허용하며 workflow는 이 속성을 자체 선언할 수 없다.
- loop editor 기본값은 최대 1,000회·2시간, 제품 hard limit는 10,000회·24시간, 최대 중첩 깊이는 2다.
- CSV/XLSX 출력은 사용자가 선택한 runtime output root 아래의 검증된 상대경로에만 쓴다. 같은 디렉터리의 임시 파일을 검증·durable flush한 후 `os.replace`로 교체하며, 실패·취소 시 기존 정상 파일을 보존한다.
- 반복 중간 journal에 비멱등 성공/진행 action이 있으면 자동 resume 전에 `RESUME_UNSAFE`로 중단하고 수동 복구를 요구한다.
- 각 작업은 실패 테스트 작성 → 실패 확인 → 최소 구현 → 관련 테스트·정적 검사 → 독립 커밋 순서를 지킨다.

---

## Plan Set and Execution Order

이 MVP는 다섯 개의 강하게 순차 의존하는 milestone으로 구성한다. 각 milestone은
독립적인 review gate와 자동 검증 결과를 만들며, 다음 순서만 허용한다.

1. [M1 Domain Core](2026-07-27-universal-rpa-m1-domain-core.md)
2. [M2 Recorder and Normalizer](2026-07-27-universal-rpa-m2-recorder-normalizer.md)
3. [M3 Studio Editor and Validator](2026-07-27-universal-rpa-m3-editor-validator.md)
4. [M4 Windows Runner](2026-07-27-universal-rpa-m4-windows-runner.md)
5. [M5 Extraction, Report, and Packaging](2026-07-27-universal-rpa-m5-extraction-report-packaging.md)

각 하위 계획은 `universal_rpa/`를 현재 디렉터리로 가정한다. M1의 공개 타입과
port가 M2~M5의 compile-time 계약이다. 선행 milestone이 review·test·commit
gate를 통과하기 전에는 후속 milestone을 구현하지 않는다.

## Working Conventions

- local interpreter: `.\.venv\Scripts\python.exe`
- unit test: `.\.venv\Scripts\python.exe -m pytest`
- lint: `.\.venv\Scripts\python.exe -m ruff check src tests samples`
- formatting check: `.\.venv\Scripts\python.exe -m ruff format --check src tests samples`
- type check: `.\.venv\Scripts\python.exe -m mypy src`
- UI 표시 문자열은 한국어, Python 식별자·JSON key·구조화 event name은 영어
- test path는 production feature를 `tests/unit`, `tests/contract`,
  `tests/integration`, `tests/ui` 아래에서 mirror
- 모든 commit은 해당 task의 파일만 stage하며 `.superpowers/`, 사용자 project,
  recording, run artifact를 포함하지 않음

## Locked Public Vocabulary

| Concern | Exact name |
|---|---|
| workflow root | `Workflow` |
| step union | `ActionStep`, `LoopStep`, `IfPresentStep`, `Step` |
| action value | `LiteralValue`, `VariableValue`, `RowBindingValue`, `SecretRefValue`, `ValueSpec` |
| Windows target | `UiaSelector`, `CoordinateFallback`, `WindowsTarget`, `TargetSpec` |
| validation | `ValidationIssue`, `ValidationReport`, `ValidationService` |
| execution | `RunRequest`, `RunInputs`, `ExecutionContext`, `ExecutionService` |
| outcomes | `ActionResult`, `RunReport`, `RunStatus`, `ErrorCode`, `LoopCursor` |
| adapters | `AutomationAdapter`, `TargetCapturePort`, `AdapterRegistry`, `AdapterDescriptor`, `TargetCaptureRequest`, `TargetCaptureResult` |
| cancellation | `CancellationToken`, `RunControl` |
| tabular data | `TableData`, `DataSourceSpec`, `DataSourcePort`, `OutputCommit` |
| secrets | `SecretRefValue`, `SecretValue`, `SecretStorePort` |
| immutable values | `FrozenMapping`, `FrozenJsonValue`, `FrozenJsonObject`, `deep_freeze_json`, `thaw_json` |
| variable preparation | `DateContext`, `PreparedVariables`, `VariablePreparationService` |

Serialized action, condition, and assertion names always use
`<adapter_id>.<local_name>`. Built-in IDs are `windows`, `clipboard`, and
`tabular`. Extension examples `web`, `http`, `mail`, and `fileops` appear only
in contract tests and adapter documentation during this MVP.

## Cross-Milestone Contracts

```python
type JsonValue = (
    None
    | bool
    | int
    | float
    | str
    | list["JsonValue"]
    | dict[str, "JsonValue"]
)
type DataCell = None | bool | int | float | str
type PreparedValue = str | int | Decimal | date | Path


class ErrorCode(StrEnum):
    INVALID_SCHEMA = "invalid_schema"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    ADAPTER_MISSING = "adapter_missing"
    ACTION_UNSUPPORTED = "action_unsupported"
    TARGET_NOT_FOUND = "target_not_found"
    TARGET_AMBIGUOUS = "target_ambiguous"
    ENVIRONMENT_MISMATCH = "environment_mismatch"
    FOREGROUND_MISMATCH = "foreground_mismatch"
    CONDITION_TIMEOUT = "condition_timeout"
    ASSERTION_FAILED = "assertion_failed"
    DATA_SOURCE_INVALID = "data_source_invalid"
    SECRET_MISSING = "secret_missing"
    OUTPUT_UNAVAILABLE = "output_unavailable"
    ACTION_FAILED = "action_failed"
    CHECKPOINT_INVALID = "checkpoint_invalid"
    RESUME_MISMATCH = "resume_mismatch"
    RESUME_UNSAFE = "resume_unsafe"
    CANCELLED = "cancelled"
    INTERNAL_ERROR = "internal_error"

class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: ErrorCode
    path: str
    safe_message: str
    severity: Literal["error", "warning"] = "error"
    step_id: UUID | None = None
```

```python
@dataclass(frozen=True, slots=True)
class ExecutionContext:
    run_id: UUID
    step_id: UUID
    iteration_path: tuple[int, ...]
    variables: FrozenMapping[str, PreparedValue]
    credential_refs: FrozenMapping[str, str]
    date_context: DateContext
    output_root: Path
    row_stack: tuple[FrozenMapping[str, DataCell], ...]
    action_outputs: FrozenMapping[UUID, FrozenJsonValue | TableData]

@dataclass(frozen=True, slots=True)
class TargetCaptureRequest:
    runtime: RuntimeEnvironment
    screen_x: int
    screen_y: int
    focused_runtime_id: tuple[int, ...] | None

@dataclass(frozen=True, slots=True)
class TargetCaptureResult:
    target: TargetSpec | None
    candidates: tuple[TargetSpec, ...]
    preview_png: bytes | None
    issues: tuple[ValidationIssue, ...] = ()

    def __post_init__(self) -> None: ...

@dataclass(frozen=True, slots=True)
class AdapterDescriptor:
    adapter_id: str
    implementation_version: str
    supports_target_capture: bool
    actions: frozenset[str]
    conditions: frozenset[str]
    assertions: frozenset[str]
    verification_by_action: FrozenMapping[
        str,
        Literal["postcondition_or_assertion", "intrinsic", "none"],
    ]
    idempotent_actions: frozenset[str]
    retryable_errors_by_action: FrozenMapping[str, frozenset[ErrorCode]]
    assertions_by_action: FrozenMapping[str, frozenset[str]]
    assertion_input_kind: FrozenMapping[
        str,
        Literal["json", "table", "output_commit"],
    ]

    def __post_init__(self) -> None: ...

@dataclass(frozen=True, slots=True)
class AssertionObservation:
    passed: bool
    evidence: FrozenJsonObject

class TargetCapturePort(Protocol):
    def capture_target(
        self,
        request: TargetCaptureRequest,
        cancellation: CancellationToken,
    ) -> TargetCaptureResult: ...

class AutomationAdapter(TargetCapturePort, Protocol):
    @property
    def adapter_id(self) -> str: ...
    def descriptor(self) -> AdapterDescriptor: ...
    def capture_target(
        self,
        request: TargetCaptureRequest,
        cancellation: CancellationToken,
    ) -> TargetCaptureResult: ...
    def validate_action_spec(
        self,
        step: ActionStep,
    ) -> tuple[ValidationIssue, ...]: ...
    def validate_condition_spec(
        self,
        condition: ConditionSpec,
    ) -> tuple[ValidationIssue, ...]: ...
    def validate_assertion_spec(
        self,
        assertion: AssertionSpec | TableAssertionSpec,
    ) -> tuple[ValidationIssue, ...]: ...
    def validate_target(
        self,
        target: TargetSpec,
        runtime: RuntimeEnvironment,
        mode: Literal["must_exist_now", "may_be_absent_now", "deferred"],
    ) -> tuple[ValidationIssue, ...]: ...
    def execute(
        self,
        request: ActionRequest,
        context: ExecutionContext,
        cancellation: CancellationToken,
    ) -> AdapterActionResult: ...
    def evaluate_condition(
        self,
        condition: ConditionSpec,
        context: ExecutionContext,
        cancellation: CancellationToken,
    ) -> ConditionObservation: ...
    def evaluate_assertion(
        self,
        assertion: AssertionSpec | TableAssertionSpec,
        subject: FrozenJsonValue | TableData | OutputCommit | None,
        target: TargetSpec | None,
        context: ExecutionContext,
        cancellation: CancellationToken,
    ) -> AssertionObservation: ...
```

`TargetCaptureResult` may select only a target contained in its immutable
`candidates`; Windows region and mandatory/user mask metadata live only inside
each candidate payload. `AdapterDescriptor.__post_init__` defensively copies and
canonicalizes every mapping and nested set into immutable values before the
registry or fingerprint can observe it.

```python
class DataSourcePort(Protocol):
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

class SecretStorePort(Protocol):
    def exists(self, reference: str) -> bool: ...
    def read(self, reference: str) -> SecretValue: ...

class WorkflowRepositoryPort(Protocol):
    def load(self, project_dir: Path) -> Workflow: ...
    def save(
        self,
        project_dir: Path,
        workflow: Workflow,
        expected_revision: int,
    ) -> Workflow: ...
```

## Milestone Gates

| Gate | Required evidence |
|---|---|
| M1 | package isolation, schema snapshot, fake adapter contract, Ruff, mypy |
| M2 | non-blocking listener, JSONL retention, mouse/key/IME/secret normalization |
| M3 | one Qt shell, three-pane editor, edit-command immutability, validation-only |
| M4 | target guards, foreground guard, waits/assertions, retry/cancel/checkpoint/resume |
| M5 | CSV/XLSX atomicity, redacted reports, harness E2E, packaged smoke, MIS pilot |

## Acceptance Trace

| Design acceptance criterion | Plan owner |
|---|---|
| one app: select, record, edit, test, run, report | M3 Tasks 3–5; M5 Task 3 |
| meaningful mouse and keyboard steps | M2 Tasks 5–7 |
| `Ctrl+A → date → Enter` split | M2 Task 6 |
| literal/variable/row/secret modes | M1 Task 2; M3 Task 6 |
| date form and whitelist calculations | M1 Task 2; M3 Task 6 |
| CSV/XLSX and depth-two loop | M1 Task 4; M4 Task 6; M5 Task 1 |
| UIA first, guarded coordinate fallback | M4 Task 2 |
| environment mismatch stops before click | M4 Tasks 1–2 |
| state wait and clipboard assertion | M4 Tasks 4–5 |
| resume after last successful iteration | M4 Task 7 |
| incomplete iteration never replays non-idempotent input automatically | M4 Task 7; M5 Task 3 |
| no secret in any artifact | M2 Tasks 1–4, 6; M4 Task 3; M5 Task 2 |
| nested workflow/event/report data cannot mutate after validation | M1 Tasks 2–5; M2 Tasks 1, 5 |
| event-time focus and queue-independent `Ctrl+Shift+F12` | M2 Tasks 1, 3–4 |
| existing output preserved on failure | M5 Task 1 |
| output stays beneath the selected runtime root | M3 Task 2; M4 Tasks 1, 7; M5 Task 1 |
| mandatory password/secret masks cannot be removed | M2 Task 4; M3 Task 6; M5 Task 2 |
| no parent import | M1 Task 1 and every full-suite gate |
| packaged app on Windows 10/11 x64 | M5 Task 5 |
| read-only MIS pilot without LLM | M5 Task 6 |

## Final Definition of Done

MVP 완료 선언은 M1~M5의 모든 checkbox, 전체 자동 test, Windows package smoke,
실제 MIS read-only pilot이 모두 통과한 뒤에만 가능하다. pilot workflow와 실제
업무 데이터는 source repository 밖의 사용자 project directory에 남기고, 저장소에는
자동 생성된 redacted acceptance summary만 포함한다.

## Execution Checklist

- [ ] Execute and review M1 Domain Core.
- [ ] Execute and review M2 Recorder and Normalizer.
- [ ] Execute and review M3 Studio Editor and Validator.
- [ ] Execute and review M4 Windows Runner.
- [ ] Execute and review M5 Extraction, Report, Packaging, and MIS pilot.
