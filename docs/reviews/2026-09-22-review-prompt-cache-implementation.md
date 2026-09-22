# 검수 고정 지침 캐시 구현 및 무과금 검증

2026-09-22 · 검수 캐시 담당 작업 · 기본값 OFF

검수 지침·원문·문장 순서·검증 기준을 바꾸지 않고 고정 지침 끝에 캐시 메타데이터를 붙였다. 환경변수 `COMPOSER_REVIEW_PROMPT_CACHE_ENABLED=1`일 때만 활성화하며, 미설정·`0`·`false`·`off` 및 알 수 없는 값은 기존 문자열 경로를 유지한다. 대소문자를 구분하지 않고 `1`, `true`, `yes`, `on`을 활성화 값으로 받는다. 총괄과 기본 OFF로 조율했다.

| 검수 경로 | 캐시 접두부 | 개별 정보 경계 |
|---|---:|---|
| 일반 본문 | 10,580자 | 장별 작성 범위·표·근거·후보 앞 |
| 장별 본문·도식 묶음 | 11,722자 | 첫 장별 검수 블록 앞 |
| 별도 도식 | 8,200자 | 카드 여부별 추가 지침·근거 사전·행 앞 |

표는 기본 검수 지침 모드의 문자 수다. 구현은 위 숫자를 하드코딩하지 않고 각 builder가 개별 입력을 붙이기 직전에 실제 지침 길이를 계산한다. 결합 관계 검수가 관측/강제 모드로 달라지면 해당 모드의 지침을 그대로 읽어 별도 접두부가 된다. 모드별 지침을 합치거나 원문을 잘라 캐시를 만들지 않는다.

`composer/prompt_metadata.py`의 `PromptMetadata`는 `str` 하위형으로 `cache_prefix_chars`를 운반한다. 뒤에 재시도 문구를 붙이면 기존 경계를 유지하고, 앞에 빈 문자열이 아닌 새 문구를 붙이면 그 내용이 개별 근거일 수 있어 캐시 경계만 0으로 해제한다. `ReviewPrompt`는 이 동작을 상속하며 기존 `response_schema`를 함께 보존한다. 일반·도식 형식 재시도의 `ReviewPrompt(prompt + RETRY_REMINDER, schema)` 호출은 원문과 스키마를 유지한 채 같은 캐시 경계를 전달한다. 최초 검수에 스키마를 새로 붙이거나 묶음 검수에 재시도를 추가하지 않았다.

공급자는 기존 `cache_prefix_chars` 계약으로 고정 접두부와 나머지 문자열을 분리한다. 두 블록을 다시 이어 붙이면 기존 프롬프트와 같다. 모델·온도·출력 상한·검수 응답 스키마 자체는 이 변경에서 수정하지 않았다. 실제 builder와 기존 `test_native_review_retry_integration.review_calls` 가짜 공급자를 연결해 일반·묶음·도식의 ON/OFF 요청 10개를 추가 대조했다. 첫 블록에만 `ephemeral` 표식이 붙고, 두 블록을 원문으로 복원하면 모델·온도·출력 상한·스키마를 포함한 전체 요청이 OFF와 일치했다. 일반·도식은 형식 재시도까지, 묶음은 기존 단일 호출까지 확인했다. 공급자 담당에게 이 계약과 결과를 전달했다.

**확인한 시험**

프로젝트 `.venv/Scripts/python.exe`와 가짜 호출자로 실행했다. 유료 모델 호출·배포·커밋·푸시는 하지 않았다.

- 변경 전 검수 스키마·직렬화·재시도·기존 공급자 캐시 경계: 107개 통과.
- 변경 후 신규 캐시 계약·기존 스키마·본문 직렬화·도식 직렬화·형식 재시도: 145개 통과.
- 캐시 ON에서 기존 의미·수치·도식·뉴스 근거 범위·형식 재시도: 129개 통과.
- 기존 공급자 통합 시험의 로컬 fixture를 통한 별도 대조: ON/OFF 가짜 요청 10개가 캐시 블록 형식 외 완전 일치.

신규 `test_review_prompt_cache.py`는 ON/OFF 전체 UTF-8 일치, 캐시 도입 전 골든 프롬프트 SHA-256 네 건과의 일치, 입력 길이/후보/장 변경에도 동일한 접두부, 장별 개별 근거 경계와 중복 ID 처리 보존, 검수 모드별 접두부 분리, 형식 재시도의 호출 수·결과·원문·캐시 경계·스키마 동시 보존, 잘못된 경계 거부를 확인한다. 원래 프롬프트의 공백·순서·줄바꿈을 바꾸지 않는다.

변경 후 표적 실행 명령:

```powershell
.venv/Scripts/python.exe -m pytest app/src/features/composer/tests/test_review_prompt_cache.py app/src/features/composer/tests/test_review_schema.py app/src/features/composer/tests/test_review_prompt_serialization.py app/src/features/composer/tests/test_diagram_prompt_compact_json.py app/src/features/composer/tests/test_initial_review_retry_ask.py -q -p no:cacheprovider

$env:COMPOSER_REVIEW_PROMPT_CACHE_ENABLED = '1'
.venv/Scripts/python.exe -m pytest app/src/features/composer/tests/test_verify.py app/src/features/composer/tests/test_verify_boundaries.py app/src/features/composer/tests/test_diagram_check.py app/src/features/composer/tests/test_diagram_review_evidence.py app/src/features/composer/tests/test_review_news_evidence_scope.py app/src/features/composer/tests/test_initial_review_retry_ask.py -q -p no:cacheprovider
```

**비용 판단과 활성화 조건**

[비용 개선 제안](2026-09-22-report-cost-quality-proposal.md)의 가정은 Haiku 4.5의 5분 캐시 쓰기 1.25배·읽기 0.1배 및 최소 4,096토큰이다. 이 작업은 그 요금을 새로 측정하거나 공급자 최소 토큰 충족 여부를 실측한 것이 아니다. 10,580/11,722/8,200은 문자 수이므로 API 토큰 수로 환산해 적중을 보장하지 않는다. 로컬에서 부정확한 글자 수 임계값이나 추정 토크나이저로 입력을 변경하지 않았다.

같은 접두부가 한 번만 쓰이면 할증만 생길 수 있다. 따라서 캐시 준비용 별도 호출·자동 활성화·캐시 적중을 가정한 호출 장부 절감은 추가하지 않았다. 반복 검수 수요가 있는 환경에서만 설정을 켠 뒤 실제 응답 모델별 `cache_creation_input_tokens`, `cache_read_input_tokens`, 일반 입력/출력 토큰과 총 원가를 확인해야 한다. 재시도에서 응답 스키마가 달라지는 경우와 캐시 만료·최소 토큰 미달은 별도로 관측한다. 오프라인 시험 통과는 실제 적중률·절감률의 증명이 아니다.

**다음 절감 후보: 변경 없는 장의 작성 결과 재사용**

다음 작업은 작성 호출만 재사용하고 현재 자료에 대한 전체 검수는 매번 수행하는 방향이 적합하다. 저장 열쇠에는 장 ID, 최종 작성 프롬프트의 정확한 해시, 해당 장의 모든 근거·출처·기간·반대 근거, 모델과 생성 설정, 프롬프트/검증 계약 버전, 사용자 맞춤 입력을 결속한다. 장별 입력이 동일하다는 증명 없이 회사명이나 문장 유사도만으로 재사용하지 않는다.

성공적으로 파싱한 정상 종료 결과만 저장 대상으로 삼고 형식 실패·부분 결과·출력 잘림·확인할 수 없는 종료 상태는 제외한다. 원래 생성의 모델·입력·응답 해시와 성공 상태를 재사용 영수증에 남긴다. 재사용은 실제 AI 호출이 아니므로 가짜 작성 호출을 장부에 넣지 않고, 현재 실행의 장별 완료 계약이 실제 호출 영수증 또는 검증 가능한 재사용 영수증을 받도록 별도로 확장해야 한다.

새 자료 때문에 해당 장의 기간·전제·반대 근거가 바뀌면 재사용하지 않는다. 전역 뉴스 기준일이 바뀌었다는 이유만으로 무조건 재사용하거나 무조건 적중시키지 말고 해당 장에 영향을 주는 입력 범위를 먼저 증명한다. 새 장과 재사용 장을 모은 뒤 사실 소유권·중복 제거·전체 본문/도식 검수·요약 결속·웹/PDF 출고 검증을 유지한다. 최초 생성에는 재사용 절감이 없고 검수 비용을 작성 재사용률만큼 줄었다고 계산하지 않는다.

이번 변경 범위는 검수 builder 두 파일, `review_schema.py`, 신규 메타데이터/설정 파일과 composer 내부 시험이다. 다른 담당자가 수정하는 `composer/logic.py`, `composer/pipeline.py`, `composer/constants.py`, `pipeline/real.py`는 편집하지 않았다.
