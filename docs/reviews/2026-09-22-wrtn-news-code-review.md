# 뤼튼 보고서의 외부 언론 누락 경로 조사

작성·운영 진단 반영일: 2026-09-22 · 현재 코드: `4b674a33347a724ae67d4095be9b4a97beac3af4` · 해당 PDF 배포: `7a15b07395305bc6a5362629722cc0c62450695b`

## 판단 범위

**해당 PDF는 뉴스가 켜진 상태에서 검색과 본문 읽기까지 실행했지만, 뉴스 수집 단계의 회사 신원·원문·주체 검증 등을 통과한 조각이 0개여서 외부 언론이 본문에 전달되지 않았다.** 운영에서 사용자 PDF SHA256와 일치하는 artifact 및 생성 진단을 찾았으므로, 이전의 “실제 경로 미확정”과 “스위치 OFF 가능성”을 이 실행에 대해서는 철회한다. 다만 개별 기사의 원문·분석 응답·입력 회사명/별칭이 이 집계 JSON에 없어, 각 탈락이 타당했는지 또는 오거부였는지까지 확정한 것은 아니다.

이번 정정에서 직접 읽은 근거는 [운영 뉴스 진단 JSON](../../app/.local_evaluation_runs/wrtn-news-review-20260922/production-news-diagnostics.json)이다. 이 작업자가 원격 운영에 접속한 것은 아니며, PDF 일치 확인과 아래 운영 설정 값은 총괄이 전달한 결과다.

- report/run: `b0fa583b8249006769d57b4a8c23fd15`
- PDF SHA256: `afb4e622aef3a10c5d181d07ec54c408424cd5eafbdfc51e6b635495a1e5ebe1`
- 배포 revision: `7a15b07395305bc6a5362629722cc0c62450695b`
- 운영 설정: `NEWS_INTAKE=1`, `ENGINE_V2=1`, `REPORT_RELEASE_MODE=FULL`. 저장된 `5b_뉴스_수집.스위치=true`와 실제 검색·분석 기록도 ON 실행을 뒷받침한다.
- 진단 JSON 자체의 SHA256: `671ae6515b6bb10af057b330e6d5700a39fff8a495fe7d95abf56b0aede64bd0`
- PDF 5쪽 전체 텍스트에 외부 언론 출처는 없고, 26·27번 출처는 공식 웹 `wrtn.io/news/#1`, `#2`다.
- 공식 웹 두 출처의 내용은 크랙 보호 및 IPO 준비다.

로컬 DB에서 생성 기록을 못 찾았다는 이전 관측은 운영 artifact 발견으로 해소됐다. 저장소 배포 파일의 환경변수 부재도 이 실행의 OFF 근거로 사용할 수 없다.

이번 정정은 지정 JSON·소스·`git show/diff`의 읽기와 이 문서 편집만 수행했다. 테스트 실행, Python/제품 모듈 import, DB 접근, 비밀 환경 파일·인증값 조회, 외부 네트워크 및 유료 호출은 하지 않았다. **이전 조사에서 시험 격리 실수로 로컬 시험 세션 3건을 생성한 실패 기록은 삭제하지 않고 마지막 절에 유지한다.**

## 핵심 결과

1. **뉴스 OFF·검색 미실행 가설은 배제된다.** 검색 11논리 호출·11실제 전송이 성공했고, 반환행 200개에서 후보 56개가 남았다.
2. **본문 15개를 읽었지만 수집 검증 통과는 0개다.** 본문은 기사 20개를 시도해 22회 호출했으며 분석은 상한 3회를 모두 사용했다. 기록은 `완전성=failed`, `자료부족=false`, `캐시재사용가능=false`다. “이 회사에 뉴스가 없다”는 결론이 아니다.
3. **법인 신원 실패 5건 중 짧은명과 연결 가능한 코드는 1건이다.** `identity_evidence_not_exact=4`, `identity_name_missing=1`이며, 후자의 실제 원인이 짧은명인지는 원문과 실제 별칭 입력을 확인해야 한다. 이전 합성 재현을 실제 5건 전체나 전체 뉴스 누락의 원인으로 확대하지 않는다.
4. **매체 허용목록에서 107개 반환행이 보류됐다.** `untrusted_publisher=107`와 `publisher_verification_required=107`는 같은 집합의 상위·하위 사유다. 합쳐 214건으로 세지 않으며 고유 기사 107개라고도 부르지 않는다.
5. **PDF 본문·목록 0은 수집 조각 0과 이어진다.** `8_뉴스_본문활용`의 수집기사·본문사용·목록·보강후보가 모두 0이다. 이 실행은 수집된 뉴스가 composer에서 전부 탈락한 사례가 아니다. 공식 웹 문서 2개는 별도 경로다.

## 운영 기록에서 확정된 단계별 탈락

| 단계 | 저장된 관측값 | 확인할 수 있는 결론 |
|---|---|---|
| 검색 | `상태=success`, 11논리 호출, 11전송, 재시도 복구 0, 검색 사유코드/전송진단 없음 | 뉴스 검색 자체가 작동했다. 미설정·검색 인증 실패·OFF가 아니다. |
| 검색 반환행 선별 | 200 = 미확인 매체 107 + 중복 URL 34 + 기간외 3 + 후보 56 | 숫자가 정확히 맞는다. 매체 필터는 URL 중복 제거보다 먼저 실행되어 107에는 쿼리 간 같은 기사 반복도 포함될 수 있다. |
| 매체 정책 | 등록 도메인 55, 미확인 도메인 57, 미확인 반환행 107 | 발행자 허용목록의 미확인이며 기사 내용이 허위라는 판정이 아니다. 두 제외 코드와 `미확인매체` 합계는 같은 107을 표현한다. |
| 후보·기간 | 전체 후보 56, 메타 이름 일치 56, 이름 비일치 0. 실행된 12개월 창 후보 52·시도 20·미시도 32 | 메타 회사명 부재가 이 실행의 최초 후보 탈락 원인은 아니다. 전체 후보와 12개월 후보의 차이 4개는 다른 기간 후보이며, 실행된 창은 12개월뿐이다. |
| 본문 획득 | 시도 기사 20, 본문 호출 22, 읽기 15, 본문 26,650자. `usable_ranges=6`, `article_tag=9` | 15개 본문을 실제로 얻었다. `article_body_unavailable=5`와 합쳐 시도 기사 20개다. |
| 본문 장애 | 경고 `fetch_robots_blocked=3`, `fetch_empty_body=2`; `metadata_only_not_body=2` | 일부 요청이 robots/빈 본문/메타 요약 조건에 걸렸다. 요청 경고와 기사 실패는 단위가 다르므로 5+2개 별도 실패 기사로 합산하지 않는다. 모든 뉴스가 robots에 막혔다는 설명도 틀리다. |
| 분석 예산 | `분석AI호출=3`, `분석호출상한=3`, `분석잔여호출=0`; 상한 사유 `window_body_budget`, `analysis_budget_exhausted` | 분석 예산을 실제로 소진했다. 배포 코드의 배치 크기 4로 3회에 최대 12기사 입력이 가능하므로 읽기 15개와 분석 완료 15개를 동일시할 수 없다. 어떤 기사들이 분석되지 않았는지는 집계만으로 특정할 수 없다. |
| 신원 검증 | `grounded_identity_unverified=5`; 상세 `identity_evidence_not_exact=4`, `identity_name_missing=1` | 5건의 법인 근거 검증이 실패했다. 전자는 반환한 신원 근거가 본문의 정확한 연속 문자열이 아니고, 후자는 정확한 근거에 허용된 회사 이름이 없다는 기계 판정이다. 타사 기사 5건이라는 뜻은 아니다. |
| 그 밖의 분석 제외 | `grounded_non_material=5`, `grounded_subject_missing=3`, `grounded_text_not_exact=1` | 비실질 판정·주체 결속·유일한 정확 인용 조건에서 제외됐다. 일부는 기사 단위, 일부는 인용 단위이므로 서로 더해 고유 탈락 기사 수를 만들지 않는다. |
| 수집 결과 | `관련성통과=0`, 기간별 `검증기사=0`, `조각=0`, `독립기사=0`, `상한잘림=0` | 공개 근거로 전달할 뉴스가 수집기에서 이미 0개다. 최종 조각 수량 상한 때문에 검증된 뉴스를 잘라낸 사례도 아니다. |
| 완료 상태 | `실패=fetch_robots_blocked`, `실패사유=[fetch_robots_blocked, fetch_empty_body]`, `완전성=failed`, `자료부족=false` | 대표 실패 코드는 전체 원인 순위가 아니다. 일부 본문 장애가 있고 최종 조각이 0이라 failed가 된 것이며, 법인/주체/원문 검증 미완료와 예산 소진도 함께 존재한다. |
| composer→PDF | `수집기사수=본문사용기사수=목록기사수=보강후보수=0`, `본문상태=뉴스근거없음`, 근거별 판정 없음 | 수집 조각 0 → composer의 뉴스 입력/사용 0 → PDF 외부 언론 본문·목록 0이라는 경로가 확인된다. |

`검증미완료`는 `article_body_unavailable`, `grounded_identity_unverified`, `grounded_subject_missing`, `grounded_text_not_exact`, `source_review_pending`이다. 이 집합과 상한 사유 때문에 재수집 없이 “모든 후보를 정상 검사했고 자료가 없었다”고 설명할 수 없다.

특히 “비실질 5 + 법인 5 + 주체 3 + 원문 1 = 읽은 15기사의 분해” 같은 산술은 성립하지 않는다. `grounded_non_material`은 `material=false`인 기사 또는 인용 안의 금지 표현에서 집계되고, 주체/인용 검사는 한 기사 안의 여러 인용을 각각 셀 수 있다. 기사별 응답 연결표가 필요하다.

## 배포 버전과 현재 코드 비교

`git show 7a15b07395305bc6a5362629722cc0c62450695b:<경로>`와 `git diff 7a15b07395305bc6a5362629722cc0c62450695b 4b674a33347a724ae67d4095be9b4a97beac3af4 -- <경로>`로 읽기 비교했다. checkout·모듈 실행·테스트는 하지 않았다.

| 비교 대상 | 결과 및 의미 |
|---|---|
| `news_intake/identity_names.py`, `grounded.py` | 두 버전 파일 동일. 전체 공식 이름 경계, 신원 원문의 정확 일치, 인용을 이용한 신원 복구 조건이 해당 배포에도 있었다. 이전 합성 재현의 규칙은 배포와 공통이지만 실제 입력 재현은 아니다. |
| `news_intake/search_snapshot.py`, `models.py`, `grounded_mapping.py` | 두 버전 파일 동일. 검색 메타 이름은 순위만 정하며, 매체 필터 후 중복 제거하는 집계 순서도 같다. |
| `news_intake/constants.py` | 추가된 것은 본문 동시수집 관련 21줄이다. 언론 허용목록·이름·본문 원문 검증 정책은 변경되지 않았다. 허용목록은 배포 줄 464, HEAD 줄 485이며 실제 정책값은 같다. |
| `pipeline/real.py`의 `_official_company_aliases`, `pipeline/news_research_context.py` | 별칭 추출 내용과 공식 문맥 정책이 같다. 배포의 별칭 함수는 줄 3063, HEAD는 줄 3128. 공식 웹에 등장한 짧은 이름을 자동 신뢰 별칭으로 올리는 수정은 현재 HEAD에도 없다. |
| 분석 예산 예약 | 배포 `real.py:6045`도 작성 예약+빈 장 복구+작가 재요청 여유를 먼저 뺀다. 실제 상한은 진단의 3회이며 정책 절대 상한 8회나 일반 주석의 예상치로 대체하지 않는다. |
| `news_intake/collection.py`, `core/news_research_adapter.py`, `pipeline/real.py`의 본문 경로 | 배포는 `collection.py:38`부터 순차 수집. HEAD에는 `body_prefetch.py`와 본문 동시성·취소/마감·관측 코드가 추가됐다. 현재 `body_prefetch.py` 줄 번호를 당시 직접 실행된 코드라고 부르면 안 된다. |
| `composer/news_usage.py`, `news_block.py`, `core/news_intake_switch.py` | 두 버전 파일 동일. 이번 0건은 해당 후단의 버전 차이로 설명되지 않는다. |

배포 `collection.py:81`의 `analyze_batch()`는 상한 확인 뒤 모델을 호출하고, `:132`의 `queue_body()`는 4기사마다 분석한다. 같은 파일 `:307` 이후는 본문 장애 코드가 있으면서 조각이 0이면 `완전성=failed`로 만든다. 단일 `실패` 값을 모든 탈락의 원인으로 읽지 않은 근거다. 현재 본문 동시수집 변경만으로 이름/원문 검증 실패가 해결됐다고 볼 근거는 없다.

## 단계별 누락 지도

아래는 재사용 가능한 일반 코드 지도이며, 전부 해당 PDF에서 발동했다는 목록이 아니다. 경로는 저장소 루트 기준, 줄 번호는 조사 HEAD 기준이다. 특히 OFF, 검색 미실행, composer에서 수집 기사 전부 제거라는 가설은 이번 운영 진단으로 배제됐다. 당시 순차 본문 경로는 위 배포 비교 절을 따른다.

| 단계 | 코드 위치 | 발동 조건 및 결과 | 구분할 기록 |
|---|---|---|---|
| 프로세스 ON/OFF | `app/src/core/news_intake_switch.py:43`, `:53`, `:91` | 미설정·`true`·`on`·공백 포함 값은 OFF. 최초 읽기 후 환경만 바꿔도 현재 프로세스 값은 유지된다. | 프로세스 부팅 시 동결값, 생성 버전, 캐시 namespace의 `news_intake` |
| 엔진 분기 | `app/src/features/pipeline/real.py:4311` | `EngineMode.V2`이면서 ON일 때만 `_run_news_search_branch` 생성. 공식 근거의 빈칸 여부는 이 분기 조건이 아니다. | 엔진 모드, `5b_뉴스_검색스냅샷` 존재 여부 |
| 구형 생략 기록 | `real.py:9136`, `:4675` | 기본 수집은 `6_수집_뉴스`에 검색·채택 0과 생략 문구 기록. 새 뉴스 세션 또는 준비 실패가 있으면 이 기록을 제거하고 `5b` 기록으로 바꾼다. | `6_수집_뉴스.생략`과 `5b` 단계의 조합. 단순 `5b` 부재는 중도 종료도 배제해야 한다. |
| 별칭·공식 문맥 준비 | `real.py:3128`, `:6152`; `pipeline/news_research_context.py:21` | 별칭은 기업개황의 `corp_name_eng`, `corp_eng_name`, `stock_name`에서만 얻는다. 공식 문맥은 신원 결속된 문서의 조각을 장별로 취한다. 확인된 도메인이 기업개황 홈페이지 호스트와 맞지 않으면 공식 도메인은 빈 값이다. | 실제 입력 `company_name`, `aliases`, `domain`, 회사 문맥 길이·지문; 현재 집계 진단만으로 입력 이름 전체를 복원할 수 없다. |
| 분석 호출 예산 예약 | `real.py:6171`; `app/src/core/news_research_adapter.py:110` | 작성·복구·재요청 예약량을 뺀 잔여 호출 수로 뉴스 분석 상한을 제한한다. 0도 유효한 정책이다. | `5b_뉴스_검색스냅샷.AI분석호출상한`, 수집의 `분석호출상한`, `상한사유` |
| 검색어 구성 | `news_intake/search_snapshot.py:79`; `identity_names.py:79` | 전체 공식명, 제한된 공식 별칭, 사업·협력·전략·발표, 확인 공식 도메인, 과거 연도 검색. 짧은 브랜드명을 추측해 만들지 않는다. | 원래 `query_attempts`의 query·sort·start·display·topic·window. 집계 steps에는 검색어 자체가 보존되지 않는다. |
| 검색 호출 | `search_snapshot.py:263` | 최대 12 논리 호출·12 관측 전송·90초. 각 검색은 `display=20`, `start=1`; 현재 경로는 같은 쿼리의 2페이지를 읽지 않는다. 인증/미설정/제공자 실패는 검색어를 바꾸어 반복하지 않는다. 전송 관측이 없는 callback도 추가 검색 중단. | `상태`, `사유코드`, `검색논리호출`, `검색실제전송`, `검색전송진단`, `검색전송시도` |
| URL·발행처·날짜 후보 필터 | `search_snapshot.py:128`; `constants.py:485` | `originallink` 우선. 공식 도메인 또는 언론 허용목록만 통과. 발행일 없음·미래·36개월 이전·제목 없음은 제외. 제목/요약의 회사명 부재는 즉시 탈락 조건이 아니라 읽기 우선순위다. | `선별`, `제외`, `미확인매체`, `메타이름일치후보`, `메타이름비일치후보` |
| 후보 예산·기간 배분 | `search_snapshot.py:339`; `collection.py:87`, `:196` | URL 중복 제거, 최대 후보 80개. 본문은 12·24·36개월 창, 기본 16·4·4기사 몫과 남은 몫 재배분. 충분한 근거·비용/시간 한계에서 종료. | `기간별`, `기간개월`, `상한사유`, `candidate_budget`, `window_body_budget` |
| 본문 주소·robots·HTTP | `real.py:7322`, `:7384`, `:7441`; `body_prefetch.py:203`, `:231` | 원문 우선 및 제한된 URL 변형/포털 폴백. origin 변경, robots 차단, HTTP 오류, 타임아웃, 깨진 인코딩 등은 본문 근거를 만들지 못한다. 확인 언론 원문이 없는 포털 URL 단독은 최초 후보 필터에서 탈락 가능. | `본문호출`, `본문읽기`, `본문단계`, `시도경고`, `fetch_*`, `article_body_unavailable` |
| 실제 본문 경계 | `news_intake/fetch.py:348`; `body_prefetch.py:289` | 메타 description만 얻은 결과, 미파싱 HTML, 미확인 최종 URL, 짧은 본문, 실제 본문 날짜가 기간 밖인 경우는 배제. 검색 설명문을 기사 본문으로 승격하지 않는다. | `metadata_only_not_body`, `unparsed_html_not_body`, `untrusted_effective_url`, `body_too_short`, `body_published_outside_window` |
| 분석 실행 한계 | `collection.py:114`, `:170` | 기사당 12,000자·전체 200,000자·전체 수집 180초·배치 4기사·정책상 최대 분석 8회. 운영에서는 예약량에 따라 더 적다. 분석 전 deadline 초과도 `analysis_budget_exhausted`로 표시된다. | `분석AI호출`, `분석입력글자`, `분석응답글자`, `상한사유`, `body_input_truncated` |
| 모델 응답 및 회사 신원 | `grounded.py:155`, `:176`, `:329` | 엄격 응답 구조, `same_company=true`, `material=true`, 본문 안의 신원 원문, 전체 이름 경계, 공식 문맥 어휘 겹침 요구. 신원 원문 실패 시 독립 검증된 같은 기사 인용으로 복구할 수 있다. | `grounded_wrong_company`, `grounded_identity_unverified`, `법인검증상세`, `identity_recovered_from_excerpt` |
| 인용 원문·주체·시점 | `grounded.py:211`, `:240` | 25~1,000자 연속 원문이며 본문에서 유일해야 한다. 회사명 또는 명시적 제품/조직 관계를 결속하고 허용 슬롯·발언 귀속·계획/완료·사건 날짜를 검증한다. | `grounded_text_not_exact`, `grounded_subject_missing`, `grounded_invalid_slot`, `grounded_attribution_required`, `grounded_plan_mismatch`, `grounded_event_date_unverified` |
| 사건·기사·조각 최종 선정 | `grounded_mapping.py:147`; `collection.py:391` | 사건 중복, 기사당 최대 인용 2개, 최종 기사 12·조각 16·조각 전체 12,000자 상한. 충분성은 수집 중단/진단 기준이며 기사 1건을 무조건 버리는 최저 수량이 아니다. | `duplicate_event`, `article_excerpt_budget`, `fragment_budget`, `독립기사`, `조각`, `장별조각` |
| typed 입력 전달 | `real.py:7713`, `:6619`; `pipeline/evidence_transport.py:259` | 뉴스 스위치·회사 ID·문서 신원/본문 해시·의미 슬롯·원문 범위 등 계약을 만족해야 composer 입력이 된다. | 부분 모드 `v2_조각_typed전달.보조/검증실패제외/검증실패_사유별`, FULL 입력계약 차단 단계 |
| 작성 누락 및 보강 | `composer/news_usage.py:70`; `composer/pipeline.py:1909` | 작성자의 구체적 제외, 소유 슬롯 없음, 미검증 표시, 날짜/매체 없음, 외국어 원문, 동일 사건 등은 자동 보강 제외. 보강 후보도 Reviewer를 통과해야 한다. | `8_뉴스_본문활용.보강후보수/근거별판정`, `뉴스근거판정` |
| 본문 검수·게시 | `news_usage.py:144`; `pipeline.py:2056` | verified·확인 등급, 허용 장, 뉴스 단독 인용, 정확한 발행일·매체 귀속, 한국어 설명, 원문 수치 지지 필요. 검수 탈락 안내는 PDF 본문에 남기지 않는다. | `근거별판정[].검증경과[].사유코드`: `review_removed`, `not_verified`, `mixed_sources`, `attribution_invalid`, `korean_body_unverified`, `unsupported_number` 등 |
| 보도표 필터 | `composer/pipeline.py:984`; `news_block.py:381` | 본문 검수 후보였다가 최종 본문에서 없어진 조각은 표에서도 제외. 장 소유권·메타데이터·기사 중복도 검사한다. 부분 보고서는 claim slot으로 소유권을 복구한다. | `뉴스_보도표.행수/장별행수`, `뉴스_보도표_불가.사유별`, 특히 `body_review_rejected` |
| 렌더 및 출처 결속 | `composer/render.py:657`; `public_manifest.py:1028` | 행의 조각 인용이 출처 번호표에 없으면 렌더에서 그 행을 건너뛴다. 공개 보도표는 manifest 및 출처 결속을 거친다. | composer의 news rows·출처 번호 매핑·최종 report/manifest 비교 |

### 조사 중 혼동하기 쉬운 경로

- `real.py:7848`의 `_collect_news_intake()` 및 `news_intake/select.py`, `classify.py`, `mapping.py` 중심 설명은 구형 경로다. 현재 v2 주 경로는 `_run_news_search_branch()` → `prepare_news_research()` → `_collect_grounded_news()` → `collect_from_snapshot()`이다. 따라서 “공식 자료가 충분하니 뉴스 미호출”이라는 구형 트리거를 현재 v2 전체에 적용하면 오진한다.
- `real.py:639`는 ON에만 별도 캐시 namespace를 준다. `news_research_context.py:71`부터 공식 digest·뉴스 snapshot digest·기준일을 묶는다. 이번 일치 artifact에는 ON 검색·본문·분석의 실제 기록이 있으므로 OFF 캐시를 원인으로 들 필요가 없다.
- `shared/report_evidence/constants.py:70`은 9장 `competitive_position`의 뉴스를 제외한다. `shared/report_quality/supplementary_prose.py:13`은 뉴스 산문으로 1장 정체성과 9장 공식 비교를 대체하지 못하게 한다. 따라서 뉴스 활성화만으로 9장의 회계정책 내용 문제를 해결할 수 없다. 9장 내용의 적합성은 별도 범위다.

## 구체적인 결함 후보와 최소 수정 제안

### 후보 A — 공식 법인명과 짧은 브랜드명의 연결 부재

**분류: 이전 합성 재현과 실제 `identity_name_missing=1`이 관련될 가능성. 해당 1건의 오거부 여부는 미확인.**

`real.py:3128`의 별칭 어댑터는 기업개황 필드만 사용한다. `identity_names.py:37`의 파생 이름은 공식 한글/영문 이름의 알파벳 독음 치환에 한정되며, 정식 이름의 접두 `뤼튼`을 만들지 않는다. `identity_context`에 브랜드가 적혀 있어도 그 텍스트는 공식 이름 목록으로 승격되지 않는다.

합성 입력에서 모델이 `same_company=true`, `material=true`를 반환해도 원문에 긴 법인명이 없으면 기계 검증이 제거했다. 이는 오탐 방지 의도가 있는 규칙이며 단순 부분 문자열 허용으로 고쳐서는 안 된다.

**최소 제안:** 공식 문서에서 법인명↔브랜드/축약명 관계를 명시적으로 확인한 별칭만 근거 URL·문서/범위 지문과 함께 뉴스 회사 문맥으로 전달한다. `news_intake`가 다른 feature를 직접 import하지 않도록 `core/news_research_adapter.py`와 공유 불변 자료형을 경계로 삼는다. `Wrtn`나 `뤼튼`을 회사별 하드코딩으로 추가하지 않는다. 별칭을 추가할 때 snapshot의 회사 digest도 달라져야 한다.

**실제 로그와의 구분:** 회사 신원 실패 5건은 `identity_evidence_not_exact=4`와 `identity_name_missing=1`이다. 전자 4건을 짧은명 문제로 바꾸어 설명해서는 안 된다. 검색 후보 56개는 모두 메타 이름 일치였으므로 “검색 단계에서 뤼튼이라는 이름을 못 알아봤다”는 설명도 성립하지 않는다.

**남은 확인:** 실제 `company_name`, `stock_name`/공식 별칭, `identity_name_missing` 1건의 `entity_evidence` 및 본문/인용을 대조한다. 이미 짧은 별칭이 주어졌거나 모델이 회사 이름 없는 문장을 신원 근거로 골랐다면, 앞선 합성 입력과 같은 원인으로 결론낼 수 없다.

### 후보 B — 한국어 공식 문맥과 동의어의 어휘 불일치

**분류: 이전 합성 재현에 한정된 일반 후보. 이번 실제 상세 진단에 `identity_context_mismatch`는 없어 원인으로 채택하지 않는다.**

`grounded.py:155`는 회사명을 확인한 뒤 공식 문맥과 기사 신원 근거의 2글자 이상 단어/부분 문자열 겹침도 요구한다. 공식 문맥 `생성형 인공지능 서비스 개발 및 운영`에 대해 합성 원문 `뤼튼테크놀로지스는 AI 솔루션을 출시했다고 밝혔다.`는 `identity_context_mismatch`다. 문맥과 표현이 다르면 같은 회사의 기사도 제외될 여지가 있다. 반대로 평범한 공통 단어 하나가 겹치면 통과할 수 있으므로 의미적 신원 증명을 완전히 대신하지도 못한다.

**최소 제안:** 우선 상세 사유와 회사 문맥 입력을 회수한다. 확인된 제품·브랜드·임원·도메인 등 구조화된 신원 단서로 대체할 때에만 규칙을 조정한다. 문맥 조건을 통째로 삭제하거나 `same_company=true`만 믿는 완화는 제안하지 않는다.

### 후보 C — 전문 매체 및 원문 없는 포털 링크의 후보 탈락

**분류: 매체 정책에 따른 실제 보류 107반환행 확인. 개별 매체를 허용했어야 하는지 및 기사 채택 가능성은 미검증.**

이전 합성 재현에서 `zdnet.co.kr`는 `news_report`, `platum.kr`, `venturesquare.net`, `news.naver.com`은 빈 값이었다. 실제 진단에도 `platum.kr=3`, `venturesquare.net=1`, `ddaily.co.kr=5`, `koreatimes.co.kr=5` 등 총 57도메인·107반환행의 보류가 있다. 이 수치는 발행자 신원 검증 결과나 고유 기사 수가 아니다. 원문 없는 포털 `link`만 오는 경로의 탈락은 코드상 가능하지만, 이번 진단에 그 구체적 사례가 있다는 근거는 없다.

**후속 제안:** 실제 `미확인매체`의 발행자 근거를 확인한 뒤 필요하면 허용목록을 확장하되 검색·본문·분석 상한은 그대로 둔다. 현재는 이미 통과한 후보 56개에서도 검증 조각이 0이므로 허용목록 확대만으로 PDF 개선을 보장할 수 없다. 이 정정에서는 외부 발행자 확인이나 정책 변경을 수행하지 않았다.

### 후보 D — 불완전 수집을 정상적인 채택 0건으로 요약

**분류: 이전 합성 재현으로 확인된 일반 관측 결함. 이번 실행에서는 `실패=fetch_robots_blocked`가 있으므로 해당 “none/정상 0건 축약” 분기를 실제로 탔다고 주장하지 않는다.**

`collection.py:414` 이후에는 실패·상한·검증 미완료를 구분하지만 `real.py:2984`의 `_sources_from()`은 `실패` 유무만 검사한다. 분석 예산 0, 신원 미확인, 본문 획득 실패처럼 `실패=null`, `완전성=partial`인 경우를 `none / 채택 조건 통과 0건`으로 축약한다. 기사 일부가 살아 있으면 불완전 수집이어도 `ok`가 될 수 있다.

**최소 제안:** 출처 요약 생성 시 `완전성`, `상한사유`, `검증미완료`, `자료부족`를 함께 읽어 “확인 미완료”와 “검사 완료 후 0건”을 구분한다. 상세 단계는 유지하며 뉴스 본문 공개 기준은 바꾸지 않는다. 또한 분석 배치의 deadline 초과와 호출 수 소진이 같은 `analysis_budget_exhausted` 코드여서, 둘을 별도 코드로 나누면 후속 원인 판별이 쉬워진다.

## 추가 비용 없이 할 최소 수정 우선순위 3개

아래는 구현하지 않은 제안이다. 새 검색/본문/모델 재호출이나 유료 재생성을 전제하지 않고, 확보된 입력·응답이 있을 때 로컬 대조부터 한다. 운영의 분석 상한 3회를 늘리거나 검증기를 끄는 변경은 제안하지 않는다.

1. **원문 신원 근거 불일치 4건과 인용 불일치 1건의 결속을 먼저 보완한다.** 배포와 HEAD의 `grounded.py`는 동일하며, 이미 유효한 인용으로 신원 근거를 복구하는 코드가 있다. 이를 새 기능처럼 다시 추가하지 말고 기존 복구가 실패한 기사 ID·인용 ID·첫 실패 코드를 기록한다. 저장된 원문/응답 대조에서 공백·개행 등 표현 정규화만이 원인으로 확인될 경우에만, 하나의 원본 연속 범위로 역매핑할 수 있는 결정적 보정으로 제한한다. 복수 위치·생략·의미 변경은 계속 거절한다. 원문을 보지 않은 채 모델의 요약을 정확 인용으로 승인하지 않는다.
2. **이름 누락 1건이 공식 축약명 문제로 확인되면, 기존 공식 자료에서 증명한 별칭만 전달한다.** 법인명↔브랜드 관계 원문과 문서 지문을 갖는 별칭을 사용한다. 회사별 문자열 하드코딩·부분 이름 무조건 허용·검색 결과에서 별칭 추측은 하지 않는다. 추가 모델 호출 대신 이미 수집한 공식 근거를 사용하고 기존 검색/프롬프트 입력을 대체해 호출·토큰 예산을 늘리지 않는다. 이 수정의 예상 영향은 해당 유형에 한정되며 신원 원문 불일치 4건이나 모든 미채택을 해결한다고 보지 않는다.
3. **집계와 기사별 미완료 관측을 보완한다.** 매체 보류 107을 상위/하위 사유로 중복 합산하지 않고, 읽은 본문 수·분석 입력 기사 수·예산 때문에 미분석된 수를 분리한다. 기사/인용의 비밀값 없는 식별자에 단계·첫 탈락 코드·예산 상태를 묶고, 출처 요약에는 `failed/partial`, `검증미완료`, `상한사유`를 보존한다. 상한 소진 후 다음 분석이 불가능한 상태에서 불필요한 본문 요청을 계속하는지 점검하고 중단할 수 있게 한다. 모델 판정 자체를 추가 호출로 재심하지 않아 비용을 늘리지 않는다.

매체 확대는 발행자 검증이 선행돼야 하는 후속 정책 검토다. 107개 반환행 전체를 자동 허용하는 방식은 최소 수정으로 제안하지 않는다. 기존 56후보의 원문 결속 실패를 먼저 이해해야 허용목록 확대가 같은 분석 예산 안에서 실제 도움이 되는지 판단할 수 있다.

## 이전 조사에서 수행한 무료 재현 결과

모든 아래 뉴스·본문·모델 출력은 이전 조사에서 실행한 로컬 합성 자료다. **이번 운영 진단 정정에서는 재실행하지 않았다.** 실제 검색 서비스, 기사 사이트, 유료 모델은 사용하지 않았다. 날짜는 저장소 fixture의 `2026-09-08` 기준, 합성 기사는 `2026-09-01`이다. 운영의 3회 분석·본문 15개·신원 실패 5건과 이 합성 수치를 혼합하지 않는다.

| 입력 차이 | 검색/선별 | 본문읽기/분석 | 최종 조각 | 관측 |
|---|---:|---:|---:|---|
| 긴 법인명+영문명만 입력, 본문은 `뤼튼은 …` | 1 / 1 | 1 / 1 | 0 | `identity_name_missing`, `grounded_identity_unverified`, `partial` |
| 본문을 `뤼튼테크놀로지스는 …`으로 변경 | 1 / 1 | 1 / 1 | 1 | 제외 없음, `insufficient`이지만 확보한 1개 조각은 유지 |
| 짧은 `뤼튼`을 공식 검증됐다고 가정한 별칭으로 추가 | 1 / 1 | 1 / 1 | 1 | 제외 없음 |
| 분석 호출 정책을 0으로 설정 | 1 / 1 | 1 / 0 | 0 | `analysis_budget_exhausted`, `partial`, `자료부족=false`; 요약은 `none / 채택 조건 통과 0건` |
| 본문 대신 같은 텍스트를 메타 description 단계로 반환 | 1 / 1 | 0 / 0 | 0 | `metadata_only_not_body`, `article_body_unavailable` |
| 같은 문장이 본문에 두 번 있고 한 문장을 인용으로 선택 | 1 / 1 | 1 / 1 | 0 | `grounded_text_not_exact`; 문장은 실재하지만 위치가 유일하지 않아 거절 |

마지막 두 행은 각각 본문/원문 범위를 보장하는 의도된 방어 조건이다. 실재 문장이라는 이유만으로 자동 통과시킬 수정은 제안하지 않는다.

### 이전 재현 스크립트 보존본

이전 조사 증거를 보존하기 위해 남긴 코드이며, 이번 작업에서는 테스트·모듈 import 금지에 따라 실행하지 않았다. 이전 실행 방식은 저장소 루트에서 Python stdin으로 전달해 `.venv/Scripts/python.exe -X utf8 -B -`를 쓰는 것이었다. SQLite 연결·네트워크·비밀 환경 파일 읽기를 차단하는 코드이며 fixture helper는 해당 HEAD의 로컬 테스트 코드다.

```python
import sys
import json
from dataclasses import replace
from datetime import date

sys.path.insert(0, "app")

def guard(event, args):
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = str(args[0]).replace("\\", "/").lower()
        if any(p == ".env" or p.startswith(".env.") for p in path.split("/")):
            raise RuntimeError("비밀 환경 파일 읽기 차단")
    if event in {"socket.connect", "socket.getaddrinfo", "sqlite3.connect"}:
        raise RuntimeError("외부 호출 및 DB 연결 차단")

sys.addaudithook(guard)

from src.features.news_intake.tests.test_collection import (
    collect, item, POLICY, BODY, analyzer,
)
from src.features.news_intake.models import NewsCompanyContext, NewsCollectionPolicy, NewsBodyFetchResult
from src.features.news_intake.search_snapshot import search_plan, source_category
from src.features.news_intake.grounded import _identity_failure
from src.features.news_intake import constants as c
from src.features.pipeline.real import _sources_from

company = NewsCompanyContext(
    "뤼튼테크놀로지스", aliases=("Wrtn Technologies, Inc.",),
    domain="https://wrtn.io", identity_context="생성형 인공지능 서비스 개발 및 운영",
)
body = "뤼튼은 생성형 인공지능 서비스 개발 및 운영 사업을 확대하고 2026년 9월 1일 기업용 서비스를 출시했다고 밝혔다."
print("검색어", [q[0] for q in search_plan(company, date(2026, 9, 8))])

for label, context, text in (
    ("짧은명", company, body),
    ("전체명", company, body.replace("뤼튼은", "뤼튼테크놀로지스는")),
    ("별칭가정", replace(company, aliases=company.aliases + ("뤼튼",)), body),
):
    result = collect(
        items=[item(title="뤼튼 신규 서비스", description="인공지능 서비스 출시")],
        company=context, fetch=lambda url: text,
    )
    keys = ("선별", "본문읽기", "분석AI호출", "조각", "법인검증상세", "제외", "완전성")
    print(label, json.dumps({k: result.diagnostics[k] for k in keys}, ensure_ascii=False))

synonym = "뤼튼테크놀로지스는 AI 솔루션을 출시했다고 밝혔다."
print("동의어문맥", _identity_failure(synonym, synonym, company))
for host in ("zdnet.co.kr", "platum.kr", "venturesquare.net", "news.naver.com"):
    print("매체정책", host, repr(source_category("https://" + host + "/synthetic", company, NewsCollectionPolicy())))

cases = (
    ("예산0", {"policy": replace(POLICY, max_analysis_calls=0)}),
    ("메타뿐", {"fetch": lambda url: NewsBodyFetchResult(text=BODY, stage=c.BODY_STAGE_META_DESCRIPTION)}),
    ("원문반복", {
        "fetch": lambda url: BODY + " " + BODY,
        "analyze": analyzer(lambda rows, payload: [
            dict(row, excerpts=[dict(row["excerpts"][0], text=BODY)]) for row in rows
        ]),
    }),
)
for label, kwargs in cases:
    result = collect(**kwargs)
    keys = ("조각", "실패", "제외", "상한사유", "완전성", "자료부족")
    print(label, json.dumps({k: result.diagnostics[k] for k in keys}, ensure_ascii=False))
    if label == "예산0":
        sources = _sources_from([dict(result.diagnostics, step="5b_뉴스_수집")])
        print("요약", [(s.name, s.state, s.detail) for s in sources if s.name == "뉴스"])
```

## 남은 검증

- **개별 기사 판정의 정당성:** 당시 기사별 입력 본문·본문 해시, 3회 분석의 요청/응답, 기사/인용 ID 연결표가 보존돼 있는지 확인한다. `grounded_non_material=5` 중 모델의 비실질 판정과 인용 금지어 조건을 구분하고, 신원 원문 불일치 4·이름 누락 1·주체 3·인용 원문 1을 각각 대조해야 오거부를 확정할 수 있다. 이 집계만으로 모델 오류·파서 오류·진짜 타사/비실질 기사를 구분할 수 없다.
- **실제 회사 이름 입력:** `company_name`, 공식 별칭, 신원 문맥 및 회사 digest를 기사별 신원 근거와 대조한다. `identity_name_missing=1`의 원인이 짧은 `뤼튼`이었는지는 아직 불명이다. `검증된이름변형=[]`는 파생 변형이 없다는 뜻이지 공식 별칭 전체가 비었다는 뜻이 아니다.
- **분석 범위와 미시도:** 본문 15개 중 모델에 전달된 기사 ID, 예산 때문에 전달되지 못한 기사 ID, 최근 후보 32개 및 다른 기간 4개의 미시도 상태를 확인한다. 분석 3회가 더 많았으면 반드시 유용한 기사가 나왔을 것이라고 추론하지 않는다.
- **매체 정책:** 57개 보류 도메인의 발행자 근거와 고유 URL을 확인한 뒤 우선순위를 정한다. 107은 검색 반환행 단위이며 107개 고유 기사나 214개 제외가 아니다. 외부 발행자 검증은 이 정정 작업에서 수행하지 않았다.
- **수정 후 검증 조건:** 추가 권한이 주어지면 저장된 원문/응답만으로 오프라인 회귀를 설계하되, 확인된 축약명과 동명이인·타사 브랜드·복수 인용 위치·의미 변경을 함께 검증한다. 이번 작업에서는 모든 테스트와 모듈 import를 금지대로 실행하지 않았다.

생성 식별자·PDF 일치·ON·검색 성공·수집 검증 0·composer 사용 0은 이미 확인됐으므로 다시 미확정 질문으로 돌리지 않는다. 남은 것은 개별기사 판정 오류 여부와 수정 효과다. API 키·세션 값·비밀 환경 파일은 이 검증에 필요하지 않다.

## 이전 조사에서 실행한 시험과 한계

아래 표는 이전 조사 당시의 기록이다. 이번 정정에서는 테스트나 Python/제품 모듈을 실행하지 않고 JSON·문서·git 객체만 읽었다. 이전 검증 환경은 저장소 `.venv`의 Python 3.13이며 `-B`, pytest `-p no:cacheprovider`, `--confcutdir=app/src`를 사용했다. 네트워크 연결·DNS 및 비밀 환경 파일 열기를 차단한 프로세스에서 실행했다.

| 실행 대상 | 결과 | 해석 |
|---|---|---|
| `app/src/features/news_intake/tests` + `app/src/core/tests/test_news_intake_switch.py` | **567 passed in 16.00s** | 기존 검색·본문·회사 신원·시점·예산·동시성·스위치 계약의 회귀 통과 |
| `composer/tests/test_news_usage.py`, `test_news_block.py`, `test_news_alternatives.py`; `pipeline/tests/test_news_research_context.py`, `test_news_call_budget.py` | **106 passed, 3 failed in 13.54s** | 3개 실패는 모두 뉴스 웹 모드 시험의 Windows asyncio loopback socketpair를 이 조사자의 전면 네트워크 차단 훅이 막아서 발생. 제품 결함으로 집계하지 않음. PDF/Notion·본문/보도표 관련 통과 시험도 이 실행에 포함됨. |
| 위 합성 재현 | 표에 적은 결과 확인 | 실제 회사 프로필·기사·모델 응답을 재생한 결과가 아님 |

### 시험 격리 실패 및 영향 기록

추가 composer 실행에서 `--confcutdir=app/src`로 상위 `app/conftest.py`를 제외한 것이 잘못이었다. 해당 conftest는 실제 저장소를 격리하는 공통 fixture를 갖고 있다. `test_news_usage.py`의 웹 모드 시험 3개가 `auth.logic.create_session("admin@example.com", True)`를 먼저 호출한 뒤, 네트워크 차단 훅에 걸려 실패했다. 이 때문에 **기본 `app/data/storage.db`에 관리자 시험 세션 3건이 생성**됐다.

직후 `sqlite3`의 `mode=ro` 연결로 시험 계정의 생성시각만 확인했다. 토큰·토큰 해시는 출력하지 않았다. 확인된 UTC 생성시각은 다음과 같다.

- `2026-09-22T12:20:52.423483+00:00`
- `2026-09-22T12:20:53.452915+00:00`
- `2026-09-22T12:20:53.944269+00:00`

`storage/sessions.py:save_session()`은 기존 만료 세션 정리도 수행한다. 이전 DB 사본과 대조하지 않았으므로 만료 세션 삭제 여부/행수는 확인할 수 없다. 생성 3건만이 유일한 DB 변경이었다고 단정하지 않는다. 이 작업자는 삭제나 복구 작업을 하지 않았고, Orca escalation 및 status로 총괄에 영향과 정확한 시각을 보고했다. 이후 합성 재현에는 `sqlite3.connect` 차단도 추가했다. 원격 운영 서비스나 유료 API 호출은 없었다.

이전 조사에서 보고서 밖에 의도적으로 수정한 소스는 없지만, 위 DB 부수효과 때문에 그 조사를 “완전한 읽기 전용 실행”이라고 보고할 수는 없다. 이번 정정 작업은 이 문서만 수정했으며 DB에 접근하지 않았다. 실패 기록은 정리 여부와 관계없이 유지한다.

**총괄의 사후 조치:** 2026-09-22 12:31 UTC 전달에 따르면 총괄이 로컬 DB를 먼저 읽기 전용으로 조회한 뒤, 위 세 생성시각과 `admin@example.com`·관리자 여부·정확한 만료시각이 일치하는 3행만 단일 트랜잭션에서 삭제했다. 직전 대비 전체 행 수 3 감소와 대상 잔여 0을 확인했으며 인증값은 조회·출력하지 않았다고 보고했다. 이 작업자는 DB를 재조회하지 않고 해당 전달을 기록한다. 앞선 잘못된 시험 호출이 기존 만료 세션을 삭제했는지 및 삭제 행 수는 사전 스냅샷이 없어 여전히 불명이다.
