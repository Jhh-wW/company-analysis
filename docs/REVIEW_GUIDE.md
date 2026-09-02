# 개발·기획 검수 안내

이 문서는 이 작업 폴더를 직접 연 검수자가 제품 계약, 실행, 검증, 출고와 운영 한계를
한 번에 확인하는 시작점이다. 현재 상태가 바뀌면 날짜별 안내를 늘리지 말고 이 파일을
갱신한다.

## 1. 먼저 읽을 정본

1. [문서 지도](README.md)와 권위 순서
2. [출력물 기준](출력물%20기준/README.md)
3. [런타임 출고 계약](출력물%20기준/90_공통_규칙/런타임_출고_계약.md)
4. [시스템 개요](architecture/system-overview.md)와
   [기능별 책임 지도](architecture/feature-map.md)
5. [웹서비스 실행 안내](../app/README.md)와
   [분석 엔진 안내](../analysis_engine/README.md)
6. [배포 교체 계약](architecture/deployment-contract.md)

`docs/출력물 기준/`의 20개 문서가 내용·목차·PDF 품질의 정본이다. 시험 건수처럼 변하는
숫자는 문서에서 인용하지 말고 아래 명령으로 직접 세어 확인한다.

## 2. 현재 제품 계약

- 입력: **회사명 필수, 주소 힌트 선택**
- 제외: 채용공고, 공고 이미지/OCR, 직무·개인 맞춤, 자소서·면접 답안
- 스키마: `Report.schema_version=company-report-v4-canonical`
- 구조: 핵심 요약, 필수 1~8장, 조건부 9장 `동종업계 비교 결과`, 출처·검증 부록의 고정 정본
- 근거: 공식 자료 우선, 외부 자료는 교차검증, 사실 원장 단일 소유
- 사실 선택: 1차 결과가 완결되면 즉시 확정하고, 불완전·파싱/출력 이상일 때만 2차를 호출한다. 2차 단독 결과가 완결될 때만 채택하며 회차 합집합·세 번째 호출·다수결은 금지한다.
- 문장 검수: 원문과 앞뒤 공백 제외 완전일치한 문장만 코드로 확정하고 AI 검수를 생략한다. 나머지는 Writer와 별도 무문맥 Reviewer를 유지한다. 명시적 `False` 문장만 한 번 재작성하며, 재작성문도 완전일치하면 코드로 확정하고 나머지만 새 Reviewer가 검사한다. 검수 결과가 없거나 다시 실패한 문장은 삭제한다.
- 요약: 원문 완전일치 코드 확정 또는 독립 Reviewer를 통과한 본문 문장 3~5개를 프로그램이 결정론적으로 고르며 요약 전용 AI를 호출하지 않는다.
- 비교: 양사 공식 근거의 지표·기간·연결/별도 범위가 맞으면 9장을 결합한다. 맞지 않으면 9장을 생략하고 표준 부족 사유를 붙인 `Grade.PARTIAL` 1~8장 기본 보고서를 출고한다.
- 출력: 같은 자동출고 레코드의 보고서를 웹·PDF·Notion에 동등 렌더
- 파일 정본: 필수 자동검사 전 항목과 최종 해시 결속을 통과한 PDF

주소가 비어도 회사 식별부터 결과까지 진행되어야 한다. 화면이나 코드가 지역·주소를
필수로 요구하면 정본을 바꾸지 말고 release blocker로 처리한다. 구형 DOCX/Word와
채용 결합 필드는 호환 코드일 뿐 신규 조사·캐시·출력에 연결하지 않는다.

## 3. 저장소와 실행 경계

- `app/`: FastAPI 화면, 인증, 비용·공유 제어, pipeline 조립, 저장, PDF·Notion
- `analysis_engine/`: 실제 회사 식별·자료 수집·판정 엔진
- `docs/출력물 기준/`: 사람이 읽는 내용·출고 정본
- `render.yaml`: 관리자 전용 웹 1개만 선언한 Blueprint. 인증된 정기 작업 3개는
  코드·내부 경로까지만 있고 운영 adapter가 없어 아직 cron으로 선언하지 않음
- `.github/workflows/quality-gate.yml`: app·engine 회귀 시험과 Docker health 확인

`analysis_engine`은 독립 설치 패키지가 아니다. `app/src/features/pipeline/real.py`가
저장소 배치를 전제로 경로를 추가해 동적으로 읽으므로 디렉터리를 삭제하거나 Docker
allowlist에서 빼면 `PIPELINE=real`이 깨진다.

## 4. 환경과 비밀값

| 범위 | 핵심 값 |
|---|---|
| 안전한 첫 실행 | `PIPELINE=demo`, `BETA_ADMIN_ONLY=1` |
| 배포 인증 | `ADMIN_EMAILS`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` |
| 공유 링크 | `SHARE_PUBLIC_BASE_URL` |
| 근거·자동출고 | `PROVENANCE_SEAL_SECRET` (`PDF_RELEASE_PARTICIPANTS`는 구형 감사자료 해석용) |
| 내부 정기 작업 | `BACKUP_TRIGGER_SECRET`, `MAINTENANCE_TRIGGER_SECRET`, 각 HTTPS trigger URL |
| 외부 백업 | `BACKUP_S3_*`, 최소 권한 `AWS_*` 자격증명 |
| 실제 조사 | `DART_API_KEY`, `ANTHROPIC_API_KEY`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET` |
| 선택 전송 | `NOTION_TOKEN`, `NOTION_PARENT_PAGE_ID` |

형식과 조건은 [`app/.env.example`](../app/.env.example), Render 생성·보관·복구 절차는
[Render 운영 배포](../app/docs/Render_배포.md)를 따른다. 비밀값, 실제 참여자 `sub`,
로컬 관리자 URL을 Git·문서·메신저·화면 캡처에 남기지 않는다.

## 5. 안전한 로컬 실행

Python 3.13을 사용한다.

```powershell
cd app
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt -r ..\.github\requirements-ci.txt
.\로컬데모켜기.ps1
```

로컬 데모는 외부 provider를 호출하지 않고 `app/.local_demo/`에 격리한다. 임의 회사의
실제 흐름을 보기 위한 기본 성능시험도 먼저 비용 없는 미리보기로 실행한다.

```powershell
.\실시간성능시험켜기.ps1 -Port 8020
```

유료 provider 호출은 사용자가 비용 발생을 명시적으로 승인하고 현재 PowerShell
프로세스에 네 provider 비밀을 넣은 경우에만 `-EnablePaidProviders`를 붙인다.
실행기가 출력하는 로컬 관리자 URL은 bearer 권한과 같으므로 공유하지 않고 종료 뒤
폐기한다.

## 6. 검증 기준과 갱신 명령

고정된 통과 개수는 코드가 추가될 때마다 낡으므로 인수 기준으로 사용하지 않는다.
아래 명령을 같은 작업트리에서 실행해 실패·오류 0을 확인하고, 실제 통과·warning·
deselected 수는 최종 통합 회귀 뒤 작성하는 리뷰 반영결과 문서에 기록한다. 대용량 DART
목록·과거 데모 15건·검증된 공시 원문은 `local_integration`으로 분리되어 기본 CI에서
명시적으로 제외되며, 해당 marker를 선택했는데 로컬 자료가 없으면 시험은 실패한다.

```powershell
$repo = '<저장소를 받은 폴더>'
$py = Join-Path $repo '.venv\Scripts\python.exe'
$env:ANALYSIS_ENGINE_DISABLE_DOTENV = '1'
$env:TLDEXTRACT_CACHE = Join-Path $repo 'app\.cache\tldextract'
Set-Location -LiteralPath (Join-Path $repo 'app')
& $py -m pytest src -q -p no:cacheprovider --basetemp='C:\pt_app'
Set-Location -LiteralPath $repo
& $py -m pytest analysis_engine/src -q -p no:cacheprovider --basetemp='C:\pt_eng'
& $py -m pytest deploy/tests ops/tests app/tools/tests -q -p no:cacheprovider --basetemp='C:\pt_ops'
```

⚠️ 위 세 묶음(`app/src`, `analysis_engine/src`, `deploy/tests ops/tests
app/tools/tests`)은 **각각 따로** 실행해야 한다(2026-09-02 실측). `app/src`와
`analysis_engine/src`를 한 pytest 세션에 같이 넣으면 같은 basename의 시험
파일(`test_credentialed_http.py`, `test_logic.py`)이 서로 다른 폴더에 있는데
`__init__.py`가 없어 `import file mismatch`로 **수집 자체가 중단**된다 — 실패가
아니라 그 세션의 모든 시험이 아예 실행되지 않는다. 실패·오류가 이 형태로 나오면
먼저 세 묶음을 분리해서 실행했는지부터 확인한다.

대용량 로컬 자료까지 준비된 경우에만 app 폴더에서 아래 명령을 별도로 실행한다. 자료가
없는데 marker를 선택하면 실패하는 것이 정상이며, 기본 회귀의 녹색으로 숨기지 않는다.

```powershell
.\.venv\Scripts\python -m pytest src/features/pipeline/tests `
  src/features/business_candidate/tests -q -m local_integration `
  --basetemp=.pytest_tmp_local_integration
```

CI와 같은 최종 확인은 GitHub Actions `quality-gate`에서 app·engine 시험 뒤 Docker
이미지를 만들고 `/healthz`까지 통과했는지 본다. 독립 A/B 판정의 전제가 바뀌는
내용·보안·PDF 변경이면 해당 검토도 다시 수행해 PASS 근거를 갱신한다.

## 7. 자동검사·자동출고

```text
report_standard 통과
  → report SHA-256 고정
  → PDF prepare·전 페이지 PNG 렌더와 SHA-256 고정
  → 사실·인용·수치·구조·금지 문구·PDF 렌더·채널 동등성 자동검사
  → 검사 전후 report/PDF/page hash 재대조
  → 같은 자동출고 레코드로 웹·PDF·Notion 허용
```

필수 1~8장이나 출고 검사 하나라도 실패하거나 검사 뒤 hash가 바뀌면 부분 화면도 공개하지 않고
전체를 `GATE_STOPPED`한다. 비교 계약 미성립으로 9장을 생략한 `Grade.PARTIAL` 정본은 이 실패에
해당하지 않는다. 반대로 9장을 실었는데 비교 계약이 맞지 않으면 출고를 차단한다. 수동 승인
GET/POST는 `410 Gone`이며 구형 세 역할 승인 테이블은
감사자료로만 보존한다. 파일럿 후보군은 P01~P25로 보존하되 실제 유료 실행·사용자
전수 검토 범위는 P01~P10으로 제한한다. P11~P25는 별도 승인 전 provider 호출이
금지된다. 이 파일럿은 자동검사 보정용이며 서비스 건별 출고 승인이 아니다.

내부 AI 원가는 실패 호출을 포함해 stage·실제 model ID·일반/cache token·batch·원가로
기록한다. 고객 청구는 자동출고 뒤에만 별도 결정하며 실패·`GATE_STOPPED`·출고 실패는
0원이다. 공개 판매가는 아직 확정되지 않았고 서버 월 고정비는 AI 변동원가와 분리한다.

## 8. 배포·정기 작업·복구

- 코드 기본 안전값: `PIPELINE=demo`, `BETA_ADMIN_ONLY=1`
  (★ 2026-08-26 정정 — 운영 배포는 `PIPELINE=real`로 **이미 돌고 있다**.
   기본값이 `demo`인 것은 «로컬에서 실수로 과금되지 않게» 하기 위한 것이지
   「아직 배포 안 했다」는 뜻이 아니다)
- 첫 무료 배포는 Uvicorn worker `1`, Render instance `1`, 임시 `/var/data`이며 잠듦·재시작 시
  세션·보고서·실행 기록 초기화를 허용한다. 정식 베타 전에는 Starter와 영속 디스크로 전환한다.
- 매일 04:00 KST 외부 recovery-generation 백업, 월요일 04:10 주간 관리자 XLSX,
  매일 04:20 휴지통·멈춘 작업 정리는 **후속 목표 시각**이다. 현재 Blueprint에는
  production adapter가 없어 cron을 의도적으로 선언하지 않았다.
- cron은 영속 디스크를 직접 읽지 않고, 서로 분리된 32바이트 이상 Bearer 비밀과 정확한
  HTTPS 내부 경로로 웹에 요청한다. redirect는 거부하고 작업은 기간별 claim으로 멱등화한다.
- SQLite snapshot과 그 snapshot이 참조하는 최초 승인 PDF를 묶은 `rg-...` 복구 세대
  전체를 내려받아 `backup_sqlite.py verify <세대 디렉터리>`로 검증한다. DB 한 파일은
  신규 PDF 복구 자료로 인정하지 않는다.
- DB와 별도로 OAuth·provider·Notion 비밀, `SHARE_PUBLIC_BASE_URL`,
  `PROVENANCE_SEAL_SECRET`을 비밀 관리자에 보관

위 정기 작업은 코드·로컬 회귀까지만 완료했다. 실제 Render trigger URL,
S3 bucket·권한·독립 signer/checkpoint·실패 알림은 아직 만들거나 실행하지 않았다.
현재 웹 배포 버튼만 눌러도 이 재해 복구 경계가 자동으로 생기지는 않는다.

`PROVENANCE_SEAL_SECRET`을 잃거나 바꾸면 기존 seal·캐시의 신뢰를 복구할 수 없어
출고가 차단된다. 구형 참여자 JSON은 과거 감사자료의 역할 해석에만 필요하다.
상세 절차는 [Render 운영 배포](../app/docs/Render_배포.md)와
[장기 휴면 백업](../app/docs/장기_휴면_백업.md)을 따른다.

## 9. 알려진 운영 한계

- SQLite 계약 때문에 multiworker·다중 instance 운영은 지원하지 않는다.
- Google Places 후보 검색은 결과 보관·표시 약관 검토가 끝날 때까지 잠겨 있다.
- 실제 OAuth, provider, Notion 계정과의 staging smoke test는 배포 환경에서 별도로
  수행해야 하며 로컬 mock PASS로 대체하지 않는다.
- Docker build, 원격 CI, Render, 외부 S3 백업·복구훈련은 이번 로컬 스냅샷의 PASS 범위가 아니다.
- 비용 원장은 예상비용 기반 운영 차단이며 provider 청구액의 절대 hard cap이 아니다.
- PDF 자동 구조·시각 검사가 실제 screen reader, 인쇄 장치, 모든 PDF/UA 조건을 완전히
  대신하지 않는다.
- 공유 링크는 capability다. 유출되면 만료를 기다리지 말고 즉시 철회한다.
- 실행 중 provider thread는 취소 신호만으로 즉시 끝나지 않을 수 있으므로 종료·재배포
  때 진행 중 작업과 원장을 확인한다.

## 10. 안전 규칙

- 외부 호출·유료 API·실제 계정 전송은 명시적 승인 없이 실행하지 않는다.
- 실제 DB, `.env`, API/OAuth/Notion 비밀, 백업, 사용자 식별자를 커밋하지 않는다.
- 조사 원문을 공개 저장소에 재배포하지 않고 정제 요약과 검증 해시만 남긴다.
- 정본과 자동검사를 통과하지 않은 초안·구형 캐시·PDF를 공개·다운로드·Notion 전송하지 않는다.
- 기존 사용자 변경이 있는 dirty worktree에서 무관한 파일을 되돌리거나 삭제하지 않는다.

## 11. 검수 체크리스트

- [ ] 문서 지도와 출력물 기준 20개를 읽음
- [ ] 회사명 단독 입력과 선택 주소 입력을 각각 확인
- [ ] `PIPELINE=demo` 로컬 실행과 관리자 URL 폐기 확인
- [ ] app·analysis_engine 회귀 시험을 새로 실행하고 이 문서의 숫자를 갱신
- [ ] GitHub Actions와 Docker `/healthz` 확인
- [ ] 필수 자동검사 전 항목 통과 → 동일 hash 자동출고 → 웹·PDF·Notion 허용 확인
- [ ] 검사 하나 실패·검사 후 hash 변경·수동 승인 URL에서 세 채널 우회 불가 확인
- [ ] 필수 `claim_type`·내부 SID 링크·3개 완료 회계연도 표·장별 본문 상한을 완결한 사실 선택 1차는 호출 1회로 끝나고, 불완전·이상일 때만 2차까지 실행하며 두 회차 합집합·세 번째 호출이 없음을 확인
- [ ] 원문과 앞뒤 공백 제외 완전일치한 문장만 AI 검수 0회로 확정하고, 그 밖에는 Writer와 Reviewer가 분리되며 명시적 `False`만 한 번 재작성하는지 확인. 재작성 완전일치는 코드로 확정하고 나머지만 새 Reviewer가 검사하며, 검수 누락·재실패 문장은 삭제되는지 확인
- [ ] 핵심 요약이 별도 AI 호출 없이 검수 통과 본문 3~5개와 해당 `fact_ids`에서 결정론적으로 만들어지는지 확인
- [ ] 9장 포함 `Grade.COMPLETE`와 9장 생략·표준 사유 포함 `Grade.PARTIAL` 정본이 각각 같은 출고 게이트와 채널 동등성을 통과하는지 확인
- [ ] 실패 고객 청구 0원과 실패 호출 내부 AI 원가 보존 확인
- [ ] G3.5는 P01~P10만 실행·전수 검토하고 P11~P25 provider 호출 0 확인
- [ ] 내부 trigger의 exact HTTPS URL·비밀 분리·redirect 거부·기간별 멱등성 확인
- [ ] SQLite 백업 해시 검증과 비밀 복구 묶음 확인
- [ ] 운영 한계와 미완료 staging 시험을 이슈·release 판단에 반영
- [ ] 날짜별 리뷰를 현재 정본이나 최신 PASS 증거로 인용하지 않음
