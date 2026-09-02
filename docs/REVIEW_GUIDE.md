# 검수 안내

저장소를 직접 열어 제품 계약·실행·시험·출고 경계를 한 번에 확인하는 시작점이다.
상태가 바뀌면 날짜별 안내를 늘리지 말고 이 파일을 고친다.

## 1. 읽는 순서

1. [문서 지도](README.md)
2. [출력물 기준](출력물%20기준/README.md)과 [런타임 출고 계약](출력물%20기준/90_공통_규칙/런타임_출고_계약.md)
3. [시스템 개요](architecture/system-overview.md)와 [기능별 책임 지도](architecture/feature-map.md)
4. [웹서비스 실행 안내](../app/README.md)와 [분석 엔진 안내](../analysis_engine/README.md)
5. [배포 교체 계약](architecture/deployment-contract.md)

`docs/출력물 기준/`이 내용·목차·PDF 품질의 정본이다. 시험 건수처럼 변하는 숫자는
문서에서 인용하지 말고 §5의 명령으로 직접 센다.

## 2. 저장소 경계

| 경로 | 역할 |
|---|---|
| `app/src/features/<기능>/` | 한 기능의 로직·상수·전용 시험을 한 폴더에 모은다 |
| `app/src/core/` | 두 개 이상 기능이 실제로 공유하는 형식·경로만 둔다 |
| `app/src/web/` | HTTP·화면·라우팅과 기능 조립 |
| `analysis_engine/` | 실제 회사 식별·자료 수집·판정 엔진 |
| `deploy/` · `ops/` | 컨테이너·배포 계약, 운영 절차와 그 절차를 지키는 시험 |
| `render.yaml` | web service 1개, `numInstances: 1`, `/var/data` 1GB 영속 디스크, `autoDeployTrigger: off` |
| `.github/workflows/quality-gate.yml` | app·엔진·배포·운영 시험과 컨테이너 스모크 |

기능 사이의 직접 import는 하지 않는다. 공유가 필요하면 `core/`를 거친다. 정기 작업 3개(외부
백업·주간 XLSX·휴지통 정리)는 코드와 인증 경로까지만 있고 cron으로 선언돼 있지 않다.

`analysis_engine`은 독립 설치 패키지가 아니다. `app/src/features/pipeline/real.py`가
저장소 배치를 전제로 경로를 추가해 동적으로 읽으므로, 디렉터리를 옮기거나 Docker
allowlist에서 빼면 `PIPELINE=real`이 깨진다.

## 3. 제품 계약

- 입력: **회사명 필수, 주소 힌트 선택.** 주소가 비어도 회사 식별부터 결과까지 진행된다.
- 제외: 채용공고, 공고 이미지·OCR, 직무·개인 맞춤, 자소서·면접 답안
- 스키마: `Report.schema_version=company-report-v4-canonical`
- 구조: 핵심 요약, 필수 1~8장, 조건부 9장 `동종업계 비교 결과`, 출처·검증 부록
- 근거: 공식 자료 우선, 사실 원장 단일 소유
- 사실 선택: 1차 결과가 완결되면 즉시 확정한다. 불완전·파싱 이상일 때만 2차를 부르고, 2차
  단독 결과가 완결될 때만 채택한다. 회차 합집합·세 번째 호출·다수결은 금지다.
- 문장 검수: 원문과 앞뒤 공백 제외 완전일치한 문장만 코드로 확정한다. 나머지는 작성한 쪽과
  별개인 검수를 거치고, 명시적 `False` 문장만 한 번 재작성한다. 검수 결과가 없거나 다시
  실패한 문장은 삭제한다.
- 요약: 검수를 통과한 본문 문장 3~5개를 프로그램이 결정론적으로 고른다. 요약 전용 AI 호출은 없다.
- 비교: 양사 공식 근거의 지표·기간·연결/별도 범위가 맞을 때만 9장을 붙인다. 맞지 않으면
  9장을 생략하고 표준 부족 사유를 붙인 `Grade.PARTIAL`로 출고한다.
- 출력: 같은 자동출고 레코드를 웹·PDF·Notion에 동등 렌더하며 **PDF가 다운로드 정본**이다.

화면이나 코드가 주소를 필수로 요구하면 정본을 바꾸지 말고 release blocker로 다룬다.

## 4. 로컬 실행

Python 3.13을 쓰고, 가상환경은 **저장소 루트**에 만든다.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r app\requirements.txt -r .github\requirements-ci.txt
cd app
.\로컬데모켜기.ps1
```

로컬 데모는 외부 provider를 부르지 않고 `app/.local_demo/`에 격리한다. 임의 회사로 실제
흐름을 보려면 `app`에서 `.\실시간성능시험켜기.ps1 -Port 8020`을 쓰되, 기본 실행은 외부
호출 0건인 미리보기다. 유료 provider 호출은 비용 발생을 명시적으로 승인하고 현재
PowerShell 프로세스에 네 provider 비밀을 넣은 경우에만 `-EnablePaidProviders`를 붙인다.
실행기가 출력하는 로컬 관리자 URL은 관리자 권한과 같으므로 공유하지 않고 종료 뒤 폐기한다.

## 5. 시험

고정된 통과 개수는 코드가 늘어날 때마다 낡으므로 인수 기준으로 쓰지 않는다. 아래 네
묶음을 **각각 따로** 실행해 실패·오류 0을 확인한다. `app/src`와 `analysis_engine/src`를
한 세션에 모으면 같은 이름의 시험 파일이 겹쳐 `import file mismatch`로 **수집 자체가
중단된다** — 실패가 아니라 그 세션의 시험이 한 건도 실행되지 않는다. 저장소 루트에서
`TLDEXTRACT_CACHE`를 정한 뒤 실행하며, 아래 네 줄은
`.github/workflows/quality-gate.yml`이 CI에서 실행하는 명령과 같다.

```powershell
$env:TLDEXTRACT_CACHE = "$PWD\.cache\tldextract"
python -m pytest app/src app/tools/tests -q -m "not local_integration" --basetemp .pytest_tmp_ci_app
python -m pytest analysis_engine/src -q --basetemp .pytest_tmp_ci_engine
python -m pytest deploy/tests -q --basetemp .pytest_tmp_ci_deploy
python -m pytest ops -q --basetemp .pytest_tmp_ci_ops
```

`ops` 시험 중 일부는 Windows에서 symlink 권한 때문에 건너뛰므로 리눅스 CI에서 처음
실행된다. 대용량 DART 목록·과거 데모·검증된 공시 원문이 필요한 시험은
`local_integration` marker로 분리돼 기본 실행에서 빠진다. 자료가 없는데 marker를 고르면
실패하는 것이 정상이며, 기본 회귀의 녹색으로 덮지 않는다.

```powershell
.\.venv\Scripts\python -m pytest app/src/features/pipeline/tests `
  app/src/features/business_candidate/tests -q -m local_integration `
  --basetemp=.pytest_tmp_local_integration
```

CI의 마지막 확인은 네 시험 묶음이 통과한 뒤 Docker 이미지를 만들고 컨테이너의
`/readyz`가 HTTP 200을 돌려주는지 보는 것이다.

## 6. 불변식

**출고 게이트는 fail-closed다.**

```text
report_standard 통과
  → report SHA-256 고정
  → PDF prepare·전 페이지 PNG 렌더와 SHA-256 고정
  → 사실·인용·수치·구조·금지 문구·PDF 렌더·채널 동등성 자동검사
  → 검사 전후 report/PDF/page hash 재대조
  → 같은 자동출고 레코드로 웹·PDF·Notion 허용
```

필수 1~8장이나 검사 하나라도 실패하거나 검사 뒤 hash가 바뀌면 부분 화면도 공개하지 않고
전체를 `GATE_STOPPED`한다. 비교 계약 미성립으로 9장을 생략한 `Grade.PARTIAL`은 이 실패가
아니다. 반대로 9장을 실었는데 비교 계약이 맞지 않으면 출고를 막는다. 수동 승인
GET/POST는 `410 Gone`이고 구형 세 역할 승인 테이블은 감사자료로만 남는다.

**세 채널은 같은 봉인 블록을 읽는다.** 웹·PDF·Notion은 각자 문장을 만들지 않고 같은 공개
projection의 블록을 채널 문법으로만 그린다. 보이는 글자가 블록 값과 달라지면 채널 동등성
검사가 막는다. 표식의 모양(위첨자·평문)은 채널마다 다를 수 있다.

**한도는 원자적으로 잡는다.** 유료 단계는 예상 원가를 먼저 예약하고, 예약을 더한 합이
기준을 넘으면 호출 전에 막는다. 진행 중 예약을 빼고 계산하면 조사가 도는 동안 새 조사가
들어와 천장을 넘으므로, 누적 판정은 종결된 실측 원가와 진행 중 예약을 함께 센다. 이
기준은 청구액의 절대 상한이 아니라 호출을 허용할지 정하는 입장 상한이다.

## 7. 접근 계층과 파일럿 실행 범위

| 갈래 | 통과 조건 | 새 조사 |
|---|---|---|
| 관리자 | `ADMIN_EMAILS`의 구글 계정 | 허용. `/admin` 접근도 이 갈래만 |
| 회원 | 관리자가 초대 명단에 넣은 구글 계정 | 허용. 사람마다 하루 성공 건수·비용 한도 |
| 초대 링크 | 관리자가 발급한 링크 주소·QR | 로그인 없이 허용. 링크마다 하루·누적 한도와 만료일 |
| 그 밖 | 로그인도 링크도 없음 | 막힘. 미리 만들어 둔 보고서 열람만 |

`BETA_ADMIN_ONLY=1`이면 로그인 벽이 켜진다. **구글 로그인만으로는 통과하지 못한다** —
초대 명단에 활성 상태로 있어야 한다. 링크 발급·초대·`/k/` 입구가 열리는지는
`DEPLOYMENT_RUNTIME_CONTRACT` 값이 정한다.

관리자 화면은 오늘 상태·초대 링크·회원·보고서·비용·운영 여섯 묶음이며 PC와 폰이 같은
여섯을 본다. 링크 철회·회원 빼기·한도 변경·만료 연장은 확인 화면을 거쳐야 실행되고,
한도 변경과 만료 연장은 이유도 20자 이상 적어야 한다. 옛 주소는 지우지 않고 새 화면으로
넘긴다.

파일럿 후보군은 `P01`~`P25`로 보존하되 **실제 유료 실행과 전수 검토 범위는 `P01`~`P10`으로
제한한다. `P11`~`P25`는 별도 승인 전 provider 호출이 금지된다.** 이 파일럿은 자동검사
보정용이며 서비스 건별 출고 승인이 아니다.

## 8. 환경과 비밀값

| 범위 | 값 |
|---|---|
| 안전한 첫 실행 | `PIPELINE=demo`, `BETA_ADMIN_ONLY=1` (코드 기본값) |
| 배포 인증 | `ADMIN_EMAILS`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` |
| 배포 경계 | `DEPLOYMENT_RUNTIME_CONTRACT`, `PUBLIC_ORIGIN`, 빈 `FORWARDED_ALLOW_IPS` |
| 초대 링크 | `SHARE_PUBLIC_BASE_URL`, `SHARE_LINK_MAX_AGE_DAYS`(선택) |
| 근거 봉인 | `PROVENANCE_SEAL_SECRET` |
| 품질 운영모드 | `REPORT_RELEASE_MODE`, `ENGINE_V2` |
| 정기 작업 | `BACKUP_TRIGGER_SECRET`, `MAINTENANCE_TRIGGER_SECRET`, 각 HTTPS 내부 경로 |
| 외부 백업 | `BACKUP_S3_*`, 최소 권한 자격증명 |
| 실제 조사 | `DART_API_KEY`, `ANTHROPIC_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` |
| 선택 전송 | `NOTION_TOKEN`, `NOTION_PARENT_PAGE_ID` |

형식은 [`app/.env.example`](../app/.env.example), 배포 절차는
[Render 운영 배포](../app/docs/Render_배포.md)를 따른다. `PROVENANCE_SEAL_SECRET`을 잃거나
바꾸면 기존 봉인과 캐시를 신뢰할 수 없어 출고가 막힌다. 비밀값과 로컬 관리자 URL을
Git·문서·메신저·화면 캡처에 남기지 않는다.

## 9. 알려진 한계

- SQLite 계약 때문에 multiworker·다중 instance 운영을 지원하지 않는다.
- 외부 백업·정기 작업은 코드와 로컬 회귀까지만 있다. 실제 trigger URL, 버킷·권한,
  독립 서명, 실패 알림은 아직 만들지 않았다. 배포 버튼만 눌러도 재해 복구가 생기지 않는다.
- Google Places 후보 검색은 약관 검토가 끝날 때까지 잠겨 있다.
- 실제 OAuth·provider·Notion 계정과의 배포 환경 확인은 로컬 mock 통과로 대체하지 않는다.
- PDF 자동 검사가 실제 screen reader·인쇄 장치·모든 PDF/UA 조건을 대신하지 않는다.
- 초대 링크는 가진 사람이 곧 권한이다. 유출되면 만료를 기다리지 말고 즉시 철회한다.
- 종료 신호만으로 진행 중 provider 작업이 즉시 끝나지 않는다. 재배포 때 확인한다.

## 10. 검수 체크리스트

- [ ] 문서 지도와 출력물 기준을 읽고, 회사명 단독 입력과 선택 주소 입력을 각각 확인
- [ ] `PIPELINE=demo` 로컬 실행과 관리자 URL 폐기 확인
- [ ] §5의 네 묶음을 새로 실행해 실패·오류 0 확인
- [ ] GitHub Actions와 컨테이너 `/readyz` 확인
- [ ] `P11`~`P25` provider 호출 0 확인
- [ ] 필수 자동검사 전 항목 통과 → 같은 hash 자동출고 → 웹·PDF·Notion 허용 확인
- [ ] 검사 하나 실패·검사 후 hash 변경·수동 승인 URL에서 세 채널 우회 불가 확인
- [ ] 사실 선택 1차 1회, 불완전할 때만 2차, 합집합·세 번째 호출 없음 확인
- [ ] 완전일치 문장만 검수 0회 확정, 나머지는 작성/검수 분리, 재실패 문장 삭제 확인
- [ ] 핵심 요약이 별도 AI 호출 없이 검수 통과 본문에서 결정론적으로 만들어지는지 확인
- [ ] 9장 포함 `Grade.COMPLETE`와 9장 생략 `Grade.PARTIAL`이 같은 게이트를 통과하는지 확인
- [ ] 실패 고객 청구 0원과 실패 호출 내부 원가 보존 확인
- [ ] 하루·누적 한도 소진 시 새 조사만 막히고 저장된 보고서는 열리는지 확인
- [ ] SQLite 백업 해시 검증과 비밀 복구 묶음 확인
- [ ] 날짜가 붙은 과거 검토 문서를 현재 정본이나 최신 통과 증거로 인용하지 않음
