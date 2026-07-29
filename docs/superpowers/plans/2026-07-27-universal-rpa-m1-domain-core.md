# Universal RPA M1 Domain Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 부모 저장소와 독립적으로 설치·검증되는 Python package, workflow schema v1, 공통 결과 모델, adapter 확장 계약, deterministic fake adapter를 완성한다.

**Architecture:** `src/universal_rpa` 아래의 Pydantic Domain Core는 UI와 Windows API를 import하지 않는다. 직렬화 모델은 extra field를 거부하고 frozen value object로 동작하며, adapter registry가 built-in adapter와 `universal_rpa.adapters` entry point를 같은 계약으로 다룬다.

**Tech Stack:** Python `>=3.12,<3.14`, Pydantic 2, JSON Schema, pytest, Ruff, mypy, setuptools `src` layout.

## Global Constraints

- 작업 디렉터리는 `universal_rpa/`이며 이 디렉터리 밖의 Python module·fixture·설정을 import하지 않는다.
- workflow schema major version은 문자열 `"1"`이고, 알 수 없는 major version과 extra field는 자동 보정 없이 거부한다.
- runtime model에는 LLM, network AI, arbitrary code/shell field를 두지 않는다.
- 모든 Pydantic persistence model은 `ConfigDict(extra="forbid", frozen=True)`를 사용한다.
- action·condition·assertion 이름은 `<adapter_id>.<local_name>` 정규식을 통과해야 한다.
- 테스트는 실제 mouse·keyboard 입력을 보내지 않고 fake adapter만 사용한다.

---

### Task 1: Self-contained project scaffold and isolation gate

**Files:**

- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `README.md`
- Create: `.github/workflows/windows.yml`
- Create: `src/universal_rpa/__init__.py`
- Create: `src/universal_rpa/py.typed`
- Create: `tests/unit/test_project_isolation.py`

**Interfaces:**

- Produces: `universal_rpa.__version__: str`
- Produces: `universal_rpa.WORKFLOW_SCHEMA_MAJOR: Final[str]`
- Consumes: no parent-repository module or configuration

- [ ] **Step 1: Create package metadata and the failing isolation test**

Use this project metadata; keep every tool path relative to this directory.

```toml
[build-system]
requires = ["setuptools>=75", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "universal-rpa-studio"
version = "0.1.0"
description = "Deterministic local Windows RPA Studio"
readme = "README.md"
requires-python = ">=3.12,<3.14"
dependencies = [
  "PySide6>=6.8,<7",
  "pydantic>=2.10,<3",
  "pynput>=1.7.7,<2",
  "pywinauto==0.6.9",
  "pywin32>=308,<400",
  "openpyxl>=3.1,<4",
]

[project.optional-dependencies]
dev = [
  "mypy>=1.14,<2",
  "pytest>=8.3,<10",
  "pytest-qt>=4.4,<5",
  "ruff>=0.9,<1",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
universal_rpa = ["py.typed"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra --strict-markers"
markers = [
  "windows_e2e: sends input to the deterministic Windows test harness",
  "mis_pilot: requires the approved read-only MIS pilot environment",
]

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP", "RUF"]

[tool.mypy]
python_version = "3.12"
strict = true
packages = ["universal_rpa"]
```

```python
# tests/unit/test_project_isolation.py
from __future__ import annotations

import ast
import sys
from importlib.metadata import metadata
from pathlib import Path
from sys import stdlib_module_names

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_IMPORT_ROOTS = stdlib_module_names | {
    "PySide6", "openpyxl", "pydantic", "pynput", "pywinauto",
    "pythoncom", "typing_extensions", "universal_rpa",
    "win32api", "win32con", "win32cred", "win32event", "win32file",
    "win32gui", "win32process", "win32security",
}


def test_package_exposes_schema_major_and_version() -> None:
    import universal_rpa

    assert universal_rpa.WORKFLOW_SCHEMA_MAJOR == "1"
    assert universal_rpa.__version__ == "0.1.0"


def test_root_import_is_lightweight_and_resolves_only_to_this_project() -> None:
    import universal_rpa

    assert Path(universal_rpa.__file__).resolve().is_relative_to(
        (PROJECT_ROOT / "src" / "universal_rpa").resolve()
    )
    forbidden = {
        "PySide6", "pywinauto", "_common", "production_daily_rpa",
        "utility_daily_rpa", "wip_daily_rpa",
    }
    assert forbidden.isdisjoint(sys.modules)


def test_source_import_graph_has_no_undeclared_parent_modules() -> None:
    for source in (PROJECT_ROOT / "src" / "universal_rpa").rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.partition(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = {node.module.partition(".")[0]}
            else:
                continue
            assert roots <= ALLOWED_IMPORT_ROOTS, (source, roots - ALLOWED_IMPORT_ROOTS)


def test_supported_python_range_is_exact() -> None:
    assert metadata("universal-rpa-studio")["Requires-Python"] == ">=3.12,<3.14"
```

- [ ] **Step 2: Create a clean virtual environment and verify the test fails**

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest tests/unit/test_project_isolation.py -q
```

Expected: FAIL because `WORKFLOW_SCHEMA_MAJOR` and `__version__` are not defined.

- [ ] **Step 3: Add the minimal package API and repository exclusions**

```python
# src/universal_rpa/__init__.py
from typing import Final

__version__: Final[str] = "0.1.0"
WORKFLOW_SCHEMA_MAJOR: Final[str] = "1"

__all__ = ["WORKFLOW_SCHEMA_MAJOR", "__version__"]
```

`.gitignore` must include `.venv/`, `build/`, `dist/`, `*.spec~`, `.pytest_cache/`,
`.mypy_cache/`, `.ruff_cache/`, `__pycache__/`, `*.py[cod]`, `projects/`,
`recordings/`, `artifacts/`, and `*.credential`.
`README.md` must state Windows 10/11 x64, interactive unlocked session, no
UAC/MFA/CAPTCHA bypass, no runtime LLM, and that commands run from this directory.
The CI workflow must run on `windows-latest` for Python 3.12 and 3.13 and execute
the Task 1 test plus Ruff and mypy.

- [ ] **Step 4: Run the isolation and metadata gate**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_project_isolation.py -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all commands exit 0 and importing the package does not load PySide6,
pywinauto, or any parent MIS module.

- [ ] **Step 5: Commit the isolated package base**

```powershell
git add pyproject.toml .gitignore README.md .github src/universal_rpa tests/unit/test_project_isolation.py
git commit -m "build(universal-rpa): scaffold isolated package"
```

---

### Task 2: Target, value mode, variable, and date-rule models

**Files:**

- Create: `src/universal_rpa/domain/__init__.py`
- Create: `src/universal_rpa/domain/types.py`
- Create: `src/universal_rpa/domain/targets.py`
- Create: `src/universal_rpa/domain/values.py`
- Create: `src/universal_rpa/application/date_rules.py`
- Create: `tests/unit/domain/test_targets.py`
- Create: `tests/unit/domain/test_values.py`
- Create: `tests/unit/domain/test_immutable_json.py`
- Create: `tests/unit/application/test_date_rules.py`

**Interfaces:**

- Produces: `JsonValue`, `FrozenMapping`, `FrozenJsonValue`, `FrozenJsonObject`,
  `deep_freeze_json`, `thaw_json`
- Produces: `RuntimeEnvironment`, `NormalizedRect`, `UiaSelector`,
  `CoordinateFallback`, `WindowsTarget`, `TargetSpec`, `DateContext`
- Produces: `LiteralValue`, `VariableValue`, `RowBindingValue`,
  `SecretRefValue`, `ValueSpec`, `VariableDefinition`
- Produces: `evaluate_date_expression(expression, context) -> date`

- [ ] **Step 1: Write failing model-invariant tests**

```python
def test_nested_json_is_defensively_copied_and_deeply_immutable() -> None:
    source = {"nested": {"items": ["safe"]}}
    target = TargetSpec(adapter_id="fake", payload=source)
    source["nested"]["items"][0] = "mutated"
    assert thaw_json(target.payload) == {"nested": {"items": ["safe"]}}
    with pytest.raises(TypeError):
        target.payload["nested"]["items"][0] = "mutated"


def test_coordinate_must_be_normalized() -> None:
    with pytest.raises(ValidationError):
        RelativePoint(x=1.01, y=0.5)


def test_coordinate_fallback_requires_recorded_window_identity() -> None:
    with pytest.raises(ValidationError):
        CoordinateFallback.model_validate(
            {
                "point": {"x": 0.5, "y": 0.5},
                "recorded_dpi_x": 96,
                "recorded_dpi_y": 96,
                "recorded_client_width": 800,
                "recorded_client_height": 600,
            }
        )


def test_windows_target_requires_selector_or_fallback() -> None:
    with pytest.raises(ValidationError):
        WindowsTarget(selector=None, coordinate_fallback=None)


def test_secret_reference_cannot_accept_plaintext() -> None:
    with pytest.raises(ValidationError):
        SecretRefValue.model_validate(
            {"mode": "secret_ref", "credential_ref": "erp/password", "value": "plain"}
        )


def test_row_binding_accepts_only_one_row_column() -> None:
    assert RowBindingValue(template="{{ row.factory }}").column_name == "factory"
    with pytest.raises(ValidationError):
        RowBindingValue(template="{{ row['factory'] }}")


def test_variable_sources_are_typed_and_cross_references_are_explicit() -> None:
    variable = VariableDefinition.model_validate(
        {
            "variable_id": "start_date",
            "label": "시작일",
            "value_type": "date",
            "source": {
                "source_type": "date_rule",
                "expression": {"operation": "run_date"},
            },
        }
    )
    assert variable.source.source_type == "date_rule"
    with pytest.raises(ValidationError):
        VariableDefinition.model_validate(
            {
                "variable_id": "password",
                "label": "비밀번호",
                "value_type": "secret",
                "source": {"source_type": "fixed_default", "value": "plain"},
            }
        )


def test_month_end_handles_leap_year() -> None:
    expression = DateExpression(operation="month_end", operand=DateExpression(operation="run_date"))
    assert evaluate_date_expression(
        expression,
        DateContext(today=date(2024, 1, 1), run_date=date(2024, 2, 10)),
    ) == date(2024, 2, 29)
```

- [ ] **Step 2: Run the three test modules and confirm import failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/domain/test_targets.py tests/unit/domain/test_values.py tests/unit/domain/test_immutable_json.py tests/unit/application/test_date_rules.py -q
```

Expected: FAIL with missing domain modules.

- [ ] **Step 3: Implement the exact value and target shapes**

```python
type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
type DataCell = JsonScalar

K = TypeVar("K")
V = TypeVar("V")

@dataclass(frozen=True, slots=True)
class FrozenMapping(Mapping[K, V]):
    _items: tuple[tuple[K, V], ...]
    @classmethod
    def from_mapping(cls, value: Mapping[K, V]) -> "FrozenMapping[K, V]": ...
    @classmethod
    def empty(cls) -> "FrozenMapping[K, V]": ...
    def __getitem__(self, key: K) -> V: ...
    def __iter__(self) -> Iterator[K]: ...
    def __len__(self) -> int: ...

type FrozenJsonValue = (
    JsonScalar
    | tuple["FrozenJsonValue", ...]
    | FrozenMapping[str, "FrozenJsonValue"]
)
type FrozenJsonObject = FrozenMapping[str, FrozenJsonValue]

def deep_freeze_json(value: JsonValue | FrozenJsonValue) -> FrozenJsonValue: ...
def thaw_json(value: FrozenJsonValue) -> JsonValue: ...

class RelativePoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)

class NormalizedRect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)

class UiaSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    automation_id: str | None = None
    control_type: str | None = None
    name: str | None = None
    class_name: str | None = None
    ancestor_path: tuple["UiaSelector", ...] = ()

class CoordinateFallback(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    recorded_process_executable: str = Field(min_length=1)
    recorded_window_class: str = Field(min_length=1)
    point: RelativePoint
    recorded_dpi_x: int = Field(gt=0)
    recorded_dpi_y: int = Field(gt=0)
    recorded_client_width: int = Field(gt=0)
    recorded_client_height: int = Field(gt=0)

class WindowsTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    selector: UiaSelector | None
    coordinate_fallback: CoordinateFallback | None
    target_region: NormalizedRect | None = None
    mandatory_sensitive_regions: tuple[NormalizedRect, ...] = ()
    user_sensitive_regions: tuple[NormalizedRect, ...] = ()
    diagnostic_absolute_x: int | None = None
    diagnostic_absolute_y: int | None = None

class TargetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    adapter_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    payload: FrozenJsonObject

class RuntimeEnvironment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    interactive_desktop: bool
    process_id: int = Field(gt=0)
    process_executable: str
    top_level_hwnd: int
    window_title: str
    window_class: str
    foreground_hwnd: int
    dpi_x: int = Field(gt=0)
    dpi_y: int = Field(gt=0)
    client_width: int = Field(gt=0)
    client_height: int = Field(gt=0)
    monitor_scale: float = Field(gt=0)
```

`UiaSelector` requires at least one of automation ID, control type, name, or class.
`WindowsTarget` requires a selector or coordinate fallback. `target_region`,
`mandatory_sensitive_regions`, and `user_sensitive_regions` are normalized to
the recorded top-level client area and persist inside the target. Password and
secret-derived regions are mandatory and cannot be removed; the masking union is
mandatory plus user regions. `TargetSpec.payload` is defensively deep-frozen and
validated by the owning adapter before use. `FrozenMapping` is tuple-backed,
recursively freezes list/dict input, and Pydantic field validators/serializers use
`deep_freeze_json`/`thaw_json` so JSON schema and on-disk JSON remain ordinary
objects/arrays. No frozen model may retain a caller-owned mutable container.

```python
class LiteralValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["literal"] = "literal"
    value: str | int | float | bool | None

class VariableValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["variable"] = "variable"
    variable_id: str

class RowBindingValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["row_binding"] = "row_binding"
    template: str = Field(pattern=r"^\{\{ row\.[A-Za-z_][A-Za-z0-9_]* \}\}$")

    @property
    def column_name(self) -> str:
        return self.template[7:-3]

class SecretRefValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["secret_ref"] = "secret_ref"
    credential_ref: str = Field(min_length=1)

ValueSpec = Annotated[
    LiteralValue | VariableValue | RowBindingValue | SecretRefValue,
    Field(discriminator="mode"),
]

class RunInputSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_type: Literal["run_input"] = "run_input"
    required: bool = True

class FixedDefaultSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_type: Literal["fixed_default"] = "fixed_default"
    value: str | int | float | bool | None

class InlineChoiceSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_type: Literal["inline_options"] = "inline_options"
    options: tuple[str, ...] = Field(min_length=1)

class DataColumnSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_type: Literal["csv_column", "xlsx_column"]
    data_source_id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    column_name: str = Field(min_length=1)
    required: bool = True

class DateContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    today: date
    run_date: date

class DateExpression(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    operation: Literal["today", "run_date", "add_days", "month_start", "month_end"]
    operand: "DateExpression | None" = None
    days: int | None = None

class DateRuleSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_type: Literal["date_rule"] = "date_rule"
    expression: DateExpression

class CredentialSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_type: Literal["credential_ref"] = "credential_ref"
    credential_ref: str = Field(min_length=1)

VariableSource = Annotated[
    RunInputSource
    | FixedDefaultSource
    | InlineChoiceSource
    | DataColumnSource
    | DateRuleSource
    | CredentialSource,
    Field(discriminator="source_type"),
]

class VariableDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    variable_id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    label: str = Field(min_length=1)
    value_type: Literal["text", "date", "integer", "decimal", "path", "choice", "secret"]
    source: VariableSource
```

`VariableDefinition` enforces this exhaustive source/type matrix:

- `run_input`: text/date/integer/decimal/path;
- `fixed_default`: text/date/integer/decimal/path with strict typed parsing;
- `inline_options`: choice only, trimmed non-blank unique options;
- `csv_column`/`xlsx_column`: choice option providers only, matched to the exact
  referenced data-source kind; the user-selected scalar is stored in `RunInputs`;
- `date_rule`: date only;
- `credential_ref`: secret only.

A data column never means “take the first row” and never replaces
`RowBindingValue` inside a loop. Integer rejects bool, decimal rejects NaN and
infinity, date requires ISO `YYYY-MM-DD`, and path validation never expands shell
syntax. Plaintext defaults are forbidden for secret. Conversion failures are
validation errors before execution, never implicit string coercions.

Implement `DateExpression` as a recursive Pydantic model whose `operation` is
exactly `today`, `run_date`, `add_days`, `month_start`, or `month_end`.
`today`/`run_date` accept no operand; `month_start`/`month_end` require one
operand; `add_days` requires one operand and an integer `days`. Implement with
`datetime.date`, `calendar.monthrange`, and `timedelta`; never call `eval`.

- [ ] **Step 4: Verify invariants, formatting, and typing**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/domain/test_targets.py tests/unit/domain/test_values.py tests/unit/domain/test_immutable_json.py tests/unit/application/test_date_rules.py -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass, including leap-year and unknown-operation rejection.

- [ ] **Step 5: Commit the value and target model**

```powershell
git add src/universal_rpa/domain src/universal_rpa/application tests/unit/domain tests/unit/application
git commit -m "feat(universal-rpa): add values targets and date rules"
```

---

### Task 3: Conditions, safe errors, and result aggregation

**Files:**

- Create: `src/universal_rpa/domain/errors.py`
- Create: `src/universal_rpa/domain/conditions.py`
- Create: `src/universal_rpa/domain/results.py`
- Create: `src/universal_rpa/infrastructure/redaction.py`
- Create: `tests/unit/domain/test_conditions.py`
- Create: `tests/unit/domain/test_results.py`
- Create: `tests/unit/infrastructure/test_redaction.py`

**Interfaces:**

- Produces: `ErrorCode`, `RpaError`, `ValidationIssue`, `ValidationReport`
- Produces: `ConditionSpec`, `WaitSpec`, `AssertionSpec`, `TableAssertionSpec`
- Produces: `TableData`, `OutputCommit`, `ActionResult`, `RunReport`, `aggregate_run_status`
- Produces: `sanitize_evidence(value) -> FrozenJsonObject`

- [ ] **Step 1: Write failing timeout, evidence, and status tests**

```python
def test_every_wait_has_a_finite_timeout() -> None:
    with pytest.raises(ValidationError):
        WaitSpec.model_validate(
            {"condition": {"condition_type": "windows.element_exists"}, "timeout_ms": 0}
        )


def test_table_assertion_rejects_inverted_row_range() -> None:
    with pytest.raises(ValidationError):
        TableAssertionSpec(min_rows=10, max_rows=9)


def test_safe_result_rejects_clipboard_body() -> None:
    with pytest.raises(ValidationError):
        ActionResult(
            run_id=uuid4(),
            step_id=uuid4(),
            iteration_path=(),
            status="failed",
            started_at=datetime.now(UTC),
            duration_ms=1,
            attempt_count=1,
            error_code="assertion_failed",
            safe_message="표 검증 실패",
            evidence={"clipboard_text": "secret rows"},
        )


def test_optional_absence_does_not_make_run_partial() -> None:
    results = [
        action_result(status="success"),
        action_result(status="skipped", skip_reason="if_present_absent"),
    ]
    assert aggregate_run_status(results) == "success"


def test_explicit_failed_row_skip_makes_run_partial() -> None:
    results = [
        action_result(status="success"),
        action_result(status="skipped", skip_reason="skip_iteration"),
    ]
    assert aggregate_run_status(results) == "partial"


def test_preflight_failure_is_typed_without_fabricating_action_result() -> None:
    report = run_report(
        status="failed",
        results=(),
        error_code=ErrorCode.ENVIRONMENT_MISMATCH,
        safe_message="대상 실행 환경이 기록 환경과 다릅니다",
    )
    assert report.results == ()
    assert report.error_code is ErrorCode.ENVIRONMENT_MISMATCH
    assert report.safe_message == "대상 실행 환경이 기록 환경과 다릅니다"


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_failed_and_cancelled_take_precedence(status: str) -> None:
    assert aggregate_run_status([action_result(status=status)]) == status
```

- [ ] **Step 2: Run focused tests and confirm they fail**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/domain/test_conditions.py tests/unit/domain/test_results.py tests/unit/infrastructure/test_redaction.py -q
```

Expected: FAIL because condition/result types are missing.

- [ ] **Step 3: Implement finite waits and safe result objects**

`ErrorCode` must use the exact values locked in the master plan. Define:

```python
class ConditionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    condition_type: str = Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
    target: TargetSpec | None = None
    expected: FrozenJsonValue = None

class WaitSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    condition: ConditionSpec
    timeout_ms: int = Field(gt=0, le=86_400_000)
    poll_interval_ms: int = Field(default=100, gt=0)

class AssertionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    assertion_type: str = Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
    expected: FrozenJsonValue = None

class TableAssertionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    assertion_type: Literal["clipboard.table"] = "clipboard.table"
    required_headers: frozenset[str] = frozenset()
    min_rows: int | None = Field(default=None, ge=0)
    max_rows: int | None = Field(default=None, ge=0)
    required_tokens: frozenset[str] = frozenset()
    allow_empty: bool = False
```

```python
class LoopCursor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    loop_step_id: UUID
    row_index: int = Field(ge=0)

@dataclass(frozen=True, slots=True)
class TableData:
    headers: tuple[str, ...]
    rows: tuple[tuple[DataCell, ...], ...]

    def __post_init__(self) -> None: ...

class OutputCommit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    destination: Path
    format: Literal["csv", "xlsx"]
    sheet_name: str | None
    row_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    headers_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    committed: bool
    producer_step_id: UUID
    producer_cursor: tuple[LoopCursor, ...] = ()

ActionStatus = Literal["success", "skipped", "failed", "cancelled"]
RunStatus = Literal["success", "partial", "failed", "cancelled"]
SkipReason = Literal["if_present_absent", "disabled", "skip_iteration"]

class ActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: UUID
    step_id: UUID
    iteration_path: tuple[int, ...] = ()
    iteration_cursor: tuple[LoopCursor, ...] = ()
    status: ActionStatus
    started_at: datetime
    duration_ms: int = Field(default=0, ge=0)
    attempt_count: int = Field(default=1, ge=1, le=4)
    error_code: ErrorCode | None = None
    safe_message: str = ""
    evidence: FrozenJsonObject = Field(default_factory=FrozenMapping.empty)
    skip_reason: SkipReason | None = None
    output_commit: OutputCommit | None = None

class RunReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    run_id: UUID
    workflow_id: UUID
    workflow_revision: int = Field(ge=0)
    status: RunStatus
    started_at: datetime
    finished_at: datetime
    error_code: ErrorCode | None = None
    safe_message: str = ""
    results: tuple[ActionResult, ...]
    completed_iterations: int = Field(ge=0)
    total_iterations: int | None = Field(default=None, ge=0)
    last_checkpoint_cursor: tuple[LoopCursor, ...] | None = None
    output_commits: tuple[OutputCommit, ...] = ()
```

Define `RpaError(code: ErrorCode, safe_message: str, evidence: FrozenJsonObject | None = None)` so `str(error)` returns only `safe_message` and raw exception text is never retained.

`WaitSpec` rejects `poll_interval_ms > timeout_ms`.
`TableData` rejects blank/duplicate headers, nested cells, and row-width drift in
`__post_init__`. `OutputCommit` requires `sheet_name=None` for CSV and a nonblank
sheet for XLSX; producer step/cursor binds revalidation to the exact save action.
`ActionResult` and `RunReport` use the normative fields above—there is no external
schema assumption. All datetimes are UTC and `finished_at >= started_at`.
Skipped results require a reason; non-skipped results reject one.
Success/skipped cannot carry an error; failed must carry one; cancelled uses
`ErrorCode.CANCELLED`. A successful/partial `RunReport` rejects a run-level error;
a failed/cancelled report always carries a typed run-level `error_code` and
nonblank `safe_message`, even when an action result repeats that cause. This lets
preflight, resume, and cancellation failures use `results=()` without fabricating
an `ActionResult`; cancelled reports use `ErrorCode.CANCELLED`.
`output_commits` contains at most one latest commit per
case-normalized resolved destination; replacing an output replaces its older
commit entry rather than retaining an impossible historical hash. Before model creation, recursively reject case-insensitive
keys `text`, `raw_text`, `clipboard`, `clipboard_text`, `secret`, `password`,
`token`, and `value`. `aggregate_run_status(results: Sequence[ActionResult])`
uses cancelled → failed → explicit `skip_iteration` partial → success; disabled steps and
absent `if_present` groups do not make a run partial.

- [ ] **Step 4: Run the focused and cumulative M1 tests**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass and result JSON contains only safe evidence.

- [ ] **Step 5: Commit conditions and results**

```powershell
git add src/universal_rpa/domain src/universal_rpa/infrastructure tests/unit
git commit -m "feat(universal-rpa): add safe conditions and results"
```

---

### Task 4: Workflow aggregate, recursive loop rules, and JSON Schema

**Files:**

- Create: `src/universal_rpa/domain/action_parameters.py`
- Create: `src/universal_rpa/domain/workflow.py`
- Create: `src/universal_rpa/application/workflow_codec.py`
- Create: `scripts/export_schema.py`
- Create: `docs/schemas/workflow-v1.schema.json`
- Create: `tests/unit/domain/test_action_parameters.py`
- Create: `tests/unit/domain/test_workflow.py`
- Create: `tests/unit/application/test_workflow_codec.py`
- Create: `tests/unit/test_schema_snapshot.py`

**Interfaces:**

- Produces: `MouseButtonParameters`, `DragParameters`, `ScrollParameters`,
  `PressKeyParameters`, `HotkeyParameters`, `validate_builtin_action_parameters`
- Produces: `ActionStep`, `LoopStep`, `IfPresentStep`, `Step`, `Workflow`
- Produces: `InlineDataSource`, `CsvDataSource`, `XlsxDataSource`,
  `DataSourceSpec`, `RunPolicy`, `OutputPolicy`
- Produces: `load_workflow`, `dump_workflow`, `export_workflow_schema`

- [ ] **Step 1: Write failing recursive-workflow and schema tests**

```python
def test_builtin_action_parameters_have_exact_typed_fields() -> None:
    assert validate_builtin_action_parameters(
        "windows.drag",
        {"button": "left", "end_point": {"x": 0.8, "y": 0.4}},
    )["button"] == "left"
    hotkey = validate_builtin_action_parameters(
        "windows.hotkey", {"key": "a", "modifiers": ["ctrl"]}
    )
    assert hotkey["key"] == "a"
    assert hotkey["modifiers"] == ("ctrl",)
    assert validate_builtin_action_parameters(
        "windows.press_key", {"key": "enter"}
    )["key"] == "enter"
    with pytest.raises(ValidationError):
        validate_builtin_action_parameters(
            "windows.scroll", {"horizontal_delta": 0, "vertical_delta": 0}
        )
    with pytest.raises(ValidationError):
        validate_builtin_action_parameters(
            "windows.hotkey", {"key": "f12", "modifiers": ["ctrl", "shift"]}
        )


def test_schema_major_two_is_rejected_without_migration() -> None:
    payload = valid_workflow_payload()
    payload["schema_version"] = "2"
    with pytest.raises(UnsupportedSchemaVersion):
        load_workflow(payload)


def test_third_nested_loop_is_rejected() -> None:
    workflow = workflow_with_loop_depth(3)
    with pytest.raises(ValidationError, match="maximum loop depth is 2"):
        Workflow.model_validate(workflow)


def test_workflow_cannot_self_declare_idempotency() -> None:
    with pytest.raises(ValidationError):
        action_step(idempotent=True)


def test_composite_steps_cannot_retry() -> None:
    with pytest.raises(ValidationError):
        loop_step(failure_policy={"mode": "retry", "retry_count": 1})
    with pytest.raises(ValidationError):
        if_present_step(failure_policy={"mode": "retry", "retry_count": 1})


def test_if_present_accepts_only_positive_target_presence_and_cannot_nest() -> None:
    with pytest.raises(ValidationError):
        PresenceSpec.model_validate(
            {
                "condition_type": "windows.value_equals",
                "target": windows_target_spec(),
                "timeout_ms": 1_000,
            }
        )
    with pytest.raises(ValidationError):
        PresenceSpec.model_validate(
            {
                "condition_type": "windows.element_exists",
                "target": windows_target_spec(),
                "timeout_ms": 1_000,
                "expected": False,
            }
        )
    with pytest.raises(ValidationError):
        PresenceSpec(
            condition_type="web.element_exists",
            target=windows_target_spec(),
            timeout_ms=1_000,
        )
    with pytest.raises(ValidationError):
        if_present_step(steps=(loop_step(steps=(if_present_step(),)),))


def test_data_source_shapes_are_discriminated_and_project_relative() -> None:
    csv = CsvDataSource(
        data_source_id="orders",
        label="주문",
        path="inputs/orders.csv",
        encoding="cp949",
    )
    assert csv.source_type == "csv"
    for unsafe in (r"C:\orders.csv", "../orders.csv", "inputs/../orders.csv"):
        with pytest.raises(ValidationError):
            CsvDataSource(
                data_source_id="orders",
                label="주문",
                path=unsafe,
                encoding="utf-8",
            )
    with pytest.raises(ValidationError):
        InlineDataSource(
            data_source_id="rows",
            label="행",
            headers=("factory",),
            rows=(({"nested": "no"},),),
        )


def test_save_table_requires_a_dominating_extraction_in_same_iteration_frame() -> None:
    with pytest.raises(ValidationError):
        workflow_with_steps(
            action_step(action_type="tabular.save_table", input_step_id=MISSING_STEP_ID),
        )
    with pytest.raises(ValidationError):
        workflow_with_optional_extraction_then_unconditional_save()
    with pytest.raises(ValidationError):
        workflow_with_disabled_extraction_then_save()


def test_extraction_and_coordinate_fallback_require_assertion() -> None:
    with pytest.raises(ValidationError):
        action_step(action_type="clipboard.extract_table", assertions=())
    with pytest.raises(ValidationError):
        action_step(target=coordinate_only_target(), postcondition=None, assertions=())
```

- [ ] **Step 2: Run workflow and schema tests and verify failure**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/domain/test_action_parameters.py tests/unit/domain/test_workflow.py tests/unit/application/test_workflow_codec.py tests/unit/test_schema_snapshot.py -q
```

Expected: FAIL because `Workflow` and codec do not exist.

- [ ] **Step 3: Implement the tagged step union and reference validation**

```python
class NoParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

class MouseButtonParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    button: Literal["left", "right", "middle"] = "left"

class DragParameters(MouseButtonParameters):
    end_point: RelativePoint

class ScrollParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    horizontal_delta: int = Field(ge=-120_000, le=120_000)
    vertical_delta: int = Field(ge=-120_000, le=120_000)

class PressKeyParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str = Field(pattern=r"^[a-z0-9_]+$")

ModifierKey = Literal["ctrl", "alt", "shift", "win"]

class HotkeyParameters(PressKeyParameters):
    modifiers: tuple[ModifierKey, ...]

BUILTIN_ACTION_PARAMETER_MODELS = FrozenMapping.from_mapping(
    {
        "windows.click": MouseButtonParameters,
        "windows.double_click": MouseButtonParameters,
        "windows.drag": DragParameters,
        "windows.scroll": ScrollParameters,
        "windows.press_key": PressKeyParameters,
        "windows.hotkey": HotkeyParameters,
        "windows.activate_window": NoParameters,
        "windows.set_text": NoParameters,
        "windows.wait": NoParameters,
    }
)

def validate_builtin_action_parameters(
    action_type: str,
    parameters: Mapping[str, JsonValue] | FrozenJsonObject,
) -> FrozenJsonObject: ...

class FailurePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: Literal["stop", "retry", "skip_iteration"] = "stop"
    retry_count: int = Field(default=0, ge=0, le=3)
    backoff_ms: int = Field(default=500, ge=0, le=60_000)
```

`ScrollParameters` requires at least one nonzero delta. `PressKeyParameters` uses
an explicit Windows key whitelist (command keys, navigation, function keys); it
does not accept arbitrary key names. `HotkeyParameters` requires one primary key
and at least one modifier, rejects duplicate/out-of-order modifiers against the
canonical `ctrl, alt, shift, win` order, and rejects recorder control chords
`Ctrl+Shift+F11/F12`. The built-in map is exact: click/double-click use
`MouseButtonParameters`, drag uses `DragParameters`, scroll uses
`ScrollParameters`, press-key/hotkey use their named models, and
activate/set-text/wait use `NoParameters`. Validation returns a deep-frozen
canonical object; M2, M3, and M4 all consume this same function.

```python
class ActionStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    step_id: UUID
    label: str = Field(min_length=1)
    kind: Literal["action"] = "action"
    enabled: bool = True
    failure_policy: FailurePolicy = FailurePolicy()
    action_type: str = Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
    input_step_id: UUID | None = None
    target: TargetSpec | None = None
    value: ValueSpec | None = None
    parameters: FrozenJsonObject = Field(default_factory=FrozenMapping.empty)
    precondition: WaitSpec | None = None
    postcondition: WaitSpec | None = None
    wait: WaitSpec | None = None
    assertions: tuple[AssertionSpec | TableAssertionSpec, ...] = ()

class LoopStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    step_id: UUID
    label: str = Field(min_length=1)
    kind: Literal["loop"] = "loop"
    enabled: bool = True
    failure_policy: FailurePolicy = FailurePolicy()
    data_source_id: str
    steps: tuple["Step", ...]

class PresenceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    condition_type: str = Field(pattern=r"^[a-z][a-z0-9_]*\.(element_exists|window_exists)$")
    target: TargetSpec
    timeout_ms: int = Field(gt=0, le=86_400_000)
    poll_interval_ms: int = Field(default=100, gt=0)

class IfPresentStep(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    step_id: UUID
    label: str = Field(min_length=1)
    kind: Literal["if_present"] = "if_present"
    enabled: bool = True
    failure_policy: FailurePolicy = FailurePolicy()
    condition: PresenceSpec
    steps: tuple["Step", ...]

Step = Annotated[ActionStep | LoopStep | IfPresentStep, Field(discriminator="kind")]

class ProjectRelativePath(RootModel[str]):
    model_config = ConfigDict(frozen=True)

class OutputRelativePath(RootModel[str]):
    model_config = ConfigDict(frozen=True)

class InlineDataSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_type: Literal["inline"] = "inline"
    data_source_id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    label: str = Field(min_length=1)
    headers: tuple[str, ...] = Field(min_length=1)
    rows: tuple[tuple[DataCell, ...], ...] = Field(min_length=1)

class CsvDataSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_type: Literal["csv"] = "csv"
    data_source_id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    label: str = Field(min_length=1)
    path: ProjectRelativePath
    encoding: Literal["utf-8", "utf-8-sig", "cp949"]

class XlsxDataSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_type: Literal["xlsx"] = "xlsx"
    data_source_id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    label: str = Field(min_length=1)
    path: ProjectRelativePath
    sheet_name: str = Field(min_length=1)

DataSourceSpec = Annotated[
    InlineDataSource | CsvDataSource | XlsxDataSource,
    Field(discriminator="source_type"),
]

class TargetAppSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    app_id: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    process_executable: str = Field(min_length=1)
    window_class: str = Field(min_length=1)
    window_title: str | None = None

class EnvironmentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    require_interactive_desktop: Literal[True] = True
    require_foreground_before_input: Literal[True] = True
    coordinate_client_size_tolerance_percent: Literal[2] = 2

class RunPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_iterations: int = Field(default=1_000, ge=1, le=10_000)
    max_runtime_seconds: int = Field(default=7_200, ge=1, le=86_400)

class OutputPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact_retention_days: int = Field(default=30, ge=1, le=365)
    failure_screenshots: bool = True

class Workflow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["1"] = "1"
    workflow_id: UUID
    name: str = Field(min_length=1)
    revision: int = Field(ge=0)
    target_apps: tuple[TargetAppSpec, ...] = Field(min_length=1)
    environment_policy: EnvironmentPolicy = EnvironmentPolicy()
    variables: tuple[VariableDefinition, ...] = ()
    data_sources: tuple[DataSourceSpec, ...] = ()
    steps: tuple[Step, ...] = Field(min_length=1)
    run_policy: RunPolicy = RunPolicy()
    output_policy: OutputPolicy = OutputPolicy()
    created_at: datetime
    updated_at: datetime
```

Inline headers are trimmed, non-blank, unique, and every immutable row has
exactly the same width with scalar `DataCell` values; nested list/dict cells and
post-validation mutation are impossible. `ProjectRelativePath` is a normalized POSIX path under `inputs/`.
`OutputRelativePath` uses the same lexical restrictions but is resolved only
under `RunInputs.output_directory`; both reject absolute paths, backslashes,
empty/`.`/`..` segments, drive/device syntax, and symlink/junction escape after
resolution.
A selected external CSV/XLSX is copied atomically into the project `inputs/`
directory before its workflow spec is created. CSV encoding is never inferred.
XLSX opens only the named sheet. `VariableDefinition` column sources must resolve
to the same kind and an existing `data_source_id`.

`PresenceSpec` is deliberately not a wrapper around general `WaitSpec`. Its
namespace must equal `target.adapter_id`, its local name is only
`element_exists` or `window_exists`, and it has no expected/negation/value field.
Normal zero-match through timeout means optional absence; ambiguity, adapter
error, environment mismatch, or cancellation remains failure. An
`IfPresentStep` may contain actions and loops but may not contain another
`IfPresentStep` at any depth, including through a nested loop.

`RunPolicy` defaults to 1,000 iterations and 7,200 seconds, rejects values above
10,000 and 86,400. `OutputPolicy` defaults artifact retention to 30 days.
`Workflow` uses exactly the normative fields above, requires UTC timestamps with
`updated_at >= created_at`, and has validators that:

1. enforce unique step, variable, and data-source IDs;
2. resolve every variable/data-source reference;
3. enforce loop depth two;
4. allow `skip_iteration` only inside a loop;
5. require assertion/postcondition for coordinate fallback and extraction;
6. reject retry on `LoopStep` and `IfPresentStep`; adapter-owned idempotency and
   retryable-error validation is performed by M3 `ValidationService`;
7. require `ActionStep.wait` for `windows.wait` and reject a wait payload on any other action;
8. allow `input_step_id` only on `tabular.save_table`; its enabled
   `clipboard.extract_table` producer must be earlier, structurally dominate the
   consumer on every path, and be in the same iteration frame. A reference may
   not escape an `IfPresentStep`, disabled branch, sibling loop, or outer frame;
9. enforce the `PresenceSpec` restriction and reject nested `IfPresentStep`;
10. resolve every variable source against its exact data-source kind and reject
    missing columns during M3 preview validation;
11. deep-freeze all parameter/payload trees before the validated Workflow becomes
    observable. `retry_count` means additional retries, so total attempts are at
    most `1 + retry_count` (four).

`load_workflow` parses JSON or a mapping, inspects `schema_version` first, and
raises `UnsupportedSchemaVersion` for anything except string `"1"`.
`dump_workflow` emits stable UTF-8 JSON with sorted keys and a trailing newline.
`export_schema.py --check` compares the generated schema bytes with
`docs/schemas/workflow-v1.schema.json`.

- [ ] **Step 4: Generate schema and run the full M1 quality gate**

```powershell
.\.venv\Scripts\python.exe scripts/export_schema.py
.\.venv\Scripts\python.exe -m pytest tests/unit -q
.\.venv\Scripts\python.exe scripts/export_schema.py --check
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all commands exit 0; a byte change to any schema model makes
`--check` fail.

- [ ] **Step 5: Commit workflow schema v1**

```powershell
git add src/universal_rpa/domain/action_parameters.py src/universal_rpa/domain/workflow.py src/universal_rpa/application/workflow_codec.py scripts/export_schema.py docs/schemas/workflow-v1.schema.json tests/unit
git commit -m "feat(universal-rpa): define workflow schema v1"
```

---

### Task 5: Adapter protocol, registry, repository ports, and fake contract

**Files:**

- Create: `src/universal_rpa/ports/__init__.py`
- Create: `src/universal_rpa/ports/automation.py`
- Create: `src/universal_rpa/ports/repositories.py`
- Create: `src/universal_rpa/ports/credentials.py`
- Create: `src/universal_rpa/ports/data_sources.py`
- Create: `src/universal_rpa/ports/artifacts.py`
- Create: `src/universal_rpa/adapters/__init__.py`
- Create: `src/universal_rpa/adapters/registry.py`
- Create: `src/universal_rpa/adapters/fake.py`
- Create: `tests/contract/automation_adapter_contract.py`
- Create: `tests/contract/test_fake_adapter.py`
- Create: `tests/unit/adapters/test_registry.py`
- Create: `docs/architecture/adapter-development.md`

**Interfaces:**

- Produces: all cross-milestone protocols locked in the master plan, plus `TargetCapturePort`, `TargetCaptureRequest`, `TargetCaptureResult`, `ActionRequest`, `ConditionObservation`, `DataPreview`, `SecretValue`, and `ExecutionContext`
- Produces: `AdapterRegistry.register`, `require`, `load_entry_points`
- Produces: `FakeAutomationAdapter.script`, `calls`, `reset`
- Consumes: M1 Tasks 2–4 domain models

- [ ] **Step 1: Write the shared adapter contract and registry failure tests**

```python
class AutomationAdapterContract:
    def make_adapter(self) -> AutomationAdapter:
        raise NotImplementedError
    def make_supported_request(self, adapter: AutomationAdapter) -> ActionRequest:
        raise NotImplementedError
    def side_effect_count(self, adapter: AutomationAdapter) -> int:
        raise NotImplementedError

    def test_cancelled_request_has_no_side_effect(self) -> None:
        adapter = self.make_adapter()
        token = CancellationToken()
        token.cancel()
        result = adapter.execute(
            self.make_supported_request(adapter), execution_context(), token
        )
        assert result.error_code is ErrorCode.CANCELLED
        assert self.side_effect_count(adapter) == 0

    def test_capture_target_honors_descriptor_capability(self) -> None:
        adapter = self.make_adapter()
        captured = adapter.capture_target(target_capture_request(), CancellationToken())
        if not adapter.descriptor().supports_target_capture:
            assert captured.target is None
            assert captured.candidates == ()
            assert captured.preview_png is None
            assert [issue.code for issue in captured.issues] == [ErrorCode.ACTION_UNSUPPORTED]
            return
        assert captured.target is not None
        assert captured.target.adapter_id == adapter.adapter_id
        assert captured.target in captured.candidates
        assert captured.preview_png is None or captured.preview_png.startswith(b"\x89PNG")

    def test_capture_result_cannot_select_a_target_outside_candidates(self) -> None:
        with pytest.raises(ValueError):
            TargetCaptureResult(
                target=fake_target(adapter_id=self.make_adapter().adapter_id),
                candidates=(),
                preview_png=None,
            )

class TargetingAutomationAdapterContract(AutomationAdapterContract):
    def configure_ambiguous_target(self, adapter: AutomationAdapter) -> None:
        raise NotImplementedError

    def test_ambiguous_target_maps_to_common_error_without_side_effect(self) -> None:
        adapter = self.make_adapter()
        self.configure_ambiguous_target(adapter)
        result = adapter.execute(
            self.make_supported_request(adapter), execution_context(), CancellationToken()
        )
        assert result.error_code is ErrorCode.TARGET_AMBIGUOUS
        assert self.side_effect_count(adapter) == 0

# test_fake_adapter.py implements both hooks with FakeAutomationAdapter.script/calls.
# Windows implements both contracts; clipboard/tabular implement only the base contract.

def test_registry_rejects_retry_metadata_outside_declared_actions() -> None:
    adapter = FakeAutomationAdapter(
        descriptor=descriptor(idempotent_actions=frozenset({"fake.unknown"}))
    )
    with pytest.raises(ValueError, match="idempotent action must be declared"):
        AdapterRegistry().register(adapter)


def test_registry_rejects_duplicate_adapter_id() -> None:
    registry = AdapterRegistry()
    registry.register(FakeAutomationAdapter(adapter_id="fake"))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(FakeAutomationAdapter(adapter_id="fake"))


def test_descriptor_source_mutation_cannot_change_registered_capability_or_fingerprint() -> None:
    source = {"fake.read": frozenset({ErrorCode.ACTION_FAILED})}
    descriptor = descriptor(retryable_errors_by_action=source)
    registry = AdapterRegistry()
    registry.register(FakeAutomationAdapter(descriptor=descriptor))
    before = registry.descriptor_fingerprint()
    source["fake.read"] = frozenset({ErrorCode.TARGET_NOT_FOUND})
    registered = registry.require("fake").descriptor()
    assert registered.retryable_errors_by_action["fake.read"] == frozenset(
        {ErrorCode.ACTION_FAILED}
    )
    assert registry.descriptor_fingerprint() == before
```

- [ ] **Step 2: Run contract tests and confirm missing interfaces**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contract/test_fake_adapter.py tests/unit/adapters/test_registry.py -q
```

Expected: FAIL because ports, registry, and fake adapter are absent.

- [ ] **Step 3: Implement synchronous ports and deterministic discovery**

Use the exact `AutomationAdapter` signature in the master plan. Add:

```python
class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise RpaError(ErrorCode.CANCELLED, "실행이 취소되었습니다")

type PreparedValue = str | int | Decimal | date | Path
type ResolvedValue = FrozenJsonValue | PreparedValue | TableData | OutputCommit | SecretValue

class SecretValue:
    @classmethod
    def from_text(cls, value: str) -> "SecretValue": ...
    @contextmanager
    def reveal(self) -> Iterator[str]: ...
    def __repr__(self) -> str:
        return "SecretValue(<redacted>)"
    def __str__(self) -> str:
        raise TypeError("SecretValue cannot be converted to str")

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
class ActionRequest:
    action_type: str
    target: TargetSpec | None
    parameters: FrozenJsonObject
    value: ResolvedValue | None
    has_postcondition_or_assertion: bool

@dataclass(frozen=True, slots=True)
class ConditionObservation:
    satisfied: bool
    observed: ResolvedValue | None
    evidence: FrozenJsonObject

@dataclass(frozen=True, slots=True)
class AssertionObservation:
    passed: bool
    evidence: FrozenJsonObject

@dataclass(frozen=True, slots=True)
class DataPreview:
    headers: tuple[str, ...]
    rows: tuple[FrozenMapping[str, DataCell], ...]
    total_row_count: int | None

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
VerificationMode = Literal["postcondition_or_assertion", "intrinsic", "none"]

@dataclass(frozen=True, slots=True)
class AdapterDescriptor:
    adapter_id: str
    implementation_version: str
    supports_target_capture: bool
    actions: frozenset[str]
    conditions: frozenset[str]
    assertions: frozenset[str]
    verification_by_action: FrozenMapping[str, VerificationMode]
    idempotent_actions: frozenset[str]
    retryable_errors_by_action: FrozenMapping[str, frozenset[ErrorCode]]
    assertions_by_action: FrozenMapping[str, frozenset[str]]
    assertion_input_kind: FrozenMapping[
        str,
        Literal["json", "table", "output_commit"],
    ]

    def __post_init__(self) -> None: ...

@dataclass(frozen=True, slots=True)
class AdapterActionResult:
    output: FrozenJsonValue | TableData | None
    evidence: FrozenJsonObject
    error_code: ErrorCode | None = None
    safe_message: str = ""
    output_commit: OutputCommit | None = None

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

class AdapterRegistry:
    def register(self, adapter: AutomationAdapter) -> None: ...
    def require(self, adapter_id: str) -> AutomationAdapter: ...
    def load_entry_points(
        self,
        group: str = "universal_rpa.adapters",
    ) -> tuple[str, ...]: ...
```

`AutomationAdapter.validate_action_spec`, `validate_condition_spec`, and
`validate_assertion_spec` are pure, deterministic, side-effect-free schema checks
used by M3 before execution. `AutomationAdapter.validate_target(target, runtime,
mode)` always validates payload and environment. `must_exist_now` additionally requires exactly one
match; `may_be_absent_now` accepts a normal zero-match but not ambiguity or
adapter/environment error; `deferred` performs no live existence lookup.
`AutomationAdapter.capture_target(request, cancellation)` returns a typed target
plus an optional PNG kept in memory; it never writes a preview.
`TargetCaptureResult.__post_init__` requires a non-`None` selected `target` to be
one of `candidates`; there are no top-level region fields. For Windows,
every candidate stores its own `target_region` and mandatory/user sensitive
regions inside `TargetSpec.payload`; there is no duplicate top-level region state.
When present, PNG pixels are exactly the selected top-level client at
`request.runtime.client_width × client_height`, with no desktop/non-client area.
M3 verifies decoded dimensions before masking/persistence. When `supports_target_capture=False`, it performs no native work
and returns no target, candidates, or preview plus exactly one safe
`ACTION_UNSUPPORTED` issue. `validate_target`, `capture_target`, `execute`, condition polling, and assertion
evaluation all check cancellation before native/UIA work.

`register` accepts IDs matching `^[a-z][a-z0-9_]*$`, rejects duplicates, and
requires every descriptor action/condition/assertion to start with the same ID. Namespaced assertion evaluation is dispatched to the assertion namespace owner, never hard-coded in the runner.
Every declared action has exactly one verification mode. Metadata keys,
`idempotent_actions`, retryable-error keys, and `assertions_by_action` keys must
be subsets of `actions`; compatible assertion names must be declared by the same
adapter and each declared assertion has one `assertion_input_kind` plus a working
`evaluate_assertion` route. Retryable errors are honored only for actions in
`idempotent_actions`. `implementation_version` is non-blank and joins the
canonical descriptor fingerprint used by resume. `AdapterDescriptor.__post_init__`
defensively copies and converts all four mapping fields to `FrozenMapping`
(including each nested retry/assertion set) before registration. Every
`DataSourcePort.preview` row and `iter_rows` yield is likewise a newly built
`FrozenMapping`; raw parser/build inputs may accept `Mapping`, but no observable
port result retains caller-owned mutable state. Registry copies every mapping
and nested set to immutable, canonically sorted values so caller mutation cannot
change capability or fingerprint after registration. Workflow JSON cannot
promote idempotency, retryable errors, or assertion compatibility.
`load_entry_points` sorts by entry-point name before loading, instantiates each
trusted factory once, and returns registered IDs. It does not install packages.

`FakeAutomationAdapter` is driven by an in-memory FIFO script and records calls
only after cancellation and capability checks. It can return 0/1/2 target
matches, condition observations, safe errors, and output commits. Raw exceptions
are mapped to `ErrorCode.INTERNAL_ERROR` without their message.

Document an external adapter package's entry point:

```toml
[project.entry-points."universal_rpa.adapters"]
web = "company_rpa_web:create_adapter"
```

The document must explicitly state that B/C implementations are not included,
adapters are administrator-installed trusted code, and workflows cannot supply
Python or shell code. It must reserve IDs `web`, `http`, `mail`, and `fileops`, show
each adapter owning validation of its `TargetSpec.payload`, and include a registry
contract test that registers deterministic fake adapters under all four IDs without
importing Playwright, an HTTP client, a mail client, or filesystem implementation.

- [ ] **Step 4: Run adapter contract, schema, and isolation regression**

```powershell
.\.venv\Scripts\python.exe -m pytest tests/contract tests/unit -q
.\.venv\Scripts\python.exe scripts/export_schema.py --check
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m ruff format --check src tests scripts
.\.venv\Scripts\python.exe -m mypy src
```

Expected: all pass; the shared suite is capability-driven and contains no
fake-only `.script`/`.calls` assumption. Fake and Windows targeting suites show
side-effect count zero for cancellation, invalid parameters, unknown action, and
ambiguous target; clipboard/tabular pass the non-targeting base suite.

- [ ] **Step 5: Commit and record the M1 review gate**

```powershell
git add src/universal_rpa/ports src/universal_rpa/adapters tests/contract tests/unit/adapters docs/architecture/adapter-development.md
git commit -m "feat(universal-rpa): add adapter extension contract"
git status --short
```

Expected: only intentional later-plan files, if any, are untracked; no M1 code is
left unstaged.

## M1 Completion Gate

Run from `universal_rpa/`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit tests/contract -q
.\.venv\Scripts\python.exe scripts/export_schema.py --check
.\.venv\Scripts\python.exe -m ruff check src tests scripts
.\.venv\Scripts\python.exe -m ruff format --check src tests scripts
.\.venv\Scripts\python.exe -m mypy src
```

Do not start M2 unless every command exits 0 and a reviewer confirms that
`src/universal_rpa` imports no parent-repository module.
