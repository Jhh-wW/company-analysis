"""과거 사고·제재 기록의 날짜를 지워 현재 과제로 보이지 않게 한다."""
from collections.abc import Mapping, Sequence
import unicodedata

from src.features.composer import challenge_event_constants as c


def _surface(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def _days(text: str):
    return frozenset(tuple(int(part) for part in day) for day in c.EVENT_DAY_RE.findall(text))


def _dated_event_records(source: str):
    table = bool(c.EVENT_TABLE_HEADER_RE.search(_surface(source)) and "|" in source)
    header_categories = {
        name for name, pattern in c.EVENT_CATEGORY_RES.items()
        if table and pattern.search(_surface(source.split(";", 1)[0]))
    }
    records = []
    for unit in c.EVENT_RECORD_SPLIT_RE.split(source):
        years = frozenset(c.EVENT_DATE_RE.findall(unit))
        if not years:
            continue
        surface = _surface(unit)
        if not table and not c.ACTUAL_EVENT_RE.search(surface):
            continue
        categories = {name for name, pattern in c.EVENT_CATEGORY_RES.items() if pattern.search(surface)}
        categories.update(header_categories)
        if categories:
            records.append((categories, years, bool(c.ONGOING_RECORD_RE.search(surface)), _days(unit)))
    return records


def challenge_event_scope_problem(text: str, sources: Mapping[str, str], *, cells: Sequence[str] | None = None) -> str:
    """자기 인용의 날짜 있는 실제 사건만 제약한다. 원문을 수정하거나 사실을 추가하지 않는다.

    실제 발생일·처분일이 있는 사건은 본문과 표에도 그 발생 기간을 남겨야 한다.
    발행일·법 시행일·일반적인 조건부 규정은 실제 사건 날짜로 승격하지 않는다.
    완료·진행 여부는 해당 발생 기간의 행 안에서만 확인하며 다른 행으로 빌리지 않는다.
    """
    candidate = " ".join(cells) if cells is not None else text
    surface = _surface(candidate)
    # 실제로 받거나 발생한 사건과 제재 가능성·사고 예방 위험은 다른 주장이다.
    claim_surface = _surface(text)
    if c.GENERAL_EVENT_RISK_RE.search(claim_surface) and not c.ACTUAL_EVENT_RE.search(claim_surface):
        return ""
    categories = {name for name, pattern in c.EVENT_CATEGORY_RES.items() if pattern.search(surface)}
    if not categories:
        return ""
    candidate_years = frozenset(c.EVENT_YEAR_RE.findall(candidate))
    candidate_days = _days(candidate)
    records = [record for source in sources.values() for record in _dated_event_records(source)]
    for category in categories:
        relevant = [record for record in records if category in record[0]]
        if not relevant:
            continue
        selected = [record for record in relevant if (
            record[3] & candidate_days if candidate_days else record[1] & candidate_years
        )]
        if not selected:
            return c.TIME_BINDING_PROBLEM
        # 같은 연도에 완료·진행 사건이 섞이면 어느 사건인지 연도만으로 정하지 않는다.
        # 명시적인 현재 완료 상태는 진행 주장과 다르다. 다른 절의 미해결·진행
        # 표지는 제거하지 않아 뒤의 완료 문구로 현재 문제를 면제하지 못한다.
        current_claim = c.CURRENT_COMPLETED_ACTION_RE.sub("", surface)
        if c.CURRENT_EVENT_RE.search(current_claim) and not all(record[2] for record in selected):
            return c.TIME_BINDING_PROBLEM
    return ""
