# 프로젝트 실행·품질 감사와 후속 수정 — 2026-09-08

이 문서는 최초 읽기 전용 감사와 후속 수정의 상태를 구분한다. 최초 감사 기준은 HEAD `28d89132`이며, 아래 보관 기록의 줄 번호는 수정 전 소스 기준이다. 현재 공유 워크트리에는 다른 담당자의 뉴스·composer·출고 변경이 함께 있으므로 이번 담당의 시험 결과를 전체 통합 품질 보장으로 해석하면 안 된다.

## 현재 결론과 범위

| 항목 | 이번 상태 | 사용자 영향·다음 우선순위 |
|---|---|---|
| P1 평가 런처 인증키 누락 | 수정 | NCP 두 필수 키 이름을 앱 기동 계약과 일치시켰다. 구형 NAVER_CLIENT 이름으로 대체할 수 없다. |
| P1 운영·평가 스위치 불일치 | 수정 | 저장소 계약과 9월 8일 관측 운영 스위치를 별도 profile로 제공하고 비밀 없는 설정 JSON·SHA-256을 기록한다. |
| P1 실제 커밋 신원 누락 | 후속 통합 점검에서 추가 발견·수정 | ready 상태여도 build identity가 불명확해 본조사가 막혔다. 검증한 실제 HEAD를 APP_GIT_COMMIT에 전달하며 미커밋·미추적 실행 소스는 유료 기동 전에 중단한다. |
| P1 SQLite 동기 busy 대기 | 진행 라우트만 최소 수정 | 진행 화면/API의 접근 검사·저장 보고서·게이트 진단·중단 조회를 스레드로 이동했다. 인증 미들웨어·다른 결과 라우트 전반은 보류이므로 전체 event loop 결함이 해결됐다는 뜻은 아니다. |
| P2 재시작 후 GATE_STOPPED 상태 소실 | 수정 | 결과 라우트와 같은 영속 진단 판정에 근거해 진행 화면은 303, API는 finished/recovered를 반환한다. 권한 검사가 먼저이고 상세 회사명·내부 사유는 진행 API에 추가하지 않는다. |
| P2 첫 열람 KPI 오해 표시 | 표시·nullable 계약 수정, 계측 재개 보류 | 현재 값은 미측정으로 표시하며 과거 기록 0건·누적 기록·조회 불가를 분리했다. 순수 GET 쓰기와 개인 추적을 추가하지 않았고, 실제 계측 재개는 별도 제품 결정으로 남긴다. |
| P2 README 배포·부분보고서 설명 | 조정자가 대표 문서 수정 | 저장소의 자동 배포 설정과 실제 대시보드 적용 여부를 구분하고, FULL 요청의 조건부 부분 제공와 현재 뉴스 조사 경로를 문서에 반영했다. 아래 수정 전 비교 근거는 보관한다. |
| 대표 회사 실제 품질 비교 | 실행 도구·8사 확정 목록·구판 지표 준비 | 무료 DART 실측과 기존 PDF 6개 추출을 마쳤다. 실제 유료 호출은 실행하지 않았으며 코드 통합 뒤 조정자가 시작해야 한다. |

검토 영역은 기동·설정·배포, 후보 확인·본조사 실행 경로, job 수명과 복구, 두 단계 cache 및 생성 멱등성, 인증·CSRF·보고서 권한·민감정보 경계, 실패 표시·자료 부족, 자동 품질 판정·출고, 비용 예약·정산·진단·KPI, 배포 문서와 기존 평가 도구이다. 직접 뉴스 수집·본문 생성·뉴스 목록 렌더·Notion 수정은 다른 담당 영역이다. 아래 보관 기록에 각 영역의 파일·줄·재현 근거를 남겼다.

## 수정 근거와 검증 경계

- 런처 `app/실시간성능시험켜기.ps1:72`의 NCP 허용 목록은 `app/src/web/evaluation_mode.py:49`의 필수 키 목록과 자동 대조한다. 부모 전체 환경은 전달하지 않고, Google Places 잠금·loopback 단일 worker·격리 DB·건당/일일 예산·종료 정리·Ctrl+C·숨김 자식 창을 유지한다.
- `Get-EvaluationFeatureSettings`(`app/실시간성능시험켜기.ps1:213`)는 기본 `RepositoryContract`에서 render.yaml의 닫힌 설정만 읽는다. `Explicit`는 명시 인자용이며 `ProductionObserved20260908`는 조정자의 실제 Render 환경 페이지 관측값을 고정한다. 인자 `-NewsIntake/-RevenueTableV2/-TypedDartCollector/-EvidenceReclassify/-NewsroomDateAI`는 정확히 0 또는 1만 받는다.
- 조정자는 2026-09-08 Render 환경 목록을 펼쳐 확인한 뒤 ENGINE_V2=1, NEWS_INTAKE=1, REPORT_RELEASE_MODE=FULL을 전달했다. REVENUE_TABLE_V2·TYPED_DART_COLLECTOR·EVIDENCE_RECLASSIFY·NEWSROOM_DATE_AI는 키가 없고 앱 기본값 0이다. 따라서 render.yaml의 REVENUE_TABLE_V2=1은 실제 운영값과 다르다. 이 사실은 조정자의 관측 전달이며 이번 담당이 운영 브라우저를 직접 읽은 것은 아니다.
- 실행 설정 영수증(`app/실시간성능시험켜기.ps1:575`)의 `matches_observed_production_switches`와 `production_parity=not_verified`는 다르다. 스위치 일치만으로 배포 커밋·모델·데이터·cache·실행 환경 전체가 같다고 주장하지 않는다. 키 값·부모 환경·CSRF·쿠키는 설정 영수증에 저장하지 않는다.
- 추가 P1 근거는 `app/src/features/pipeline/real.py:2738`의 `build_identity.cache_usable` 검사와 런처의 Git 커밋 전달 부재이다. 이 상태에서는 health ready와 실제 본조사 가능 여부가 달랐다. `app/실시간성능시험켜기.ps1:300`의 `Get-EvaluationCodeReceipt`가 실제 Git 저장소 경계·40자리 HEAD·실행 소스 상태를 읽고, `:485`에서 검증된 값만 APP_GIT_COMMIT으로 전달한다. 부모가 제공한 APP/RENDER 커밋 값으로 대신하지 않는다. app/src·analysis_engine/src·엔진이 직접 쓰는 도구·런처·의존성·배포 계약에 staged/unstaged 또는 Git이 추적하지 않은 변경이 있으면 유료 기동 전에 종료 2로 중단한다. 루트 전체 문서·키·DB·평가 산출물은 해당 검사 대상이 아니다.
- 실제 임시 Git 저장소 시험에서 HEAD와 자식/snapshot 값의 일치, 부모의 임의 SHA 무시, 추적 소스 수정과 미추적 실행 소스의 기동 거부를 검증했다. 평가 HTTP 도구도 `code_identity_verified`가 없는 기존 유료 서버 영수증을 GET 전에 거부한다. 조정자가 검토한 변경을 로컬 커밋하고 서버를 재시작해야 실제 비교를 시작할 수 있다.
- 진행 복구 `app/src/web/routers/analysis.py:2050`은 결과 라우트의 영속 게이트 판정을 재사용한다. `progress_page:2062`, `progress_api:2121`에서 권한 판정 후 읽으며, 진단 저장소 장애는 503, 비용 미확정 FAILED는 임의의 GATE_STOPPED로 승격하지 않는다.
- `app/src/web/tests/test_stopped_evidence_guidance.py:294`는 재시작 상태에서 결과·진행·API를 함께 비교한다. `:329`는 미확정 비용 실패의 오분류 방지, `:355`는 DB 대기 중 다른 coroutine이 실행되는지, `:388`은 영속 진단 실패의 503 구분을 검사한다. 추가 권한 회귀는 `test_report_access_routes.py`를 사용한다.
- 동일한 실제 ASGI·임시 SQLite 재현을 수정 후 다시 실행했다. 메모리 작업이 없는 상태에서 결과 **200 / 진행 303 / 진행 API 200**으로 일치했으며, 0.45초 EXCLUSIVE 잠금 중 20ms 타이머는 수정 전 **0.466~0.471초**에서 수정 후 **0.035초**로 줄었다. SQLite 3.50.4·DELETE journal·외부 통신 차단 조건은 같다. 진행 요청 하나에 대한 근거이며 전체 미들웨어의 잠금 내성을 증명하지 않는다.
- 정기 readiness는 이미 `app/src/web/routers/health.py:130`, `:155`에서 저장소/공급자 조회를 `asyncio.to_thread`로 이동한 상태였다. 이번 수정 범위를 진행 함수로 제한했고 전역 인증 미들웨어 리팩터링은 하지 않았다.
- 도구 단위 검증은 공급자 없는 HTTP MockTransport, 별도 SQLite, Windows PowerShell 가짜 자식, 실제 DPAPI 왕복을 사용한다. PDF 경로 시험에는 pypdf로 만든 빈 PDF를 사용하므로 실제 보고서의 디자인·내용 품질 증거가 아니다. 웹 시험 환경의 기존 PDF 대역도 동일한 한계가 있다.

## 첫 열람 KPI 표시 수정 — 2026-09-08 후속

이 후속은 관리자가 측정 불가를 현재 0% 또는 자료 수집 대기로 오해하는 P2 표시 결함만 다룬다. 첫 열람 생산 경로가 닫힌 상태는 유지하며, 새로운 계측 POST·보고서 GET의 DB 쓰기·개인 추적은 추가하지 않았다. 과거 기록용 저장 함수나 기존 설문 저장 경로도 변경하지 않았다.

- `app/src/features/admin_dashboard/kpi.py:31`의 `KpiSummary.measured_responses`·`within_target`은 현재 미측정에서 `None`이며 `measurement_status`는 `paused`이다. `historical_measured_responses`·`historical_within_target`은 기존 기록을 성공적으로 조회한 경우에만 정수다. 조회한 실제 빈 기록은 `0`, 조회 불가는 `None`이므로 두 상태가 다르다.
- `kpi.py:185`의 `summary`는 기존 기간 필터와 휴지통 제외를 유지하면서 저장된 행을 과거 참고값으로 반환한다. 최근 7일 안의 행이나 과거 5건 이상이 있어도 현재 계측으로 승격하지 않는다.
- `app/src/web/routers/dashboard.py:505`의 `_kpi_context`를 첫 화면·HTML 새로고침·회원 화면이 함께 쓴다. `:1165`의 회원 기간별 조회도 같은 미측정 계약을 적용하고, KPI 과거 자료 조회만 실패하면 다른 관리자 항목을 계속 표시한다.
- `app/src/web/templates/_admin_today.html:25`와 `admin_members.html:102`는 **계측 중단 · 미측정**, **과거 측정 기록**, **현재 집계가 아닙니다**를 표시한다. 과거 기록의 비율을 현재 0%·100%로 표시하지 않으며, 별점 만족도의 정상적인 자료 수집 대기 문구는 유지한다. 이 KPI 카드에는 개인 이메일이나 보고서별 열람 시각을 추가하지 않았다.
- 무과금 표적 회귀 **18개 통과, 3.96초**: `features/admin_dashboard/tests/test_kpi.py` 전체, `web/tests/test_dashboard.py`의 미측정·과거 0/5건·저장소 실패·응답시간 명칭·회원 GET/설문·관리자 접근 시험, `web/tests/test_legacy_readonly_delivery.py` 전체를 실행했다. 과거 5건에서 목표 충족 0건인 상황도 현재 0%로 표시하지 않으며, 기간 집계·휴지통 제외를 유지한다. 회원 GET 뒤 KPI 행과 사건은 모두 0건이고, 기존 공개 GET의 쓰기 연결 차단 시험도 통과했다.
- 시험은 격리 SQLite·기존 Demo/승인 대역으로 실행했고 실제 API·유료 호출·커밋·배포는 하지 않았다. 기존 Starlette/httpx 지원 종료 예정 경고 1개는 남아 있다. 이는 KPI 표시와 읽기 경계의 근거이며 전체 보고서 내용 품질이나 새로운 계측의 정확성을 검증한 결과가 아니다.

남은 결정은 실제 첫 열람 계측을 다시 제품에 도입할지 여부다. 이 변경은 수집을 재개하거나 관리자에게 현재 응답시간 실적이 존재한다고 주장하지 않는다. 일반 인증 미들웨어·다른 라우트의 SQLite 동기 대기 등 상단 표의 별도 보류 항목도 이번 KPI 수정 범위에 포함하지 않았다.

## 실제 비교시험 실행 방법

앱 폴더에서 조정자가 키를 메모리로 전달한 런처를 사용한다. 키 값은 명령 인자나 문서에 적지 않는다.

```powershell
.\실시간성능시험켜기.ps1 -Port 8027 -EnablePaidProviders -ConfigurationProfile ProductionObserved20260908 -PerRunExpectedCostCapKrw 2000 -DailyExpectedCostCapKrw 20000
```

런처가 표시한 격리 RUNROOT의 DB와 설정 영수증을 지정한다. 사전검사는 GET만 수행하며 새 유료 POST를 보내지 않는다.

```powershell
..\.venv\Scripts\python.exe -B -m tools.evaluate_companies --origin http://127.0.0.1:8027 --storage-db RUNROOT/storage.db --settings-snapshot RUNROOT/evaluation-settings.json --manifest tools/fixtures/evaluation-companies-confirmed-2026-09-08.json --batch-id baseline
```

통합·신원 확인·실행 소스 커밋을 마치고 실제 실행하도록 지시받은 뒤 같은 명령에 `--execute`를 붙인다. 접수 후 조회 중단은 같은 회차에서 `--resume-only`로 복구한다. 이는 새 POST를 보내지 않는다. 같은 회사의 의도적인 반복 시험은 별도 `--batch-id repeat1`을 명시해야 하며 새 유료 호출이다. 같은 회차의 manifest·설정·DB 신원·서버 CSRF 지문·소스가 바뀌면 자동 재개를 차단한다.

`app/tools/evaluate_companies.py`는 기존 `CanonicalPilotRunner`의 HTTP form/법인 확인/정합 원장 읽기만 재사용한다. 25사 전용 실행기나 내부 engine을 호출하지 않으며, 자체 manifest와 명시적인 --execute 범위를 사용한다. 실행 흐름은 GET / → POST /confirm → DART 후보 정확 일치 → 확정 법인명·번호 검증 → POST /run → GET /api/progress → GET /result → GET /download/pdf 이다.

- POST 전에 디스크 동기화한 체크포인트를 남긴다. 전송 응답이 불확실하면 전체 회차를 중단하며 자동 재전송하지 않는다. 본조사 ID를 이미 확보한 실패는 진단과 중단 상태를 추가 보관한다.
- 커널 파일 잠금으로 같은 폴더의 동시 평가를 막는다. 프로세스 강제 종료 시 잠금은 운영체제가 해제한다. 현재 사용자 Windows DPAPI로 공개 보고서 접근 쿠키만 암호화해 보관하고, HTML·CSRF·승인 토큰은 산출물로 내보내지 않는다.
- DB는 읽기 전용 연결로 조회한다. lifecycle·최종 outcome·report 존재·비용 이벤트 합계·잔여 inflight 정합성이 확인돼야 다음 회사로 간다. 미정산·모순·누락은 0원 성공으로 바꾸지 않는다.
- 산출물은 RUNROOT/http-evaluation-artifacts/회차/회사 아래 `report.json`, 공식 `report.pdf`, 존재하면 `public-projection.json`, `diagnostics.json`, `ledger.json`, `metrics.json`이다. 실패에서는 보고서/PDF가 없을 수 있고 `interruption.json`으로 상태를 남긴다. 원본 진단과 DPAPI 파일은 내부 평가 자료이며 공개 게시 대상으로 취급하지 않는다.
- 설정·manifest·DB 불변 신원·서버 지문과 소스/설치 버전 영수증을 체크포인트에 묶는다. 출력하는 콘솔 요약에는 원격 응답 본문·쿠키·키 값을 포함하지 않는다.
- 서버 재시작으로 CSRF와 provenance seal이 바뀌면 기존 회차의 자동 재개를 허용하지 않는다. 저장소의 영속 보고서 복구 기능과 유료 시험의 서버 신원 보존은 서로 다른 계약이다. 불확실한 POST를 계속하려면 조정자의 비용·lifecycle 조사와 명시적인 새 회차 판단이 필요하다.

## 회사군과 비교 기준

`app/tools/evaluation-company-proposals.json`은 확인 전 제안 목록이고, 실제 실행용은 `app/tools/fixtures/evaluation-companies-confirmed-2026-09-08.json`이다. 무료 DART 기업목록·기업개황 응답으로 8사 법인번호·법인명·주소를 확정했다. SM/JYP의 종목명 약칭을 법인명으로 그대로 사용하지 않고 실제 `(주)에스엠엔터테인먼트` / `(주)제이와이피엔터테인먼트` 응답을 기록했다. 조회 방법은 [DART 기업개황 개발가이드](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019002)와 [고유번호 개발가이드](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019018)를 따랐다.

앱솔브랩을 소프트웨어 후보로 분류했던 최초 가정은 실제 기업개황의 업종 20423·celimax 홈페이지와 불일치해 폐기했다. 추가 두 업종은 **화장품 제조(앱솔브랩)**와 **전기 제어·산업 자동화(인텍에프에이, 업종 2812)**로 정정했다. 아래는 2023-09-09~2026-09-08 DART 공시 유형 A/F의 API 집계이며 정정 공시를 포함할 수 있다. 이 수치로 기업 규모나 뉴스 부족까지 확정하지 않는다.

| 회사 | 확인한 DART 번호 | 정기 공시 A | 외부감사 관련 F |
|---|---|---:|---:|
| 우리은행 | 00254045 | 13 | 0 |
| SM | 00260930 | 15 | 0 |
| HYBE | 01204056 | 18 | 0 |
| YG | 00613318 | 14 | 0 |
| JYP | 00258689 | 12 | 0 |
| 멀티캠퍼스 | 00425351 | 13 | 0 |
| 앱솔브랩 | 01921621 | 0 | 1 |
| 인텍에프에이 | 00674966 | 0 | 3 |

모든 조회는 HTTP 200이었고, 실제 조회 자료 없음(status 013)은 0으로 기록했다. 인증·접속 장애라면 null로 남기도록 무료 확인 도구 `app/tools/confirm_evaluation_manifest.py`를 구성했다. 근거 원본 필드는 확정 manifest의 `identity_evidence`에 저장했고 인증키·API 요청의 인증 쿼리는 저장하지 않았다.

자료 가용성은 ‘회사 규모’의 대용치로 삼지 않는다. 각 회사에 동일 기준일·기간을 적용해 공식 공시/재무·공식 웹·유효 뉴스 본문·최신성의 가용성을 먼저 기록하고, 가용성이 비슷한 회사끼리 장별 충실도·출처 정확성·오류율을 비교한다. 은행·교육·엔터테인먼트·추가 두 업종에서 동일한 증거량 대비 품질 차이가 나는지 본다. 특정 업종의 섹션 숫자를 일률 할당량으로 강제하지 않는다.

| 비교 항목 | 자동 산출 | 사람이 확인할 것 |
|---|---|---|
| 뉴스 검색·본문 조회·본문 사용 | 저장된 단계의 검색/본문읽기/본문사용기사수 | 검색 누락, 실제 주장에 유용한지, 사용·제외 사유 |
| 목록 독립 기사·사건 | 목록기사수·실질사건수 관측값, 없으면 null | 재송고·사건 중복, 제목만 다른 동일 기사 |
| 날짜 | 기사 기간별 관측값, PDF 날짜 문자열 계수 | 발행일/사건일 혼동, 허위 정밀 날짜, 최신성 |
| 중복·회사 혼입 | 긴 동일 줄 중복, manifest의 비교 회사명 출현 횟수 | 정상 경쟁사 언급인지 실제 회사 혼입인지 |
| 장별 충실도 | 장별 본문 줄·표·empty_reason | 질문에 답하는 사업/구조/재무 설명, 근거 대비 누락 |
| 자료 부족과 장애 | 수집 실패 코드·제외 사유·최종 게이트·미관측값 | 자료 부재와 timeout/인증/차단/검수 실패를 구분 |
| 비용·시간 | 정합 원장 비용, 요청별 AI 이벤트, lifecycle 시간, HTTP 전체 경과 | cache hit/miss·예약·정산과 반복 실행 비용 차이 |

baseline PDF 경로를 회사 항목의 `baseline_pdf`에 지정하면 동일 pypdf 추출 함수로 구판/신판의 날짜·중복·문자·페이지 지표를 계산한다. 조정자가 지정한 Downloads의 기존 PDF 6개를 실제로 읽었으며 결과는 `app/tools/fixtures/evaluation-baseline-metrics-2026-09-08.json`에 SHA-256과 함께 저장했다. 원본 PDF는 수정하지 않았다. 실행 전 baseline을 읽을 수 없는 경우에는 유료 POST 전에 중단한다. 구판 PDF에는 내부 JSON/수집 진단이 없을 수 있으므로 그 지표는 비교 불가로 남긴다. 자동 `human_quality_judgment`는 항상 null이며, 기사 수가 많거나 기존 테스트가 통과했다는 이유로 사람이 검수한 품질 합격을 만들어 내지 않는다.

## 시험 기록

최초 감사의 255개 표적 시험과 후속 45개 표적 시험은 각각 당시 소스에서 통과했다. 중간 검증은 런처·HTTP 도구 독립 31건, HTTP 도구·무료 법인 확인·진행 복구·권한 41건이었다. 실제 Git 신원 보호까지 추가한 **최종 전체 소유 범위 표적 시험은 63건 통과(46.51초)**했다. 중간 묶음은 중복이므로 합산하지 않는다. Windows 실제 가짜 자식 실행·실제 임시 Git 커밋·미커밋 기동 차단·DPAPI 왕복·불확실 POST 재전송 차단·공식 PDF 경로·미정산 차단·기존 PDF 사전검사·권한 격리를 검사했다. 공유 워크트리 동시 편집 중 발생한 composer 상수 import 오류는 담당자가 복구한 후 웹 시험을 재개해 통과했으며 해당 파일을 임의 수정하지 않았다.

재현 명령은 앱 폴더 기준 다음과 같다. 모든 pytest는 별도 임시 DB를 사용했으며 실제 유료 공급자를 호출하지 않았다.

```powershell
..\.venv\Scripts\python.exe -B -m pytest tools/tests/test_realtime_evaluation_launcher.py tools/tests/test_evaluate_companies.py tools/tests/test_confirm_evaluation_manifest.py src/web/tests/test_stopped_evidence_guidance.py src/web/tests/test_report_access_routes.py -q -p no:cacheprovider --basetemp TEMP_PATH
```

외부 유료 호출은 0건이다. 무료 DART 실조회와 baseline 원본 읽기는 별도로 수행한 준비 작업이며 보고서 품질 합격을 의미하지 않는다. 최종 회귀 이후 두 CLI의 콘솔 인코딩만 UTF-8로 고정하고 --help 실행으로 한국어 출력을 확인했다.

## 최초 읽기 전용 감사 기록 — 수정 전 근거 보관

이하의 “현재”·결함 설명·줄 번호는 HEAD `28d89132` 시점의 조사 기록이다. 후속 해결 여부는 위 상태표를 우선한다.

# 코드·기능 품질 감사 — 뉴스 수집·렌더 외 영역

감사일: 2026-09-08 / 읽은 HEAD: `28d89132` / 담당 작업: `task_2c1a320e8c7f`

워크트리 코드는 수정하지 않았다. 외부 네트워크를 차단하고 별도 임시 DB에서 두 결함을 재현했으며, 표적 회귀 시험 255건을 실행했다. 유료 provider, 운영 서버, 실제 사용자 DB, 배포는 호출·변경하지 않았다. 후속 메시지에서 유료 시험 허용을 전달받았지만 이 감사에서는 비교 실행기를 찾고 정확성 조건을 정리하는 데 그쳤다. 실제 PDF 5개와 뉴스 내용 감사는 조정자가 담당하므로 중복 판정하지 않았다.

경로는 저장소 루트 `C:/Users/jh-wo/.claude/workspace/기업분석2` 기준이다. 심각도 P1은 유료 검증 또는 서비스 운영에 큰 영향을 주어 다음 실험·운영 전 우선 수정할 항목, P2는 사용자 안내·관측·문서의 확인된 기능 결함이다. 이번 범위에서 새 P0나 인증 우회는 입증하지 못했다. 이것은 보안 전체가 안전하다는 판정이 아니다.

## 확인한 결함과 수정 우선순위

| 순서 | 심각도 | 결함 | 사용자·운영 영향 | 증거 수준 |
|---|---|---|---|---|
| 1 | P1 | 실시간 평가 실행기가 현재 필수 인증키와 운영 스위치를 누락 | 유료 평가 서버 시작 실패 및 운영과 다른 경로 비교 | allowlist·startup 검증 정적 추적 및 값 부재 확인 |
| 2 | P1 | 일반 async 요청에서 SQLite 잠금을 동기 대기 | 한 요청의 DB 지연이 같은 worker의 다른 요청과 liveness까지 지연 | 실제 ASGI 요청 + SQLite 잠금 재현 |
| 3 | P2 | 저장된 게이트 중단을 진행 주소에서 복구하지 않음 | 완료된 중단 사유를 잃고 불필요한 재조사 유도 | 임시 DB + 실제 HTTP 경로 재현 |
| 4 | P2 | 첫 열람 계측이 끊겼는데 응답시간 KPI를 계속 표시 | 설문을 받아도 ‘자료 모으는 중’이 해소되지 않음 | 전체 호출점 추적 + 기존 통합 시험 통과 |
| 5 | P2 | 공개 README가 배포·부분보고서 정책과 불일치 | 커밋의 배포 효과와 실제 제공 품질을 잘못 판단 | 문서와 설정·생산 분기 직접 대조 |

### 1. 유료 평가 실행기는 필수 인증키를 지워 기동에 실패하며 운영 스위치도 누락한다

- **기동 차단:** 실행기의 provider allowlist는 `DART_API_KEY`, `ANTHROPIC_API_KEY`, 구형 `NAVER_CLIENT_ID/SECRET`만 받는다(`:60-65`). 현재 앱은 `NCP_APIGW_API_KEY_ID/KEY`를 필수로 요구한다(`app/src/web/evaluation_mode.py:49-53`). 부모에 NCP 키가 있어도 삭제되고 파일 입력도 무시되므로, 유료 모드의 `validate_startup_configuration()`이 `EvaluationConfigurationError`를 낸다(`:167-171`, 호출점 `web/runtime.py:386`). 이는 단순히 뉴스 없는 결과가 나오는 문제가 아니라 **현행 launcher의 유료 서버가 정상 시작하지 못하는 결함**이다. 실제 비밀값을 읽거나 출력하지 않고 변수 이름과 제어 흐름만 대조했다.
- `app/실시간성능시험켜기.ps1:334-340`은 부모 환경을 OS 필수값과 승인된 provider 키만 남기고 지운다. `:408-412`는 `ENGINE_V2`와 `REPORT_RELEASE_MODE`만 다시 주입한다. 최종 허용 목록 `:414-427`에도 아래 스위치가 없다.
- `REVENUE_TABLE_V2`, `NEWS_INTAKE`, `TYPED_DART_COLLECTOR`, `EVIDENCE_RECLASSIFY`, `NEWSROOM_DATE_AI`는 실행기 전체에서 환경변수 리터럴이 0건이다. `-ProviderEnvFile`도 키 목록에 없는 이름을 무시한다(`:287`). 부모 환경을 미리 설정하는 방식으로도 전달되지 않는다.
- 운영 Blueprint는 `render.yaml:58-59`에서 `REVENUE_TABLE_V2=1`을 명시한다. 새 매출표 스위치는 미설정이면 OFF다(`app/src/core/revenue_table_switch.py:56-63`). 따라서 `-EngineV2 -ReleaseMode FULL`로 실행해도 적어도 매출표 파서는 운영 설정과 다르다.
- 나머지 스위치가 실제 배포에서 현재 켜졌는지는 이번에 조회하지 않았다. 다만 이 실행기로 해당 ON 분기를 시험할 수 없다는 사실은 확정이다. 특히 사용자 요청인 뉴스 적극 보강의 비교에 이 실행기를 그대로 쓰면 대상 경로가 꺼진 결과를 재게 된다.
- **먼저 고칠 것:** NCP 필수 인증키를 launcher·startup·시험에서 동일하게 맞추고, 평가 실행기에 비밀과 분리된 정확한 기능 스위치 입력을 추가하고, 실험 시작 전에 운영과 평가의 커밋·엔진 모드·출고 모드·기능 스위치·요청 모델·cache namespace를 비교하는 영수증을 남긴다. 현재 launcher 결과를 운영판 개선의 증거로 쓰면 안 된다.

### 2. 일반 요청의 DB 잠금이 단일 이벤트 루프를 점유한다

- `app/src/web/routers/analysis.py:2105-2110`의 async 진행 API는 `request_helpers.require_report_access`를 동기 호출한다. `app/src/web/request_helpers.py:308-314` → `app/src/features/report_access/logic.py:106-121` → `storage_db.connect_readonly_existing()`로 이어진다.
- SQLite 연결은 동기이며 busy timeout은 5초다(`app/src/features/storage/db.py:593-597`, `app/src/features/storage/constants.py:23-24`). async 결과 라우트도 저장소 조회를 직접 수행한다(`app/src/web/routers/reports.py:649-650`). 앱은 worker 1개로 배포된다(`app/Dockerfile:75`).
- **재현:** 임시 DB를 bootstrap하고 유효한 PUBLIC grant를 발급했다. 별도 스레드에서 SQLite `BEGIN EXCLUSIVE`를 0.45초 유지하는 동안 실제 ASGI `/api/progress/{id}` 요청과 20ms 비동기 타이머를 실행했다. Python의 외부 소켓 연결은 차단했다.
- **결과:** 로컬 SQLite `3.50.4`, `journal=delete`, 진행 API HTTP 410, 20ms 타이머 실측 **0.466초**. 잠금을 기다리는 동안 다른 coroutine도 실행되지 않았다. 이 재현은 410 응답 자체가 결함이라는 주장이 아니라 DB 대기가 event loop 전체로 전파된 증거다.
- 영향은 DB 잠금·느린 디스크가 발생할 때 나타난다. 운영의 실제 지연 빈도나 배포 SQLite 버전은 측정하지 않았다. 하지만 DELETE journal은 프로젝트가 의도적으로 지원하는 동작이므로 무효한 환경 가정이 아니다(`db.py:64-84`).
- `/readyz`는 이미 같은 문제를 알고 `asyncio.to_thread`로 읽기 검사를 분리한다(`app/src/web/routers/health.py:123-129`). 일반 요청은 이 보호가 없다.
- **먼저 고칠 것:** 접근 판정과 관련 DB 트랜잭션 전체를 제한된 worker thread로 이동한다. SQLite connection을 thread 사이로 나누지 않는다. 실제 DB 잠금 중 `/healthz`와 다른 요청의 응답 지연을 함께 검증한다.

### 3. 결과 페이지가 복원할 수 있는 게이트 중단을 진행 페이지는 ‘없음’으로 바꾼다

- `app/src/web/routers/reports.py:680-699`는 저장된 lifecycle·final gate 진단으로 `GATE_STOPPED`를 복원한다.
- 반면 `app/src/web/routers/analysis.py:2057-2090`과 `:2113-2171`은 메모리 Job이 없을 때 성공한 보고서와 강제 중단 기록만 찾는다. 정상적으로 게이트 중단된 실행의 영속 진단은 조회하지 않고 410을 반환한다.
- **재현:** 기존 `test_stopped_evidence_guidance.py`의 안전한 `_stopped_job`으로 `internal_evidence_contract` 중단을 만들고 `recording.record_run`으로 DB에 기록했다. 메모리 Job을 넣지 않고 같은 브라우저 grant로 실제 경로를 조회했다.
- **결과:** `/result/{id}`는 **200**, `/progress/{id}`는 **410**, `/api/progress/{id}`는 **410 `job_unavailable`**. 기록과 권한이 있는데도 진행 탭은 결과 주소로 가지 못한다.
- 사용자가 진행 화면을 띄워 둔 동안 중단 직후 서버가 재시작되거나, 오래된 메모리 Job이 정리되면 명확한 내부 오류·자료 부족·접속 장애 설명을 잃는다. `job_runtime.py:702-708`은 만료된 완료 Job을 제거한다.
- 기존 회귀 `app/src/web/tests/test_stopped_evidence_guidance.py:294-316`은 재시작 뒤 **결과 주소만** 검사해 이 누락을 잡지 못한다.
- **먼저 고칠 것:** 두 진행 경로도 권한 판정 후 영속 terminal 상태를 확인하고, 이미 종료된 gate stop이면 `/result/{id}`로 연결한다. 성공·게이트 중단·기술 실패·실제 없는 ID를 구분하는 하나의 복구 계약이 필요하다.

### 4. 회원 첫 열람 KPI의 생산 호출 경로가 모두 닫혔다

- `_render_result_page` 호출점은 두 곳이며 둘 다 `pure_delivery_read=True`를 전달한다(`app/src/web/routers/reports.py:660-667`, `:714-721`).
- 회원 첫 열람 기록은 `if not pure_delivery_read` 안에만 있다(`:2156-2164`). LINK 보고서 열람 사건도 동일 조건이다(`:2142-2150`). 별도 POST 계측 호출점은 찾지 못했다.
- 설문 저장은 `dashboard_kpi.record_first_survey`를 호출하지만(`app/src/web/routers/dashboard.py:1091-1099`), 첫 열람 행이 없으면 `None`으로 종료된다(`app/src/features/admin_dashboard/kpi.py:122-127`).
- **실행 증거:** 기존 실제 회원 열람·설문 통합 시험 `app/src/web/tests/test_dashboard.py:160-193`을 실행해 통과했다. 시험 자체가 열람·설문 후 측정 **0건**을 기대하며, `:188-189`에 측정 불가라고 적혀 있다.
- 그런데 관리자 화면은 ‘3분 내 구분점 응답’과 측정 건수를 계속 표시한다(`app/src/web/templates/admin_members.html:102`, `_admin_today.html:25`). DB 읽기 자체는 성공하므로 `dashboard.py:649-657`은 5건 미만인 ‘자료 모으는 중’ 분기로 들어간다. 현 경로에서는 새 데이터로 5건을 채울 수 없다.
- **먼저 고칠 것:** 공개 GET의 불변성을 보존하면서 계측이 필요하다는 명시적 제품 결정 전에는 해당 지표를 ‘현재 계측 중단’으로 표시한다. 기존 privacy/read-only 의도를 확인하지 않고 열람 쓰기를 되살리지 않는다. 현재 숫자를 회사별 이해도·품질 판정에 쓰지 않는다.
- **후속 상태:** 관리자 표시와 nullable 측정 계약은 이 문서 상단의 KPI 후속에 따라 수정했다. 위 줄 번호와 재현 결과는 최초 감사의 보관 근거이며, 계측 재개는 여전히 보류다.

### 5. 대표 문서가 실제 배포·품질 정책과 반대다

- **배포:** `README.md:159-160`, `app/README.md:87-88`은 `autoDeployTrigger: off`이며 수동 배포 전에는 반영되지 않는다고 설명한다. 실제 `render.yaml:26-32`는 main 커밋 시 자동 배포인 `commit`이고, `deploy/README.md:89`도 이를 설명한다. Blueprint Sync나 Dashboard 실제 적용 여부는 외부 조회하지 않았으므로 설정 파일의 사실까지만 확정한다.
- **부분 보고서:** `docs/architecture/system-overview.md:43-48`는 FULL에서 일부만 공개하지 않고 전체를 멈추며 SHADOW만 부분본을 허용한다고 설명한다. 실제 `app/src/features/pipeline/real.py:3260-3275`는 FULL 사전검사의 DART fallback 조건에 따라 요청 모드를 SHADOW로 바꾼다. `official_evidence_preflight.py:281-320`에는 웹 일시 장애·일부 의미칸 부족·독립 문서 하한 부족의 세 갈래가 있다.
- 부분 출고 전환 자체는 최근 사용자가 확인된 내용을 제공하라고 정한 방향과 맞을 수 있으므로 **이를 무조건 제거하라는 결함 보고가 아니다**. 생산 코드와 대표 문서·파일럿의 ‘부분 출고 금지’ 전제가 서로 다르다는 문제다.
- **먼저 고칠 것:** 배포의 현재 효과와 FULL 요청에서 조건부 부분 제공되는 정책을 문서에 반영한다. 요청 모드·실제 적용 모드·부분 전환 사유를 평가 데이터에서 별도 기록해야 회사별 품질이 같은 기준인지 비교할 수 있다.

## 실행 경로와 책임 지도

1. `web/main.py`가 middleware·라우터를 조립하고, `web/runtime.py:44-64`의 `make_pipeline`이 demo/real을 선택한다. Real에는 `ProductionOfficialEvidenceCollector`를 주입한다.
2. `web/routers/analysis.py:1253`에서 후보·법인을 확정하고, `:1906`의 `start_run`이 일회용 확인·접근·비용·슬롯을 확인해 Job을 시작한다. 회원·LINK·PUBLIC 접근 정본은 `features/report_access`이며 `request_helpers.py:308`을 공유한다.
3. `web/job_runtime.py:1074-1104`가 worker thread에서 파이프라인을 실행한다. 사용자 취소와 실제 provider 종료를 구분한다(`:599-630`). 전체 절대 마감과 종료 정리가 있다(`:633-690`, `:1114-1155`).
4. `features/pipeline/real.py:1338`은 저수준 엔진을 동적으로 적재한다. `:2827`부터 회사정보·공공기관/상장/외감 판정·공시·재무·공식 원문 사전검사·생성 신원·캐시/중복 작업 조정·composer를 연결한다. 동적 엔진의 실제 파일은 `analysis_engine/tools/run_pilot.py`다.
5. 캐시는 단순 회사명 키가 아니다. 배포·엔진·모드·회사·DART 접수번호/재무와 공식 snapshot의 신원을 묶고, `web/generation_singleflight.py` 및 `features/report_delivery/singleflight.py:36-66`의 비용 통장별 lease로 같은 생성 작업을 합친다. FULL 재사용에는 출고 권위 재검사가 있다(`generation_singleflight.py:232-273`).
6. 비용은 `web/paid_runtime.py:886`, `:998`에서 phase admission/정산을 소유하고, Job은 내부 AI 사용량을 성공·실패 모두 기록한다(`job_runtime.py:1272-1309`). 고객 청구는 자동출고 뒤 별도 결정한다. 평가에서는 provider attempt 비용과 고객 청구/성공건 차감을 구분해야 한다.
7. 생성은 `pipeline/real.py:5269`의 composer 조립으로 이어지고, `shared/report_quality/assessment.py:442`와 `report_standard`가 구조·사실·근거·품질을 검증한다. 자동출고·Delivery/artifact를 확정한 후에야 완료 상태가 열린다(`job_runtime.py:1553-1564`, `web/routers/reports.py:1419`). 공개 GET은 영속 Delivery를 읽는다(`reports.py:649-667`).
8. 실행·실패 진단은 `web/recording.py`, `features/observability`, `features/final_gate_diagnostic`이 저장한다. 별도 단계 기록과 최종 사유를 함께 보아야 한다. 하나의 최종 gate code로 수집·분류·작성·저장 원인을 대체하면 안 된다.

구조상 큰 조립 파일은 `pipeline/real.py` 7,641행, `job_runtime.py` 2,930행, `analysis.py` 2,228행, `reports.py` 2,584행이다. 이것만으로 결함이라고 판정하지 않았다. 다만 변경 영향은 한 feature 폴더만으로 닫히지 않는 상태다. `docs/adr/0001-feature-oriented-structure.md:47` 이후는 기존 직접 import를 점진적으로 줄이는 예외를 설명하며, 현재 사용자 AGENTS의 새 cross-feature import 금지와 함께 읽어야 한다. 광범위 재구조화보다 위 결함의 경계 함수를 소유 기능으로 옮기는 작은 수정이 우선이다.

## 자료가 적은 회사와 산업 간 품질

- 법인 판정은 공식 사이트 수집보다 먼저 일어난다. DART 법인 코드가 없으면 NOT_FOUND이고(`pipeline/real.py:2858-2860`), 비상장 회사는 감사보고서 또는 DART 재무자료가 없으면 즉시 거부된다(`:2937-2966`, `analysis_engine/src/features/judgment/logic.py:75-81`). 공식 홈페이지에 충분한 비재무 자료가 있어도 이 판단에 아직 참여하지 않는다. 모든 규모·산업이라는 최신 요구에 대해 **지원 범위 공백**으로 기록해야 한다. 자료가 실제로 부족하다는 검증과 동일한 판정은 아니다.
- 감사보고서는 있지만 DART 재무 API가 없는 비상장사는 생성 신원을 별도로 만들되 캐시 재사용을 막는 분기가 있다(`pipeline/real.py:3282-3303`, `shared/report_source_identity.py:171-182`). 반복 조사 비용은 재무 API가 있는 기업과 달라질 수 있다. 현재 이 정책을 단독 비용 결함으로 판정하지는 않았다. live 표본에서 반복 실행의 실제 호출 수·원가를 따로 재야 한다.
- FULL 품질 하한은 실질 주장 40개·검증비율 50%·독립 문서 8개다(`shared/report_quality/constants.py:63-65`). 규모만 다른 회사에 같은 기준을 적용하는 것은 가능하지만, 하나의 상세 사업보고서와 여러 짧은 웹 문서의 실제 정보량이 같다는 뜻은 아니다. 독립 문서수만으로 ‘자료 가용성 비슷함’을 정의하면 안 된다.
- 순수 자료 부족, 전송 실패/차단, 문서 파싱 실패, 읽었지만 의미칸 분류 실패, 작성 품질 미달을 각각 나눈 후 같은 자료 수준끼리 비교해야 한다. 최근 fallback은 이 중 일부를 실제 적용 모드 변경으로 흡수하므로 모드 기록이 필수다.

## 기존 비교 도구와 사용할 회사군

### 도구

- **고정 회사군:** `features/pilot_evaluation/manifest.py:30-143`에 P01~P25의 이름·법인번호·주소가 고정되어 있다. 상장 10, 비상장 공시 8, 경계 7이다. 경계군은 YG/JYP/SM/NAVER처럼 별칭·법인 식별이 어려운 회사도 포함하므로 ‘경계=자료 빈약’으로 읽으면 안 된다.
- **실제 웹 실행:** `app/tools/run_canonical_pilot25.py` → `CanonicalPilotRunner`가 `/confirm`→선택 법인 검증→`/run`→진행→결과와 SQLite 원장을 결속한다. 체크포인트로 중복 유료 시작을 피한다. preflight도 로컬 DB에 checkpoint binding을 쓰므로 순수 read-only가 아니다(`runner.py:674-706`). 이번에는 실행하지 않았다.
- **현재 유료 선택 제한:** CLI는 P01~P10만 허용한다(`run_canonical_pilot25.py:33-48`, `manifest.py:149-151`). Runner 생성자는 승인 ID 집합 주입을 지원하지만(`runner.py:288-317`), manifest 검증은 여전히 25개 구성에 묶여 있고, 사용자 품질 집계기는 P01~P10만 본다. 새 승인과 별개로 실행·집계 계약을 함께 확장해야 한다.
- **기존 합격선:** 10/10 법인 정확성, 8/10 완성, 자동/사용자 판정 일치 90%, 완료 P90 30분, 평균 내부 AI 원가 300원·P90 500원(`contract.py:27-38`). 잘못된 법인·부분본·중대 사실/인용/수치 오류 자동 통과가 있으면 실패한다. 최신 부분 제공 정책에 맞게 완성/부분을 분리해야 하며 현재 10개 상장사 집계만으로 전체 규모·산업 품질을 주장할 수 없다.
- **무과금 보조:** `shadow_harness.py:1-26`은 P11~P25의 저장 관측값·옛자료 판정불가·자료 없음만 요약한다. 새 수집이나 현재 엔진의 실시간 품질을 검증하지 않는다. `quality_store.py:1030-1082`는 사용자 판정과 DB 증거를 결속한다.
- **임의 회사 실행기:** `app/실시간성능시험켜기.ps1`은 격리 데이터·명시 유료 opt-in·단건/일일 상한을 갖는다. 기본 per-run 2,000원·daily 5,000원은 요청 제한이며 실제 한 건 가격이 아니다. 앞의 스위치 불일치를 먼저 해결해야 운영과 같은 경로를 시험한다.

### 권장 비교 구성

| 묶음 | 회사 후보 | 확인할 차이 |
|---|---|---|
| 실제 사용자 재현 | SM, HYBE, YG, JYP, 멀티캠퍼스 | 같은 날짜/빌드에서 기존 5개 PDF 증상을 재현하고 수정 전후 비교 |
| 금융 | 우리은행 + 기존 P14 넥스트증권 또는 P15 글로벌머니익스프레스 | 금융업의 공시 분류·재무 계정·공식 홈페이지 자료 처리 |
| 규모가 다른 제조 | P02 현대자동차 또는 P04 SK하이닉스 ↔ P09 로보스타, P17 인텍에프에이 | 동일 의미칸의 정보가 있을 때 회사 규모가 출력 품질을 좌우하는지 |
| 소프트웨어·비상장 | P08 플래티어 ↔ P13 앱솔브랩, 기존 회귀의 인이지 사례 | 공개 재무 API 유무·감사보고서 형식·제품/수익구조의 의미 분류 |
| 실제 자료 부족 | 위 후보를 무료 사전조사한 뒤 선정 | 작은 회사라는 이유로 선입견을 두지 않고 실제 문서·의미칸 가용성으로 선정 |

각 회사의 현시점 자료 가용성은 이번에 외부 조회하지 않았다. 따라서 위 표는 대표성 후보이며 ‘작아서 자료가 없다’거나 ‘현재 동일 자료 수준이다’라는 사실 주장이 아니다. P14/P15도 우리은행과 사업구조가 같다는 뜻이 아니라 금융업 파서의 하위 업종 비교용이다.

### 비교에 반드시 남길 값

1. 회사 규모/업종은 표본 설명으로 두고, **무료 사전자료 상태**(문서 고유 ID·내용 hash·날짜 범위·원문 길이·장별 의미칸 준비·미분류 수·재무 3개년/감사 fallback·공식/언론 출처 종류)를 먼저 고정한다.
2. 생성 빌드와 모든 기능 스위치, 모델, 요청 모드와 실제 모드, 원문 snapshot, cache hit/miss, owner/waiter를 기록한다. 변경 전후는 가능하면 같은 원문 snapshot으로 재생해 수집 변동과 모델/코드 변동을 분리한다.
3. 장별로 법인 정확성, 원문으로 뒷받침되는 문장 비율, 핵심 사실 누락, 날짜/인과/수치/단위 정확성, 일반론·중복·타사 정보 혼입, 자료 부족과 접속 장애의 올바른 표시를 독립 검수한다.
4. 성능은 성공만이 아니라 중단까지의 지연·재시도 횟수·전체 비용을 기록한다. provider attempt 원장, pipeline 합계, customer charge, 캐시 재사용 비용을 나눠 대조한다. 평균만 쓰지 말고 각 실행 결과와 P90을 함께 본다.
5. ‘비슷한 품질’은 동일한 원문 가용성 구간에서의 장별 근거 충족·오류율·누락률로 평가한다. 안전하게 중단한 회사와 내용이 부실한데 통과한 회사를 같은 성공률 숫자로 합치지 않는다.

## 시험과 한계

- 묶음 A **125 통과, 5.56초**: Job lifecycle, runtime failure diagnostics, stopped evidence guidance, response security, 회원 열람·설문 KPI 1건, singleflight, budget state machine, official evidence preflight.
- 묶음 B **130 통과, 20.42초**: report access routes, request limits, release mode cache isolation, storage cache, spend store.
- Python `-B`, pytest `-p no:cacheprovider`, 저장소 밖 임시 basetemp와 APP_DATA_ROOT/STORAGE_DB_PATH/관측 경로를 사용했다. provider 환경키를 제거하고 외부 socket.connect를 차단했다. Windows asyncio용 loopback만 허용했다. 각 실행의 기존 Starlette/httpx 지원 종료 예정 경고 1개는 기능 실패가 아니었다.
- 최초 재현 스크립트는 `C:/Users/jh-wo/AppData/Local/Temp/company-quality-audit-20260908-task2/재현.py`에 있다. 실행하면 새 임시 DB를 만들고 HTTP 상태와 DB 잠금 중 타이머 지연을 출력한다. 저장소나 운영 DB는 수정하지 않는다. 수정 후 재실행 값은 이 문서 상단에 별도로 기록했다.
- 모든 test 개수 통과는 보고서 내용 품질 증명이 아니다. 웹 공통 fixture는 다른 기능 시험을 위해 PDF 후보·승인 대역을 제공한다(`app/src/web/tests/conftest.py:26` 이후). 실제 회사 원문→문장→수치→채널 결과의 품질을 확인하려면 별도 원문 기반 비교가 필요하다.
- 비용 원장 손실, cache/idempotency 우회, 인증 우회는 이번 표적 실행에서 새로 입증되지 않았다. 외부 공급자 장애·실제 장기 비용·배포 부하까지 검증했다는 주장은 하지 않는다.
- 배포 제약은 코드에도 명시돼 있다: Render 단일 instance/영속 디스크, 플랫폼 종료 유예와 앱 drain 차이(`render.yaml:15-24`, `job_runtime.py:124`, `runtime.py:417-419`), 외부 복구세대 업로드/독립 서명 adapter 미완성(`docs/architecture/system-overview.md:121-122`와 `render.yaml` 말미), 일반 public forwarded evidence verifier 및 image supply-chain verifier 부재(`deploy/README_배포_절차.md:7-12`, `:111` 이후). 이를 새로 발견한 보안 침해로 과장하지 않았지만, 실제 운영 승인·재해복구 완료의 증거도 현재 로컬 시험만으로 만들 수 없다.

## 다음 수정의 권장 순서

유료 비교용 실행 설정 일치를 먼저 확보하고, 실제 PDF 감사의 중대 오류 수정과 병행해 남은 DB 대기 격리와 정책 문서를 점검한다. 첫 열람 KPI의 오해 표시는 수정했으며, 실제 계측을 재개할지는 별도 제품 결정으로 남긴다. 자료 가용성이 비슷한 규모·업종별 짝은 같은 원문 snapshot과 같은 실행 설정으로 비교한다. 판정 임계값을 낮추거나 전체 파이프라인을 다시 짜는 제안은 이번 확인된 결함의 해결 근거가 아니다.

추가 무과금 재현: launcher가 허용하는 DART·Anthropic·구형 NAVER 이름에 가짜 값을 넣고 NCP 두 이름을 비운 상태에서 validate_startup_configuration을 직접 실행했다. 실제 provider를 호출하지 않고 EvaluationConfigurationError와 NCP_APIGW_API_KEY_ID, NCP_APIGW_API_KEY 누락 안내가 나왔다. 저장한 재현.py도 다시 실행하여 결과 200/진행 410과 20ms 타이머 0.471초를 재확인했다.
