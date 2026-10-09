"""관리 표의 약한 단일어를 회사의 실제 운영·미래 계획과 구분한다."""

from __future__ import annotations

from collections.abc import Callable

from features.evidence_collection import constants as c


def _value_exchange_units(text: str) -> tuple[str, ...]:
    """서로 별개라고 명시한 회계·사업 절도 관계를 합치지 않는다."""

    return tuple(
        unit
        for sentence in c.FACT_UNIT_BOUNDARY_PATTERN.split(text)
        for unit in c.VALUE_EXCHANGE_SEPARATE_CLAUSE_PATTERN.split(sentence)
    )


def _value_exchange_policy_only(unit: str) -> bool:
    """실제 거래 사건 없이 조건·기준만 정한 수익인식 절인지 본다."""

    customer_payment = (
        any(marker in unit for marker in c.VALUE_EXCHANGE_CUSTOMER_PAYER_MARKERS)
        and any(marker in unit for marker in c.VALUE_EXCHANGE_NAMED_FEE_MARKERS)
        and any(marker in unit for marker in c.VALUE_EXCHANGE_CUSTOMER_PAYMENT_MARKERS)
    )
    return (
        any(marker in unit for marker in c.VALUE_EXCHANGE_POLICY_CONDITION_MARKERS)
        and any(marker in unit for marker in c.VALUE_EXCHANGE_POLICY_TREATMENT_MARKERS)
        and not customer_payment
        and not c.VALUE_EXCHANGE_COMPLETED_EVENT_PATTERN.search(unit)
    )


def _value_exchange_accounting_only(unit: str) -> bool:
    """고객·제공물이 수식어인 회계측정과 실제 수취 사건을 구별한다."""

    fee_payment = (
        any(marker in unit for marker in c.VALUE_EXCHANGE_NAMED_FEE_MARKERS)
        and any(marker in unit for marker in (
            *c.VALUE_EXCHANGE_CUSTOMER_PAYMENT_MARKERS,
            *c.VALUE_EXCHANGE_PAYMENT_ACTION_MARKERS,
        ))
    )
    return (
        any(marker in unit for marker in c.VALUE_EXCHANGE_ACCOUNTING_ONLY_MARKERS)
        and any(marker in unit for marker in c.VALUE_EXCHANGE_ACCOUNTING_TREATMENT_MARKERS)
        and not fee_payment
        and not c.VALUE_EXCHANGE_COMPLETED_EVENT_PATTERN.search(unit)
    )


def value_exchange_policy_observed(text: str) -> bool:
    """수익인식 적용규칙은 무신호 재판정 차선으로 재유입하지 않는다."""

    return any(
        _value_exchange_policy_only(unit)
        for unit in c.FACT_UNIT_BOUNDARY_PATTERN.split(text)
    )


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


def value_exchange_supported_hits(
    text: str, keywords: tuple[str, ...], direct_hit: Callable[[str, str], bool],
) -> tuple[str, ...]:
    """실제 교환관계가 있는 같은 문장·행의 키워드만 점수로 인정한다.

    조각의 원문은 줄이거나 바꾸지 않는다. 약한 단어가 다른 절의 고객·제품
    표지와 우연히 공존하는 것만으로 필수 가치교환 칸을 채우지 않는다.
    """

    supported: set[str] = set()
    for unit in _value_exchange_units(text):
        unit_hits = {keyword for keyword in keywords if direct_hit(keyword, unit)}
        if not unit_hits:
            continue
        if _value_exchange_policy_only(unit) or _value_exchange_accounting_only(unit):
            continue
        recipient = any(marker in unit for marker in c.VALUE_EXCHANGE_RECIPIENT_MARKERS)
        business_object = any(marker in unit for marker in c.VALUE_EXCHANGE_OBJECT_MARKERS)
        delivery = any(marker in unit for marker in c.VALUE_EXCHANGE_DELIVERY_MARKERS)
        consideration = any(marker in unit for marker in c.VALUE_EXCHANGE_CONSIDERATION_MARKERS)
        benefit = (
            any(marker in unit for marker in c.VALUE_EXCHANGE_BENEFIT_MARKERS)
            and any(marker in unit for marker in c.VALUE_EXCHANGE_BENEFIT_ACTION_MARKERS)
        )
        # 회계상 금융자산·부채의 '대가'와 외부 감사인의 행위는 회사가
        # 고객에게 판매한 서비스가 아니다. 명시 고객 거래는 보존한다.
        if not recipient and any(
            marker in unit for marker in c.VALUE_EXCHANGE_ACCOUNTING_ONLY_MARKERS
        ):
            continue
        if not recipient and any(
            marker in unit for marker in c.VALUE_EXCHANGE_EXTERNAL_AUDITOR_MARKERS
        ):
            continue
        if recipient and business_object and (benefit or delivery):
            supported.update(unit_hits)
        elif recipient and delivery and consideration:
            supported.update(unit_hits)
        elif business_object and delivery and consideration:
            nominal_loan_execution = (
                "실행" in unit
                and not any(
                    marker in unit for marker in c.VALUE_EXCHANGE_DELIVERY_MARKERS
                    if marker != "실행"
                )
                and not c.VALUE_EXCHANGE_LOAN_EXECUTION_PATTERN.search(unit)
            )
            if not nominal_loan_execution:
                supported.update(unit_hits)
    return tuple(keyword for keyword in keywords if keyword in supported)


def value_exchange_has_payment_route(text: str) -> bool:
    """같은 판매조건 단위에 회사→상대방 경로와 대금 회수방법이 있는지 본다.

    고유 거래처 이름이나 업종을 열거하지 않는다. 제품명이 없어도 고객에게
    판매하고 현금·어음 등으로 받는 방법은 가치교환 후보가 될 수 있다.
    """

    return any(
        not _value_exchange_policy_only(unit)
        and
        any(marker in unit for marker in c.VALUE_EXCHANGE_ROUTE_MARKERS)
        and any(marker in unit for marker in c.VALUE_EXCHANGE_SELLER_MARKERS)
        and any(marker in unit for marker in c.VALUE_EXCHANGE_ROUTE_DIRECTION_MARKERS)
        and any(marker in unit for marker in c.VALUE_EXCHANGE_PAYMENT_MARKERS)
        and any(marker in unit for marker in c.VALUE_EXCHANGE_PAYMENT_ACTION_MARKERS)
        and any(marker in unit for marker in c.VALUE_EXCHANGE_PAYMENT_CONTEXT_MARKERS)
        and not any(marker in unit for marker in c.VALUE_EXCHANGE_ACCOUNTING_ONLY_MARKERS)
        for unit in _value_exchange_units(text)
    )


def value_exchange_has_direct_relation(text: str) -> bool:
    """약키워드가 없어도 실제 이용료·대금회수·서비스 효용은 보존한다."""

    for unit in _value_exchange_units(text):
        if _value_exchange_policy_only(unit) or _value_exchange_accounting_only(unit):
            continue
        recipient = any(marker in unit for marker in c.VALUE_EXCHANGE_RECIPIENT_MARKERS)
        business_object = any(marker in unit for marker in c.VALUE_EXCHANGE_OBJECT_MARKERS)
        delivery = any(marker in unit for marker in c.VALUE_EXCHANGE_DELIVERY_MARKERS)
        accounting_only = any(
            marker in unit for marker in c.VALUE_EXCHANGE_ACCOUNTING_ONLY_MARKERS
        )
        if recipient and business_object and delivery:
            fee_payment = (
                any(marker in unit for marker in c.VALUE_EXCHANGE_NAMED_FEE_MARKERS)
                and any(marker in unit for marker in (
                    *c.VALUE_EXCHANGE_CUSTOMER_PAYMENT_MARKERS,
                    *c.VALUE_EXCHANGE_PAYMENT_ACTION_MARKERS,
                ))
            )
            concrete_benefit = any(
                marker in unit for marker in c.VALUE_EXCHANGE_CONCRETE_BENEFIT_MARKERS
            )
            if fee_payment or concrete_benefit:
                return True
        if (
            not accounting_only
            and any(marker in unit for marker in c.VALUE_EXCHANGE_SELLER_MARKERS)
            and any(marker in unit for marker in c.VALUE_EXCHANGE_SALE_PROCEEDS_MARKERS)
            and any(marker in unit for marker in c.VALUE_EXCHANGE_PAYMENT_ACTION_MARKERS)
        ):
            return True
    return False


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
