"""감사보고서 재무제표 파서의 결정론적 계약 상수."""

from __future__ import annotations

from decimal import Decimal


DISPLAY_UNIT = "억원"
DISPLAY_PLACES = 0
OUTPUT_YEAR_COUNT = 2
EVIDENCE_MAX_CHARS = 64_000
PLAIN_METRIC_WINDOW_CHARS = 260
AUDIT_REPORT_STATEMENT_SOURCE = "audit_report_statement"

DIAGNOSTIC_STATEMENT_NOT_FOUND = "손익계산서 미탐"
DIAGNOSTIC_UNIT_NOT_FOUND = "단위 미확인"
DIAGNOSTIC_YEAR_NOT_FOUND = "연도 미확인"
DIAGNOSTIC_METRIC_NOT_FOUND = "필수 계정 미탐"
DIAGNOSTIC_AMOUNT_NOT_FOUND = "금액 미확인"

UNIT_DIVISORS: dict[str, Decimal] = {
    "원": Decimal("100000000"),
    "천원": Decimal("100000"),
    "백만원": Decimal("100"),
}

METRIC_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("매출액", ("매출액", "영업수익", "수익(매출액)")),
    (
        "영업이익",
        ("영업이익", "영업이익(손실)", "영업손실"),
    ),
    (
        "당기순이익",
        (
            "당기순이익",
            "당기순이익(손실)",
            "연결당기순이익",
            "당기연결순이익",
            "당기순손실",
            "연결당기순손실",
            "당기연결순손실",
        ),
    ),
)

LOSS_ONLY_ALIASES = frozenset(
    {
        "영업손실",
        "당기순손실",
        "연결당기순손실",
        "당기연결순손실",
    }
)

KOREAN_MONTHS = (
    "",
    "일월",
    "이월",
    "삼월",
    "사월",
    "오월",
    "유월",
    "칠월",
    "팔월",
    "구월",
    "시월",
    "십일월",
    "십이월",
)
