"""회사 유형 구분 — 문서 증거로만 판정하는지(회사 이름 하드코딩 금지) 시험."""

from __future__ import annotations

from features.evidence_collection import constants as c
from features.evidence_collection.classify import classify_company_type
from features.evidence_collection.models import CollectedDocument, CollectionAttempt, DocumentTextRange


def _document(source_kind: str, requirement: str = c.REQUIREMENT_REQUIRED) -> CollectedDocument:
    return CollectedDocument(
        company_id="00126380",
        document_id=f"{source_kind}:20250315000001",
        canonical_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250315000001",
        source_tier=c.SOURCE_TIER_OFFICIAL,
        source_kind=source_kind,
        publisher=c.DART_PUBLISHER_NAME,
        title="공시",
        published_on="20250315",
        collected_at="2026-08-31T00:00:00+09:00",
        content_sha256="a" * 64,
        identity_binding="corp_code=00126380;rcept_no=20250315000001",
        usable_ranges=(DocumentTextRange(0, 10),),
        collector_version=c.COLLECTOR_VERSION,
        parser_version=c.PARSER_VERSION,
        requirement=requirement,
    )


def test_사업보고서가_있고_매출액이_있으면_listed() -> None:
    documents = [_document(c.SOURCE_KIND_BUSINESS_REPORT)]
    fragments = ["최근 3개년 매출액은 매년 증가했다."]
    assert classify_company_type(documents, fragments) == c.COMPANY_TYPE_LISTED


def test_감사보고서만_있으면_audit_only() -> None:
    documents = [_document(c.SOURCE_KIND_AUDIT_REPORT)]
    fragments = ["감사보고서는 재무제표만을 감사 대상으로 한다."]
    assert classify_company_type(documents, fragments) == c.COMPANY_TYPE_AUDIT_ONLY


def test_매출액이_없고_이자수익만_있으면_financial() -> None:
    documents = [_document(c.SOURCE_KIND_BUSINESS_REPORT)]
    fragments = ["당사의 주요 수익은 이자수익과 수수료수익으로 구성된다."]
    assert classify_company_type(documents, fragments) == c.COMPANY_TYPE_FINANCIAL


def test_사업보고서를_낸_금융회사도_financial로_판정한다() -> None:
    """대형 카드사처럼 사업보고서를 내면서도 매출액 대신 이자수익을 쓰는 경우."""
    documents = [_document(c.SOURCE_KIND_BUSINESS_REPORT)]
    fragments = ["당사는 여신전문금융업을 영위하며 주요 수익은 이자수익이다."]
    assert classify_company_type(documents, fragments) == c.COMPANY_TYPE_FINANCIAL


def test_매출액_언급이_있으면_금융_키워드가_있어도_financial이_아니다() -> None:
    documents = [_document(c.SOURCE_KIND_BUSINESS_REPORT)]
    fragments = ["최근 3개년 매출액은 증가했고 이자수익도 일부 포함되어 있다."]
    assert classify_company_type(documents, fragments) == c.COMPANY_TYPE_LISTED


def _list_attempt(source_kind: str, state: str, requirement: str = c.REQUIREMENT_REQUIRED) -> CollectionAttempt:
    return CollectionAttempt(
        company_id="00126380",
        attempt_id=f"list:{source_kind}",
        source_kind=source_kind,
        requirement=requirement,
        state=state,
        slot_ids=("identity:corporate_identity",),
        reason_code=c.REASON_LIST_QUERY_OK if state == c.ATTEMPT_STATE_OK else c.REASON_LIST_QUERY_FAILED,
        elapsed_ms=0,
        bytes_downloaded=0,
        documents_seen=0,
    )


def test_P1_1_문서가_없으면_audit_only로_지어내지_않고_undecided를_돌려준다() -> None:
    """team-lead 통보(2026-08-31) — audit_only는 긍정적 사실 주장이라 근거 없이 쓰면 안 된다."""
    assert classify_company_type([], []) == c.COMPANY_TYPE_UNDECIDED


def test_P1_1_필수_목록_조회가_FAILED면_문서가_있어도_undecided다() -> None:
    documents = [_document(c.SOURCE_KIND_BUSINESS_REPORT)]
    fragments = ["최근 3개년 매출액은 매년 증가했다."]
    attempts = [_list_attempt(c.SOURCE_KIND_AUDIT_REPORT, c.ATTEMPT_STATE_FAILED)]

    assert classify_company_type(documents, fragments, attempts=attempts) == c.COMPANY_TYPE_UNDECIDED


def test_P1_1_선택_목록_조회_FAILED는_undecided_사유가_아니다() -> None:
    """반기·분기(OPTIONAL) 조회 실패는 판정을 막지 않는다 — 필수 목록만 본다."""
    documents = [_document(c.SOURCE_KIND_BUSINESS_REPORT)]
    fragments = ["최근 3개년 매출액은 매년 증가했다."]
    attempts = [
        _list_attempt(c.SOURCE_KIND_SEMIANNUAL_REPORT, c.ATTEMPT_STATE_FAILED, requirement=c.REQUIREMENT_OPTIONAL),
    ]

    assert classify_company_type(documents, fragments, attempts=attempts) == c.COMPANY_TYPE_LISTED


def test_P1_1_문서는_있지만_판정_신호가_없으면_undecided다() -> None:
    """사업/감사보고서 어느 source_kind도 아닌 문서만 있는 경우(예: 보충 자료뿐)."""
    documents = [_document(c.SOURCE_KIND_SEMIANNUAL_REPORT, requirement=c.REQUIREMENT_OPTIONAL)]
    fragments = ["당사는 반기 실적을 보고한다."]

    assert classify_company_type(documents, fragments) == c.COMPANY_TYPE_UNDECIDED
