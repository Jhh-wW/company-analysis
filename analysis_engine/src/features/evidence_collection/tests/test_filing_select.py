"""관련 공시 묶음 선택 — 우선순위·폴백·정정 계보·상한 시험."""

from __future__ import annotations

import time

from features.evidence_collection import constants as c
from features.evidence_collection.filing_select import (
    FilingListResult,
    RawFilingRow,
    _pick_latest_with_lineage,
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


# ══════════════════════════════════════════════════════════
# P0-6 — 정정공시가 더 최신 원공시를 이기던 결함(3관점 독립 확정)
# ══════════════════════════════════════════════════════════


def test_P0_6_다른_연도_원공시가_옛_연도_정정본보다_최신이면_원공시가_이긴다() -> None:
    """조회 창에 여러 사업연도가 섞였을 때(연 단위 조회라 흔하다) — 옛 연도
    정정본이 더 최신인 다음 연도 원공시를 밀어내면 안 된다.
    """
    rows = [
        _row("20240301000001", "[기재정정]사업보고서 (2023.12)"),  # 옛 연도 정정본
        _row("20250315000005", "사업보고서 (2024.12)"),  # 더 최신인 다음 연도 원공시
    ]

    chosen, lineage = _pick_latest_with_lineage(rows)

    assert chosen.rcept_no == "20250315000005"
    assert lineage == ""  # 원공시가 이겼으니 계보 없음


def test_P0_6_같은_연도면_정정본이_원공시를_이기고_계보를_남긴다() -> None:
    rows = [
        _row("20250301000001", "사업보고서 (2025.03)"),
        _row("20250315000002", "[기재정정]사업보고서 (2025.03)"),
    ]

    chosen, lineage = _pick_latest_with_lineage(rows)

    assert chosen.rcept_no == "20250315000002"
    assert lineage == "20250301000001"


def test_P2_괄호_앞_공백_차이만으로_정정_계보가_끊기지_않는다() -> None:
    """원공시는 「사업보고서(2025.03)」인데 정정본이 「사업보고서 (2025.03)」처럼
    괄호 앞 공백이 다르게 들어와도 같은 계보로 묶인다.
    """
    rows = [
        _row("20250301000001", "사업보고서(2025.03)"),  # 괄호 앞 공백 없음
        _row("20250315000002", "[기재정정]사업보고서 (2025.03)"),  # 괄호 앞 공백 있음
    ]

    chosen, lineage = _pick_latest_with_lineage(rows)

    assert chosen.rcept_no == "20250315000002"
    assert lineage == "20250301000001"


def test_P0_6_정정본만_있으면_그것을_쓰고_계보는_비운다() -> None:
    rows = [_row("20250315000002", "[기재정정]사업보고서 (2025.03)")]

    chosen, lineage = _pick_latest_with_lineage(rows)

    assert chosen.rcept_no == "20250315000002"
    assert lineage == ""  # 조회 범위 안에 원공시가 없어 계보를 못 채운다(정직한 빈 값)


def test_P0_6_select_related_filings_종단에서도_다른_연도_원공시가_이긴다() -> None:
    """select_related_filings 전체 흐름에서도 P0-6이 실제로 적용되는지 확인."""
    fetcher = FakeFetcher(list_responses_by_pblntf_ty={
        "A": FilingListResult(state="OK", rows=(
            _row("20240301000001", "[기재정정]사업보고서 (2023.12)"),
            _row("20250315000005", "사업보고서 (2024.12)"),
        )),
    })

    result = select_related_filings(fetcher, "00126380")

    primary = [f for f in result.selected if f.source_kind == c.SOURCE_KIND_BUSINESS_REPORT][0]
    assert primary.rcept_no == "20250315000005"
    assert primary.lineage_original_rcept_no == ""


# ══════════════════════════════════════════════════════════
# P1-3 — deadline이 목록 조회에는 적용되지 않던 결함(목록 단계)
# ══════════════════════════════════════════════════════════


def test_P1_3_deadline을_이미_넘겼으면_새_목록_조회를_시작하지_않는다() -> None:
    fetcher = FakeFetcher(list_responses_by_pblntf_ty={
        "A": FilingListResult(state="OK", rows=(_row("20250315000001", "사업보고서 (2025.03)"),)),
    })
    already_passed_deadline = time.monotonic() - 1.0

    result = select_related_filings(fetcher, "00126380", deadline_at=already_passed_deadline)

    assert fetcher.list_calls == []  # 새 조회를 «시작조차» 하지 않았다
    assert result.selected == ()
    truncated = [a for a in result.attempts if a.reason_code == c.REASON_DEADLINE_EXCEEDED]
    assert len(truncated) == len(c.FILING_KIND_SPECS)  # 4개 spec 전부 TRUNCATED
    assert all(a.state == c.ATTEMPT_STATE_TRUNCATED for a in truncated)
    assert all(a.attempt_id.startswith("list:") for a in truncated)


def test_P1_3_deadline이_없으면_예전과_같이_전부_조회한다() -> None:
    """deadline_at을 안 주면(기본값 None) 기존 호출부와 100% 호환된다."""
    fetcher = FakeFetcher(list_responses_by_pblntf_ty={
        "A": FilingListResult(state="OK", rows=(_row("20250315000001", "사업보고서 (2025.03)"),)),
    })

    result = select_related_filings(fetcher, "00126380")

    assert len(fetcher.list_calls) >= 1
    assert result.selected[0].rcept_no == "20250315000001"
