"""설립 목적을 현재 계속사업으로 바꾼 경우 자기 인용의 현재 활동을 확인한다."""

from collections.abc import Mapping
import unicodedata

from src.features.composer import founding_purpose_constants as c
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND


def _surface(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def _activity_text(unit: str) -> str:
    """설립 목적 구절만 가려 같은 문장의 별도 현재 활동을 남긴다."""
    return c.PURPOSE_OBJECT_RE.sub(" ", unit)


def _current_activities(unit: str) -> list[str]:
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
        if (c.CURRENT_BUSINESS_RE.search(surface)
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
    for unit in c.UNIT_RE.split(text):
        surface = _surface(c.HISTORICAL_FOUNDING_CLAIM_RE.sub(" ", _activity_text(unit)))
        if not c.CURRENT_CLAIM_RE.search(surface):
            continue
        # 같은 인용 묶음에 다른 현재 사업이 있어도 설립 목적의 실행 근거를 빌리지 않는다.
        mentioned = [item for item in items if item in surface]
        if any(not any(item in source for source in current) for item in mentioned):
            return SCOPE_CONDITION_UNBOUND
        if not mentioned and not current:
            return SCOPE_CONDITION_UNBOUND
    return ""
