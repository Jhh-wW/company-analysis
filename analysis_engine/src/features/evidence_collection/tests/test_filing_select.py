"""관련 공시 묶음 선택 — 우선순위·폴백·정정 계보·상한 시험."""

from __future__ import annotations

from features.evidence_collection import constants as c
from features.evidence_collection.filing_select import (
    FilingListResult,
    RawFilingRow,
    select_related_filings,
)
from features.evidence_collection.tests.fixtures.fake_fetcher import FakeFetcher, RaisingFetcher


def _row(rcept_no: str, report_nm: str, rcept_dt: str = "20250315") -> RawFilingRow:
    return RawFilingRow(rcept_no=rcept_no, report_nm=report_nm, rcept_dt=rcept_dt)


def test_사업보고서가_있으면_그것을_고르고_감사보고서는_조회하지_않는다() -> None:
    fetcher = FakeFetcher(list_responses_by_pblntf_ty={
        "A": FilingListResult(state="OK", rows=(_row("20250315000001", "사업보고서 (2025.03)"),)),
    })

    result = select_related_filings(fetcher, "00126380")

    assert result.selected[0].source_kind == c.SOURCE_KIND_BUSINESS_REPORT
    assert result.selected[0].rcept_no == "20250315000001"
    assert ("00126380", "F") not in fetcher.list_calls  # 감사보고서 폴백 미시도


def test_사업보고서가_없으면_감사보고서로_폴백한다() -> None:
    fetcher = FakeFetcher(list_responses_by_pblntf_ty={
        "A": FilingListResult(state="OK", rows=()),  # 013류 — 정상 조회, 자료 없음
        "F": FilingListResult(state="OK", rows=(_row("20250401000001", "감사보고서"),)),
    })

    result = select_related_filings(fetcher, "00164788")

    primary = [f for f in result.selected if f.requirement == c.REQUIREMENT_REQUIRED]
    assert len(primary) == 1
    assert primary[0].source_kind == c.SOURCE_KIND_AUDIT_REPORT
    assert primary[0].rcept_no == "20250401000001"
    # 사업보고서 MISSING 시도가 기록으로 남는다
    business_attempts = [a for a in result.attempts if a.source_kind == c.SOURCE_KIND_BUSINESS_REPORT]
    assert business_attempts[0].state == c.ATTEMPT_STATE_MISSING


def test_사업보고서_조회가_실패해도_감사보고서_폴백을_시도한다() -> None:
    fetcher = FakeFetcher(list_responses_by_pblntf_ty={
        "A": FilingListResult(state=c.ATTEMPT_STATE_FAILED),
        "F": FilingListResult(state="OK", rows=(_row("20250401000001", "감사보고서"),)),
    })

    result = select_related_filings(fetcher, "00164788")

    primary = [f for f in result.selected if f.requirement == c.REQUIREMENT_REQUIRED]
    assert primary[0].source_kind == c.SOURCE_KIND_AUDIT_REPORT
    business_attempts = [a for a in result.attempts if a.source_kind == c.SOURCE_KIND_BUSINESS_REPORT]
    assert business_attempts[0].state == c.ATTEMPT_STATE_FAILED


def test_기재정정이_있으면_정정본을_쓰고_원공시_rcept_no를_계보에_남긴다() -> None:
    fetcher = FakeFetcher(list_responses_by_pblntf_ty={
        "A": FilingListResult(state="OK", rows=(
            _row("20250301000001", "사업보고서 (2025.03)"),
            _row("20250315000002", "[기재정정]사업보고서 (2025.03)"),
        )),
    })

    result = select_related_filings(fetcher, "00126380")

    primary = [f for f in result.selected if f.source_kind == c.SOURCE_KIND_BUSINESS_REPORT][0]
    assert primary.rcept_no == "20250315000002"
    assert primary.lineage_original_rcept_no == "20250301000001"


def test_첨부정정은_본문이_없어_후보에서_통째로_빠진다() -> None:
    fetcher = FakeFetcher(list_responses_by_pblntf_ty={
        "A": FilingListResult(state="OK", rows=(
            _row("20250301000001", "사업보고서 (2025.03)"),
            _row("20250320000003", "[첨부정정]사업보고서 (2025.03)"),
        )),
    })

    result = select_related_filings(fetcher, "00126380")

    primary = [f for f in result.selected if f.source_kind == c.SOURCE_KIND_BUSINESS_REPORT][0]
    assert primary.rcept_no == "20250301000001"  # 첨부정정이 아니라 원공시가 선택됨


def test_반기_분기보고서는_사업보고서와_같은_A_쿼리_결과를_재사용한다() -> None:
    fetcher = FakeFetcher(list_responses_by_pblntf_ty={
        "A": FilingListResult(state="OK", rows=(
            _row("20250315000001", "사업보고서 (2025.03)"),
            _row("20250815000002", "반기보고서 (2025.06)"),
            _row("20251115000003", "분기보고서 (2025.09)"),
        )),
    })

    result = select_related_filings(fetcher, "00126380")

    a_calls = [call for call in fetcher.list_calls if call[1] == "A"]
    assert len(a_calls) == 1  # pblntf_ty="A" 조회는 딱 한 번(비용 상한)
    kinds = {f.source_kind for f in result.selected}
    assert kinds == {
        c.SOURCE_KIND_BUSINESS_REPORT,
        c.SOURCE_KIND_SEMIANNUAL_REPORT,
        c.SOURCE_KIND_QUARTERLY_REPORT,
    }


def test_상한을_넘는_후보는_TRUNCATED로_기록되고_제외된다(monkeypatch) -> None:
    monkeypatch.setattr(c, "MAX_RELATED_FILINGS", 1)
    fetcher = FakeFetcher(list_responses_by_pblntf_ty={
        "A": FilingListResult(state="OK", rows=(
            _row("20250315000001", "사업보고서 (2025.03)"),
            _row("20250815000002", "반기보고서 (2025.06)"),
        )),
    })

    result = select_related_filings(fetcher, "00126380")

    assert len(result.selected) == 1
    assert len(result.truncated) == 1
    truncated_attempts = [a for a in result.attempts if a.state == c.ATTEMPT_STATE_TRUNCATED]
    assert len(truncated_attempts) == 1
    assert truncated_attempts[0].reason_code == c.REASON_CAP_REACHED


def test_fetcher가_예외를_던져도_FAILED로_흡수하고_죽지_않는다() -> None:
    result = select_related_filings(RaisingFetcher(), "00126380")

    assert all(a.state == c.ATTEMPT_STATE_FAILED for a in result.attempts)
    assert result.selected == ()
