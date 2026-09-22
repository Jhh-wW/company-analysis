# 동일 입력 뉴스 분석 묶음 캐시 검토

## 결과와 운영 판단

동일한 뉴스 분석 묶음의 **검증을 통과한 정상 단일 provider 응답**만 프로세스 메모리에서 재사용한다. 새 검색과 본문 fetch는 계속 실행하며, 현재 본문에서 근거를 복원하고 현재 검증기를 다시 통과한다. 뉴스 분석 단계의 실제 전송과 비용은 줄지만, 전체 보고서 비용이 항상 감소한다는 보장은 없다.

운영 기본값은 **OFF**이며 `NEWS_ANALYSIS_EXACT_CACHE=1`로 명시적으로 선택한다. 미설정·`0`·오타는 OFF다. 캐시 자체와 호출 몫 보존의 안전성은 아래 시험으로 확인했다. coordinator는 비용 최소화가 우선이고 전체 비용 증가 반례가 있으므로 이번 배포에서도 기본 OFF를 유지하기로 결정했다. 배포·실제 외부 호출·성능 측정은 하지 않았다.

coordinator는 최초 요구의 “요청 전체 실제 금액 무조건 불증”을 확인 후 철회하고, **기존 금액 상한 불변, 미전송·미과금, 논리 호출 몫 보존, 뉴스 단계 비용 불증**으로 승인 기준을 좁혔다. 공용 `ProviderBudget` 금액 hold 및 비용 원장은 수정하지 않았다.

## 키와 저장 경계

- 모델 문자열, 정확한 `EngineBuildIdentity` wire, 기능 버전, 회사 문맥의 모든 필드, 기준일, 수집 정책의 모든 필드를 결속한다.
- 순서 있는 후보의 **모든 metadata**, 각 기사의 잘리기 전 본문 SHA-256, 분석 입력 본문 SHA-256, 정확한 prompt·schema·max_tokens를 결속한다. prompt가 같아도 잘린 뒤쪽 본문이 바뀌면 miss다.
- 키로는 위 입력 전체를 직렬화한 SHA-256만 저장한다. 회사 문맥·본문·prompt·인증정보·provider usage는 저장하지 않는다.
- 응답의 `entity_evidence`, `text`, `time_evidence`, `subject_evidence`는 정수 범위로 바꿔 저장한다. 새로 fetch한 본문 없이는 인용을 복원할 수 없다. 짧은 사건명·닫힌 분류값 등 검증된 분석 metadata만 유지하며, 사건명·대상명이 본문에 포함되면 이것도 범위로 저장한다.
- TTL 300초, LRU 128개, 직렬화 값 합계 4 MiB, 항목당 64 KiB. TTL은 저장 시점 기준이고 적중으로 연장하지 않는다. 키·만료 시각·값을 함께 checksum으로 검증해 다른 키의 항목 이동, 손상, 임의 만료 연장을 miss 처리한다.
- 읽기·쓰기·퇴출은 잠금 안에서 수행한다. 반환/저장 객체는 깊은 복사로 격리한다. provider를 잠그거나 요청을 합치지 않아 동시 cold miss는 각각 기존 요청 예산으로 분석한다.

실제 전송이 정확히 1회이고, 정상 종료(`end_turn`)·확정 usage·오류 없음이 확인된 dict 응답만 저장한다. 파싱 retry 등 여러 전송, cutoff/refusal/부분 결과/검증 탈락/usage 누락/불명확 모델·빌드는 저장하지 않는다. 문자열 응답과 정상적인 부정 판정도 보수적으로 저장하지 않는다. 캐시는 완전한 분석 묶음만 다루므로 일부 기사 결과를 다른 묶음에 끼워 넣지 않는다.

## 호출 경계와 진단

기존 순서는 `collect_from_snapshot → 분석 adapter → 원본 _ask → _MeteredMessages → ProviderBudget → provider`다. 캐시 적중을 기존 provider wrapper 안에 넣어도 요청 전체 논리 몫이 자동으로 보존되는 구조는 아니었다.

따라서 `_MeteredEngine`에 별도 `_cached_provider_call_slots`를 두고 실제 전송 예약 계수와 합해 요청 상한과 `available_provider_calls()`를 계산한다. 적중은 1개의 논리 몫만 소비한다. 실제 호출 예약, tokenizer, 금액 예약, 유료 attempt, usage event는 만들지 않는다. cache hit도 유료 phase 문맥·미확정 비용 차단·요청 횟수 상한 검사를 통과해야 한다. 재시도 응답은 저장하지 않아 2회 이상 cold 호출을 warm 1회 몫으로 축소하지 않는다.

수집기의 기존 `analysis_calls`는 적중 여부와 무관하게 증가한다. 기사 상한·분석 상한·조기 충분 판단·추가 후보 순서·본문 상한을 유지하고, 잔여 분석 예산 0이면 본문 조회나 캐시 조회를 시작하지 않는다. 현재 시간 마감도 적중 때문에 우회하지 않는다. 시간 단축 자체가 마감에 걸리던 실행의 진행 범위를 달리할 수 있으므로 실시간 실행 간 절대적인 동일 결과까지 주장하지 않는다.

기존 호환 필드 `분류AI호출`/`분석AI호출`은 논리 횟수를 유지한다. 새 진단은 다음과 같다.

| 필드 | 의미 |
| --- | --- |
| `분석논리호출` | 기존 배치 분석 슬롯 소비 수 |
| `분석provider호출` | 실제 `messages.create` 진입 수; 관측 없는 외부 콜백은 `None` |
| `분석provider미관측` | 실제 전송 수를 확인하지 못한 분석 콜백 수 |
| `분석캐시적중` | 현재 재검증·논리 몫 차감까지 성공한 적중 수 |
| `분석캐시보존호출` | 적중으로 유지한 기존 요청의 논리 호출 몫 |

`_provider_dispatch_count`는 금액 admission을 통과한 실제 SDK 호출 직전에만 증가한다. 기존 `_provider_call_count`는 admission 전 예약 수이므로, 이것을 실제 전송 진단으로 사용하지 않는다.

기능 간에는 `shared/news_analysis_port.py`의 DTO·ContextVar callback만 사용한다. 캐시 정책·저장·검증은 `news_intake`에 남고 `real.py`가 구현을 직접 import하지 않는다.

## 금액 불증의 범위와 재현된 한계

`test_real_bounded_ask_cannot_reuse_saved_logical_slot_for_optional_call`은 실제 `_v2_ask_via_provider`를 호출해 cold/warm 모두 동일한 후속 호출 수에서 `AskFatalError(call_limit=True)`가 발생하는지 확인한다. 보호 몫 0/2/5 각각 시험하고, 이후 재시도도 실제 전송 전에 차단된다. 충분한 금액 예산과 같은 작업 경로에서는 warm의 실제 호출과 비용이 뉴스 1회만큼 감소한다.

`test_monetary_headroom_can_change_optional_work_so_default_remains_off`는 금액 admission 반례다. 금액 예약 잔액을 후속 선택 호출의 예상비용과 같게 둔다. cold는 뉴스 실비가 차감되어 선택 호출이 막히지만, warm은 뉴스 실비가 0이므로 더 비싼 선택 호출이 허용된다. 두 실행 모두 기존 금액 상한 안에 있지만 **warm 전체 실비가 cold보다 높다**. 따라서 요청 전체 비용 무조건 감소를 주장하지 않는다. 전체 금액의 동일한 작업 경로까지 보존하려면 비청구 금액 hold의 별도 설계·승인이 필요하다.

## 검증

root `.venv/Scripts/python.exe`와 현재 checkout의 `PYTHONPATH=app`을 사용했다. `STORAGE_DB_PATH`를 임시 경로로 지정하고 공통 `app/conftest.py`가 각 시험 DB·관측 파일을 `tmp_path`로 격리했다. 외부 API·네트워크·실DB·비밀·push·deploy는 사용하지 않았다.

원본 `analysis_engine/tools/run_pilot.py::_ask`와 실제 `build_usage_diagnostic` 함수 AST를 읽어 실행하되, SDK 응답 및 무료 token 계수만 가짜로 제공했다. 따라서 `stop_reason`이 실제 원본 usage에 존재하며 cold 응답이 캐시를 채우고 warm이 실제 전송을 생략함을 시험한다.

- 캐시·실제 adapter·엔진 AST 계약 집중 시험: **121개 통과, 5.23초**.
- 최종 뉴스 전체 및 provider/예산 관련 회귀: **861개 통과, 17.22초**.
- 전체 `NewsCollectionResult`는 적중/실제 전송 진단만 정규화한 뒤 동일하다. 검색 횟수와 옵션, 본문 fetch 순서, 선택된 기사·조각·hash·상한·중단 사유도 동일하게 비교했다.
- 회사/정책/후보 전 필드, 모델/빌드/기준일/prompt/schema/token/순서/잘린 본문 뒤쪽 변경 miss, TTL/LRU/byte 용량, 동시성, 깊은 복사, 손상·현재 검증 변화·실패·retry·zero budget·마감·실제 bounded ask를 포함한다.

최종 회귀 범위는 `news_intake/tests` 전체, pipeline의 `test_news_analysis_exact_cache`, `test_news_call_budget`, `test_news_parallel_runtime`, `test_request_metering`, `test_real_contract`, `test_provider_parallel`, `test_request_budget_degradation`, `test_empty_section_recovery_calls`, `test_diagram_budget_metering`, budget의 `test_provider_budget`이다. `--basetemp .local-artifacts/pytest-news-exact-final`로 실행했다.

commit은 작업 완료 보고에 기록한다. 실제 비용 절감률·적중률·속도 개선 수치는 이번 offline 검증으로 추정하지 않는다.
