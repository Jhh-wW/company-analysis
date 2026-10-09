"""인용한 조사 행위 자체에 공동 관계가 있는지만 검사한다.

전체 제휴 사실의 승인기는 아니다. 조사 대상의 수·기업명, 발행처, 다른 문장의
협약은 조사 행위의 상대를 증명하지 않는다. 일반 연구개발·계약은 기존 검수에 남긴다.
"""
from collections.abc import Mapping

from src.shared.report_evidence import partnership_scope_constants as c


def research_units(text: str) -> tuple[str, ...]:
    return tuple(unit.strip() for sentence in c.SENTENCE_BOUNDARY_RE.split(text)
                 for unit in c.ACTIVITY_BOUNDARY_RE.split(sentence)
                 if unit.strip() and c.RESEARCH_ACTION_RE.search(unit))


def is_joint_research(unit: str) -> bool:
    return bool(c.JOINT_RESEARCH_RE.search(unit) and not c.NEGATED_RELATION_RE.search(unit))


def research_partnership_problem(text: str, slot: str) -> str:
    """자기 인용에 단독 조사만 있는데 제휴 칸으로 지원하면 거절한다."""
    if slot != c.PARTNERSHIP_SLOT:
        return ""
    units = research_units(text)
    if units and not any(is_joint_research(unit) for unit in units):
        return c.PARTNERSHIP_RESEARCH_PROBLEM
    return ""


def research_partnership_claim_problem(
    text: str, own_sources: Mapping[str, str], *, claim_slot: str = "",
) -> str:
    """다른 인용·문장의 관계를 빌려 단독 조사를 제휴로 승격하지 않는다."""
    sources = tuple(unit for value in own_sources.values() for unit in research_units(value))
    if not sources or all(is_joint_research(unit) for unit in sources):
        return ""
    # 여러 인용에 단독 조사와 별도 공동 조사가 섞인 경우도 자동 면제하지 않는다.
    # 정확히 공동 조사 문장 하나를 고른 후보만 그 문장의 관계로 판정할 수 있다.
    claims = research_units(text)
    if claims and all(is_joint_research(unit) and any(
        "".join(unit.split()) == "".join(source.split()) for source in sources
        if is_joint_research(source)
    ) for unit in claims):
        return ""
    if claim_slot == c.PARTNERSHIP_SLOT or (
        claims and c.RELATION_RE.search(text)
    ):
        return c.PARTNERSHIP_RESEARCH_PROBLEM
    return ""
