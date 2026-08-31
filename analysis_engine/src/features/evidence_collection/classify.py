"""회사 유형 구분 — 관측된 공시 구성과 재무제표 항목으로만 판정한다.

회사 이름을 하드코딩하지 않는다(요구사항 6번). 판정 근거는 두 가지뿐이다:
①어떤 종류의 공시를 실제로 냈는가(사업보고서 유무) ②본문에 「매출액」
항목이 있는가·금융업 특유의 수익 항목(이자수익 등)이 있는가.
"""

from __future__ import annotations

from collections.abc import Iterable

from features.evidence_collection import constants as c
from features.evidence_collection.models import CollectedDocument


def classify_company_type(
    documents: Iterable[CollectedDocument],
    fragment_texts: Iterable[str],
) -> str:
    """관측된 공시 구성과 조각 원문만으로 listed/audit_only/financial을 정한다."""
    docs = list(documents)
    joined_text = "\n".join(fragment_texts)

    has_business_report = any(doc.source_kind == c.SOURCE_KIND_BUSINESS_REPORT for doc in docs)
    has_audit_report = any(doc.source_kind == c.SOURCE_KIND_AUDIT_REPORT for doc in docs)

    has_revenue_line_item = c.REVENUE_LINE_ITEM_KEYWORD in joined_text
    has_financial_company_signal = any(
        keyword in joined_text for keyword in c.FINANCIAL_COMPANY_REVENUE_KEYWORDS
    )

    # 「매출액」이 아예 없는데 금융업 특유의 수익 항목이 있으면 금융형으로 본다.
    # 사업보고서·감사보고서 어느 쪽을 냈는지와 무관하다 — 대형 금융사도
    # 사업보고서를 낸다(예: 카드사).
    if not has_revenue_line_item and has_financial_company_signal:
        return c.COMPANY_TYPE_FINANCIAL
    if has_business_report:
        return c.COMPANY_TYPE_LISTED
    if has_audit_report:
        return c.COMPANY_TYPE_AUDIT_ONLY
    # 문서가 아예 없거나 판정 신호가 없을 때는 가장 보수적인 값으로 남긴다 —
    # 「사업보고서형」이라고 지어내지 않는다.
    return c.COMPANY_TYPE_AUDIT_ONLY
