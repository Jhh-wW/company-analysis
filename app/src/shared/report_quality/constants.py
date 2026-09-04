"""여러 보고서 기능이 공유하는 품질·수치 계약과 승인된 하한."""

from __future__ import annotations

from decimal import Decimal
from typing import Final

QUALITY_CONTRACT_VERSION: Final[str] = "report-quality-v1"
# 기존 v1 기본값을 바꾸지 않는다. 새 계약은 FULL 후보가 9장 모두를 실제로
# 채웠는지 명시적으로 평가할 때만 이름으로 선택한다. 과거 보고서와 현재
# SHADOW 결과를 새 규칙으로 소급 재판정하지 않기 위한 별도 버전이다.
# v2 FULL은 이미 발급된 보고서 영수증을 읽기 위해 영구 보존한다. 해석 문장
# 상한을 뒤늦게 같은 이름에 덮어쓰면 과거 영수증의 뜻이 바뀌므로, 새 생성은
# v3로 올리고 v2는 조회·무결성 재검산용으로만 남긴다.
LEGACY_STRICT_QUALITY_CONTRACT_VERSION: Final[str] = "report-quality-v2-full"
STRICT_QUALITY_CONTRACT_VERSION: Final[str] = "report-quality-v3-full"
STRICT_QUALITY_CONTRACT_VERSIONS: Final[frozenset[str]] = frozenset(
    {
        LEGACY_STRICT_QUALITY_CONTRACT_VERSION,
        STRICT_QUALITY_CONTRACT_VERSION,
    }
)
LEGACY_UNVERSIONED_CONTRACT: Final[str] = "legacy-unversioned"
NUMERIC_BINDING_VERSION: Final[str] = "numeric-binding-v1"
NUMERIC_CHECK_PREFIX: Final[str] = f"{NUMERIC_BINDING_VERSION}:"
VERIFIED_PROSE_CLAIM_TYPE: Final[str] = "verified_prose"
INTERPRETATION_CLAIM_TYPE: Final[str] = "evidence_based_interpretation"
# V2 FULL composer가 실제 공개 FactRecord로 생산하는 닫힌 종류다. 임의 문자열을
# «해석이 아니므로 사실»이라고 세면 claim_type 오타 하나가 해석 상한과 검증
# 비율을 함께 우회한다. report_standard의 과거 canonical 어휘와 섞지 않고,
# 새 FULL 생성 경로가 실제로 생산하는 네 사실 종류만 여기서 소유한다.
HISTORICAL_PERFORMANCE_RATE_CLAIM_TYPE: Final[str] = (
    "historical_performance_rate"
)
COMPETITIVE_COMPARISON_CLAIM_TYPE: Final[str] = "competitive_comparison"
COMPETITIVE_COMPARISON_CONTEXT_CLAIM_TYPE: Final[str] = (
    "competitive_comparison_context"
)
COMPARISON_PROGRAM_CLAIM_TYPES: Final[frozenset[str]] = frozenset(
    {
        COMPETITIVE_COMPARISON_CLAIM_TYPE,
        COMPETITIVE_COMPARISON_CONTEXT_CLAIM_TYPE,
    }
)
COMPARISON_JUDGMENTS: Final[frozenset[str]] = frozenset(
    {"competitive_advantage", "operating_characteristic"}
)
STRICT_FACTUAL_CLAIM_TYPES: Final[frozenset[str]] = frozenset(
    {
        VERIFIED_PROSE_CLAIM_TYPE,
        HISTORICAL_PERFORMANCE_RATE_CLAIM_TYPE,
        COMPETITIVE_COMPARISON_CLAIM_TYPE,
        COMPETITIVE_COMPARISON_CONTEXT_CLAIM_TYPE,
    }
)
STRICT_PUBLIC_CLAIM_TYPES: Final[frozenset[str]] = frozenset(
    {*STRICT_FACTUAL_CLAIM_TYPES, INTERPRETATION_CLAIM_TYPE}
)

# 승인된 v2 완성 하한. 시험이 숫자를 복사하지 않고 이 값을 직접 읽는다.
MIN_SUBSTANTIVE_CLAIMS: Final[int] = 40
MIN_VERIFIED_RATIO: Final[Decimal] = Decimal("0.50")
MIN_DOCUMENT_SOURCES: Final[int] = 8
MAX_NOTICE_ONLY_SECTIONS: Final[int] = 1

# 1~8장 중 근거가 있는 장도 claim 하나로 끝나면 COMPLETE가 될 수 없다.
# 부족한 보고서를 삭제하는 값이 아니라 COMPLETE/PARTIAL을 가르는 하한이다.
MIN_CLAIMS_PER_COVERED_SECTION: Final[int] = 2
REQUIRED_QUALITY_SECTION_IDS: Final[tuple[str, ...]] = (
    "identity",
    "business_model",
    "portfolio",
    "past_changes",
    "current_challenges",
    "future_strategy",
    "operations_partners",
    "culture",
)
STRICT_REQUIRED_QUALITY_SECTION_IDS: Final[tuple[str, ...]] = (
    *REQUIRED_QUALITY_SECTION_IDS,
    "competitive_position",
)
STRICT_MAX_NOTICE_ONLY_SECTIONS: Final[int] = 0

# FULL 보고서의 «해석»은 사실 문장을 대신해 분량을 채울 수 없다. 기준 보고서
# 실측(진영 17.7%, 하이브 12.0%)보다 여유를 두되 폭주만 막는 상한이다.
# 장당 상한은 작가 프롬프트와 출고 게이트가 같은 정본을 읽는다. 전체 상한은
# 긴 보고서가 비율만 맞춰 해석 문장을 무한히 늘리지 못하게 개수·비율을 모두 둔다.
MAX_INTERPRETED_CLAIMS_PER_SECTION: Final[int] = 2
MAX_INTERPRETED_CLAIMS: Final[int] = 10
MAX_INTERPRETED_RATIO: Final[Decimal] = Decimal("0.25")

# 작가 입력 목표는 장당 8~12문장이지만 검수·중복 제거·수치 안전 필터 뒤
# 공개본은 기준 보고서에서 장당 약 3~5문장이다. 8을 출고 하한으로 쓰면 좋은
# 보고서도 막으므로, 새 FULL 공개본은 최소 3문장으로 얇은 1~2문장 장만 막는다.
MIN_FULL_PUBLIC_SENTENCES_PER_SECTION: Final[int] = 3

ROUNDING_MODE: Final[str] = "ROUND_HALF_UP"
# 공식 재계산 과정의 Decimal 정밀도 차이만 허용한다. 이보다 큰 값은 틀린
# 공식을 tolerance로 숨길 수 있으므로 새 생성 계약에서 거부한다.
MAX_FORMULA_TOLERANCE: Final[Decimal] = Decimal("0.000001")
