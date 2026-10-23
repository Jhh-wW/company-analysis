"""설립 목적을 현재 계속사업으로 바꾼 경우 자기 인용의 현재 활동을 확인한다."""

from collections.abc import Mapping, Sequence
import unicodedata

from src.features.composer import founding_purpose_constants as c
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND


def _surface(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def _activity_text(unit: str) -> str:
    """설립 목적 구절만 가려 같은 문장의 별도 현재 활동을 남긴다."""
    return c.PURPOSE_OBJECT_RE.sub(" ", unit)


def _current_activities(unit: str, *, allow_continuity: bool = False) -> list[str]:
    """같은 문장에서도 현재 실행과 다른 사업의 계획을 별도 절로 읽는다."""
    current = []
    historical = other_subject = False
    for clause in c.SOURCE_CLAUSE_BOUNDARY_RE.split(_activity_text(unit)):
        surface = _surface(clause)
        if c.SOURCE_CURRENT_RESET_RE.search(surface):
            historical = False
        historical = historical or bool(c.SOURCE_HISTORICAL_RE.search(surface))
        if c.SOURCE_SELF_SUBJECT_RE.search(surface):
            other_subject = False
        other_subject = other_subject or bool(c.SOURCE_OTHER_SUBJECT_RE.search(surface))
        if historical or other_subject:
            continue
        if ((c.CURRENT_BUSINESS_RE.search(surface)
             or (allow_continuity and c.CONTINUING_BUSINESS_RE.search(surface)))
                and not c.UNCONFIRMED_BUSINESS_RE.search(surface)
                and not c.SOURCE_MODAL_RE.search(surface)):
            current.append(surface)
    return current


def founding_purpose_scope_problem(text: str, sources: Mapping[str, str]) -> str:
    """역사·설립 목적은 유지하고 그 목적의 현재 실행을 추가할 때만 검사한다."""
    units = [unit for source in sources.values()
             for unit in c.UNIT_RE.split(unicodedata.normalize("NFKC", source))]
    purposes = [match for unit in units for match in c.PURPOSE_OBJECT_RE.finditer(unit)]
    if not purposes:
        return ""
    items = set()
    for purpose in purposes:
        objects = c.SUBJECT_PREFIX_RE.sub("", purpose["object"], count=1)
        for item in c.ITEM_SEPARATOR_RE.split(objects):
            item = _surface(c.ITEM_SUFFIX_RE.sub("", item))
            if item:
                items.add(item)
    current = [surface for unit in units for surface in _current_activities(unit)]
    continuing = [surface for unit in units for surface in _current_activities(unit, allow_continuity=True)]
    for unit in c.UNIT_RE.split(text):
        historical = False
        for clause in c.CLAIM_CLAUSE_BOUNDARY_RE.split(
                c.HISTORICAL_FOUNDING_CLAIM_RE.sub(" ", _activity_text(unit))):
            surface = _surface(clause)
            if c.SOURCE_CURRENT_RESET_RE.search(surface):
                historical = False
            historical = historical or bool(c.SOURCE_HISTORICAL_RE.search(surface))
            if historical or not c.CURRENT_CLAIM_RE.search(surface):
                continue
            # ‘해 왔다’는 현재 실행을 별도로 입증하지 않는다. 명시 현재 주장은 현재형 원문만 쓴다.
            evidence = (continuing if c.CONTINUING_CLAIM_RE.search(surface)
                        and not c.EXPLICIT_PRESENT_CLAIM_RE.search(surface) else current)
            mentioned = [item for item in items if item in surface]
            if any(not any(item in source for source in evidence) for item in mentioned):
                return SCOPE_CONDITION_UNBOUND
            if not mentioned and not evidence:
                return SCOPE_CONDITION_UNBOUND
    return ""


def founding_purpose_flow_problem(cells: Sequence[str], sources: Mapping[str, str]) -> str:
    """설립 목적의 업종을 명사형 현재 운영 도식으로 빌린 경우만 막는다."""
    values = tuple(_surface(cell) for cell in cells)
    if (any(c.FLOW_HISTORICAL_RE.search(value) for value in values)
            and not any(c.EXPLICIT_PRESENT_CLAIM_RE.search(value) for value in values)):
        return ""
    units = [unit for source in sources.values()
             for unit in c.UNIT_RE.split(unicodedata.normalize("NFKC", source))]
    purposes = [match["object"] for unit in units for match in c.PURPOSE_OBJECT_RE.finditer(unit)]
    if not purposes:
        return ""

    def activity_key(value: str) -> str:
        # 같은 PG 업종의 지급/지불 표기 차이만 비교한다. 다른 업종 별칭은 만들지 않는다.
        return c.FLOW_PG_ACTIVITY_RE.sub(c.FLOW_PG_ACTIVITY_CANONICAL, _surface(value))

    current = []
    for unit in units:
        for clause in _current_activities(unit):
            if c.SOURCE_SELF_SUBJECT_RE.search(clause):
                current.append(clause)
        # 현재 정산 절은 영위한다는 종결어미 없이도 회사의 직접 운영을 증명한다.
        surface = _surface(unit)
        if c.FLOW_CURRENT_SETTLEMENT_RE.fullmatch(surface):
            current.append(surface)
    for value in values:
        value = c.SOURCE_SELF_SUBJECT_RE.sub("", value)
        value = c.FLOW_PRESENT_PREFIX_RE.sub("", value)
        match = c.FLOW_NOMINAL_ACTIVITY_RE.fullmatch(value)
        if match is None:
            continue
        activity = activity_key(match["activity"])
        if (any(activity in activity_key(purpose) for purpose in purposes)
                and not any(activity in activity_key(source) for source in current)):
            return SCOPE_CONDITION_UNBOUND
    return ""
