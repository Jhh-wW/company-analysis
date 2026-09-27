"""관리 표의 약한 단일어를 회사의 실제 운영·미래 계획과 구분한다."""

from __future__ import annotations

from features.evidence_collection import constants as c


def pure_officer_compensation_table(text: str) -> bool:
    """개인별 보수 지급표 구조만 식별해 사업 사실로 재분류하지 않는다."""

    if "\n" in text:
        return False
    rows = text.split(c.SALES_TABLE_ROW_SEPARATOR)
    if len(rows) < 2 or any(c.SALES_TABLE_CELL_SEPARATOR not in row for row in rows):
        return False
    header_cells = tuple(
        cell.strip() for cell in rows[0].split(c.SALES_TABLE_CELL_SEPARATOR)
    )
    if not all(
        any(option in header_cells for option in group)
        for group in c.OFFICER_PAY_HEADER_GROUPS
    ):
        return False
    role_columns = tuple(
        index for index, cell in enumerate(header_cells)
        if cell in c.OFFICER_PAY_HEADER_GROUPS[1]
    )
    for row in rows[1:]:
        cells = tuple(cell.strip() for cell in row.split(c.SALES_TABLE_CELL_SEPARATOR))
        if not any(
            index < len(cells) and cells[index] in c.OFFICER_PAY_ROLE_CELLS
            for index in role_columns
        ):
            return False
    return bool(role_columns)


def stock_admin_production_only(
    text: str, section_heading: str, hits: tuple[str, ...],
) -> bool:
    """주식·이사회 표의 사업목적 열거에 나온 '생산' 단독 신호만 제외한다."""

    real_operation = any(
        not any(
            marker in row.split(c.SALES_TABLE_CELL_SEPARATOR, 1)[0]
            for marker in c.STOCK_ADMIN_PURPOSE_ROW_MARKERS
        )
        and any(marker in row for marker in c.STOCK_REAL_OPERATION_PRODUCT_MARKERS)
        and any(marker in row for marker in c.STOCK_REAL_OPERATION_ACTION_MARKERS)
        and any(marker in row for marker in c.STOCK_REAL_OPERATION_DELIVERY_MARKERS)
        for row in text.split(c.SALES_TABLE_ROW_SEPARATOR)[1:]
    )
    return (
        hits == ("생산",)
        and "|" in text and ";" in text
        and any(marker in section_heading for marker in c.STOCK_ADMIN_HEADING_MARKERS)
        and any(marker in text for marker in c.STOCK_ADMIN_DOCUMENT_MARKERS)
        and c.STOCK_ADMIN_GOVERNANCE_MARKER in text
        and not real_operation
    )


def accounting_value_table_only(text: str, hits: tuple[str, ...]) -> bool:
    """자산 평가표의 '가치'만으로 고객 가치 교환을 주장하지 않는다."""

    if hits != ("가치",) or "|" not in text or ";" not in text:
        return False
    header, _, rows = text.partition(c.SALES_TABLE_ROW_SEPARATOR)
    if not (
        any(marker in header for marker in c.ACCOUNTING_TABLE_HEADER_MARKERS)
        and any(marker in text for marker in c.ACCOUNTING_TABLE_SUBJECT_MARKERS)
    ):
        return False
    return not any(
        "가치" in row and any(marker in row for marker in c.CUSTOMER_VALUE_MARKERS)
        for row in rows.split(c.SALES_TABLE_ROW_SEPARATOR)
    )


def future_signal_has_context(
    text: str, section_heading: str, hits: tuple[str, ...],
) -> bool:
    """동일 문장·표 행에 계획 대상과 상태가 있을 때만 약한 신호를 싣는다."""

    sales_process = any(
        marker in section_heading for marker in c.FUTURE_SALES_PROCESS_HEADINGS
    )
    return any(
        any(hit in unit for hit in hits)
        and (
            c.FUTURE_EXPLICIT_ACTION_PLAN_PATTERN.search(unit)
            or any(marker in unit for marker in c.FUTURE_CONTEXT_ACTIVITY_MARKERS)
        )
        # 판매경로의 고객 생산계획·주문 승인 순서를 자사의 미래전략으로 읽지 않는다.
        and (
            not sales_process
            or any(marker in unit for marker in c.FUTURE_SALES_EXPLICIT_MARKERS)
        )
        and (
            c.FUTURE_EXPLICIT_ACTION_PLAN_PATTERN.search(unit)
            or
            any(marker in unit for marker in c.FUTURE_CONTEXT_INTENT_MARKERS)
            or (
                any(marker in section_heading for marker in c.FUTURE_CONTEXT_PLAN_HEADINGS)
                and any(marker in unit for marker in c.FUTURE_CONTEXT_INVESTMENT_STATUSES)
            )
            or (
                any(marker in unit for marker in c.FUTURE_CONTEXT_INVESTMENT_TARGETS)
                and any(marker in unit for marker in c.FUTURE_CONTEXT_INVESTMENT_STATUSES)
            )
        )
        for unit in c.FACT_UNIT_BOUNDARY_PATTERN.split(text)
    )
