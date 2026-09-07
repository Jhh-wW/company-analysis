"""회사 후보 탐색 경계의 정책값.

후보 탐색은 본조사나 DART 호출보다 앞에서 실행되는 무료 보조 단계다. 외부 공급자를
붙일 때도 이 상한을 우회할 수 없게 값은 한곳에 둔다.
"""

from typing import Final

ENV_PROVIDER: Final[str] = "BUSINESS_CANDIDATE_PROVIDER"
PROVIDER_DISABLED: Final[str] = "disabled"
PROVIDER_PIPELINE: Final[str] = "pipeline"
PROVIDER_GOOGLE_PLACES: Final[str] = "google_places"
ENV_GOOGLE_PLACES_API_KEY: Final[str] = "GOOGLE_PLACES_API_KEY"
ENV_GOOGLE_PLACES_BILLING_ACK: Final[str] = "GOOGLE_PLACES_BILLING_ACK"

# 한 화면에 후보가 너무 많으면 사람이 근거를 비교할 수 없고, 공급자 호출량도 커진다.
MAX_CANDIDATES: Final[int] = 3
MAX_RAW_CANDIDATES: Final[int] = 15
PROVIDER_CALLS_PER_RESOLUTION: Final[int] = 1
PROVIDER_TIMEOUT_SEC: Final[float] = 8.0

# 후보 선택 HMAC과 서버 메모리 attempt/grant가 공유하는 유효기간.
CANDIDATE_ATTEMPT_TTL_SEC: Final[int] = 300

# DART local 후보의 첫 요청은 1회성 corpCode 다운로드와 XML parse/index를
# 포함할 수 있어 외부 검색의 8초 UX 상한과 분리한다. 관측된 cold 약 14초에
# 여유를 둔 30초까지만 웹 요청이 기다린다. 기존 내부 HTTP timeout은 별도로
# bounded이며, outer timeout 뒤 worker slot은 실제 완료까지 유지해 thread 폭증을 막는다.
LOCAL_DART_PROVIDER_TIMEOUT_SEC: Final[float] = 30.0
MAX_PROVIDER_TIMEOUT_SEC: Final[float] = LOCAL_DART_PROVIDER_TIMEOUT_SEC

# 후보 응답의 어느 문자열도 이보다 길게 화면·점수 계산 경계 안으로 들이지 않는다.
# `/confirm`의 회사명 계약과 같아야 후보 버튼이 눌린 뒤 422가 되지 않는다.
MAX_NAME_CHARS: Final[int] = 120
MAX_ADDRESS_CHARS: Final[int] = 500
MAX_URL_CHARS: Final[int] = 2048
MAX_SOURCE_LABEL_CHARS: Final[int] = 80

# 후보 탐색 자체의 별도 남용 방지. 본조사 횟수·비용 장부와 섞지 않는다.
RATE_WINDOW_SEC: Final[int] = 60
RATE_MAX_SEARCHES: Final[int] = 6

# 낮은 점수의 검색 잡음은 보여주지 않는다. 이 값을 넘겨도 자동 확정하지 않는다.
MIN_CANDIDATE_SCORE: Final[float] = 0.25

# websiteUri가 포함된 Text Search Enterprise 공개 정가(USD 35/1,000
# 기준) × 서비스의 보수적 환율 1,400원. 무료 구간이어도 예산 admission은
# 최악값으로 잡는다. 청구서의 실제 할인·무료 사용량과 동일하다는 뜻은 아니다.
GOOGLE_PLACES_ACCOUNTING_COST_KRW: Final[float] = 49.0

# ── AI 보조 재정렬 ──────────────────────────────────────────────
# 결정적 규칙으로 순위가 갈리지 않을 때만 도는 보조 단계다. 자동 확정 경로가
# 아니며, 사람이 후보를 고르는 절차는 그대로다.
CANDIDATE_RERANK_MODEL: Final[str] = "claude-haiku-4-5"
# 1위와 같은 점수의 후보가 이 수 이상일 때만 AI에 물어본다. 셋 이하이면 화면
# 상한(MAX_CANDIDATES)과 같아 어차피 전부 보이므로 돈을 쓸 이유가 없다.
AI_RERANK_MIN_TIE: Final[int] = 4
# AI에 보내는 후보 수. 원시 후보 상한과 같아 정렬 대상이 잘리지 않는다.
AI_RERANK_MAX_CANDIDATES: Final[int] = MAX_RAW_CANDIDATES
# 공급자 호출과 같은 UX 상한 안에서만 응답을 기다린다.
AI_RERANK_TIMEOUT_SEC: Final[float] = PROVIDER_TIMEOUT_SEC
# 응답은 정수 목록 하나뿐이라 짧다. 길게 열어 두면 설명을 덧붙일 여지만 준다.
AI_RERANK_MAX_OUTPUT_TOKENS: Final[int] = 200
# 부동소수 점수를 같은 점수로 볼 오차. 같은 입력은 같은 값이 나오므로 아주 좁게 둔다.
AI_RERANK_TIE_EPSILON: Final[float] = 1e-9
# 운영 스위치. 값이 없거나 "1"이면 켜짐이고, 그 밖의 값(오타 포함)은 모두 꺼짐이다.
CANDIDATE_AI_RERANK_ENV_NAME: Final[str] = "CANDIDATE_AI_RERANK"
CANDIDATE_AI_RERANK_ENV_ON: Final[str] = "1"
# 재정렬 한 번의 «호출 전» 예약액. 실제 청구가 아니라 provider 호출 직전에 잡는
# 방어적 상한이라 실측 청구액보다 훨씬 크다. 생산 추정기
# (`budget/provider_budget.py::estimate_request_tokens_exact` → `usage_cost_krw`)로
# 잰 값이 근거다.
#   · 고정 여유 REQUEST_ESTIMATE_MARGIN_TOKENS(4096)만으로 5.74원
#   · 출력 상한 200 token으로 1.40원
#   · 후보 15개 프롬프트(4,105 byte)의 바이트 추정 입력 8,840 token → 합계 13.78원
#   · 후보 3개면 9.48원, 실제 청구 예상은 1.89원(입력 1,200·출력 30 기준)
# provider가 입력 token을 세어 주면 추정이 줄지만, 세어 주지 못하면 위 바이트
# 추정으로 되돌아간다. 그 최악값 13.78원에 여유를 얹어 20원으로 잡는다.
# 미사용 예약은 정산에서 그대로 풀린다. 이 값이 부족하면 재정렬 호출이 전송 전에
# 거절되므로 `pipeline/tests/test_business_candidates.py`가 추정액을 직접 재서 못 박는다.
AI_RERANK_RESERVE_KRW: Final[float] = 20.0
