"""사업목적·타사 사업과 분리한 회사 소유 현재 사업 목록."""

from __future__ import annotations

from src.shared.report_evidence import business_activity_declaration_constants as c


def declared_business_item(text: str) -> str:
    """명시 현재 구성 선언의 첫 구체 사업명만 원문 그대로 선택한다."""
    for unit in c.DECLARATION_UNIT_RE.split(text):
        unit = unit.strip()
        if c.DECLARATION_EXCLUDED_RE.search(unit):
            continue
        # 외부 고객 대상 사업명에 묶이지 않은 회계처리는 내부업무로 남긴다.
        accounting_scope = c.DECLARATION_EXTERNAL_ACCOUNTING_SERVICE_RE.sub("", unit)
        if c.DECLARATION_ACCOUNTING_PROCESS_RE.search(accounting_scope):
            continue
        declaration = c.DECLARATION_RE.fullmatch(unit)
        if declaration is None:
            continue
        items = c.DECLARATION_PREFIX_RE.sub("", declaration["items"].strip())
        items = c.DECLARATION_QUALIFIER_RE.sub("", items)
        match = c.DECLARATION_ITEM_RE.match(items)
        if match is None:
            continue
        item = match["item"].strip()
        if not c.DECLARATION_GENERIC_RE.search(item) and item in unit:
            return item
    return ""
