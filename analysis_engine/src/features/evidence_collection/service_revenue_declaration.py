"""새 문장을 만들지 않고 명시된 현재 서비스 수익원의 원문 좌표를 반환한다."""
from features.evidence_collection.business_slot_scope import business_declaration_scope_allows
from features.evidence_collection import service_revenue_declaration_constants as c


def service_revenue_declaration_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans = []
    for unit in c.SERVICE_REVENUE_UNIT_RE.finditer(text):
        value = unit.group()
        if not business_declaration_scope_allows(value) or c.SERVICE_REVENUE_PAST_RE.search(value):
            continue
        match = c.SERVICE_REVENUE_DECLARATION_RE.fullmatch(value)
        if match is None:
            continue
        items = c.SERVICE_REVENUE_ITEM_SEPARATOR_RE.split("".join(match["items"].split()))
        names = [c.SERVICE_REVENUE_ITEM_RE.fullmatch(item) for item in items]
        if not names or not all(names) or not any(
            item["name"] not in c.SERVICE_REVENUE_GENERIC_NAMES for item in names
        ):
            continue
        spans.append((unit.start() + match.start("claim"), unit.start() + match.end("claim")))
    return tuple(spans)
