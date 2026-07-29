# Universal RPA Studio 설계 명세

**상태:** 사용자 승인
**작성일:** 2026-07-27
**대상 프로젝트:** `universal_rpa/`
**제품명:** Universal RPA Studio

## 1. 결론

현재 MIS RPA의 클릭 기록, 창 상대좌표, 키 입력, 클립보드 감지 경험을 일반화해
비개발 업무 담당자가 사용할 수 있는 Windows RPA 제품을 만드는 것은 조건부로
실현 가능하다.

제품이 약속할 범위는 “어떤 앱이든 한 번 시연하면 자동으로 이해하는 RPA”가
아니다. 목표는 다음과 같다.

> 사용자가 녹화한 Windows 업무를 화면에서 라벨링하고 변수·반복·검증 규칙을
> 지정하면, 버전된 JSON 워크플로를 런타임 LLM 없이 결정론적으로 실행하는
> 로컬 RPA Studio.

범용성은 LLM이 아니라 어댑터 경계, UI 요소 식별, 상태 검증, 안전한 fallback,
실패 복구에서 확보한다.

## 2. 목표와 범위

### 2.1 MVP 목표: A 범위

- Windows 10/11 x64의 대화형 사용자 세션에서 동작한다.
- MIS/ERP 등 Windows 데스크톱 앱을 녹화하고 재생한다.
- 마우스 클릭·더블클릭·드래그·휠과 키보드 문자·명령키·단축키를 기록한다.
- 녹화된 입력을 비개발자가 단계 목록에서 라벨링하고 수정한다.
- 날짜·문자·숫자·경로를 실행 변수로 만들 수 있다.
- 인라인 목록, CSV, Excel 행을 최대 2단계까지 중첩 반복할 수 있다.
- 클립보드 표를 검증하고 CSV 또는 XLSX로 저장한다.
- UIA selector를 우선 사용하고, 검증된 환경에서만 창 상대좌표로 fallback한다.
- 조건부 대기, 제한된 재시도, checkpoint, 중단 후 재개를 지원한다.
- 한 개의 PySide6 Windows 데스크톱 앱에서 녹화부터 결과 확인까지 수행한다.
- 사용자는 JSON을 직접 편집하거나 LLM에 추가 질문할 필요가 없다.

### 2.2 확장 경계: B·C 범위

MVP에서는 다음 어댑터의 실제 동작을 구현하지 않는다. 대신 같은 워크플로 엔진에
연결할 수 있도록 어댑터 등록 계약과 action namespace 규칙을 정의한다.

- B: Playwright 기반 웹 브라우저 어댑터
- C: 파일 작업, HTTP API, 메일 어댑터

### 2.3 명시적 비범위

- Windows 잠금, 로그오프, RDP 세션 종료 상태의 GUI 실행
- UAC secure desktop 조작
- MFA 또는 CAPTCHA 우회
- 이미지 인식 또는 OCR 기반 판단
- 임의 Python, PowerShell, 배치 명령을 워크플로에서 실행
- 복잡한 임의 수식 언어, 범용 분기 그래프, 병렬 GUI 조작
- 승인, 송금, 삭제, 권한 변경 등 되돌리기 어려운 업무
- macOS 또는 Linux 지원
- 기존 MIS RPA 코드의 수정 또는 기존 모듈 import

## 3. 기존 프로젝트와의 격리

모든 제품 파일은 현재 저장소의 `universal_rpa/` 아래에 둔다.

```text
universal_rpa/
├── pyproject.toml
├── README.md
├── .gitignore
├── .github/
│   └── workflows/
├── src/
│   └── universal_rpa/
├── tests/
├── samples/
│   └── test_harness/
├── docs/
│   ├── architecture/
│   ├── schemas/
│   └── superpowers/
└── scripts/
```

격리 규칙은 다음과 같다.

- `universal_rpa/` 밖의 Python 모듈, 설정, 좌표 JSON, 데이터 파일을 import하거나
  상대경로로 참조하지 않는다.
- 부모 저장소와 가상환경, 환경변수 파일, 테스트 fixture를 공유하지 않는다.
- 현재 저장소 안에 중첩 `.git` 디렉터리를 만들지 않는다.
- 배포·테스트·문서·CI 자산을 모두 하위 프로젝트 안에 둔다.
- 실제 MIS 연동은 제품의 일반 Windows 어댑터를 통해서만 수행한다.

사용자가 만든 workflow와 실행 data는 source repository 안에 저장하지 않는다.
Studio에서 선택한 사용자 project directory에 `workflow.json`, target preview,
선택적 CSV/XLSX 입력만 저장한다. raw recording, secret, 실행 artifact는 각자의
보안·보존 정책에 따라 별도 위치에 둔다.

추후 별도 GitHub 저장소로 이력을 분리할 때는 다음 방식을 문서화한다.

```powershell
git filter-repo `
  --path universal_rpa/ `
  --path-rename universal_rpa/: `
  --force
```

분리 후 `pyproject.toml`, `.github/`, `src/`, `tests/`, `docs/`가 새 저장소
루트에 위치하며 명령과 경로를 수정하지 않아도 동작해야 한다.

## 4. 플랫폼과 기술 제약

- 지원 OS: Windows 10 x64, Windows 11 x64
- Python: `>=3.12,<3.14`
- UI: PySide6 Qt Widgets
- UI 자동화: pywinauto UIA backend, pywin32
- 입력 녹화: pynput listener를 `InputCapturePort` 뒤에서 사용
- 스키마와 검증: Pydantic 2, JSON Schema
- Excel: openpyxl
- CSV: Python 표준 `csv`
- 비밀 저장소: pywin32의 Windows Credential Manager API
- 테스트: pytest, pytest-qt
- 품질 도구: Ruff, mypy
- Windows 배포: `pyside6-deploy` 기반 실행 파일
- UI 언어: 한국어
- 워크플로 schema major version: `1`
- 런타임 LLM 및 네트워크 AI 호출: 사용하지 않음

입력 listener callback은 Windows 입력 thread를 차단하지 않도록 원본 이벤트를
메모리 queue에 넣는 일만 수행한다. 정규화와 디스크 쓰기는 별도 worker에서 한다.
프로세스 시작 시 per-monitor DPI awareness를 선언해 녹화와 재생 좌표계를 통일한다.

## 5. 아키텍처

```text
PySide6 Studio UI
  ├─ Project Home
  ├─ Recorder
  ├─ Workflow Editor
  ├─ Validator
  └─ Runner / Report
          │
Application Services
  ├─ RecordingService
  ├─ NormalizationService
  ├─ WorkflowEditingService
  ├─ ValidationService
  └─ ExecutionService
          │
Domain Core
  ├─ Workflow / Step / Target
  ├─ Variable / DataSource / Loop
  ├─ WaitCondition / Assertion
  └─ ActionResult / RunReport
          │
Ports and Adapter Registry
  ├─ Windows adapter            [MVP 구현]
  ├─ Clipboard adapter          [MVP 구현]
  ├─ CSV/XLSX adapter           [MVP 구현]
  ├─ Credential adapter         [MVP 구현]
  ├─ Web adapter                [계약만 정의]
  ├─ API adapter                [계약만 정의]
  └─ Mail/FileOps adapter       [계약만 정의]
```

### 5.1 핵심 컴포넌트

| 컴포넌트 | 책임 | 의존 대상 |
|---|---|---|
| Studio UI | 비개발자용 화면과 사용자 상호작용 | Application Services |
| RecordingService | 대상 창 한정 입력 수집과 원본 session 관리 | InputCapturePort, WindowContextPort |
| NormalizationService | 원본 입력을 의미 있는 동작으로 묶음 | Domain Core |
| WorkflowEditingService | 라벨, 변수, 반복, 대기, 검증 수정 | WorkflowRepositoryPort |
| ValidationService | 스키마, 환경, target, 입력, 출력 사전 검증 | Adapter Registry |
| ExecutionService | 단계 실행, checkpoint, 재시도, 결과 집계 | AutomationAdapter |
| TargetResolver | UIA selector와 좌표 fallback 결정 | Windows adapter |
| RunArtifactStore | 로그, 보고서, 마스킹 화면, 보존기간 관리 | 로컬 filesystem |

### 5.2 어댑터 계약

`AutomationAdapter`는 다음 정보를 제공한다.

- 안정적인 `adapter_id`
- 지원하는 action·condition·assertion 집합
- target 캡처와 target 유효성 검사
- action 실행
- condition polling
- extraction 결과 반환
- 오류를 공통 `ErrorCode`로 변환

외부 확장 어댑터는
`universal_rpa.adapters` Python entry-point group으로 등록한다. 어댑터는 관리자가
설치하는 신뢰된 코드이며, 일반 사용자가 워크플로 안에서 코드를 추가하는 기능은
제공하지 않는다.

직렬화된 action type은 `<adapter_id>.<action_name>` 형식으로 namespace를 가진다.
예를 들어 UI의 “클릭”은 `windows.click`, “클립보드 표 추출”은
`clipboard.extract_table`, “Excel 저장”은 `tabular.save_table`이다. 이 문서의
짧은 action 명칭은 UI 표시명을 뜻한다.

## 6. 사용자 경험

사용자는 한 개의 앱에서 다음 순서로 작업한다.

1. 새 자동화 업무를 만든다.
2. 실행할 앱과 대상 창을 선택한다.
3. 업무를 수행하며 입력을 녹화한다.
4. 자동 정리된 단계의 이름과 대상 요소를 확인한다.
5. 입력값을 고정값·실행변수·반복열·비밀값으로 분류한다.
6. 반복 그룹, 상태 기반 대기, 추출 검증을 설정한다.
7. 단계별 테스트와 전체 테스트를 수행한다.
8. 워크플로를 실행하고 결과 보고서를 확인한다.

워크플로 편집 화면은 다음 3영역으로 구성한다.

- 왼쪽: 단계와 반복 그룹의 트리
- 가운데: 대상 화면 미리보기와 강조 표시
- 오른쪽: 단계명, action, target, 값, wait, assertion, failure policy 속성

JSON 보기는 고급 진단용 읽기 전용 화면으로만 제공한다.

녹화 중에는 상태 banner를 항상 표시한다. 기본 전역 단축키는 다음과 같다.

- `Ctrl+Shift+F11`: 녹화 또는 실행 일시정지·재개
- `Ctrl+Shift+F12`: 즉시 중지

이 단축키는 설정에서 변경할 수 있지만 녹화 이벤트에는 포함하지 않는다.

## 7. 녹화와 정규화

### 7.1 녹화 범위

- 사용자가 선택한 process와 top-level window를 기준으로 녹화 session을 연다.
- 선택한 창 밖의 입력은 raw audit에는 “범위 밖”으로 표시하되 워크플로 후보에서
  제외한다.
- mouse move는 일반 단계로 기록하지 않는다.
- click, double-click, mouse down/up, drag 시작·종료, wheel을 기록한다.
- key down/up, modifier, special key, hotkey를 기록한다.
- 각 입력 시점의 foreground process, window, focused element, UIA 후보를 기록한다.
- 창 client rectangle, DPI, monitor scale, 상대좌표를 기록한다.
- Windows의 double-click 시간과 drag threshold를 사용한다.

### 7.2 원본 recording

원본 이벤트는 append-only JSON Lines로 저장하며 실행기는 직접 실행하지 않는다.
각 이벤트에는 다음 필드가 있다.

- `schema_version`
- `session_id`
- `event_id`
- `monotonic_ns`
- `wall_time_utc`
- `event_type`
- `payload`
- `window_context`
- `target_snapshot`
- `environment_snapshot`

원본 session은 `%LOCALAPPDATA%\UniversalRPAStudio\recordings\<session_id>\`에
보관한다. 프로젝트나 Git 저장소 안에 두지 않는다. 기본 보존기간은 7일이며
사용자가 명시적으로 보존을 선택한 session만 유지한다.

### 7.3 정규화

- Windows가 같은 double-click으로 판단한 click pair는 `double_click` 하나로 묶는다.
- down/move/up sequence는 `drag` 하나로 묶는다.
- command key와 hotkey는 `press_key` 또는 `hotkey`로 만든다.
- 같은 editable target의 연속 문자 입력은 `set_text` 하나로 묶는다.
- `Enter`, `Tab`, focus 변경 또는 1초 입력 중단을 text commit 경계로 사용한다.
- recorder 제어 단축키는 제거한다.
- 기록 간격은 고정 wait로 자동 확정하지 않고 wait 후보로만 표시한다.
- 날짜·숫자·경로 pattern은 변수 후보 badge만 표시한다.
- 자동 제안은 사용자 확인 없이 workflow 의미를 변경하지 않는다.
- 사용자는 정규화된 단계를 merge 또는 split할 수 있다.

## 8. 키보드와 값 모델

### 8.1 명령키

다음 입력은 고정 동작이다.

- `Enter`, `Tab`, `Esc`, arrow, function key
- `Ctrl+A`, `Ctrl+C` 등 modifier 조합

workflow에는 `press_key` 또는 `hotkey`로 저장하며 업무 변수로 변환하지 않는다.

### 8.2 텍스트

`set_text` action의 value mode는 다음 중 하나다.

| mode | 의미 | 예 |
|---|---|---|
| `literal` | 항상 같은 값 | `생산실적`, `F10` |
| `variable` | 실행 전 form 또는 계산 규칙에서 결정 | 시작일, 종료일 |
| `row_binding` | 반복 중인 CSV/XLSX 행에서 결정 | `{{ row.factory }}` |
| `secret_ref` | Windows Credential Manager에서 조회 | 계정 비밀번호 |

자동 정리 직후 모든 일반 텍스트의 기본 mode는 `literal`이다. 날짜·숫자·경로
pattern은 변수 후보로 표시하지만 사용자가 한 번 확인해야 mode가 바뀐다.

### 8.3 변수

지원 type:

- `text`
- `date`
- `integer`
- `decimal`
- `path`
- `choice`
- `secret`

지원 source:

- 실행 전 사용자 입력
- 고정 default
- 인라인 목록
- CSV 열
- XLSX sheet 열
- 제한된 날짜 계산
- Windows Credential Manager 참조

날짜 계산은 임의 `eval`을 사용하지 않고 다음 whitelist만 지원한다.

- `today`
- `run_date`
- `add_days(date, n)`
- `month_start(date)`
- `month_end(date)`

### 8.4 한글 IME

- raw physical key sequence만으로 한글 문자열을 재구성하지 않는다.
- editable UIA control이면 commit 시점에 ValuePattern 값을 읽어 완성 문자열을
  확보한다.
- UIA 값을 읽을 수 없으면 raw key sequence와 사용자가 확인할 text field를 함께
  제시한다.
- 재생은 UIA ValuePattern 또는 `set_edit_text`를 우선 사용한다.
- 지원하지 않는 control에서는 focus를 재검증한 뒤 붙여넣기, 마지막으로 직접
  키 입력을 시도한다.
- 값 입력 후 읽기가 가능하면 기대값과 실제값을 비교한다.

### 8.5 비밀 입력

- UIA `IsPassword`인 control의 실제 key payload는 raw recording에 저장하지 않는다.
- 녹화 단계에는 `secret_ref` 참조 자리표시자만 만든다.
- 사용자는 일반 field도 “민감 입력”으로 다시 지정할 수 있다.
- workflow, raw recording, 로그, 보고서, 스크린샷에 평문 secret을 기록하지 않는다.

## 9. 워크플로 스키마

workflow JSON의 최상위 필드는 다음과 같다.

- `schema_version`
- `workflow_id`
- `name`
- `revision`
- `target_apps`
- `environment_policy`
- `variables`
- `data_sources`
- `steps`
- `run_policy`
- `output_policy`
- `created_at`
- `updated_at`

각 step에는 안정적인 `step_id`, 필수 `label`, `kind`, `enabled`,
`failure_policy`가 있다.

지원 step kind:

- `action`
- `loop`
- `if_present`

MVP 지원 action:

- `activate_window`
- `click`
- `double_click`
- `drag`
- `scroll`
- `set_text`
- `press_key`
- `hotkey`
- `wait`
- `read_clipboard`
- `extract_clipboard_table`
- `save_table`

`save_table`은 CSV UTF-8-SIG 또는 XLSX를 지원한다. CSV input은 UTF-8,
UTF-8-SIG, CP949 중 사용자가 선택할 수 있다.

### 9.1 Target

Windows target은 다음 후보를 순서대로 가진다.

1. UIA selector
   - `automation_id`
   - `control_type`
   - `name`
   - `class_name`
   - 안정적인 ancestor path
2. 창 client area 기준 normalized relative coordinate
3. 기록 당시 absolute coordinate는 진단 정보로만 보존하며 실행 target으로 사용하지 않음

UIA selector는 실행 시 정확히 한 요소에 일치해야 한다. 0개 또는 2개 이상이면
좌표 fallback 조건을 검사하거나 실패한다.

좌표 fallback은 다음이 모두 만족될 때만 허용한다.

- process executable name과 window class가 일치
- 기록된 DPI와 현재 DPI가 일치
- 현재 client width와 height가 기록값의 ±2% 이내
- 대상 window가 foreground
- 좌표가 client rectangle 안에 있음
- step의 postcondition 또는 바로 뒤의 assertion이 존재

### 9.2 Wait와 assertion

지원 condition:

- `element_exists`
- `element_visible`
- `element_enabled`
- `window_exists`
- `value_equals`
- `value_contains`
- `clipboard_changed`
- `file_exists`
- `file_stable`
- `fixed_delay`

`fixed_delay`는 다른 상태를 읽을 수 없는 레거시 control의 fallback이다. 모든
wait에는 timeout이 필요하며 무한 대기는 허용하지 않는다. 편집기의 timeout
기본값은 30초이며 사용자가 업무별로 변경한다.

condition과 assertion은 독립 step kind가 아니라 `action` 또는 `loop`에 포함되는
검증 객체다. `wait` action은 condition을 만족할 때까지 polling하고, 일반 action은
실행 후 자신의 postcondition과 assertion을 평가한다.

표 추출 assertion:

- 필수 header 집합
- 최소·최대 row count
- 특정 token 포함
- 빈 결과 허용 여부

좌표 fallback과 모든 extraction step에는 postcondition 또는 assertion이 필수다.

### 9.3 반복

- data source: 인라인 목록, CSV, XLSX sheet
- 최대 중첩 깊이: 2
- row 값은 `{{ row.column_name }}`으로 참조
- 실행 전 data preview와 필수 열 검증
- 각 iteration 완료 시 checkpoint 기록
- 전체 iteration 상한과 최대 실행시간을 workflow에 저장
- 편집기 기본값은 최대 1,000회와 2시간이며, 제품 hard limit는 10,000회와 24시간
- 기본 iteration 실패 정책은 전체 실행 중단
- 사용자가 명시한 경우에만 실패 행 건너뛰기 허용

`if_present`는 선택적 팝업처럼 target 존재 여부만 판단하는 제한된 조건 group이다.
일반 목적의 임의 boolean 식과 복잡한 분기 graph는 지원하지 않는다.

## 10. 실행 의미론

### 10.1 사전 점검

전체 실행 전에 다음을 모두 검사한다.

1. schema와 revision을 읽을 수 있음
2. 필수 adapter가 설치됨
3. 대상 process와 window가 유일하게 식별됨
4. UIA target 또는 좌표 fallback 환경이 유효함
5. 필수 변수와 반복 data source가 유효함
6. secret reference가 존재함
7. output directory가 쓰기 가능함
8. 기존 output 파일이 잠기지 않음

검사 실패 시 UI 입력을 한 번도 보내지 않는다.

### 10.2 단계 수명주기

모든 action은 다음 순서로 실행한다.

1. target resolve
2. foreground window와 focus guard
3. precondition 평가
4. action 실행
5. postcondition 또는 assertion polling
6. `ActionResult` 기록

global mouse 또는 keyboard 입력 직전에 foreground process와 window를 다시
확인한다. 다르면 입력하지 않고 실패한다.

### 10.3 실패 정책

기본 정책은 `stop`이다.

- `retry`: idempotent로 표시된 step만 제한 횟수와 backoff로 재시도. 기본
  재시도 횟수는 0회이고 사용자가 최대 3회까지 설정할 수 있음
- `skip_iteration`: 사용자가 반복 group에서 명시한 경우만 허용
- `stop`: checkpoint 저장 후 전체 실행 중단

외부 side effect를 일으킬 가능성이 있는 step은 자동 retry를 허용하지 않는다.
MVP 범위의 조회·입력·복사 동작도 assertion 없이 성공으로 간주하지 않는다.

### 10.4 결과 상태

`ActionResult` 필드:

- `run_id`
- `step_id`
- `iteration_path`
- `status`: `success`, `skipped`, `failed`, `cancelled`
- `started_at`
- `duration_ms`
- `attempt_count`
- `error_code`
- `safe_message`
- `evidence`

전체 run 상태:

- `success`: 모든 필수 iteration 성공
- `partial`: 명시적으로 skip한 iteration만 실패
- `failed`: stop 정책으로 중단
- `cancelled`: 사용자가 중지

### 10.5 파일 안전성

- CSV/XLSX는 같은 directory의 temporary file에 먼저 저장한다.
- schema와 row count 검증이 성공한 뒤 `os.replace`로 교체한다.
- 검증 실패나 강제 중지 시 기존 정상 output은 유지한다.
- checkpoint는 output commit 이후에만 해당 iteration을 성공으로 표시한다.

## 11. 오류 보고와 운영 기록

실행 결과 화면은 다음을 표시한다.

- workflow name과 revision
- run id와 실행 환경
- 전체·성공·실패·건너뛴 iteration 수
- 추출 row count와 output 경로
- 실패한 반복 행과 step label
- 안전한 오류 설명과 시도 횟수
- 마지막 성공 checkpoint
- 실패 단계 재시험, checkpoint 재개, 보고서 내보내기 동작

구조화 로그에는 입력 text 원문과 clipboard 본문을 기록하지 않는다. clipboard는
길이, hash, header, row count만 evidence로 남긴다.

스크린샷은 실패 시에만 생성한다. 사용자가 지정한 민감 영역과 password control을
마스킹한다. 실행 로그·보고서·실패 스크린샷의 기본 보존기간은 30일이다.

## 12. 보안과 안전

- secret은 Windows Credential Manager에 저장하고 workflow에는 reference만 둔다.
- 사용자 workflow에서 임의 코드 또는 shell 명령을 실행할 수 없다.
- 녹화는 명확한 사용자 시작·일시정지·종료 동작으로만 제어한다.
- emergency stop을 모든 실행보다 높은 우선순위로 처리한다.
- raw recording은 프로젝트와 Git 밖의 per-user application data에 둔다.
- workflow를 공유할 때 raw recording과 실행 artifact는 포함하지 않는다.
- selector가 애매하거나 environment가 다르면 자동 click하지 않는다.
- 스케줄 실행은 MVP에서 제공하지 않으며 사용자가 로그인한 interactive session만
  지원한다.

## 13. 테스트 전략

### 13.1 단위 테스트

- Pydantic workflow schema와 migration 거부
- key down/up을 `press_key`, `hotkey`, `set_text`로 정규화
- 날짜 helper와 variable validation
- CSV/XLSX row binding
- 최대 2단계 loop와 iteration limit
- wait timeout과 backoff
- failure policy와 checkpoint
- secret redaction
- atomic output commit

### 13.2 어댑터 계약 테스트

같은 contract suite를 fake adapter와 Windows adapter에 적용한다.

- target uniqueness
- foreground guard
- condition polling
- 공통 error code
- cancellation
- evidence에 secret이 없음

### 13.3 Windows Test Harness

`samples/test_harness/`에 결정론적 PySide6 앱을 제공한다.

- 일반 text, date, Korean IME, password control
- click, double-click, drag, scroll, hotkey 대상
- 늦게 나타나는 control
- modal popup
- clipboard table
- 창 이동과 resize
- 중복 selector
- 의도적 timeout
- 잠긴 output file

이 앱으로 recorder, editor, runner, report를 반복 가능한 통합 테스트로 검증한다.

### 13.4 실제 MIS smoke test

자동 테스트가 모두 통과한 뒤 기존 MIS의 읽기 전용 추출 업무 하나를 pilot으로
선정한다.

- UI 입력 전 validation-only 실행
- 한 공장·한 기간으로 step test
- 여러 공장 반복
- clipboard header, 공장 token, row count 검증
- 별도 test output에 저장

기존 운영 RPA나 운영 output을 pilot 동안 변경하지 않는다.

## 14. MVP 완료 기준

다음 조건을 모두 충족해야 MVP를 완료로 본다.

1. 한 앱에서 대상 창 선택, 녹화, 편집, 테스트, 실행, 보고가 가능하다.
2. 마우스와 키보드 입력이 의미 있는 단계로 표시된다.
3. `Ctrl+A → 날짜 입력 → Enter`가 `hotkey + set_text + press_key`로 분리된다.
4. 사용자가 text를 고정값, 변수, 반복열, secret으로 지정할 수 있다.
5. 날짜 form과 whitelist 날짜 계산이 동작한다.
6. CSV/XLSX 반복과 최대 2단계 중첩 반복이 동작한다.
7. UIA selector 우선과 검증된 좌표 fallback이 동작한다.
8. 환경 불일치 시 좌표 click 전에 실행이 중단된다.
9. 상태 기반 wait와 clipboard table assertion이 동작한다.
10. 실패 후 마지막 성공 iteration부터 재개할 수 있다.
11. 로그, workflow, report, screenshot에 secret이 없다.
12. 검증 실패 시 기존 정상 output이 보존된다.
13. 모든 자동 테스트가 부모 프로젝트 import 없이 통과한다.
14. Windows 10/11 x64에서 패키징된 앱이 실행된다.
15. 실제 MIS의 승인된 읽기 전용 pilot workflow가 추가 LLM 입력 없이 실행된다.

## 15. 구현 마일스톤

### M1. 독립 프로젝트와 Domain Core

- 자기완결형 package, schema, adapter contract
- fake adapter
- variable, loop, wait, result 모델
- 자동 테스트 기반

### M2. Recorder와 Normalizer

- 대상 창 선택
- mouse·keyboard listener
- UIA target snapshot과 environment fingerprint
- raw session 저장과 retention
- command key·text·drag 정규화

### M3. Studio Editor와 Validator

- 통합 PySide6 shell
- 3영역 단계 편집기
- value mode, variable form, loop editor
- target preview와 재지정
- schema·environment validation

### M4. Windows Runner

- UIA target resolver
- 좌표 fallback guard
- mouse·keyboard·clipboard action
- 상태 기반 wait와 assertion
- cancellation, retry, checkpoint

### M5. 추출·보고·패키징

- CSV/XLSX data source와 output
- atomic save
- redacted report와 retention
- Windows Test Harness
- MIS pilot
- `pyside6-deploy` packaging

각 마일스톤은 실패 테스트 작성, 최소 구현, 자동 검증, 독립 커밋 순서로 진행한다.
상세 구현 계획은 M1부터 M5까지 순차 의존성을 유지하되, 각 마일스톤이 독립적으로
검토·거절·검증 가능한 task 경계를 갖도록 작성한다.

## 16. 주요 위험과 완화

| 위험 | 완화 |
|---|---|
| custom grid가 UIA를 노출하지 않음 | 환경 fingerprint가 맞을 때 상대좌표 fallback과 필수 assertion |
| 창 이동·DPI 변경 | per-monitor DPI awareness, client-relative coordinate, 실행 전 허용범위 검사 |
| 로딩 지연 | 고정 sleep보다 condition polling, timeout, 데이터 assertion |
| 잘못된 창에 key 입력 | 모든 global input 직전 foreground process·window guard |
| 한글 IME 재생 불일치 | 완성 text를 UIA Value로 입력, 붙여넣기 fallback, 실제값 확인 |
| password·token 기록 | IsPassword masking, secret reference, raw session 외부 저장과 retention |
| partial output을 정상으로 오인 | atomic replace 전에 schema·row count 검증 |
| 부분 실패를 성공으로 보고 | 구조화 ActionResult와 `partial`·`failed` 분리 |
| UI 변경으로 selector 중복 | selector uniqueness 필수, target 재보정 |
| 화면 잠금·RDP 종료 | 지원하지 않는 운영 조건으로 명시하고 실행 전 desktop 상태 검사 |

## 17. 승인된 핵심 결정

- A 범위를 먼저 완성하고 B·C는 adapter 확장 경계만 정의한다.
- 현재 저장소 안의 `universal_rpa/`에 두되 추후 별도 GitHub 저장소로 분리한다.
- 부모 프로젝트 코드를 재사용하거나 import하지 않는다.
- 비개발 업무 담당자를 1차 사용자로 삼는다.
- 녹화·편집·테스트·실행을 한 Windows 앱에 통합한다.
- 하이브리드 target resolver를 사용한다.
- keyboard command와 업무 text를 별도 action으로 취급한다.
- variable과 최대 2단계 반복을 MVP에 포함한다.
- JSON은 내부 형식이며 사용자 직접 편집을 요구하지 않는다.
- 실패 시 중단하는 fail-closed를 기본값으로 삼는다.
- runtime LLM과 임의 code execution을 사용하지 않는다.
