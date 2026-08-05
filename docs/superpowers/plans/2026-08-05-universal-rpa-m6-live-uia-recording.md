# Universal RPA M6: Live UIA Element Resolution for Recording

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 출하 배선에서 기록기가 사용 가능한 단계 후보를 생성하도록 실제 UIA 요소 해석기를 구현한다. 현재는 모든 키 입력이 무조건 마스킹되어 기록 결과가 항상 비어 있다.

## 배경: 왜 지금 기록기가 동작하지 않는가

M5 대화형 E2E를 처음 실행하면서 발견됐다. 증거 체인:

| 관측 | 값 |
|---|---|
| 기록된 키 이벤트 | 6건 (`ctrl` down, `a` down, `a` up, `ctrl` up, `enter` down, `enter` up) |
| `in_scope` / `capture_state` | 모두 `True` / `recording` |
| 저장된 payload | 전부 `{"redacted": true}` |
| 정규화 후보 | **0개** |

원인은 **두 곳**이며, 둘 다 고쳐야 한다. 하나만 고치면 증상이 그대로다.

**(A) UIA 스텁** — `bootstrap.py`의 `_CoordinateOnlyUia`는 `element_from_runtime_id`가 항상 `None`, `elements_from_point`가 항상 `()`, `password_elements`가 항상 `()`를 반환한다.

**(B) runtime id 미발행** — `bootstrap.py`의 `_FocusPollingCapture._publish_focus`가 `cached_uia_runtime_id=None`으로 스냅샷을 발행한다. `WindowsWindowContext.capture_context`는 `runtime_id is not None`일 때만 `element_from_runtime_id`를 호출하므로, (A)를 고쳐도 호출 자체가 일어나지 않는다.

두 조건이 모두 막혀 `target_snapshot`이 항상 `None`이 되고, `domain/recording.py`의 `record_raw_event`에서 `safe_to_reveal`이 요구하는 `target is not None`이 절대 성립하지 않아 모든 키가 마스킹된다.

이 마스킹 자체는 **올바른 fail-closed 설계다.** 타깃을 확인할 수 없으면 사용자가 무엇에 타이핑했는지 알 수 없고, 그때 키 내용을 저장하는 것이 오히려 위험하다. 고쳐야 할 것은 마스킹 규칙이 아니라 타깃을 실제로 해석하지 못하는 배선이다.

**Architecture:** `UiaFacade` 프로토콜(`adapters/windows/context.py`)의 실제 구현을 추가하고, 포커스 폴러가 UIA runtime id를 발행하도록 한다. `capture_target_snapshot`은 덕 타이핑으로 요소 속성을 읽으므로, pywinauto/COM 요소를 그 형태로 번역하는 얇은 래퍼가 필요하다.

**Tech Stack:** M1–M5, pywinauto `UIAElementInfo`, `comtypes` UIA COM 인터페이스, pywin32.

## Global Constraints

- 마스킹 규칙(`record_raw_event`의 `safe_to_reveal`)은 **완화하지 않는다.** 타깃이 확인되지 않으면 여전히 전부 마스킹한다.
- 비밀번호 필드(`is_password=True`)의 키는 어떤 경로로도 저장하지 않는다.
- UIA 호출은 입력 훅 콜백 안에서 수행하지 않는다. 훅을 막으면 사용자 입력 전체가 지연된다.
- UIA 조회 실패는 예외를 밖으로 던지지 않고 `None`을 반환해 기존 fail-closed 경로로 떨어진다.
- 요소 해석에 상한 시간을 두고, 초과 시 마스킹된 이벤트로 진행한다.
- 실제 창 없이 검증 가능한 단위 테스트를 먼저 만든다. E2E는 확인용이지 설계 근거가 아니다.

---

### Task 1: pywinauto/COM 요소를 스냅샷 형태로 번역하는 어댑터

**Files:**

- Create: `src/universal_rpa/adapters/windows/uia_elements.py`
- Create: `tests/unit/adapters/windows/test_uia_elements.py`

**Interfaces:**

- Consumes: pywinauto `UIAElementInfo`
- Produces: `UiaElementView` — `capture_target_snapshot`이 읽는 속성을 노출하는 래퍼

`capture_target_snapshot`이 실제로 읽는 것(중복 이름은 대체 경로):

| 속성 | 대체 이름 | 용도 |
|---|---|---|
| `automation_id` | — | 셀렉터 |
| `control_type` | — | 셀렉터, `editable` 기본값 |
| `name` | — | 셀렉터 |
| `class_name` | — | 셀렉터 |
| `runtime_id` | `get_runtime_id` | 이벤트 동일성 확인 |
| `is_password` | `get_is_password` | 마스킹 결정 |
| `editable` | `is_editable` | `observed_value` 노출 여부 |
| `get_value` | `value_pattern.get_value`, `value` | 관측값 |
| `bounds` | `bounding_rectangle` | 정규화 사각형 |

pywinauto의 `UIAElementInfo`는 `rectangle`을 노출하고 `bounds`/`bounding_rectangle`은 노출하지 않으므로 번역이 필요하다. `is_password`는 COM 요소의 `CurrentIsPassword`에서 읽는다.

- [ ] **Step 1: 각 속성 매핑과 누락 시 안전한 기본값에 대한 실패 테스트 작성**

`is_password`를 읽을 수 없을 때 `True`로 간주해 fail-closed 되는지 반드시 고정한다.

- [ ] **Step 2: 테스트 실패 확인** (`ModuleNotFoundError`)
- [ ] **Step 3: `UiaElementView` 구현**
- [ ] **Step 4:** `.\.venv\Scripts\python.exe -m pytest tests/unit/adapters/windows/test_uia_elements.py -q`
- [ ] **Step 5: 커밋**

---

### Task 2: 실제 `UiaFacade` 구현

**Files:**

- Create: `src/universal_rpa/adapters/windows/uia_facade.py`
- Create: `tests/unit/adapters/windows/test_uia_facade.py`

**Interfaces:**

- Produces: `PywinautoUiaFacade` — `element_from_runtime_id`, `elements_from_point`, `password_elements`

세 메서드 모두 실패 시 예외 대신 빈 결과를 반환한다. `element_from_runtime_id`는 runtime id로 요소를 찾고, 반환 요소의 runtime id가 요청과 다르면 `None`을 반환한다(`capture_context`가 이미 이 검증을 하지만, 어댑터에서도 확인해 재사용된 id를 걸러낸다).

- [ ] **Step 1: 실패·불일치·타임아웃 경로 테스트 작성**

```python
def test_a_reused_runtime_id_resolves_to_nothing() -> None: ...
def test_a_uia_error_yields_no_element_rather_than_raising() -> None: ...
def test_password_elements_are_reported_for_masking() -> None: ...
def test_resolution_gives_up_within_its_budget() -> None: ...
```

- [ ] **Step 2: 테스트 실패 확인**
- [ ] **Step 3: 구현.** COM 초기화는 스레드별 1회, 조회 예산 초과 시 `None`.
- [ ] **Step 4:** 단위 테스트 + `mypy src`
- [ ] **Step 5: 커밋**

---

### Task 3: 포커스 폴러가 UIA runtime id를 발행하도록 수정

**Files:**

- Modify: `src/universal_rpa/bootstrap.py`
- Create: `tests/unit/test_bootstrap_focus_publishing.py`

**이것이 (B) 결함의 수정이며, Task 2 없이 단독으로는 효과가 없다.**

`_FocusPollingCapture._publish_focus`가 현재 `cached_uia_runtime_id=None`을 발행한다. 포커스된 요소의 runtime id를 조회해 실으면 `capture_context`가 `element_from_runtime_id`를 호출하게 된다.

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_focus_snapshot_carries_the_focused_element_runtime_id() -> None: ...
def test_a_uia_failure_publishes_a_snapshot_without_a_runtime_id() -> None: ...
def test_focus_polling_never_blocks_on_uia_longer_than_its_budget() -> None: ...
```

- [ ] **Step 2: 테스트 실패 확인**
- [ ] **Step 3: 구현.** 조회는 폴링 스레드에서만; 입력 훅 콜백 안에서는 하지 않는다.
- [ ] **Step 4:** `pytest tests/unit -q`
- [ ] **Step 5: 커밋**

---

### Task 4: 출하 배선 교체와 회귀 방지

**Files:**

- Modify: `src/universal_rpa/bootstrap.py` (`_CoordinateOnlyUia` 제거)
- Modify: `tests/unit/test_bootstrap_registry.py`
- Create: `tests/unit/test_bootstrap_recording_wiring.py`

**이것이 (A) 결함의 수정이다.**

- [ ] **Step 1: 실패 테스트 작성**

```python
def test_production_bootstrap_wires_a_real_uia_facade() -> None:
    """스텁이 다시 들어오면 기록기가 조용히 빈 결과를 내므로 배선을 고정한다."""
    services = build_services(...)
    assert not isinstance(services.window_context._uia, _CoordinateOnlyUia)


def test_a_keyboard_event_with_a_resolved_target_is_not_redacted() -> None:
    """(A)와 (B)를 함께 덮는다: 둘 중 하나만 고치면 이 테스트는 실패한다."""
```

두 번째 테스트가 이 계획의 **핵심 회귀 방지선**이다. 가짜 UIA 파사드와 가짜 포커스 소스로 실제 창 없이 검증한다.

- [ ] **Step 2: 테스트 실패 확인**
- [ ] **Step 3: `_CoordinateOnlyUia` 제거하고 `PywinautoUiaFacade` 배선**
- [ ] **Step 4:** 전체 비대화형 스위트 + ruff + mypy
- [ ] **Step 5: 커밋**

---

### Task 5: 대화형 확인

**Files:**

- Modify: `tests/integration/windows/test_recorder_editor_roundtrip.py`

- [ ] **Step 1:** 전용 세션에서 실행

```powershell
$env:RPA_INTERACTIVE_DESKTOP = "1"
.\.venv\Scripts\python.exe -m pytest tests/integration/windows/test_recorder_editor_roundtrip.py -m windows_e2e -q
```

기대: `test_complete_keyboard_roundtrip`의 `normalized_actions == ["windows.hotkey", "windows.set_text", "windows.press_key"]`.

> ⚠️ **잠금 해제된 전용 데스크톱이 필요하다.** 사용 중인 데스크톱에서는 harness가 포그라운드를 잡지 못해 결과가 무의미하다. M5에서 동일 코드가 7~11개 통과로 흔들린 것이 이 때문이다.

- [ ] **Step 2:** 비밀번호 필드 입력이 여전히 마스킹되는지 확인 — 이 계획이 **완화하면 안 되는** 불변식이다.

---

## 완료 게이트

- 실제 창 없이 도는 단위 테스트가 (A)와 (B) 각각을 독립적으로 고정한다.
- 타깃이 해석된 키 이벤트만 키 식별자를 남기고, 그 외에는 전부 마스킹된다.
- 비밀번호 필드 입력은 어떤 경로로도 저장되지 않는다.
- 전용 세션에서 recorder → editor → preflight → runner → report 왕복이 통과한다.
- `_CoordinateOnlyUia`가 저장소에 남아 있지 않다.

## 참고: 함께 발견됐으나 이 계획의 범위가 아닌 것

- **`windows.drag`/`windows.scroll`이 UIA 타깃에서 동작하지 않음** — `input_driver.py`가 `ResolvedCoordinateTarget`에서만 좌표를 얻는다. 별도로 처리한다.
- **더블클릭이 관측되지 않음** — 실행은 성공하나 `double_click_count`가 증가하지 않는다. pywinauto의 클릭 간 지연이 시스템 더블클릭 간격을 초과하는 것으로 의심되나 미확증.
