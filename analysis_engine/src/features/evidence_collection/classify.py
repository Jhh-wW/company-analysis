"""회사 유형 구분 — 관측된 공시 구성과 재무제표 항목으로만 판정한다.

회사 이름을 하드코딩하지 않는다(요구사항 6번). 판정 근거는 두 가지뿐이다:
①어떤 종류의 공시를 실제로 냈는가(사업보고서 유무) ②본문에 「매출액」
항목이 있는가·금융업 특유의 수익 항목(이자수익 등)이 있는가.

★ P1-1 수정(2026-08-31 team-lead 통보) — 판정 근거가 될 문서를 하나도
확보하지 못했거나 필수 목록 조회 자체가 FAILED였으면 ``undecided``를
돌려준다. 예전 코드는 이 경우에도 무조건 ``audit_only``로 «단정»해서,
수집 장애 때문에 문서를 못 받은 상장사가 «감사보고서만 내는 회사»라는
근거 없는 사실 주장으로 기록됐다. attempts를 보지 않는 한 이 구분이
불가능하므로 이 함수의 시그니처를 확장했다.
"""

from __future__ import annotations

from collections.abc import Iterable

from features.evidence_collection import constants as c
from features.evidence_collection.models import CollectedDocument, CollectionAttempt


def _required_list_query_failed(attempts: list[CollectionAttempt]) -> bool:
    return any(
        attempt.attempt_id.startswith("list:")
        and attempt.requirement == c.REQUIREMENT_REQUIRED
        and attempt.state == c.ATTEMPT_STATE_FAILED
        for attempt in attempts
    )


def classify_company_type(
    documents: Iterable[CollectedDocument],
    fragment_texts: Iterable[str],
    attempts: Iterable[CollectionAttempt] = (),
) -> str:
    """관측된 공시 구성과 조각 원문만으로 listed/audit_only/financial/undecided를 정한다."""
    docs = list(documents)
    joined_text = "\n".join(fragment_texts)
    attempts_list = list(attempts)

    if not docs or _required_list_query_failed(attempts_list):
        # 판정 근거를 확보하지 못했다 — 「가장 보수적인 값」이라며 audit_only로
        # 지어내지 않는다. undecided는 긍정적 사실 주장이 아니다.
        return c.COMPANY_TYPE_UNDECIDED

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
    # 문서는 있지만(has_business_report·has_audit_report가 모두 거짓) 판정
    # 신호가 없을 때도 audit_only로 지어내지 않는다 — undecided로 정직하게 남긴다.
    return c.COMPANY_TYPE_UNDECIDED
