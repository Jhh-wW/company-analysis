"""회계 항목에서 직접 밝히지 않은 사업 관계를 만들어 내는 것을 제한한다."""
from collections.abc import Mapping
import unicodedata

from src.features.composer import revenue_activity_scope_constants as c
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND


def _units(text: str) -> tuple[str, ...]:
    return tuple(unit.strip() for unit in c.UNIT_BOUNDARY_RE.split(
        unicodedata.normalize('NFKC', text)) if unit.strip())


def _own_actual_units(sources: Mapping[str, str]) -> tuple[str, ...]:
    result = []
    for source in sources.values():
        own = True
        for unit in _units(source):
            foreign = tuple(c.OTHER_OWNER_RE.finditer(unit))
            selves = tuple(match for match in c.SELF_OWNER_RE.finditer(unit)
                           if not any(other.start() <= match.start() < other.end() for other in foreign))
            if foreign or selves:
                own = (selves[-1].start() if selves else -1) > (foreign[-1].start() if foreign else -1)
            if own and not c.NON_ACTUAL_RE.search(unit):
                result.append(unit)
    return tuple(result)


def _starts(text: str) -> frozenset[str]:
    return frozenset(c.ACTIVITY_PREFIX_RE.sub('', match['activity'])
                     for match in c.START_ACTIVITY_RE.finditer(text))


def _businesses(text: str) -> frozenset[str]:
    return frozenset(match['business'] for match in c.BUSINESS_SCOPE_RE.finditer(text))


def revenue_activity_scope_problem(text: str, own_sources: Mapping[str, str]) -> str:
    """명시 매출·고객 관계와 사업 개시 주장만 검사하며 회계 항목 자체는 남긴다."""
    units = _own_actual_units(own_sources)
    cost_present = any(c.EXPORT_COST_RE.search(value) for value in own_sources.values())
    for claim in _units(text):
        if c.NON_ACTUAL_RE.search(claim):
            continue
        # 비용 존재는 같은 지역의 실제 매출·판매 고객 관계를 입증하지 않는다.
        export_claim = (c.EXPORT_REVENUE_RE.search(claim) or c.EXPORT_CUSTOMER_RE.search(claim)
                        or c.EXPORT_SALE_RE.search(claim))
        businesses = _businesses(claim)
        if cost_present and export_claim and not any(
            (not businesses or businesses <= _businesses(unit))
            and (c.EXPORT_SOURCE_REVENUE_RE.search(unit) or c.EXPORT_SOURCE_ACTION_RE.search(unit))
            for unit in units):
            return SCOPE_CONDITION_UNBOUND
        years = frozenset(c.YEAR_RE.findall(claim))
        for activity in _starts(claim):
            if not any(activity in _starts(unit)
                       and (not years or not c.YEAR_RE.search(unit)
                            or bool(years & frozenset(c.YEAR_RE.findall(unit)))) for unit in units):
                return SCOPE_CONDITION_UNBOUND
    return ''
