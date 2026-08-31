"""전문(全文) 분할 — 목차·상투 문구 제외, 고정 표제 밖 문단 포착 시험."""

from __future__ import annotations

from features.evidence_collection.models import CollectedDocument, DocumentTextRange
from features.evidence_collection.segment import segment_document, usable_ranges_from_candidates
from features.evidence_collection.tests.fixtures.synthetic_documents import (
    LISTED_BUSINESS_REPORT_TEXT,
)


def test_목차_구간의_본문은_후보에서_빠진다() -> None:
    candidates = segment_document(LISTED_BUSINESS_REPORT_TEXT)

    assert not any("회사의 개요, 사업의 내용, 재무에 관한 사항 순서" in c.text for c in candidates)


def test_반복되는_면책_문구는_상투_문구로_빠진다() -> None:
    candidates = segment_document(LISTED_BUSINESS_REPORT_TEXT)

    assert not any("외부감사인의 감사의견과 별도로 작성" in c.text for c in candidates)


def test_고정_표제_밖의_문단도_후보로_잡힌다() -> None:
    """옛 방식(종류당 첫 1,200자)은 앞부분만 봤다 — 뒤쪽 장도 잡히는지 확인."""
    candidates = segment_document(LISTED_BUSINESS_REPORT_TEXT)
    texts = [c.text for c in candidates]

    # V~VIII장(임원·계약·향후계획·시장현황)은 문서 뒤쪽에 있다 — 첫 1,200자
    # 방식이면 절대 잡히지 않는다.
    assert any("경영진 리더십 원칙" in t for t in texts)
    assert any("장기 공급계약" in t for t in texts)
    assert any("해외 생산라인 투자" in t for t in texts)
    assert any("동종업계는 소수 기업이 과점" in t for t in texts)


def test_문단_수는_9개_이상이다() -> None:
    """실측 커버리지 2.4%였던 옛 방식과 달리 문서 전체에서 여러 문단을 뽑는지."""
    candidates = segment_document(LISTED_BUSINESS_REPORT_TEXT)
    assert len(candidates) >= 9


def test_usable_ranges는_CollectedDocument에_그대로_들어간다() -> None:
    candidates = segment_document(LISTED_BUSINESS_REPORT_TEXT)
    ranges = usable_ranges_from_candidates(candidates)

    document = CollectedDocument(
        company_id="00126380",
        document_id="dart_business_report:20250315000001",
        canonical_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250315000001",
        source_tier="TIER_1_OFFICIAL",
        source_kind="dart_business_report",
        publisher="금융감독원 전자공시시스템(DART)",
        title="사업보고서",
        published_on="20250315",
        collected_at="2026-08-31T00:00:00+09:00",
        content_sha256="a" * 64,
        identity_binding="corp_code=00126380;rcept_no=20250315000001",
        usable_ranges=ranges,
        collector_version="evidence_collection/1.0",
        parser_version="evidence_collection_segment/1.0",
        requirement="REQUIRED",
    )
    assert len(document.usable_ranges) == len(candidates)


def test_빈_문서는_후보가_없다() -> None:
    assert segment_document("") == []
    assert segment_document("   \n\n  ") == []


def test_짧은_잔여물은_MIN_FRAGMENT_CHARS_미만이면_버린다() -> None:
    text = "I. 회사의 개요\n짧음\n"
    candidates = segment_document(text)
    assert candidates == []


def test_각_후보의_start_end는_실제_원문과_일치한다() -> None:
    candidates = segment_document(LISTED_BUSINESS_REPORT_TEXT)
    for candidate in candidates:
        assert LISTED_BUSINESS_REPORT_TEXT[candidate.start:candidate.end] == candidate.text
        assert DocumentTextRange(candidate.start, candidate.end).end > candidate.start
