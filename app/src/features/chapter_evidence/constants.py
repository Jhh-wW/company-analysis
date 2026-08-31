"""근거 후보 생산부의 예산·추정 비율·회사유형별 기대 경로 상수.

매직 넘버를 코드 곳곳에 흩어놓지 않기 위해 여기 한 곳에 모은다. 값을 바꿀
일이 생기면 이 파일만 고치면 된다 (rules/general.md 매직 넘버 금지 정합).
"""

from __future__ import annotations

from enum import Enum
from typing import Final

from src.shared.report_evidence.constants import CollectionState
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS


CHAPTER_EVIDENCE_PRODUCER_VERSION: Final[str] = "chapter-evidence-producer-v1"

# 근거 원문 문자 수 → 예상 토큰 추정 비율.
# 한국어 위주 원문은 토크나이저별로 평균 1토큰≈2~2.6자 범위를 보인다. 예산을
# 실제보다 «적게» 추정해 나중에 호출이 넘치는 사고를 피하려고 범위의 낮은 쪽인
# 2.2자/토큰(=더 많은 토큰으로 추정)을 안전 마진으로 쓴다.
CHARS_PER_ESTIMATED_TOKEN: Final[float] = 2.2

# 장 하나가 받는 근거 원문 문자 예산. 수집 슬롯이 가장 많은 장(business_model,
# 3칸)이 슬롯당 여유 있게(약 3천자) 채워도 넘치지 않도록 잡았다.
DEFAULT_MAX_CHARS_PER_SECTION: Final[int] = 12000

# 위 문자 예산을 CHARS_PER_ESTIMATED_TOKEN 로 전부 환산해도 남는 여유를 두어,
# 문자 예산이 실질적인 상한이 되고 토큰 예산이 우발적으로 먼저 걸리지 않게 한다.
DEFAULT_MAX_ESTIMATED_TOKENS_PER_SECTION: Final[int] = 6000

# build_section_bundle(logic.py)이 UNKNOWN으로 보는 두 조회 상태와 같은 집합을
# 생산부 진단에서도 그대로 쓴다 — 같은 방향의 판정이어야 하기 때문이다.
FAILURE_COLLECTION_STATES: Final[frozenset[CollectionState]] = frozenset(
    {CollectionState.FAILED, CollectionState.TRUNCATED}
)
# 정상적으로 확인했지만 자료가 없었던 상태 — INSUFFICIENT 방향의 근거가 된다.
OBSERVED_ABSENCE_COLLECTION_STATES: Final[frozenset[CollectionState]] = frozenset(
    {CollectionState.OK, CollectionState.MISSING}
)


class CompanyType(str, Enum):
    """생산부가 아는 회사 자료 형태 — 어느 조회 경로가 필수인지만 바꾼다.

    슬롯 요구 자체(REQUIRED_EVIDENCE_SLOTS_BY_SECTION)는 유형과 무관하게 항상
    같다. 유형이 바꾸는 것은 «그 슬롯을 확인하는 정상 경로가 공시인지 공식
    웹인지» 뿐이다.
    """

    LISTED = "listed"
    AUDIT_ONLY = "audit_only"
    FINANCIAL = "financial"


# CollectionAttempt.source_kind 접두어 — 실제 접두어는 수집기(claude-a-dart,
# claude-b-web) 쪽 상수가 정본이다. 여기서는 그 접두어«패턴»만 참조한다.
DART_SOURCE_KIND_PREFIX: Final[str] = "dart_"
OFFICIAL_SOURCE_KIND_PREFIX: Final[str] = "official_"

_LISTED_DEFAULT_PREFIXES: Final[dict[str, str]] = {
    section_id: DART_SOURCE_KIND_PREFIX for section_id in REQUIRED_EVIDENCE_SECTION_IDS
}

# listed: 공시(dart_*)가 대부분 슬롯의 REQUIRED, 웹은 OPTIONAL 보강.
_REQUIRED_PATH_PREFIX_LISTED: Final[dict[str, str]] = dict(_LISTED_DEFAULT_PREFIXES)

# audit_only(감사보고서만 내는 회사): 사업 설명·조직문화·전략처럼 감사보고서에
# 잘 안 담기는 서술형 내용은 공식 웹이 REQUIRED. 실적·이행 같은 재무성 내용은
# 여전히 공시(감사보고서)가 REQUIRED — past_changes:completed_execution 등.
# 명시되지 않은 나머지 장(business_model·current_challenges·
# operations_partners)은 감사보고서 주석에 담기는 재무·운영 서술이라 dart_ 를
# 기본값으로 둔다. ⚠️ 확인 못 함 — 실데이터로 이 기본값을 검증하지 않았다.
_REQUIRED_PATH_PREFIX_AUDIT_ONLY: Final[dict[str, str]] = {
    **_LISTED_DEFAULT_PREFIXES,
    "identity": OFFICIAL_SOURCE_KIND_PREFIX,
    "portfolio": OFFICIAL_SOURCE_KIND_PREFIX,
    "future_strategy": OFFICIAL_SOURCE_KIND_PREFIX,
    "culture": OFFICIAL_SOURCE_KIND_PREFIX,
    "competitive_position": OFFICIAL_SOURCE_KIND_PREFIX,
}

# financial(금융형): listed와 기대 경로가 같다. 「매출액」 대신 이자수익 등
# 대체 지표 원문을 허용하는 것은 경로 정책이 아니라 select.py가 애초에 특정
# 지표 키워드로 조각을 거르지 않는 것으로 보장된다 — 여기서는 경로만 다룬다.
_REQUIRED_PATH_PREFIX_FINANCIAL: Final[dict[str, str]] = dict(_LISTED_DEFAULT_PREFIXES)

REQUIRED_PATH_PREFIX_BY_COMPANY_TYPE: Final[dict[CompanyType, dict[str, str]]] = {
    CompanyType.LISTED: _REQUIRED_PATH_PREFIX_LISTED,
    CompanyType.AUDIT_ONLY: _REQUIRED_PATH_PREFIX_AUDIT_ONLY,
    CompanyType.FINANCIAL: _REQUIRED_PATH_PREFIX_FINANCIAL,
}


def expected_required_path_prefix(company_type: CompanyType, section_id: str) -> str:
    """이 회사유형에서 이 장의 필수 슬롯을 정상 확인하는 source_kind 접두어."""

    try:
        by_section = REQUIRED_PATH_PREFIX_BY_COMPANY_TYPE[company_type]
    except KeyError as error:
        raise ValueError(f"알 수 없는 회사 유형입니다: {company_type!r}") from error
    try:
        return by_section[section_id]
    except KeyError as error:
        raise ValueError(f"알 수 없는 근거 장 식별자입니다: {section_id}") from error
