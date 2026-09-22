# 뉴스 낭비 중단·추가 매체 독립 검토와 후속 실측 기준

2026-09-22 · 읽기 전용 독립 검토 · 기준 HEAD `61b7303a940b1541ec86f307fc7741449952ad8f` 및 당시 미커밋 diff

**판정: 현재 분석 예산 중단과 `ddaily.co.kr` 추가에서 중대 회귀 결함을 발견하지 못했다. 실제 보고서 품질 합격·시간 반감·기사 채택 개선은 미검증이다.** 총괄이 전달한 575검사 통과는 재실행하지 않았다. 테스트 실행, 애플리케이션 실행/import, 네트워크·유료 API·서버 접근, 실제 DB 열기, `.env`·credential·DPAPI 읽기, 브랜치 변경·커밋은 하지 않았다. 수정한 파일은 이 문서 하나다.

조정자는 이번 Task 범위를 현행 예산 중단·매체 추가·합격기준·평가 명령으로 확정했다. 이후 뉴스 검증과 최초 스키마 변경은 준비 후 별도 Dispatch에서 검토한다. 따라서 아직 도착하지 않은 병렬 수정에 대한 승인으로 이 문서를 사용하면 안 된다.

## 1. 현재 diff의 경계 점검

검토한 핵심은 `app/src/features/news_intake/collection.py:114,225,287,397`, `constants.py:404,481`, `tests/test_analysis_budget_prefetch.py`다. 추가로 `body_prefetch.py`, `search_snapshot.py`, 정책 모델, 기존 기간·동시 수집·매체 시험과 `app/src/core/news_research_adapter.py`를 읽었다.

| 경계 | 코드로 확인한 동작 | 판정·제한 |
|---|---|---|
| 분석 예산 0, 후보 존재 | `plan_more()`가 `lane.submit()` 및 `CallLease` 예약보다 먼저 중단한다. 본문·분석 호출은 모두 0이고 `analysis_budget_exhausted`가 남는다. | 올바르다. `자료부족=false`, `캐시재사용가능=false`가 되어 확인하지 않은 기사를 자료 없음으로 저장하지 않는다. |
| 예산 0, 후보 없음 | 계획 반복문 자체에 진입하지 않는다. 검색이 완전하면 자료 없음·재사용 가능 판정이 가능하다. | 분석할 후보 자체가 없는 경우이므로 앞 행과 구별해야 한다. 미확인 매체·검색 실패가 있으면 기존 불완전성 경계가 우선한다. |
| 예산 마지막 호출, 추가 후보 존재 | 다음 `plan_more()` 진입 시 중단한다. 성공적으로 검증한 `all_excerpts`, `document_hashes`는 지우지 않는다. | 분석 가능한 마지막 묶음 뒤 새 본문을 보내지 않는 변경이다. |
| 마지막 호출로 모든 후보 처리 | `plan_index == len(ranked_candidates)`라 새 중단 분기에 들어가지 않는다. 다음 기간도 비어 있으면 허위 상한 사유를 붙이지 않는다. | 새 시험은 후보 4개/호출 1회의 기사·조각 동등성과 캐시 가능을 확인하도록 작성됐다. |
| 다음 기간·날짜 보정 이월 | 다음 기간에 실제 후보 또는 이월 본문이 남으면 분석 잔여 횟수를 다시 확인한다. 이미 읽은 이월 본문을 재요청하지 않는다. | 앞 기간의 검증 기사 증거는 유지한다. 예산 소진 뒤 검증하지 못한 이월 자료는 채택했다고 세지 않는다. |
| 동시 실행·기사 순서 | `batch_slack = batch_size - len(batch) - in_flight - pending_carried`; 결과는 deque 순서로 소비한다. 분석 횟수는 소비 스레드 한 곳에서만 증가한다. | 배치가 차서 분석할 때 진행 중 본문이 없도록 설계돼 있어 새 예산 검사와 분석 카운터 사이에 추가 경쟁 조건을 발견하지 못했다. |
| 예외·불완전 응답 | 공급자 치명 예외는 즉시 전파되고 일반 분석 실패는 실패 사유로 남는다. 원문 불일치·응답 누락·본문 실패·잘림은 불완전성에 남는다. | 새 중단 조건이 실패를 성공이나 자료 없음으로 바꾸는 경로를 발견하지 못했다. |
| 최종 캐시·증거 | 예산 사유는 중복 제거 후 진단에 남고 캐시를 차단한다. 기사별 원문 전체 SHA-256, 채택 조각, 출처 URL 구성 코드는 변경되지 않았다. | 기존 채택 근거를 보존하는 범위의 수정이다. 충분한 근거로 조기 종료한 경우 재사용 가능한 기존 의미도 유지한다. |

잔여 한계는 다음과 같다. 이는 확인된 중대 결함이 아니라 변경의 보장 범위와 시험 공백이다.

- 새 예산 시험의 `item()`은 모두 `media.example`을 쓴다. 동시 폭 3이어도 호스트 상한 1 때문에 실제 HTTP 콜백은 겹치지 않는다. 기존 `test_collection_body_concurrency.py`에는 다른 호스트 3개 역순 완료, 분석 중 진행 요청 0, 기간 경계 시험이 있다. 다만 **분석 예산 소진과 실제 다중 호스트 역순 완료를 한 시험에서 결합한 보장**은 새 파일에 없다. 후속 동시성 변경 시 이 조합을 회귀 대상으로 삼는 것이 적절하다.
- 프롬프트가 커서 `analyze_batch()`가 재귀 분할되면 한 번 읽은 배치의 일부만 남은 분석 예산으로 처리할 수 있다. 이 경우 예산 사유와 캐시 차단은 남지만, 이미 읽은 나머지 본문까지 사용된다는 보장은 없다. 새 변경은 다음 배치의 낭비를 막는 것이며 모든 미사용 본문을 제거하는 변경은 아니다.
- 이미 진행 중인 HTTP 요청은 취소할 수 없고 전체 글자·시간 예산에 걸리면 선행 미사용 요청이 남을 수 있다. 호출 수와 미사용 정산을 따로 보존하는 기존 구조를 유지한다.
- 분석 호출 0회에서 뉴스 검색까지 0회가 되는 것은 아니다. `prepare_news_research()`는 먼저 검색 스냅샷을 만든다. 이번 목표인 불필요한 **본문 요청** 중단과 구별한다.
- 기존 마감 초과가 `analysis_budget_exhausted` 또는 `body_budget_exhausted`로 표현되는 관측 한계는 남는다. 호출 횟수 소진과 시간 소진을 별도 실측하려면 잔여 호출 및 경과 정보도 같이 읽어야 한다.

## 2. 디지털데일리 추가와 캐시 경계

총괄이 공식 홈페이지 하단의 발행 주체·신문 등록번호 `서울아00039`·등록일 `2005-09-06`을 확인했다고 전달했다. 이 검토자는 외부 사이트를 재조회하지 않고 그 확인에 따른 코드 변화만 점검했다. `Platum`은 여전히 보류 상태다.

- `ADDITIONAL_PUBLISHER_REFERENCES`에 도메인·매체명·공식 확인 URL 한 행을 추가하며 회사별 예외를 두지 않는다. 해당 도메인은 `TRUSTED_PUBLISHER_DOMAINS`에 편입되고 `NewsCollectionPolicy.trusted_publisher_domains`에 들어간다.
- `search_snapshot.policy_digest()`는 정책 버전과 `asdict(policy)` 전체를 해시한다. 따라서 **버전 문자열을 별도로 올리지 않아도 도메인 목록 추가로 정책·스냅샷 지문이 변한다.** `collect_from_snapshot()`는 회사·기준일·정책·내용 결속을 본문 요청 전에 검사하므로 구 정책 스냅샷과 신 정책을 혼용하면 거절한다.
- `host_matches()`는 정확한 호스트 또는 `'.' + expected`로 끝나는 하위 호스트만 허용한다. `www.ddaily.co.kr`·하위 호스트는 허용하지만 `ddaily.co.kr.attacker.example`·`fakeddaily.co.kr`은 허용하지 않는다. 본문 최종 URL도 같은 신뢰 정책으로 재검사한다.
- 매체 허용은 본문 접근 성공·법인 일치·원문 인용·실질 사건 검증을 대체하지 않는다. 새 `test_verified_publishers.py`의 ddaily 정상 후보·유사 도메인·robots 실패·다른 회사 거절 사례가 이 의도를 고정한다. 이번에 실행한 것은 아니다.
- 실행 중 모듈 상수만 바꾸는 운영은 대상이 아니다. 고정 소스의 새 프로세스에서 새 기본 정책을 생성해야 한다. 예산 상한과 분석/기사 검증 계약은 이번 목록 추가로 늘어나거나 완화되지 않는다.

## 3. 원본과 개선본을 비교할 합격기준 10개

기준 문서는 `2026-09-22-final-report-feedback.md`, `2026-09-22-wrtn-production-news-findings.md`, `2026-09-22-two-pdf-quality-defects.md`다. 현대글로비스 원본은 9쪽·395.12초·1,157.37원이며, 뤼튼 원본은 5쪽·외부 언론 채택 0건이다. 이 값은 당시 결과일 뿐 후속 코드의 결과가 아니다.

| 번호 | 구체적인 합격기준 | 필요한 개선 전후 증거 |
|---|---|---|
| 1 | 같은 법인·기준일·회계기간·단위로 비교하고 공시 수치 오류 0건. 현대글로비스 고객소재지 매출 합계 `29,566,409,337천원`에 투자계획을 섞지 않는다. 뤼튼 영업수익 `30.7→471.2억원`을 해당 연도에 연결하고 2024년 비교열의 미감사를 표시한다. | 공시 접수번호·원문 표·표지 KPI·4장 표/문장·재무 지문 대조. |
| 2 | 출처가 있는 매출·손익 변화 최소 한 개를 4장 산문으로 설명하고, 확보 연도 수와 제목을 일치시킨다. 표가 있는데 “비어 있다”거나 확인하지 않은 3개년 변화라고 쓰지 않는다. | 실제 4장 산문/표, 계산식과 기초값, 원문 인용 번호. |
| 3 | 검증된 사건 근거가 있으면 2장 사업 구조를 안내문과 표만으로 끝내지 않고 수익원·고객·거래 구조를 설명한다. 6·8장의 자료 이동 안내는 실제 도착 장·문장으로 추적돼야 한다. | 장별 근거 ID·최종 문장·표 및 중복 제거 후 장 내용. 근거가 없으면 정직한 부족 안내. |
| 4 | 1·2·5·9장을 회계정책 상용구로 채우지 않고 8장에 이사회 일반 규정만 남기지 않는다. 구체적인 직원 교육·보상·업무 절차는 보존한다. | 뤼튼의 수익인식·금융자산 정책과 현대글로비스 이사회 문장의 전후 위치 및 정상 교육 근거 보존. |
| 5 | 기준일 이전 목표를 현재의 예정 사실로 단정하지 않는다. 2025년 사업 개시 목표는 과거 발표 또는 현시점 재확인으로 구별한다. 회사 귀속 부산항 시설 계획은 “해석”만 붙여 무인용 통과시키지 않는다. | 문장 시제·목표일·발표일·최신 재확인 원문, 해당 주장에 직접 연결된 인용. |
| 6 | 뉴스 0건의 원인을 검색 없음·본문 실패·검증 실패·예산 소진으로 구분한다. 뤼튼은 200 검색 행·56 후보·20 시도·15 본문·3 분석·0 채택의 옛 흐름과 비교하고, 개선을 주장하려면 새로 채택된 실제 기사 최소 1건의 법인/정확 원문/실질 사건을 제시한다. | 단계별 진단과 기사별 입력/검증 결과. 등록 도메인 증가나 검사 통과만으로 채택 개선 판정을 내리지 않는다. |
| 7 | 같은 협약을 다룬 두 기사를 두 사건으로 세지 않는다. 원문·해석을 구별하고 모든 구체적 회사 사실의 인용이 본문과 부록에 연결된다. 동일 문장이 표지·여러 장·보도표에서 반복돼 정보량으로 부풀려지지 않는다. | 사건 키·기사 URL·장별 인용·중복 문장 전후 비교. 뤼튼 회계정책 반복과 현대글로비스 협약 중복을 별도로 확인. |
| 8 | 공식 웹 출처도 실제 제목·발표일/미확인 표시·원문 URL을 제공하고 `wrtn.io/news/#1`, `#2` 같은 가짜 위치를 만들지 않는다. 도식은 “상품→판매→고객” 일반어 또는 원문 없는 “개인 사용자”를 넣지 않는다. | PDF 링크 대상·출처 메타·도식의 각 노드/관계와 근거. |
| 9 | 최종 PDF 전체 쪽을 육안 검수해 표/인용 잘림·본문/안내 모순·억지 부록 분리로 인한 큰 공백을 해소하고 날짜·단위·문체를 통일한다. 표지 핵심 요약은 매출/손익 등 핵심 사실을 우선하며 내부 검증 용어를 노출하지 않는다. | 새 PDF, 페이지 이미지, 쪽별 결함표. 텍스트 추출 성공이나 페이지 수 감소만으로 합격 처리하지 않는다. |
| 10 | 시간·비용 주장은 실제 실행과 원장에 결속하고, 비용 불확실성·남은 예약·미정산은 없어야 한다. 시간 반감을 주장하려면 동일 입력·측정 범위에서 기준 대비 50% 이하를 확인한다. 실행 설정의 작성 폭 3과 실제 작성 폭 1을 구분한다. | 고정 커밋·설정 지문·입력 지문·실제 호출/토큰/캐시 비용·경과·보고서 SHA-256. 라이브 자료가 다르면 참고 비교로만 보고하고 품질 동등을 별도 판정. |

합격기준 중 결과물이 필요한 항목은 현재 모두 개선 후 검증 대기다. 기존 뤼튼의 외부 기사 0건을 “기사가 없었다”로 설명하거나, 원본 PDF 생성 성공을 최신 코드 품질 합격으로 바꾸어 기록하면 안 된다.

## 4. 실제로 존재하는 재사용 자료

다음 경로는 파일 목록 또는 해당 비밀 제외 파일을 직접 확인했다. 아래 `G`는
`app/.local_evaluation_runs/20260922_201111_09bd76b5b6605f66767d76fc/`, `W`는
`app/.local_evaluation_runs/wrtn-news-review-20260922/`를 뜻한다.

| 용도 | 존재 경로 | 재사용 범위 |
|---|---|---|
| 현대글로비스 최신 원본 결과 | `G/http-evaluation-artifacts/glovis_fixed/GLOVIS_SPEED/report.pdf`, `report.json`, `diagnostics.json`, `ledger.json`, `metrics.json` | 결과 비교·비용/단계 진단·저장 Report의 레이아웃 재검토. PDF SHA-256을 직접 계산해 `9c7591f5e50655aa147c0b65911aa028c8804392ac2e5b573f14a9c1f17e49ad`와 일치 확인. |
| 현대글로비스 공시 원문 | `G/analysis_engine/pilot/raw_filings/20250317001025.xml`, `20260318001205.xml`, `20260515000799.xml`, `20260814002854.xml` 및 각 `.official-urls-v1.json` | 공시 문장·표 fixture로 사용 가능. 현재 뉴스 전체 원문·모든 AI 응답의 대용은 아니다. |
| 법인 목록 | `G/analysis_engine/corpcode/CORPCODE.xml` | 저장 당시 DART 목록. 뤼튼 행의 법인번호 `01921445`, 영문명 `Wrtn Technologies Inc.` 확인. 기업개황 확인 완료 manifest와 같지는 않다. |
| 현대글로비스 실행 입력 | `app/.local_evaluation_runs/report-speed-20260922/company-confirmed.json` | `GLOVIS_SPEED`, `00360595`, 확정명 `현대글로비스(주)`, `identity_confirmed=true`의 기존 manifest. |
| 현대글로비스 앞선 순차/병렬 | `app/.local_evaluation_runs/20260922_182500_a5b86af7a52034a7296d1295/http-evaluation-artifacts/glovis_serial/GLOVIS_SPEED/`, `app/.local_evaluation_runs/20260922_183442_f5c6e9dcf8996c00833322f0/http-evaluation-artifacts/glovis_parallel/GLOVIS_SPEED/` | 당시 설정·결과와 비교. 기존 DB/세션으로 새 시험을 시작하지 않는다. |
| 뤼튼 원본 PDF | `C:/Users/jh-wo/Downloads/주식회사-뤼튼테크놀로지스-company-analysis.pdf` | SHA-256 직접 계산 결과 `afb4e622aef3a10c5d181d07ec54c408424cd5eafbdfc51e6b635495a1e5ebe1`. 기존 기록과 동일한 원본이다. |
| 뤼튼 PDF 검수 자료 | `W/claude/text.txt`, `page-1.png`부터 `page-5.png` | 기존 문장/배치 비교. 이번 리뷰가 새 전쪽 시각 검수를 수행했다는 뜻은 아니다. |
| 뤼튼 공시 발췌 | `W/claude/dart-20260414000008-income-statement.txt`, `dart-20260414000008-balance-sheet.txt`, `dart-20260414000008-notes.txt` | 손익·재무상태·주석 원문 발췌 fixture. 전체 제출 XML이 아닌 발췌임을 표시한다. |
| 뤼튼 공식 웹 목록 | `W/claude/wrtn-news-list-2026-09-22.txt`, `wrtn-news-links-2026-09-22.txt` | 저장된 목록 텍스트와 원문 URL 후보. 개별 외부 기사의 완전한 본문/분석 응답으로 취급하지 않는다. |
| 뤼튼 운영 집계 | `W/production-news-diagnostics.json` | 공개 가능한 단계별 집계. 5회 신원 실패의 개별 원문·응답을 복원할 수 없다. |

확인한 뤼튼 검수 폴더에는 재조립 가능한 `report.json`이나 전체 뉴스 요청/응답 fixture가 없었다. 현대글로비스 `report.json`에도 최종 출처·인용·장 내용은 있지만, 탈락한 후보를 포함하는 전체 검색/본문/모델 입력·응답은 보존되지 않는다. 완전한 무료 재생에 필요한 자료가 존재한다고 주장하지 않는다.

## 5. 무료 재생과 기존 평가 명령의 정확한 구분

`app/tools/evaluate_companies.py:553`의 CLI는 `--origin`, `--storage-db`, `--settings-snapshot`, `--manifest`를 필수로 받고 `--batch-id`, `--execute` 또는 `--resume-only`를 받는다. **`--replay`, `--fixture`, `--offline` 인자는 없다.** 기본 사전검사도 `operate():356-373`에서 잠금 파일·DB 신원 조회·DPAPI 세션 로드·서버 GET·체크포인트 생성을 수행한다. 무료의 의미는 새 유료 POST 0회이며, 무부수효과/서버 없는 원문 재생이라는 뜻이 아니다. 이 위험은 조정자에게 즉시 전달했다.

무료 점검에 바로 재사용 가능한 것은 위 공시/텍스트의 정적 비교와 feature 내부의 가짜 공급자 시험이다. 기존 검사 575건을 이번 검토 때문에 다시 돌릴 필요는 없다. 신규 회귀를 돌릴 때도 웹 tests에서 `app/conftest.py`를 우회하면 안 된다. 순수 함수 재현을 추가한다면 네트워크·DB 연결·비밀 파일·쓰기 차단과 `python -B`를 유지하며 본문/분석 콜백은 저장 원문 또는 메모리 가짜로 고정해야 한다.

현대글로비스 저장 보고서의 **레이아웃만** 무료 재생할 기존 함수는 `src.features.storage.reports.report_from_dict()`와 `src.features.export_pdf.logic.build_pdf()`다. 코드 위치는 각각 `reports.py:514`, `logic.py:3495`다. 이는 평가 CLI가 아니고, 새 기사 수집/검증/작성의 재생도 아니다. 현행 출력 검증이 구 저장본을 거절할 수 있으므로 스키마·봉인·품질 게이트를 삭제하거나 우회하지 말고 호환 실패로 기록한다. 뤼튼은 현 확인 범위에 대응 JSON이 없으므로 이 경로의 입력부터 부족하다.

## 6. 고정 소스의 새 격리 유료 실측 절차

아래 명령은 **총괄용 실행 안내이며 이 리뷰에서 실행하지 않았다.** 코드 통합·고정 후 사용할 기존 명령을 확인한 것이다. 두 회사의 최종 품질과 실제 비용은 생성 후 위 10개 기준으로 따로 판정한다.

1. 고정된 실행 checkout을 사용한다. 런처의 `Get-EvaluationCodeReceipt()`는 HEAD 40자리와 실행 소스/템플릿/런처/의존성 경로의 변경 없음을 확인하고 유료 실행을 막는다(`실시간성능시험켜기.ps1:326-355`). 이 검토자는 커밋·checkout을 하지 않았다. 실행 도중 편집·다른 작업의 병합을 하지 않는다. 런처의 청결 검사에 `app/tools` 전부가 들어가지는 않으므로 평가 도구도 같은 고정 소스에서 유지한다.

2. 현대글로비스는 기존 `company-confirmed.json`을 사용할 수 있다. 뤼튼은 기존 확정 manifest가 확인되지 않았으므로 별도의 새 제안 manifest를 준비하고 기업개황으로 확인해야 한다. 제안값은 `case_id=WRTN_QUALITY`, `input_name=뤼튼`, `expected_legal_name=주식회사 뤼튼테크놀로지스`, `corp_code=01921445`, `identity_confirmed=false`이며, 기존 PDF를 `baseline_pdf`로 연결할 수 있다. 정확한 회사명은 확인 도구의 기업개황 결과로 확정한다. 목록의 번호나 PDF 이름만 보고 `identity_confirmed=true`로 덮어쓰지 않는다.

   기존 신원 확인 명령은 app 폴더에서 다음과 같다. `$proposalPath`, `$manifestPath`는 총괄이 새 평가 준비 폴더에 정한 절대 JSON 경로다. 기존 승인된 부모 환경을 사용하는 형태이며 키값/환경 파일을 출력하는 명령이 아니다. 무료 DART 외부 조회를 수행하므로 완전 오프라인은 아니다(`confirm_evaluation_manifest.py:80-129`).

   ```powershell
   ..\.venv\Scripts\python.exe -B -X utf8 -m tools.confirm_evaluation_manifest --proposals $proposalPath --output $manifestPath
   ```

   manifest의 `schema_version`은 `company-evaluation-manifest-v1`; `cases`에 고유 case ID, 고유 8자리 법인번호, 입력명·확정 법인명·`identity_confirmed=true`가 필요하다. `baseline_pdf`는 절대 경로 또는 manifest 기준 상대 경로이며 실행 전에 실제로 읽어 검사한다. 새로 확정된 두 회사 manifest 또는 회사별 manifest를 그대로 보존한다.

3. 저장소 루트에서 기존 런처를 시작한다. 아래는 앞선 현대글로비스 실측과 같은 기능 스위치/병렬 설정이며 포트는 예시로 8031을 명시했다. 사용 중이면 기존 서버를 건드리지 말고 다른 빈 포트를 지정한다. 런처 자체가 사용 가능 여부를 확인한다. 필요한 공급자 환경은 총괄의 기존 승인된 환경에서 공급하며 이 검토에는 비밀 경로가 필요 없다.

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\app\실시간성능시험켜기.ps1 -Port 8031 -ConfigurationProfile Explicit -EngineV2 -ReleaseMode FULL -NewsIntake 1 -RevenueTableV2 1 -TypedDartCollector 0 -EvidenceReclassify 1 -NewsroomDateAI 0 -WriterMaxParallelCalls 3 -ProviderMaxConcurrentCalls 5 -NewsBodyFetchConcurrency 2 -PerRunExpectedCostCapKrw 4000 -DailyExpectedCostCapKrw 10000 -EnablePaidProviders
   ```

   검수 프롬프트 캐시는 이 명령에서는 기존 비교와 같이 꺼진다. 켜는 별도 비교에만 `-EnableReviewPromptCache`를 붙인다. 순차 기준군은 새 런처에서 세 동시 인자를 모두 `1`로 지정하고 나머지를 유지한다. 순차/병렬 양쪽이 같은 코드·입력이어야 하며, 설정값만 보고 실제 작성이 병렬로 수행됐다고 판단하지 않는다. 부분보고서는 기존 실측에서도 실제 작성 폭 1이었다.

   서버를 백그라운드로 실행해야 한다면 `Start-Process`에 `-WindowStyle Hidden`을 사용하고 해당 프로세스의 PID/명령/작업 폴더를 기록한다. 런처에 없는 데이터 루트 인자를 임의로 만들지 않는다.

4. 런처는 `app/.local_evaluation_runs/yyyyMMdd_HHmmss_<24자리 임의값>/`를 **매번 새로 만든다**(`:514-551`). 그 안에 `storage.db`, `observability/runs.jsonl`, `cache/tldextract/`, `tmp/`, `evaluation-settings.json`, `evaluation-settings.sha256`를 두고 `APP_DATA_ROOT`, `STORAGE_DB_PATH`, `TEMP/TMP`를 자식 환경에 결속한다. `PIPELINE=real`, loopback, workers 1이며 dotenv 자동 읽기를 차단한다. `-DeleteDataOnExit`는 검수 자료 보존이 필요하므로 사용하지 않는다. 기본 앱 데이터나 옛 `G/storage.db`를 복사·연결하는 절차는 없다.

5. 총괄은 런처가 출력한 **이번** 설정 파일의 부모 절대 경로를 `$evaluationRoot`에, 보존한 확정 manifest의 절대 경로를 `$manifestPath`에 넣는다. 자동으로 “가장 최근 폴더”를 선택하면 병렬 작업의 폴더를 잘못 잡을 수 있으므로 하지 않는다. app 폴더에서 다음 기존 명령을 사용한다. `$origin`은 실제 영수증의 origin과 같아야 한다.

   ```powershell
   $origin = 'http://127.0.0.1:8031'
   $storagePath = Join-Path $evaluationRoot 'storage.db'
   $settingsPath = Join-Path $evaluationRoot 'evaluation-settings.json'
   ..\.venv\Scripts\python.exe -B -X utf8 -m tools.evaluate_companies --origin $origin --storage-db $storagePath --settings-snapshot $settingsPath --manifest $manifestPath --batch-id quality_after_20260922
   ```

   사전검사가 성공한 **같은 서버·소스·설정·manifest·회차**에서 유료 요청을 보낼 명령은 다음이다.

   ```powershell
   ..\.venv\Scripts\python.exe -B -X utf8 -m tools.evaluate_companies --origin $origin --storage-db $storagePath --settings-snapshot $settingsPath --manifest $manifestPath --batch-id quality_after_20260922 --execute
   ```

   접수 완료 후 대기만 끊겼다면 동일한 인자의 `--resume-only`를 사용한다.

   ```powershell
   ..\.venv\Scripts\python.exe -B -X utf8 -m tools.evaluate_companies --origin $origin --storage-db $storagePath --settings-snapshot $settingsPath --manifest $manifestPath --batch-id quality_after_20260922 --resume-only
   ```

   `--execute`와 `--resume-only`는 배타적이다. 재개도 DPAPI 세션·DB·서버와 로컬 산출물 쓰기를 사용하며 무료 원문 재생이 아니다. 미접수 `pending`은 재개 모드에서 건너뛰고 불확실한 POST 상태는 재전송하지 않는다. 서버 재시작·소스·설정·manifest 변경은 기존 체크포인트 결속을 깨므로 새 batch ID를 붙여 실패를 숨기지 않는다. 새 회차는 새 유료 실행일 수 있다.

6. 결과 경로는 `$evaluationRoot/http-evaluation-artifacts/quality_after_20260922/<case_id>/`다. `ledger.json`, `diagnostics.json`, `metrics.json`과 보고서가 있을 때 `report.json`, `report.pdf`를 확인한다. FULL 저장 투영이 있는 경우에만 `public-projection.json`도 나온다. 중단은 `interruption.json`을 남길 수 있으며 `human_quality_judgment`는 자동으로 합격하지 않고 `null`이다. 기존 파일을 다시 내보내면 `.prior-N` 보존본이 생길 수 있으므로 최신 파일 존재만 보고 새 생성 완료로 판단하지 않는다.

## 7. 공개 설정 구조와 실측 해석 주의

세 기존 실행의 `evaluation-settings.json`에서 아래 허용 필드만 출력·대조했다. 비밀 파일과 DB는 열지 않았다.

| 설정 묶음 | 확인할 공개 필드 |
|---|---|
| 신원·격리 | `schema_version`, `origin`, `app_git_commit`, `execution_source_clean`, `code_identity_verified`, 설정 파일 SHA-256 |
| 기능 | `settings`: `ENGINE_V2`, `REPORT_RELEASE_MODE`, `NEWS_INTAKE`, `REVENUE_TABLE_V2`, `TYPED_DART_COLLECTOR`, `EVIDENCE_RECLASSIFY`, `NEWSROOM_DATE_AI` |
| 성능 | `performance_settings`: `REPORT_WRITER_MAX_PARALLEL_CALLS` 1~3, `PROVIDER_MAX_CONCURRENT_CALLS` 1~5, `NEWS_BODY_FETCH_CONCURRENCY` 1~3, `COMPOSER_REVIEW_PROMPT_CACHE_ENABLED` 0/1 — 모두 문자열 |
| 비용·비교 | `paid_providers_enabled`, `per_run_expected_cost_cap_krw`, `daily_expected_cost_cap_krw`, `production_parity` |

기존 순차/병렬의 커밋은 `97afe5d8505656404189d286130add1377c9edc5`, 최신 현대글로비스 원본은 `dacb48139655058fc6236ab0f1e928e31d20783e`다. 성능 영수증은 각각 `1/1/1/0`, `3/5/2/0`, `3/5/2/0`이고 세 실행 모두 `production_parity=not_verified`, 건당 4,000원·일일 10,000원이다. 오래된 영수증에 성능 묶음이 없으면 평가기는 현재 기본값으로 채우지 않는다.

4,000원·10,000원은 예상 예약 차단 기준이지 공급자 청구의 강제 상한이 아니다. 격리 DB가 다르면 일일 한도도 하나의 전역 실험 예산이라고 볼 수 없으므로 총괄이 두 회사/두 설정의 누적 원가를 별도로 합산해야 한다. 공급자·캐시·재시도·자료 변화가 섞인 단일 라이브 비교를 코드 효과의 확정 절감률로 일반화하지 않는다.

현재 결론은 **읽기 검토상 현 변경의 중대 회귀 없음, 새 결과물 품질과 성능 개선 미검증**이다. 필요한 후속 산출물은 고정 소스의 현대글로비스·뤼튼 새 PDF/보고서/단계 진단/비용 원장과 위 10개 항목의 전후 판정이다.

## 8. 후속: 실제 다중 호스트 동시성 회귀 시험

2026-09-22 · 별도 Task `task_b01da3044860`에서 시험 실행과 신규 시험 파일 편집을 승인받아, 위 읽기 검토에서 남긴 다중 호스트 동시성 공백을 메웠다. 앞 절의 “이번 검토에서 실행하지 않았다”는 최초 읽기 검토 시점의 기록이다. 이번 후속에서는 제품 코드를 변경하지 않고 `app/src/features/news_intake/tests/test_analysis_budget_concurrent.py`를 추가했다.

검사 결과는 **새 시험 3건 통과, 1.41초**다. 총괄이 이미 확인한 593검사는 재실행하지 않았고 이 3건과 합산해 새 전체 검사 결과로 제시하지 않는다. 시험이 관측한 값은 다음과 같다.

| 사례 | 실제 최대 동시 본문 콜백 | 분석 시작/종료 시 진행 콜백 | 본문/분석 호출 | 완료 순서 | 남은 후보/추가 요청 | 캐시 |
|---|---:|---:|---:|---|---:|---|
| 같은 12개월 기간에 후보 3개가 더 있음 | 3 | 0 / 0 | 3 / 1 | 후보 순위의 역순 | 3 / 0 | 재사용 불가 |
| 다음 24개월 기간에 후보 3개가 더 있음 | 3 | 0 / 0 | 3 / 1 | 후보 순위의 역순 | 3 / 0 | 재사용 불가 |
| 마지막 묶음 3개로 모든 후보 처리 | 3 | 0 / 0 | 3 / 1 | 후보 순위의 역순 | 0 / 0 | 재사용 가능 |

기존 `test_collection_body_concurrency.py`의 `GatedFetch`, 스레드 실행/종료 대역과 허용 매체 정책을 재사용했다. 각 후보를 서로 다른 허용 호스트에 배치하고 세 콜백이 모두 Event에서 대기하는 동안 `active == max_active == 3`, 호스트별 최대 1을 직접 확인했다. 빠르게 끝나는 콜백이나 실행 설정값만으로 동시 실행을 추정하지 않는다.

가장 앞 후보를 마지막에 풀어 완료 순서를 뒤집되, AI 분석 입력은 원래 후보 순서를 유지하는지 검사한다. 분석 콜백도 별도 Event로 붙잡아 분석 시작/종료의 진행 요청 0과 새 요청 부재를 관측한다. 수집 스레드가 완전히 끝난 뒤에도 요청 총수가 3이고 남은 후보의 URL이 요청 목록에 없는지 확인하므로, 마지막 분석 뒤 다음 묶음의 낭비도 잡는다. 실패 시에는 모든 본문·분석 Event를 해제하고 수집 스레드를 종료해 다른 시험에 남기지 않는다.

결과는 같은 스냅샷의 순차 실행과 기사·조각·문서 해시·동시 정산 블록을 제외한 전체 진단이 같다. 추가로 기사/조각 URL 순서, 전체 본문 SHA-256, 인용 구간의 정확한 원문, 조각 SHA-256을 독립적으로 확인했다. 미완료 두 사례는 `analysis_budget_exhausted`, `완전성=partial`, `자료부족=false`, `캐시재사용가능=false`를 확인하며, 정확 완료 사례는 허위 상한 사유가 없고 캐시가 가능해야 통과한다.

실행에는 현재 저장소의 `.venv/Scripts/python.exe -B -X utf8 -`를 사용했다. 부모 폴더를 먼저 만든 뒤 `app/.local_evaluation_runs/parallel-concurrent-regression/isolated/` 안에만 파일 쓰기와 임시 DB 연결을 허용하는 Python 감사 훅을 설치해 `pytest.main`을 호출했다. `app/conftest.py`는 그대로 로드했고 `--confcutdir`를 사용하지 않았다.

| 항목 | 실제 실행 설정 |
|---|---|
| `STORAGE_DB_PATH` | `.../isolated/bootstrap.db` — conftest가 각 시험의 basetemp 아래 `test.db`로 추가 격리 |
| `APP_DATA_ROOT` | `.../isolated/` |
| `OBSERVABILITY_RECORDS_PATH` | `.../isolated/runs.jsonl` — conftest의 시험별 이력 격리 유지 |
| `TEMP`, `TMP` | `.../isolated/tmp/` |
| `TLDEXTRACT_CACHE` | `.../isolated/cache/tldextract/` |
| 환경 스위치 | `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `PYTHON_DOTENV_DISABLED=1`, `ANALYSIS_ENGINE_DISABLE_DOTENV=1`, `PYTHONDONTWRITEBYTECODE=1` |
| pytest 인자 | `-q -s -p no:cacheprovider --log-file .../isolated/pytest-second.log --basetemp .../isolated/pytest-second app/src/features/news_intake/tests/test_analysis_budget_concurrent.py` |

위 `...`는 저장소 루트 아래 `app/.local_evaluation_runs/parallel-concurrent-regression`이다. 다른 사람이 재실행할 때에는 `pytest-second`를 재사용하지 말고 같은 격리 부모 아래 존재하지 않는 새 basetemp를 지정한다. 테스트 전용 합성 본문·가짜 분석만 사용했고, 성공 실행의 감사 결과는 **금지 접근 차단 시도 0건, SQLite 연결 0건**이었다. 기존 DB·비밀 파일·실제 네트워크·유료 API·서버에는 접근하지 않았다.

첫 실행은 pytest의 기본 로그 출력용 파일 열기가 격리 밖 쓰기 차단에 걸려 **시험을 시작하기 전에 내부 오류로 종료**했다. DB 연결은 0건이었고 차단된 쓰기는 수행되지 않았다. 격리를 해제하지 않고 pytest 로그를 위 격리 폴더에 명시한 다음 새 경로에서 실행해 3건 통과했다. Windows 권한 오류나 제품 코드 결함은 관측하지 못했다.

이 후속은 예산 마지막 경계의 실제 동시 실행 회귀 보강이며 실제 기업 보고서의 기사 채택률·비용 절감·PDF 품질 합격을 증명하지 않는다.
