# 실제 MIS 읽기 전용 파일럿 런북

이 문서는 Universal RPA Studio MVP의 마지막 수락 게이트인 **실제 MIS 읽기 전용
파일럿**의 감독 절차다. Windows 10 x64와 Windows 11 x64에서 각각 한 번 수행하고,
두 번 모두 통과해야 MVP 완료를 선언할 수 있다.

이 절차는 **읽기 전용**이다. 승인된 workflow는 MIS에서 조회·복사만 수행하며 어떤
쓰기·전송·상태 변경 action도 포함하지 않는다.

## 0. 전제 조건

- 대화형으로 로그인되고 **잠기지 않은** 데스크톱 세션
- `scripts/build.ps1`로 빌드하고 `scripts/smoke_packaged.ps1`을 통과한 패키지
- UAC·MFA·CAPTCHA를 우회하지 않는다. 로그인은 사람이 직접 수행한다
- 런타임 LLM 호출이 없다. 파일럿 중 추가 LLM 입력을 사용하지 않는다
- 승인된 workflow, 실제 업무 데이터, 출력물은 **저장소 밖**에 둔다

### 저장소 밖에 준비할 것

| 항목 | 위치 예시 | 비고 |
|---|---|---|
| 승인 workflow project | `C:\UniversalRPA-Pilot\project\` | 저장소에 커밋 금지 |
| 빈 출력 root | `C:\UniversalRPA-Pilot\output\` | **비어 있어야** 한다 |
| 정책 파일 | `C:\UniversalRPA-Pilot\pilot-policy.json` | 아래 형식 |
| 증거 번들 | `C:\UniversalRPA-Pilot\win11-x64\` | OS별로 분리 |

`pilot-policy.json`은 번들과 **독립적으로** 준비한다. 번들이 스스로의 정확성을
주장할 수 없어야 하므로, 필수 열과 승인 토큰은 외부 정책에서만 온다.

```json
{
  "required_headers": ["공장", "기간", "생산수량"],
  "required_token_sha256": "<승인 토큰 문자열의 SHA-256>",
  "minimum_rows": 1,
  "allowed_output_root": "C:/UniversalRPA-Pilot/output",
  "workflow_revision": 3
}
```

> `required_token_sha256`은 승인 토큰 **원문의** SHA-256이다. 원문은 이 저장소나
> 증거 문서 어디에도 기록하지 않는다.

## 1. 감독 실행 순서

아래 순서를 **그대로** 지킨다. 각 단계는 다음 단계의 전제다.

### 1.1 승인된 읽기 전용 workflow를 저장소 밖에 만든다

Studio에서 프로젝트를 열고, 조회·복사만 하는 workflow를 작성한다. revision을
기록하고 `pilot-policy.json`의 `workflow_revision`과 일치시킨다.

### 1.2 비어 있는 별도 출력 root를 선택한다

Runner 페이지에서 `C:\UniversalRPA-Pilot\output\`을 출력 root로 지정한다. 기존
업무 파일이 있는 디렉터리를 절대 선택하지 않는다.

### 1.3 검증만 실행한다 (action 0회)

Preflight/검증만 수행하고 **action을 한 번도 실행하지 않는다**. 내보낼 문서의
`action_count`는 정확히 `0`이어야 한다.

### 1.4 한 공장·한 기간으로 단일 step test를 수행한다

가장 좁은 범위로 한 번만 시험한다. `factory_count`와 `period_count`는 각각 `1`이다.

### 1.5 헤더·토큰 해시와 양수 행 수를 확인한다

추출된 표의 정규 헤더 SHA-256과 승인 토큰 SHA-256이 정책과 일치하고, 행 수가
1 이상인지 확인한다. **관측한 해시를 정책 파일로 되붙여 넣지 않는다** — 그러면
검사가 자기 자신을 검증하게 된다.

### 1.6 승인된 여러 행을 반복 실행한다

승인 범위 안의 여러 행을 실행한다. 모든 출력은 1.2의 출력 root 아래에만 생성된다.

### 1.7 완료된 iteration 직후 중단하고 한 번 resume 한다

`Ctrl+Shift+F12`로 중단한다. **iteration 중간이 아니라 완료 직후**에 멈춘다.
비멱등 action이 중간에 있으면 `RESUME_UNSAFE`가 발생하며, 이때 자동 resume을
시도하지 말고 수동 복구한다. resume은 마지막 완료 cursor **다음**에서 시작해야
하고, 이미 완료된 cursor를 재실행하면 안 된다.

### 1.8 운영 기준선이 바뀌지 않았음을 확인한다

파일럿 전후로 기존 운영 RPA 산출물과 업무 출력 파일의 해시를 비교한다. 1.6에서
승인된 시험 출력 외에 **어떤 것도 변경되지 않아야** 한다.

### 1.9 다섯 개 안전 문서를 내보낸다

| 문서 | 출처 |
|---|---|
| `validation_report` | 1.3 검증 전용 실행 |
| `step_test_report` | 1.4 단일 step test |
| `multi_run_report` | 1.6 다중 행 실행 |
| `resume_report` | 1.7 resume 실행 |
| `self_check_report` | 패키지 self-check (`--self-check`) |

`pilot-bundle.json`에는 **상대 경로만** 적는다. 절대 경로, 상위 경로(`..`),
UNC(`\\server\share`), 드라이브 문자(`C:`), 장치 이름(`CON`, `NUL`)은 모두 거부된다.

```json
{
  "bundle_schema_version": "1",
  "app_version": "0.1.0",
  "os": "windows-11-x64",
  "environment_fingerprint": "<익명 환경 지문>",
  "workflow_id": "<workflow UUID>",
  "documents": {
    "validation_report": "validation-report.json",
    "step_test_report": "step-test-report.json",
    "multi_run_report": "multi-run-report.json",
    "resume_report": "resume-report.json",
    "self_check_report": "self-check-report.json"
  }
}
```

### 1.10 두 검증 명령을 실행한다

```powershell
.\.venv\Scripts\python.exe scripts\verify_mis_pilot_report.py --bundle C:\UniversalRPA-Pilot\win10-x64\pilot-bundle.json --policy C:\UniversalRPA-Pilot\pilot-policy.json --expected-os windows-10-x64 --summary docs\validation\mis-read-only-pilot-windows-10-x64.md
.\.venv\Scripts\python.exe scripts\verify_mis_pilot_report.py --bundle C:\UniversalRPA-Pilot\win11-x64\pilot-bundle.json --policy C:\UniversalRPA-Pilot\pilot-policy.json --expected-os windows-11-x64 --summary docs\validation\mis-read-only-pilot-windows-11-x64.md
```

두 명령이 모두 `pilot gate: PASS`와 종료 코드 `0`을 반환해야 한다.

### 1.11 원본 recording 7일 정책을 적용한다

`%LOCALAPPDATA%\UniversalRPAStudio\recordings\`의 원본 recording은 기본 7일 후
삭제된다. 파일럿 종료 후 보존 기간을 연장하지 않는다.

## 2. 게이트가 검사하는 항목

`verify_mis_pilot_report.py`는 실패 시 아래 코드를 보고한다.

| 실패 코드 | 의미 |
|---|---|
| `bundle_unreadable` | 매니페스트를 읽거나 파싱할 수 없다 |
| `<name>_missing` | 다섯 문서 중 하나가 없거나 읽을 수 없다 |
| `unsafe_evidence_path` | 경로가 번들 디렉터리를 벗어나거나 링크를 지난다 |
| `duplicate_evidence_path` | 두 이름이 같은 파일을 가리킨다 (= 다섯 문서가 아니다) |
| `document_too_large` | 문서가 10 MiB를 넘는다 (파싱 전 거부) |
| `workflow_identity` | 문서 간 workflow ID/revision이 불일치한다 |
| `workflow_revision` | 외부 승인 revision과 다르다 |
| `app_version` / `schema_version` | 하나의 빌드가 아니다 |
| `environment_fingerprint` | 하나의 기계가 아니다 |
| `expected_os` | 기대 OS와 다르다 |
| `run_chronology` / `duplicate_run_id` | 시간순이 아니거나 같은 run이다 |
| `validation_only` | 검증 실행이 성공이 아니거나 action을 실행했다 |
| `step_test_scope` | 한 공장·한 기간이 아니다 |
| `required_headers` / `required_token` | 정책 해시와 불일치한다 |
| `minimum_rows` | 행 수가 최소치 미달이다 |
| `output_root_containment` | 출력이 승인 root 밖이다 |
| `output_commit_invalid` | commit되지 않았거나 digest가 유효하지 않다 |
| `resume_cursor` | 마지막 완료 cursor 다음에서 시작하지 않았다 |
| `duplicate_completed_cursor` | 완료된 iteration을 재실행했다 |
| `package_self_check` | 네 self-check가 모두 통과하지 않았다 |
| `forbidden_field` | secret·본문·selector·창 제목·원시 예외가 남아 있다 |
| `absolute_path_value` | 절대 고객 경로가 남아 있다 |

## 3. 커밋 정책

파일럿 후 저장소에 커밋할 수 있는 것은 **자동 생성된 편집 요약뿐**이다.

**커밋 가능**

- `docs/validation/mis-read-only-pilot-windows-10-x64.md`
- `docs/validation/mis-read-only-pilot-windows-11-x64.md`
- `docs/validation/mvp-acceptance-evidence.md`

**커밋 금지**

workflow, 원본 recording, 미등록 `.jsonl`, target preview, credential, 고객
CSV/XLSX, 전체 보고서, 실패 screenshot, 고객 절대 경로, 승인 토큰 원문, 관측된 digest.

커밋 전에 위생 스캔으로 확인한다.

```powershell
.\.venv\Scripts\python.exe scripts\repository_hygiene.py
git status --short
```

생성된 요약에는 버전·OS·개수·통과 여부만 들어간다. digest, 토큰, workflow ID,
경로는 의도적으로 제외된다 — 이 파일은 커밋되며, 공장명이나 기간처럼 입력 공간이
좁은 값의 digest는 복원될 수 있다.
