"""DART 후보 프로필 보강의 호출 상한과 근거 우선순위."""

from typing import Final


# 공식 이름 후보 전체를 주소와 비교한 뒤 화면에서 세 장으로 줄인다.
# 자동 확정 임계값이 아니라 무료 DART 기업개황의 요청 상한이다.
DART_PROFILE_ENRICHMENT_LIMIT: Final[int] = 15
DART_PROFILE_WORKERS: Final[int] = 3
DART_PROFILE_NO_DATA_STATUS: Final[str] = "013"

# 고유번호·종목코드와 정확한 공식명은 약어/부분 일치보다 먼저 보강한다.
DART_PROFILE_EXACT_MATCH_KINDS: Final[frozenset[str]] = frozenset(
    {"exact_id", "exact_name", "spacing", "legal_suffix"}
)

# 공식 약어 색인으로 확인된 강한 근거만 다양성 예약을 쓸 수 있다.
# token/trigram은 남은 슬롯만 사용해 정확 후보를 밀어내지 못한다.
DART_PROFILE_DIVERSE_MATCH_KINDS: Final[frozenset[str]] = frozenset(
    {"acronym_token", "acronym_reading", "acronym_cross_script"}
)
