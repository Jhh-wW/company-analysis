# HTTP 기업 비교시험·뉴스 계량 독립 검토

## 수정 후 독립 재검증 — 2026-09-08 14:36 KST

후속 작업: `task_280e83c5df64` / 배정: `ctx_9947e5854531`.

**초기 P2 두 건은 아래 오프라인 재현 범위에서 해결을 확인했다.** 코디네이터가 수정한 제품 소스를 읽고 기존 임시 재현을 새 계약에 연결했다. 제품 소스·기존 시험은 수정하지 않았으며, 이 보고서와 저장소 밖 재현 파일만 갱신했다. HEAD는 여전히 `28d89132d180cc2d812725136730c6202d29df34`이고 미커밋 공유 소스에 대한 결과이므로 clean 커밋·배포·유료 실측 완료를 뜻하지 않는다.

### 호출 예약·보충 검수 결과

재현은 이제 `real._MeteredEngine.available_provider_calls(reserved_calls=shared.report_recovery.MAX_TOTAL_AI_CALLS)`로 여유를 계산하고 `core.news_research_adapter.prepare_news_research(max_analysis_calls=...)`를 실제 호출한다. 그 함수가 만든 `NewsResearchSession.collect()`를 통해 검색 정책과 같은 정책으로 본문을 분석한다. 숫자 5를 재현 코드에서 직접 뉴스 상한으로 설정하지 않았다.

| 사례 | 이전 사용 | 배정/실제 뉴스 분석 | FULL 작가 | FULL 검수 | 전체 허용 | 결과 |
|---|---:|---:|---:|---:|---:|---|
| 기본 FULL | 0 | 5 / 5 | 9 | 1 | 15 | 보고서 반환 |
| 두 장 보충 FULL | 0 | 5 / 5 | 11 | 2 | 18 | 보충 Reviewer까지 완료 |
| 이전 1회 뒤 두 장 보충 | 1 | 4 / 4 | 11 | 2 | 18 | 보충 Reviewer까지 완료 |
| 이전 5회 뒤 두 장 보충 | 5 | 0 / 0 | 11 | 2 | 18 | 보충 Reviewer까지 완료 |

제품 카운터와 실제 FULL 보충 분기는 그대로 사용하고 외부 검색·본문·AI 응답만 오프라인 대역으로 제공했다. 모든 보충 사례의 생산 장부는 작가 11회·검수 2회, 합계 13회였으며 차단된 역할은 없었다. 원래 보충 Reviewer에서 발생하던 `RequestCallLimitReached`는 재현되지 않았다.

추가로 5회·4회·0회 정책의 snapshot 정책 지문이 서로 다른 것을 확인했다. 검색 이후 분석 정책만 1회 늘려 `session.collect()`를 호출하면 `ValueError`로 분석 전에 거절됐고 분석 카운터는 증가하지 않았다. 0회 정책은 정상 생성되며 뉴스 분석 callback은 실제로 0회였다.

분석 범위의 제한도 보존된다. 이 대역의 기본 사례는 **기사 본문 24건을 읽고 20건을 분석**했으며, 뉴스 분석 5회 상한 때문에 남은 4건은 분석하지 않았다. 이전 사용 1회 사례는 본문 읽기 20건·분석 16건, 뉴스 0회 사례는 본문 읽기 4건·분석 0건이었다. 모두 `상한사유=[analysis_budget_exhausted]`, `완전성=partial`, `자료부족=false`, `실패=null`로 기록되어 상한 중단을 자료 부재나 공급자 장애로 표기하지 않았다. **24기사 전체 분석 또는 이전과 동일한 조사 깊이가 보장됐다고 해석하면 안 된다.**

현재 구현의 13회 보호는 FULL의 기본 작성·검수와 허용된 최대 보충에 맞는 계약이다. FULL 외 SHADOW/ENFORCE에도 여유를 남기지만 flat 경로의 모든 파싱 재시도와 선택적 개선이 성공한다는 보장은 아니다. 이전 사용량이 이미 5회를 넘으면 뉴스 상한을 0으로 낮춰도 FULL 최대 13회를 모두 확보할 수 없다는 산술적 제한도 그대로다. 이번 완주 재현은 이전 사용량 0·1·5회 조건에 대한 검증이며 API 비용 한도·응답 시간·모델 품질의 실측 보장은 아니다.

### 뉴스 지표 재추출 결과

기존 `reproduce.py`가 현재 `real.py`의 AST에서 실제 생산 단계명 `8_뉴스_본문활용`을 읽도록 유지했다. 실제 오프라인 `V2RunOutput`을 현재 평가기에 넣었을 때 본문 사용 **2 → 2**, 목록 기사 **2 → 2**로 일치했고, 근거별 판정·실질 사건 수·기간별 기사 수도 생산 결과와 같다는 단정을 통과했다. 소비 위치는 현재 `app/tools/evaluate_companies.py:200-218`, 허용 단계명은 `app/tools/evaluation_constants.py:10`이다. 구형 `뉴스_본문활용`의 호환성도 제한 회귀 시험에서 통과했다.

### 재검증 산출물과 시험

임시 폴더는 `C:/Users/jh-wo/AppData/Local/Temp/company-news-review-task45e`다.

- 현재 재현: `reproduce_budget.py`, `reproduce.py` — 각각 Python 종료 코드 0.
- 수정 후 결과: `budget-revalidation-results.json`, `metrics-revalidation-results.json`.
- 제한 회귀: `followup_checks.py`, `followup-checks.log` — **33 통과, 2.07초, 차단한 네트워크 시도 0회**. 평가기·뉴스 호출 예산·뉴스 신원 문맥 세 파일을 실행했다.
- 초기 결과 보존: `initial-budget-results.json`, `initial-reproduction-results.json`, `initial-reproduce_budget.py`. 아래 초기 결함 기술은 이 시점의 실패 기록이다.

두 재현과 회귀 시험 모두 시작부터 socket 연결·이름 조회·전송을 차단했다. 실제 HTTP·유료 API 호출, 커밋, 배포는 0회이며 후속 유료 실제 보고서 비교는 아직 수행하지 않았다.

| 수정 후 확인 파일 | SHA-256 — 14:36 KST |
|---|---|
| `app/src/features/pipeline/real.py` | `403bde61e8c5fd4adb131a80b8ea20b07fa7ace7a5a8c68e9ecfbb9db4791924` |
| `app/src/core/news_research_adapter.py` | `182cd802289356f451eb2cde43b2cecad33af8dff35cbb2d00ed76942eb7633b` |
| `app/src/features/news_intake/models.py` | `ea262258f5a1f739824b98c6b6f2e64c254314e7c0172f95dd887f08f9582ffc` |
| `app/tools/evaluate_companies.py` | `fdcf65ee3d6b9644008e15fb443f92e8dbdfaa5ff83956c6d8039421ee6aea0e` |
| `app/tools/evaluation_constants.py` | `3fda42fd2af27b62fc36f1f808158f0338943347e00c3f3366276534fd5ade49` |
| `app/src/shared/report_recovery.py` | `17b1f3114014e1772efe39ea6217895e86a8c13ba041bdc301356018a8a00679` |

## 초기 검토 기록 — 수정 전

검토 작업: `task_45e659b7f1ff` / 배정: `ctx_2793cb9e90d3`.

2026-09-08 14:23~14:26 KST 기준 공유 워크트리를 읽기 전용으로 검토했다. 당시 새로 확인한 결함은 **P2 두 건**이었다. 기본 FULL의 필수 호출이 항상 상한을 넘는 P1은 재현되지 않았다. 두 결함은 유료 비교시험의 관측 정확성과 조건부 보충 완주에 영향을 주어 수정·재검증을 요청했고, 그 후속 결과는 이 문서 위에 별도로 기록했다. 소스 수정, 실제 HTTP POST, 공급자 API, 커밋, 배포는 하지 않았다.

## 초기 P2 1 — 뉴스 본문 활용 지표의 생산·소비 단계명이 다르다

- 소비 위치: `app/tools/evaluate_companies.py:199-218`, 특히 200행.
- 생산 위치: `app/src/features/pipeline/real.py:5665-5667`.
- 실제 기록은 `8_뉴스_본문활용`인데 평가기는 `뉴스_본문활용`만 선택한다. 따라서 `news_body_used_count`, `independent_news_article_count`, `news_distinct_event_count`, `news_period_counts`, `news_usage_decisions`가 기록이 있어도 모두 `null`이 된다.
- 현재 `app/tools/tests/test_evaluate_companies.py`의 지표 시험도 소비자의 잘못된 문자열을 직접 만들어 넣어 이 연결 오류를 발견하지 못한다.

오프라인 FULL 작성기·검수기 대역으로 만든 실제 `V2RunOutput.news_usage_diagnostics`를 사용하고, `real.py`의 단계명을 AST에서 직접 추출하여 평가기에 넣었다.

| 관측값 | 생산 결과 | 평가 결과 |
|---|---:|---:|
| 본문 사용 기사 | 2 | `null` |
| 뉴스 목록 기사 | 2 | `null` |
| 근거별 판정 | 기록 있음 | `null` |

이 오류는 기사 수를 0으로 꾸미지는 않지만, 이번 유료 시험의 핵심 비교 지표를 매번 잃는다. 단계명을 공통 계약에 결속하고 실제 생산 단계명을 소비하는 연결 시험이 필요하다. 전체 `diagnostics.json`에는 원본 단계가 남으므로 이미 생긴 원장으로 재추출할 수 있다.

재현 파일: `C:/Users/jh-wo/AppData/Local/Temp/company-news-review-task45e/reproduce.py`.
결과 파일: 같은 폴더의 `reproduction-results.json`.

## 초기 P2 2 — 뉴스 6회 뒤 두 장 보충의 필수 재검수가 호출 상한에 막힌다

- 요청 상한: `app/src/core/constants.py:304`의 실제 값은 **18**이다. `real.py:1030` 부근의 16번째 차단 주석으로 15회라고 해석하면 안 된다.
- 뉴스 상한: `app/src/features/news_intake/constants.py:349-356`의 본문 24기사, 묶음 4기사, 분석 최대 8회.
- 뉴스 분석 위치: `app/src/features/news_intake/collection.py:66-96`, 요청 카운터: `app/src/features/pipeline/real.py:796-818`, 실제 공통 경계: `real.py:1033`.
- FULL 1차 작성·검수: `app/src/features/composer/pipeline.py:1014-1078`.
- 승인된 두 장 보충 작성·검수: 같은 파일 `1413-1459`.

기본 FULL은 작가 9회와 묶음 검수 1회로 10회이며, 뉴스 8회와 합쳐도 18회다. 다만 FULL에는 1차 검증 후 최대 두 장을 다시 작성하고 반드시 재검수하는 정식 복구 경로가 있다. 이 경로는 3회를 더 필요로 한다.

실제 `collect_search_snapshot` / `collect_from_snapshot`에 최근 16건·그 이전 4건·더 이전 4건을 입력했다. 검색·본문·분석 응답만 대역으로 공급하고, 분석과 작성·검수는 같은 제품 `_MeteredEngine.reserve_provider_call()`을 사용했다. 같은 주제의 서로 다른 숫자를 갖는 유효 본문이므로 주제 다양성 조기 종료를 충족하지 않고 24건을 읽는다. 보충 대상은 기존 `_RecoveringPacketWriter`의 `identity`, `business_model` 두 장이다.

| 경로 | 뉴스 분석 | 작가 | 검수 | 전체 허용 | 결과 |
|---|---:|---:|---:|---:|---|
| 기본 FULL | 6 | 9 | 1 | 16 | 보고서 반환 |
| 두 장 보충 FULL | 6 | 11 | 1 | 18 | 보충 검수에서 `RequestCallLimitReached` |

보충 검수는 19번째 호출이 되어 차단된다. 검증하지 않은 본문을 내보내지는 않지만, 이미 뉴스 분석·기본 작성·보충 작성까지 비용을 쓴 뒤 정상 복구가 끝나지 않는다. 이 재현은 실제 공급자 전송과 원가를 측정한 것이 아니라, 실제 수집 스케줄·실제 FULL 복구 분기·실제 요청 카운터의 충돌을 확인한 것이다.

권장 계약은 전역 18회를 유지하면서 FULL 기본 10회와 최대 보충 3회를 우선 확보하고 뉴스의 가용 호출을 잔여 예산과 결속하는 것이다. 보충 시작 시에도 필요한 작가 수와 재검수 1회를 함께 확보해야 한다. 뉴스 분석 5회 제한만 설정하면 조사 범위가 줄 수 있으므로 묶음 구성·상한 중단 진단까지 함께 검증해야 한다.

**묶음 크기 5만으로 24기사/5회를 보장할 수 없다.** 현재 기간 창마다 남은 묶음을 분석하므로 `ceil(16/5) + ceil(4/5) + ceil(4/5) = 6`회다. 또 본문 최대 길이 12,000자 다섯 개만으로 60,000자가 되어 회사 문맥과 프롬프트를 더하면 60,000자 입력 상한을 넘고 `analyze_batch`가 다시 분할한다. 출력 상한 5,000토큰에 기사별 판정과 발췌가 모두 들어가는지도 검증해야 한다.

재현 파일: `C:/Users/jh-wo/AppData/Local/Temp/company-news-review-task45e/reproduce_budget.py`.
결과 파일: 같은 폴더의 `budget-results.json`. 각 시나리오의 본문 읽기 24회, 분석 6회, 분석 실패 `null`, 네트워크 시도 0회가 기록된다.

## 추가 요청: 모드별 호출 예약

실제 `real.py:5383-5394`는 FULL만 typed packet을 만든다. 그러므로 시험 대역에서 모든 모드에 packet을 넣은 호출 수를 운영 SHADOW/ENFORCE의 호출 수로 일반화하면 안 된다.

| 모드와 입력 | 기본 작성·검수 | 추가 호출과 실패 처리 |
|---|---|---|
| FULL / typed packet | 작가 9 + 묶음 검수 1 | 최대 두 장 보충 작가 2 + 필수 재검수 1; 요약은 추출식 0회; 장별 파싱 재시도 없음 |
| SHADOW / 실제 flat 입력 | 작가 9 + 본문 의미검수 1 | 장별 작가 파싱 재시도와 의미검수 파싱 재요청 가능; 재작성·도식 검수·요약 작성/검수는 요청 한도에서 저하 가능 |
| ENFORCE_NO_PARTIAL / 실제 flat 입력 | 작가 9 + 본문 의미검수 1 | flat 파싱 재시도 가능; 요약은 추출식 0회; FULL 보충 회차 없음 |

근거는 `composer/logic.py:1398-1400`, `composer/verify.py:953-957,1184,1443-1471`, `composer/pipeline.py:487-539,1110-1138,1210-1222`, `composer/diagram_check.py:409`다. flat 경로의 본문 첫 검수는 필수이고 재시도 횟수는 입력에 따라 달라지므로, 단순히 10회만 남겨 두고 이후 작가 재시도를 무조건 허용하면 필수 검수 몫까지 소모할 수 있다. 아직 작성하지 않은 장과 첫 검수 몫을 우선 보호하고 재시도·선택적 개선은 잔여 한도에서만 허용하는 구분이 필요하다. 이 추가 모드 분석은 정적 검토이며 새 운영 실패로 집계하지 않았다.

## 요청한 나머지 경계의 확인 결과

1. **실제 SHA와 dirty 증빙.** 시작·후속 확인의 HEAD는 모두 `28d89132d180cc2d812725136730c6202d29df34`다. 현재 런처·`real.py` 등은 수정 상태이며 평가기·상수와 새 뉴스 파일은 미추적 상태다. 따라서 이 공유 워크트리를 그 커밋 그대로 실행한 clean 소스라고 주장할 수 없다. 런처 `300-328`행은 실제 Git HEAD와 실행 경로의 porcelain 상태를 읽고 유료 모드에서 dirty를 차단한다. 이번 검토에서는 커밋하거나 이 차단을 우회하지 않았다.
2. **설정 증빙.** 런처 `575-598`행이 비밀을 제외한 설정 JSON과 SHA-256을 저장한다. `ProductionObserved20260908`은 `ENGINE_V2=1`, `REPORT_RELEASE_MODE=FULL`, `NEWS_INTAKE=1`, 나머지 네 스위치 0이다. 기본 `RepositoryContract`와 운영 관측 profile은 구분되어 있으며 `production_parity=not_verified`가 유지된다. 이번 검토는 서버를 띄우지 않았으므로 실제 새 자식 프로세스의 적용 설정이나 배포 일치를 인증한 것이 아니다.
3. **정상 HTTP·중복 POST·재개.** 평가기 `start()`는 POST 전에 `identity_submission_uncertain`, `run_submission_uncertain`를 저장하고 성공 접수 뒤 `running`으로 바꾼다. 불확실 상태는 재전송을 차단하며 `resume_only`는 `running`의 GET 조회만 재개한다. 파일 잠금, 설정/manifest/서버/DB 신원 결속, DPAPI 쿠키 보관을 확인했다. `/confirm`의 최초 workflow는 `request_helpers.py:1445-1454`에서 소비하며 후보 확인은 `analysis.py:1331`에서 토큰을 꺼낸다. `/run`은 `analysis.py:1953-1995`에서 확인 시도를 검증·소비하고 실행 상태를 바꾼다. 이번 범위에서는 새 중복 과금 결함을 입증하지 못했다.
4. **뉴스 owner·계량 순서.** 검색 snapshot 준비는 `real.py:3513-3560`, owner/cache 조정은 `3575-3579`, grounding은 `3856-3866`이다. 기본 analyzer는 `6177-6187`에서 요청의 계량 client로 `_ask()`를 호출한다. 최종 `messages.create()`는 `1033-1037`의 호출 카운터와 `ensure_paid_phase()`, `1074-1085`의 예산·attempt 뒤 실제 gateway로 진입한다. `generation_singleflight.py:1158-1182`는 owner/bypass 외 상태와 예약 실패를 차단한다. 뉴스 grounding이 owner 전에 유료 모델로 나가는 새 결함은 확인되지 않았다.
5. **report/PDF 추출.** `finish()`는 원장과 회사 일치를 확인하고 공식 PDF 경로를 GET하며 PDF 서명·본문 추출·해시를 확인한다. 저장된 `report.json`의 `sections.lines/prose_lines/tables`는 실제 저장 스키마와 맞는다. 다만 날짜·반복문장·타법인 단어는 PDF 전체의 문자 패턴 계수이고 기사별 사실 검증이 아니다. 코드가 이 제한과 사람 판정 미완료를 명시하는 것은 적절하다. 기사 본문/목록/제외 사유의 자동 비교는 위 P2 1을 고쳐야 한다.

검토 중 공유 소스의 `partial` 안내가 바뀌었다. 초기에는 검증 미완료나 상한 중단까지 접속 장애로 안내하는 문구가 있었으나, 재현 당시 최신 FULL 결과는 “일부 뉴스 후보의 확인을 끝내지 못해 …”로 출력됐다. 이 항목과 사용자에게 전달받은 profile 문맥·Reviewer 거절 목록·공식 약칭 수정은 새 미해결 결함으로 중복 보고하지 않는다. `EVIDENCE_RECLASSIFY=1`의 기존 owner 이전 호출 문제도 이번 두 결함과 구별하며 기본 OFF 경로의 신규 차단 근거로 사용하지 않았다.

## 시험과 재현 명령

저장소 밖 임시 스크립트만 추가했다. 앱 소스와 기존 시험 파일은 수정하지 않았다. Python의 socket 연결·이름 조회·전송 이벤트를 시작부터 전부 차단했고 실제 HTTP POST와 유료 API는 0회다.

```powershell
python -B 'C:/Users/jh-wo/AppData/Local/Temp/company-news-review-task45e/reproduce.py'
python -B 'C:/Users/jh-wo/AppData/Local/Temp/company-news-review-task45e/reproduce_budget.py'
python -B 'C:/Users/jh-wo/AppData/Local/Temp/company-news-review-task45e/run_checks.py'
```

최종 제한 시험: **146 통과, 3 제외, 14.81초, 차단한 네트워크 시도 0회**. 결과는 같은 임시 폴더의 `final-checks.log`다. 대상은 평가기, 뉴스 신원 문맥, 파이프라인 뉴스 연결, 뉴스 표 연결, 본문 활용, 수집 시험 여섯 파일이다. 처음에는 네트워크 차단 상태에서 Windows asyncio가 내부 소켓을 만드는 웹 표시 시험 세 개가 실패했고, 제품 결함으로 집계하지 않고 마지막 실행에서 제외했다. 환경 기본 Python에 일부 패키지가 없어 저장소의 기존 `.venv/Lib/site-packages`를 사용했으며 설치나 네트워크 접근은 하지 않았다.

위 검증은 오프라인 대역 결과다. 사용자 PDF 여섯 개에 대한 수정본의 실제 새 보고서를 생성하거나 품질 개선을 확정한 것이 아니다. 공유 워크트리가 동시에 수정되고 있으므로 아래 지문은 관측 시점의 파일을 식별하며 이후 수정의 통과를 뜻하지 않는다.

## 주요 파일 지문 — 2026-09-08 14:23:30 KST

| 경로 | SHA-256 |
|---|---|
| `app/tools/evaluate_companies.py` | `dc87d17d050dc905971da728cee8954a1614071f0897e8cf543e09f6b5f6ef77` |
| `app/tools/evaluation_constants.py` | `3edcfba38fcdebffdb56b25f25eade2fd76a4ffbdbc9aca76a2a1748047c4696` |
| `app/실시간성능시험켜기.ps1` | `22b8bd2d8b9a49f3dc010c8dbb9a90583a902074b08ae3faf7d00d64a7fe9ce5` |
| `app/src/features/pipeline/real.py` | `c32933d665563c537e6e21e60975a75939b7a1278c810fbc9a2a2114f4d8dfce` |
| `app/src/core/constants.py` | `06f9dab6f34881568d70b062714c5ece731e1818fc98ac5e40cc3c7d1ae0f26b` |
| `app/src/features/news_intake/collection.py` | `45246955101b03be7f8924b3d51bc1112dcd2ea242eb208c6c64d736061caa19` |
| `app/src/features/news_intake/constants.py` | `850806a92af6298ca421bb855666dfddacd86da5ff85d87afe0d7655a96e78a8` |
| `app/src/features/composer/pipeline.py` | `426a639e450e85b789ca9a428e0e2ad7b23989d0e6d424571b620d71024cc9af` |
