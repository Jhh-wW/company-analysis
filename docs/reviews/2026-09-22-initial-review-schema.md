# 최초 본문 검수의 구조화 출력 적용

2026-09-22 · 최초 flat 본문 검수 JSON 재호출 감소 · 조사·계획·구현·무료 시험 기록

최초 본문 검수가 일반 문자열로 나가 JSON 형식 오류로 실패하면, 같은 12.4만 자 입력을 스키마를 붙여 다시 보냈다. 이 문서는 «최초 요청부터» 이미 구현된 구조화 출력 계약을 붙여 그 재호출을 줄이는 좁은 수정의 조사·계획·시험 기록이다. 실제 공급자 속도·절감액은 새 유료 실측 전에는 확정하지 않는다.

## 1. 실측 근거 (수정 전)

- 실행 `e5f7dcb26977d772eb8730e70f435172`의 `diagnostics.json`에 `8_본문검수_응답판독` 관측이 있다. 시도 1은 경로 `flat`, 판독 `json_syntax`, 추출방식 `failed`, 입력 123,891자, 응답 10,438자, 요청 81건, 응답행 0, json시작offset 8, json끝offset 10,433이다.
- 시도 2(재요청)는 입력 123,950자(+59자 = `RETRY_REMINDER`), 응답 10,761자, 81건 모두 판독됐다. 원장 순번 14·15의 원가는 202.54원·208.94원, 합계 411.48원이다([병목 검토 2절](2026-09-22-fixed-next-bottleneck-review.md)).
- 첫 답은 JSON 앞에 8자 서두가 있었고 본문 안에서 문법이 깨졌다. 구조화 출력은 응답을 스키마 문법으로 제약하므로 이 종류의 실패(서두·문법)를 구조적으로 막는다. 의미 오류(번호 누락·장 오인·인용 불일치)는 막지 않으며 기존 검증기가 그대로 거절한다.

## 2. 코드 조사 결과 (소스 `4b674a33`, 작업 트리 기준)

| 위치 | 확인 내용 |
|---|---|
| `composer/verify.py::_ask_verdicts` 2258~2273행 | 첫 호출은 `_build_review_prompt` 결과(일반 str 또는 캐시 ON 시 `PromptMetadata`) 그대로. 파싱 실패 후에만 `ReviewPrompt(prompt + RETRY_REMINDER, FLAT_REVIEW_SCHEMA)`. `PARSE_RETRY_LIMIT=1`. |
| `composer/verify.py` 2227~2229행 | `initial_ask is not None` 이 곧 «최초 본문 검수»의 식별자다. 재작성 재검수(`_recheck_rewritten`)·요약·`verify_sentences` 기본 경로는 `initial_ask=None`. |
| `composer/pipeline.py` 1625·1682·1995행 | 운영은 `initial_reviewer_ask`를 항상 넘기고 FULL은 `_CallLedgerRecorder.wrap`으로 감싼다. 래퍼는 프롬프트 객체를 그대로 `ask(prompt)`에 전달하므로 `response_schema` 속성이 보존된다. |
| `composer/review_schema.py` | `ReviewPrompt(PromptMetadata)`는 `cache_prefix_chars`를 원문에서 이어받고 `+`·`+=`에서 스키마·경계를 보존한다. `FLAT_REVIEW_SCHEMA` 직렬화 2,391자, SDK 변환 후에도 2,391자. |
| `pipeline/real.py::_v2_ask_via_provider` 5844~5875행 | `cache_prefix_chars`로 두 블록을 만들고, `response_schema`가 있으면 `output_config.format.json_schema`를 붙인다. 두 표식은 이미 재요청에서 동시에 쓰이며 서로 독립이다. |
| `pipeline/real.py::_MeteredMessages._create_admitted` 1291~1315행 | `output_config`는 `_provider_output_config`로 SDK 정규화 뒤 전송하고, 무료 계수 `count_input_tokens`에도 같은 설정을 넘긴다. 예약액은 «상한×단가»라 스키마 유무로 바뀌지 않고, 정산은 실제 usage다. |
| `pipeline/real.py` 6750~6762행 | 최초 검수 상한 24,000, 재요청 상한은 첫 답 실제 출력×1.5(하한 12,000). 이 수정으로 바꾸지 않는다. |
| `pipeline/evidence_reclassify_step.py` 596행 | 근거 재판정은 이미 «첫 요청부터» `output_config`를 보낸다 — 최초 요청 구조화 출력의 기존 선례다. |
| grouped·diagram | `_ask_grouped_verdicts`·`diagram_check._review_rows`는 별도 함수이며 이 수정이 닿지 않는다. |

## 3. 계획 (조정자 승인 대상)

**수정 범위(제안).** `_ask_verdicts` 안에서 `initial_ask is not None`일 때만 첫 호출 프롬프트를 `ReviewPrompt(prompt, FLAT_REVIEW_SCHEMA)`로 포장한다. 관측(`_observe_attempt`)에도 같은 객체를 넘긴다. 재요청 줄(`ReviewPrompt(prompt + RETRY_REMINDER, FLAT_REVIEW_SCHEMA)`)·호출자 선택·`PARSE_RETRY_LIMIT`·`_apply_grounding` 인자는 한 글자도 바꾸지 않는다. 별도 helper 모듈은 만들지 않는다 — 인라인 3줄이 verify.py 접촉면이 가장 작다.

**불변 항목.** 전송 문자열 바이트(`str(prompt)`)·캐시 경계(`cache_prefix_chars` 승계)·모델·온도·출력 상한·호출자(`initial_ask`/`initial_retry_ask`)·정산 경로·의미 검증기(`_parse_verdicts`→`_apply_grounding`) 전부 그대로다. 후속 재검수(`initial_ask=None`)는 예전처럼 일반 문자열로 시작하고 재요청만 스키마다.

**대안 B(모든 flat 첫 호출에 스키마)를 택하지 않는 이유.** 실측 중복은 최초 검수 12.4만 자 한 곳이다. 재검수·요약 검수는 입력이 작고 기존 시험 9건이 «첫 호출 일반 문자열» 계약을 고정하고 있어 접촉면이 넓어진다.

**파일 경계(승인 요청).**
- 수정: `app/src/features/composer/verify.py` `_ask_verdicts` 2258~2262행 부근만(다른 함수 불가침, 병행 수정자와 겹치지 않게 최소 diff).
- 기대값 갱신 2건: `composer/tests/test_review_schema.py::test_flat_native_retry_preserves_initial_retry_callable`(첫 호출 `type is str` → `ReviewPrompt`), `pipeline/tests/test_native_review_retry_integration.py::test_only_initial_parse_retry_sends_schema_with_separate_cap`(첫 요청 `output_config` 기대 추가). 두 시험 모두 `initial_ask`를 넘기는 경우라 새 계약과 정면 충돌한다.
- 선택: `composer/review_schema.py` 모듈 docstring 첫 문단(「최초 검수에는 스키마를 더하지 않는다」) 갱신. 코드 변경 없음.
- 신규: `composer/tests/test_initial_review_schema.py`, `pipeline/tests/test_initial_review_schema.py`(계량 경계 시험은 pipeline→composer 방향 의존만 허용되므로 pipeline feature 안에 둔다).
- `pipeline/real.py`는 수정하지 않는다(필요 없음).

## 4. 시험 계획 (무료, 가짜 공급자)

composer 신규 파일:
1. 최초 검수(initial_ask 있음)는 첫 호출부터 `response_schema is FLAT_REVIEW_SCHEMA`, `str(prompt)` 바이트가 `_build_review_prompt` 결과와 같고, 정상 응답이면 호출 1회.
2. `initial_ask` 없는 재검수는 첫 호출 일반 str, 재요청만 스키마(기존 계약 유지).
3. 스키마 첫 답도 판독 실패면 `initial_retry_ask`로 `prompt + RETRY_REMINDER` 스키마 재요청 1회, 3회째 없음. 재요청 실패·예외·`AskFatalError` 처분은 기존과 동일.
4. 처분 동등성: 같은 응답 대본(정상 / 번호 누락 / 장 오인 / 인용 불일치 / 계약 밖 판정값 / 수치 증명 없는 참 / JSON 불량)을 스키마 첫 호출 경로와 일반 첫 호출 경로에 재생해 반환 사전·`diagnostics`·`grounding_problems`·프로토콜 관측이 같다.
5. 캐시 ON/OFF: 첫 프롬프트의 `cache_prefix_chars`가 캐시 표식 원문과 같고(ON>0, OFF=0), 재요청도 같다.

pipeline 신규 파일:
6. 계량 경계: 첫 요청에 `output_config`(SDK 정규화 스키마)와 상한 24,000이 실리고, 무료 계수 `count_tokens`에도 같은 설정이 전달되며, 예약·정산·관측 수가 호출 수와 같다. 첫 답 실패 시 재요청 상한이 첫 답 출력×1.5로 잡히는 기존 동작 유지. 캐시 ON이면 두 블록+스키마가 함께 실린다.

실행: `.venv/Scripts/python.exe -X utf8 -m pytest -p no:cacheprovider` 로 관련 파일만, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `STORAGE_DB_PATH=app/.local_evaluation_runs/initial-schema-fix/isolated/storage.db`(부모 생성), `app/conftest.py` 유지.

## 5. 위험과 실측 전 미확정 항목

- 스키마는 입력에 더해진다(2,391자). 첫 호출 입력이 그만큼 늘고 첫 문법 컴파일 지연이 붙을 수 있다. 크기·지연·실제 재요청 감소율은 새 실측 전 확정하지 않는다.
- 스키마 적합 ≠ 의미 합격. 번호 누락·장 오인·근거 불일치·문화 부적합은 기존대로 거절된다(시험 4).
- 상한 절단(`max_tokens`)·거부(refusal)로 빈 답이 오면 예전처럼 재요청 1회 뒤 fail-closed다. 새 복구 경로를 만들지 않는다.
- 411.48원 전부가 절감액이 아니다. 유효한 첫 답의 비용은 그대로 필요하며, 스키마가 붙어도 형식 재요청이 0이 된다는 보장은 없다.

## 6. 구현·시험 결과

조정자가 22:14(13:14Z)에 A~D 전부를 승인했다(`_ask_verdicts`·`review_schema.py`는 이 작업 담당, 병행 수정자는 다른 후보 루프의 가드·import만). 통합 HEAD `999c46fe` 위에서 작업했고 `verify.py` diff는 아래 한 덩어리뿐이다.

**구현(A).** `_ask_verdicts` 첫 호출 직전에 `initial_prompt = ReviewPrompt(prompt, FLAT_REVIEW_SCHEMA) if initial_ask is not None else prompt` 를 두고 `_safe_ask`·`_observe_attempt`에 그 객체를 넘긴다(코드 4줄 + 주석). 재요청 줄·호출자 선택·`PARSE_RETRY_LIMIT`·`_apply_grounding` 인자는 그대로다. `ReviewPrompt.__new__`가 원문의 `cache_prefix_chars`를 이어받으므로 캐시 ON이면 경계가 보존되고 OFF면 0이다. `real.py`는 손대지 않았다 — 계량 경계가 이미 `response_schema` 표식만으로 `output_config`를 붙인다.

**기대값 갱신(B)·설명문(C).** `test_flat_native_retry_preserves_initial_retry_callable`은 첫 호출을 `ReviewPrompt`로, `test_only_initial_parse_retry_sends_schema_with_separate_cap`은 첫 요청 `output_config`를 기대하도록 바꿨다(시험 이름은 다른 문서가 참조하므로 유지). `review_schema.py` 모듈 설명문 첫 문단을 새 계약으로 고쳤다. 코드 변경은 없다.

**신규 시험(D).** `composer/tests/test_initial_review_schema.py` 34건, `pipeline/tests/test_initial_review_schema.py` 7건 (`--collect-only`로 확인).

| 시험 | 확인 내용 |
|---|---|
| 첫 요청 계약 (캐시 OFF/ON) | 첫 프롬프트가 `ReviewPrompt`, 스키마 동일 객체, UTF-8 바이트가 `_build_review_prompt` 결과와 같음, `cache_prefix_chars` 승계, 정상 응답이면 호출 1회 |
| 스키마 첫 답 실패 (invalid/broken/error × 전용 재요청 유무) | `initial_retry_ask`(없으면 `initial_ask`)로 `prompt + RETRY_REMINDER` 스키마 재요청 정확히 1회, 3회째 없음, 캐시 경계 유지 |
| 재요청 소진·치명 오류 | `None` 반환·`AskFatalError` 재전파, 관측 판독 `json_syntax` 2회 |
| 후속 재검수 | `initial_ask` 없는 `_ask_verdicts`·`_recheck_rewritten`은 첫 호출 일반 str, 재요청만 스키마 |
| 처분 동등성 15대본 | 정상·거짓·애매·번호누락·요청밖번호·계약밖판정·모순중복·장오인·수치증명일치·수치증명없는참·인용불일치·JSON불량·빈응답·재요청소진·호출오류를 두 경로에 재생해 반환 사전·`diagnostics`·프로토콜 관측·`grounding_problems`·`section_moves`·전송 바이트·호출 수가 같음 |
| 스키마 적합 ≠ 의미 합격 | jsonschema로 유효한 행이 번호 누락(미응답 1)·수치 증명 누락(`semantic_grounding_missing`)·인용 불일치(`semantic_grounding_invalid`)로 그대로 거절됨 |
| 보고서 수준 | `verify_report(initial_ask=…)`에서 번호 2 누락 문장이 두 경로 모두 제거되고 결과 보고서·진단이 같음 |
| 계량 경계 (pipeline) | 첫 요청에 SDK 정규화 스키마·상한 24,000이 실리고 무료 계수에도 같은 설정 전달, 재요청 상한 12,000/15,000/15,002 유지, 예약·정산·관측 수 = 호출 수, 캐시 ON이면 두 블록 + 스키마 + 1.25배 예약 |

**flat 파서와 «장» 필드.** flat 경로의 `_parse_verdicts`는 응답의 `장`을 읽지 않고 번호로 소유를 정한다(장 검사는 grouped 경로 `_parse_grouped_verdicts`의 계약). «장오인» 대본은 두 경로가 같은 처분(`참`)을 내는 것을 증명하며, 이 수정은 그 계약을 바꾸지 않는다.

**실행 기록.** 루트에서 `.venv/Scripts/python.exe -X utf8 -m pytest -p no:cacheprovider`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, `STORAGE_DB_PATH=app/.local_evaluation_runs/initial-schema-fix/isolated/storage.db`(부모 생성), `app/conftest.py` 유지.

| 실행 | 결과 |
|---|---|
| 수정 전 기준선: `test_review_schema`·`test_review_prompt_cache`·`test_native_review_retry_integration` | 129 통과, 31.32초 |
| 수정 전 신규 2파일 | 33 실패·8 통과, 3.37초 — 실패는 전부 «첫 호출이 str» 계약 위반이라 시험이 기능을 지키는 것을 확인. 대본 1건(«수치증명없는참»)은 원문에 글자 그대로 있는 수치라 증명 없이도 참이 맞았고, 단위 환산 쌍으로 고쳤다 |
| 수정 후 신규 2파일 + 기존 3파일 | 170 통과. 첫 실행은 4,320.63초(1시간 12분)가 걸렸고 같은 명령 재실행은 신규 41건 4.66초·기존 129건 16.83초였다. 첫 실행 지연의 원인은 확인하지 못했다(시험 안에 대기·네트워크가 없고 재실행이 정상이라 동시 실행 중이던 다른 작업의 자원 경합으로 추정). |
| `initial_ask`·`_ask_verdicts`를 쓰는 8파일 | 166 통과, 4.36초 (`test_initial_review_retry_ask`·`test_initial_reviewer_call_separation`·`test_verdict_number_coercion`·`test_v2_ask_reserved_calls_wiring` 포함). 앞 실행과 겹치므로 합산하지 않는다. |
| `git diff --check` | 통과 |

전체 composer·pipeline 묶음은 돌리지 않았다. 유료 호출·서버 실행·운영 DB·`.env` 접근·git checkout/commit/reset은 없었다.

**총괄 후속 회귀.** 작업자 완료 후 composer 전체와 pipeline의 최초 검수·네이티브 재시도·계량 시험, 뉴스 실제 동시 수집 시험을 함께 실행했다. 3,309건은 통과했고, 화면/PDF 이음매 5건만 Windows 임시 경로 길이 제한으로 실패했다(83.93초). 저장소 밖의 짧은 새 임시 경로에서 실패한 5건을 다시 실행해 모두 통과했다(9.99초). 따라서 해당 묶음의 3,314건을 확인했으며, 최초 실패 원인과 별도 재확인 사실을 생략하지 않는다. 동일한 격리 설정과 `app/conftest.py`를 유지했고 유료 API 호출은 없었다. 총괄은 제품 주석의 과거 실측 수치를 이 문서로 모아 설명만 간결하게 다듬었다.

## 7. 추정과 실측의 차이 (확정 금지 항목)

- **추정.** 실측 실행에서 첫 답이 스키마로 바로 읽혔다면 재요청 1회(입력 123,950자·208.94원)가 없었을 것이다. 대신 첫 요청 입력이 스키마 직렬화 2,391자만큼 는다. 두 값 모두 이 코드 변경 «전» 실측에서 나온 숫자이고, 변경 후 실제 절감액이 아니다.
- **실측 전 확정 금지.** 실제 공급자에서의 첫 답 파싱 성공률, 구조화 출력의 첫 문법 컴파일 지연, 응답 길이 변화(필수 `근거대조` 필드), 재요청 발생률, 절감 원가. 한 번의 유료 생성으로도 «재요청 0회»를 일반화할 수 없다.
- **바뀌지 않았음을 무료로 증명한 것.** 전송 문자열 바이트·캐시 경계·상한·호출자·정산 경로·의미 검증기의 처분. 이는 «같은 응답이면 같은 처분»이지 «같은 응답이 온다»는 뜻이 아니다.
