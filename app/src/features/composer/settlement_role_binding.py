"""정산 도식에서 수령인 위치·공제 접속 조사만 다른 동일 관계를 대조한다."""

from collections.abc import Sequence
import re

from src.features.composer.settlement_role_constants import (
    SETTLEMENT_CANDIDATE_TEMPLATE,
    SETTLEMENT_NONACTUAL_RE,
    SETTLEMENT_ROLE_RE,
    SETTLEMENT_SOURCE_TEMPLATE,
    SETTLEMENT_TAX_WORD,
    SETTLEMENT_STATEMENT_RE,
    SETTLEMENT_STATEMENT_TARGETS,
)


def settlement_role_spans(
    surfaces: Sequence[str], source_clauses: Sequence[str],
    source_surface: str, quote: str, target: str, role: str,
) -> tuple[tuple[int, int, int], ...]:
    """원문·인용의 정산 주체, 수령인, 공제 항목이 같은 과금 자리만 반환한다.

    호출자는 자기 인용 여부와 인용문 원문 포함을 먼저 확인한다. 여기서는
    완결된 회사 정산 절과 도식 칸만 비교하며, 명시되지 않은 거래를 보충하지 않는다.
    대상이나 차감 항목이 다르거나 조건·부정이 끼면 기존 엄격 검사로 돌아간다.
    """
    if (not SETTLEMENT_ROLE_RE.fullmatch(role)
            or SETTLEMENT_NONACTUAL_RE.search(source_surface)):
        return ()
    source_pattern = re.compile(SETTLEMENT_SOURCE_TEMPLATE.format(target=re.escape(target)))
    supported = any(
        (match := source_pattern.fullmatch(clause)) is not None
        and quote in clause and match.group("relation") in quote
        for clause in source_clauses
    )
    if not supported:
        return ()
    candidate_pattern = re.compile(
        SETTLEMENT_CANDIDATE_TEMPLATE.format(target=re.escape(target))
    )
    spans = tuple(
        (index, *match.span("fees"))
        for index, surface in enumerate(surfaces)
        if (match := candidate_pattern.fullmatch(surface)) is not None
    )
    # 다른 칸에서 부가세를 회사 수익 등으로 새로 해석한 행에는 이 증명을
    # 빌려주지 않는다. 이 대조가 확인한 것은 지급액에서 빠지는 항목뿐이다.
    if sum(surface.count(SETTLEMENT_TAX_WORD) for surface in surfaces) != len(spans):
        return ()
    return spans


def settlement_statement_spans(
    surfaces: Sequence[str], source_clauses: Sequence[str],
    source_surface: str, quote: str, target: str, role: str,
) -> tuple[tuple[int, int, int], ...]:
    """완결된 정산 산문/원문 도식의 공제 자리만 자기 인용과 삼중 대조한다."""
    if (target not in SETTLEMENT_STATEMENT_TARGETS
            or not SETTLEMENT_ROLE_RE.fullmatch(role)
            or SETTLEMENT_NONACTUAL_RE.search(source_surface)):
        return ()
    source_pattern = re.compile(SETTLEMENT_SOURCE_TEMPLATE.format(target="가맹점"))
    if not any(
        (match := source_pattern.fullmatch(clause)) is not None
        and quote.rstrip(".") in clause and match.group("relation") in quote
        for clause in source_clauses
    ):
        return ()
    compact_pattern = re.compile(SETTLEMENT_CANDIDATE_TEMPLATE.format(target="가맹점"))
    spans = tuple(
        (index, *match.span("fees")) for index, surface in enumerate(surfaces)
        if (match := (SETTLEMENT_STATEMENT_RE.fullmatch(surface)
                      or compact_pattern.fullmatch(surface))) is not None
    )
    if sum(surface.count(SETTLEMENT_TAX_WORD) for surface in surfaces) != len(spans):
        return ()
    return spans
