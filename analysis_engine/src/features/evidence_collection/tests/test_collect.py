"""수집 조율(collect_dart_evidence) 종단 시험 — 세 회사 유형 + 상한·중복 처리."""

from __future__ import annotations

from features.evidence_collection import constants as c
from features.evidence_collection.collect import collect_dart_evidence
from features.evidence_collection.filing_select import DocumentFetchResult, FilingListResult, RawFilingRow
from features.evidence_collection.tests.fixtures.fake_fetcher import FakeFetcher
from features.evidence_collection.tests.fixtures.synthetic_documents import (
    AUDIT_ONLY_REPORT_TEXT,
    FINANCIAL_REPORT_TEXT,
    LISTED_BUSINESS_REPORT_TEXT,
)

_NOW = "2026-08-31T00:00:00+09:00"


def _fetcher(pblntf_ty: str, row: RawFilingRow, text: str) -> FakeFetcher:
    return FakeFetcher(
        list_responses_by_pblntf_ty={pblntf_ty: FilingListResult(state="OK", rows=(row,))},
        document_responses_by_rcept_no={
            row.rcept_no: DocumentFetchResult(
                state="OK", text=text, elapsed_ms=50, bytes_downloaded=len(text.encode("utf-8")),
            ),
        },
    )


def test_상장형_수집_전체_흐름() -> None:
    row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    fetcher = _fetcher("A", row, LISTED_BUSINESS_REPORT_TEXT)

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    assert harvest.company_type == c.COMPANY_TYPE_LISTED
    assert len(harvest.documents) == 1
    assert harvest.documents[0].source_kind == c.SOURCE_KIND_BUSINESS_REPORT
    assert harvest.documents[0].requirement == c.REQUIREMENT_REQUIRED
    assert len(harvest.fragments) >= 9
    section_ids = {f.section_id for f in harvest.fragments if f.section_id}
    assert len(section_ids) >= 3  # 여러 장에 걸쳐 배정됨(전체 원문 복사 아님)
    ok_document_attempts = [
        a for a in harvest.attempts
        if a.state == c.ATTEMPT_STATE_OK and a.attempt_id.startswith("document:")
    ]
    assert len(ok_document_attempts) == 1


def test_감사보고서형_수집() -> None:
    row = RawFilingRow("20250401000001", "감사보고서", "20250401")
    fetcher = _fetcher("F", row, AUDIT_ONLY_REPORT_TEXT)

    harvest = collect_dart_evidence(fetcher, "00164788", now=_NOW)

    assert harvest.company_type == c.COMPANY_TYPE_AUDIT_ONLY
    assert harvest.documents[0].source_kind == c.SOURCE_KIND_AUDIT_REPORT


def test_금융형_수집() -> None:
    row = RawFilingRow("20250315000009", "사업보고서 (2025.03)", "20250315")
    fetcher = _fetcher("A", row, FINANCIAL_REPORT_TEXT)

    harvest = collect_dart_evidence(fetcher, "00355758", now=_NOW)

    assert harvest.company_type == c.COMPANY_TYPE_FINANCIAL


def test_문서_조회_실패는_FAILED로_기록되고_문서를_만들지_않는다() -> None:
    fetcher = FakeFetcher(
        list_responses_by_pblntf_ty={
            "A": FilingListResult(state="OK", rows=(
                RawFilingRow("20250315000001", "사업보고서", "20250315"),
            )),
        },
        document_responses_by_rcept_no={},  # 응답 없음 → FakeFetcher 기본값 FAILED
    )

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    assert harvest.documents == ()
    fail_attempts = [
        a for a in harvest.attempts
        if a.state == c.ATTEMPT_STATE_FAILED and a.attempt_id.startswith("document:")
    ]
    assert len(fail_attempts) == 1
    assert fail_attempts[0].reason_code == c.REASON_DOCUMENT_FETCH_FAILED


def test_deadline을_넘기면_TRUNCATED로_기록하고_건너뛴다() -> None:
    row = RawFilingRow("20250315000001", "사업보고서", "20250315")
    fetcher = _fetcher("A", row, LISTED_BUSINESS_REPORT_TEXT)

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW, deadline_seconds=-1.0)

    assert harvest.documents == ()
    truncated = [a for a in harvest.attempts if a.reason_code == c.REASON_DEADLINE_EXCEEDED]
    assert len(truncated) == 1
    assert truncated[0].state == c.ATTEMPT_STATE_TRUNCATED


def test_문서가_MAX_DOCUMENT_TEXT_BYTES를_넘으면_FAILED로_기록한다(monkeypatch) -> None:
    monkeypatch.setattr(c, "MAX_DOCUMENT_TEXT_BYTES", 10)
    row = RawFilingRow("20250315000001", "사업보고서", "20250315")
    fetcher = _fetcher("A", row, LISTED_BUSINESS_REPORT_TEXT)

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    assert harvest.documents == ()
    too_large = [a for a in harvest.attempts if a.reason_code == c.REASON_DOCUMENT_TOO_LARGE]
    assert len(too_large) == 1


def test_동일한_내용_SHA256은_문서_중복으로_제거한다() -> None:
    business_row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    semiannual_row = RawFilingRow("20250815000002", "반기보고서 (2025.06)", "20250815")
    fetcher = FakeFetcher(
        list_responses_by_pblntf_ty={
            "A": FilingListResult(state="OK", rows=(business_row, semiannual_row)),
        },
        document_responses_by_rcept_no={
            # 합성 시험 목적으로 두 공시가 완전히 같은 본문을 돌려주게 설정한다.
            business_row.rcept_no: DocumentFetchResult(state="OK", text=LISTED_BUSINESS_REPORT_TEXT),
            semiannual_row.rcept_no: DocumentFetchResult(state="OK", text=LISTED_BUSINESS_REPORT_TEXT),
        },
    )

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    assert len(harvest.documents) == 1
    duplicate_attempts = [a for a in harvest.attempts if a.reason_code == c.REASON_DOCUMENT_DUPLICATE]
    assert len(duplicate_attempts) == 1
    assert duplicate_attempts[0].state == c.ATTEMPT_STATE_OK
