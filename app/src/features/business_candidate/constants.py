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

# websiteUri가 포함된 Text Search Enterprise 공개 정가(2026-08-11 기준
# USD 35/1,000) × 서비스의 보수적 환율 1,400원. 무료 구간이어도 예산 admission은
# 최악값으로 잡는다. 청구서의 실제 할인·무료 사용량과 동일하다는 뜻은 아니다.
GOOGLE_PLACES_ACCOUNTING_COST_KRW: Final[float] = 49.0
