"""관련 공시 묶음 선택 — 우선순위·폴백·정정 계보·상한 시험."""

from __future__ import annotations

import time

import pytest

from core.dart_client import (
    DartAuthenticationError,
    DartLimitReached,
    DartResponseError,
    DartTransportError,
)
from features.evidence_collection import constants as c
from features.evidence_collection.filing_select import (
    DiscoveredDocumentUrl,
    DocumentFetchResult,
    FilingListResult,
    RawFilingRow,
    _pick_latest_with_lineage,
    select_related_filings,
)
from features.evidence_collection.tests.fixtures.fake_fetcher import FakeFetcher, RaisingFetcher


def _row(rcept_no: str, report_nm: str, rcept_dt: str = "20250315") -> RawFilingRow:
    return RawFilingRow(rcept_no=rcept_no, report_nm=report_nm, rcept_dt=rcept_dt)


@pytest.mark.parametrize("state", ["", "알수없음", c.ATTEMPT_STATE_TRUNCATED])
def test_fetch_결과_state는_외부조회_경계의_닫힌값만_받는다(state: str) -> None:
    """포트 오타를 회사 자료 없음이나 외부 장애로 바꿔 숨기지 않는다."""

    with pytest.raises(ValueError, match="알 수 없는 DART fetch 결과 상태"):
        FilingListResult(state=state)
    with pytest.raises(ValueError, match="알 수 없는 DART fetch 결과 상태"):
        DocumentFetchResult(state=state)


def test_실패와_부재_fetch_결과에는_성공_payload를_실을수없다() -> None:
    row = _row("20250315000001", "사업보고서 (2025.03)")
    candidate = DiscoveredDocumentUrl(
        url="https://example.com/company",
        source_member_name="document.xml",
        location="문서 1쪽",
        source_payload_sha256="a" * 64,
    )

    with pytest.raises(ValueError, match="목록 fetch 결과에는 rows"):
        FilingListResult(state=c.ATTEMPT_STATE_FAILED, rows=(row,))
    with pytest.raises(ValueError, match="문서 fetch 결과에는 문서 payload"):
        DocumentFetchResult(state=c.ATTEMPT_STATE_MISSING, text="남아 있는 원문")
    with pytest.raises(ValueError, match="문서 fetch 결과에는 문서 payload"):
        DocumentFetchResult(
            state=c.ATTEMPT_STATE_FAILED,
            official_url_candidates=(candidate,),
        )


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


def test_fetcher의_표준_네트워크예외는_FAILED로_흡수하고_죽지_않는다() -> None:
    result = select_related_filings(RaisingFetcher(), "00126380")

    assert all(a.state == c.ATTEMPT_STATE_FAILED for a in result.attempts)
    assert result.selected == ()


@pytest.mark.parametrize(
    "external_error",
    [
        DartTransportError("가짜 DART 전송 실패"),
        DartResponseError("가짜 DART 응답 실패"),
        OSError("가짜 cache I/O 실패"),
    ],
)
def test_목록조회_예상외부실패만_FAILED로_흡수한다(external_error) -> None:
    class ExternalFailureFetcher:
        def fetch_filing_list(
            self, company_id: str, pblntf_ty: str
        ) -> FilingListResult:
            raise external_error

        def fetch_document_text(self, rcept_no: str):
            raise AssertionError("목록 실패라 문서 조회에 도달하면 안 됩니다")

    result = select_related_filings(ExternalFailureFetcher(), "00126380")

    assert result.selected == ()
    assert result.attempts
    assert all(attempt.state == c.ATTEMPT_STATE_FAILED for attempt in result.attempts)


@pytest.mark.parametrize(
    "contract_error",
    [
        TypeError("가짜 callback 시그니처 불일치"),
        AttributeError("가짜 반환 자료형 불일치"),
        KeyError("가짜 필수 필드 누락"),
        AssertionError("가짜 구현 불변식 위반"),
        ValueError("가짜 포트 값 계약 위반"),
    ],
)
def test_목록조회_코드계약오류를_자료실패로_위장하지_않는다(
    contract_error,
) -> None:
    class BrokenFetcher:
        def fetch_filing_list(
            self, company_id: str, pblntf_ty: str
        ) -> FilingListResult:
            raise contract_error

        def fetch_document_text(self, rcept_no: str):
            raise AssertionError("목록 오류에서 즉시 중단되어야 합니다")

    with pytest.raises(type(contract_error)):
        select_related_filings(BrokenFetcher(), "00126380")


@pytest.mark.parametrize("fatal_error", [DartLimitReached("한도"), DartAuthenticationError("인증")])
def test_목록조회_치명오류는_즉시_전파되어_다음_전송이_0회다(fatal_error) -> None:
    calls: list[str] = []

    class FatalListFetcher:
        def fetch_filing_list(self, company_id: str, pblntf_ty: str) -> FilingListResult:
            calls.append(pblntf_ty)
            raise fatal_error

        def fetch_document_text(self, rcept_no: str):
            raise AssertionError("목록 단계에서 중단되어야 합니다")

    with pytest.raises(type(fatal_error)):
        select_related_filings(FatalListFetcher(), "00126380")

    assert calls == ["A"]


# ══════════════════════════════════════════════════════════
# 정정공시가 더 최신 원공시를 이기던 결함(3관점 독립 확정)
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
    """select_related_filings 전체 흐름에서도 정정본 우선 규칙이 실제로 적용되는지 확인."""
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


# ══════════════════════════════════════════════════════════
# generation=8 후속 item 2 재정의 — 기준은
# 「그 조회가 실제로 슬롯을 들여다봤는가」다. 목록 조회는 «찾았다»는
# 사실만으로는 어떤 슬롯도 들여다본 게 아니므로 OK만 OPTIONAL로 내린다.
# MISSING(그 공시가 실제로 없다는 확인)·FAILED(확인 자체를 못 함)는 참인
# 확인/차단 신호라 REQUIRED+광역을 그대로 유지한다.
# ══════════════════════════════════════════════════════════


def _count_list_ok_required(attempts) -> int:
    """「목록 조회 + OK + REQUIRED」가 0건임을 세는 시험용."""
    return sum(
        1 for a in attempts
        if a.attempt_id.startswith("list:")
        and a.state == c.ATTEMPT_STATE_OK
        and a.requirement == c.REQUIREMENT_REQUIRED
    )


def test_item2_목록_조회_OK는_REQUIRED가_아니라_OPTIONAL로_내려간다() -> None:
    """목록에서 찾았다는 사실만으로는 슬롯을 들여다본 게 아니다 — 근거
    유무 판정은 뒤따르는 문서 attempt가 진다.
    """
    fetcher = FakeFetcher(list_responses_by_pblntf_ty={
        "A": FilingListResult(state="OK", rows=(_row("20250315000001", "사업보고서 (2025.03)"),)),
    })

    result = select_related_filings(fetcher, "00126380")

    list_attempt = [a for a in result.attempts if a.attempt_id == "list:dart_business_report"][0]
    assert list_attempt.state == c.ATTEMPT_STATE_OK
    assert list_attempt.requirement == c.REQUIREMENT_OPTIONAL
    assert _count_list_ok_required(result.attempts) == 0


def test_item2_목록_조회_MISSING은_REQUIRED_광역을_그대로_유지한다() -> None:
    """「그 공시가 실제로 없다」는 참인 확인이므로
    자료 부족 방향(REQUIRED+광역)이 맞다. 다운그레이드하지 않는다.
    """
    fetcher = FakeFetcher(list_responses_by_pblntf_ty={
        "A": FilingListResult(state="OK", rows=()),
        "F": FilingListResult(state="OK", rows=()),
    })

    result = select_related_filings(fetcher, "00164788")

    missing_attempts = [
        a for a in result.attempts
        if a.state == c.ATTEMPT_STATE_MISSING
        and a.source_kind in (c.SOURCE_KIND_BUSINESS_REPORT, c.SOURCE_KIND_AUDIT_REPORT)
    ]
    assert missing_attempts  # 사업·감사 둘 다 MISSING이 남는지
    assert all(a.requirement == c.REQUIREMENT_REQUIRED for a in missing_attempts)
    assert all(
        set(a.slot_ids) == set(c.SOURCE_KIND_SLOT_SCOPE[a.source_kind]) for a in missing_attempts
    )


def test_item2_목록_조회_FAILED는_그대로_REQUIRED를_유지한다() -> None:
    """P1-1의 필수 목록 조회 실패 판정이 이 값에 의존한다 — 다운그레이드하면 안 된다.

    사업/감사보고서(REQUIRED spec)만 확인한다 — 반기/분기(OPTIONAL spec)는
    원래도 OPTIONAL이라 이 시험의 관심사가 아니다.
    """
    fetcher = FakeFetcher(list_responses_by_pblntf_ty={
        "A": FilingListResult(state=c.ATTEMPT_STATE_FAILED),
        "F": FilingListResult(state=c.ATTEMPT_STATE_FAILED),
    })

    result = select_related_filings(fetcher, "00126380")

    required_failed_attempts = [
        a for a in result.attempts
        if a.state == c.ATTEMPT_STATE_FAILED
        and a.source_kind in (c.SOURCE_KIND_BUSINESS_REPORT, c.SOURCE_KIND_AUDIT_REPORT)
    ]
    assert required_failed_attempts
    assert all(a.requirement == c.REQUIREMENT_REQUIRED for a in required_failed_attempts)


# ══════════════════════════════════════════════════════════
# generation=8 후속 item 3 — 목록 행 수준 혼입 방어(corp_code)
# ══════════════════════════════════════════════════════════


def test_item3_행의_corp_code가_요청과_다르면_그_행을_버리고_전용_attempt를_남긴다() -> None:
    own_row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315", corp_code="00126380")
    other_row = RawFilingRow(
        "20250315000002", "사업보고서 (2025.03)", "20250315", corp_code="99999999",
    )
    fetcher = FakeFetcher(list_responses_by_pblntf_ty={
        "A": FilingListResult(state="OK", rows=(own_row, other_row)),
    })

    result = select_related_filings(fetcher, "00126380")

    # 다른 회사 행은 후보에서 완전히 빠진다.
    assert result.selected[0].rcept_no == "20250315000001"
    mismatch_attempts = [a for a in result.attempts if a.reason_code == c.REASON_LIST_ROW_IDENTITY_MISMATCH]
    assert len(mismatch_attempts) == 1
    assert mismatch_attempts[0].documents_seen == 1  # 걸러낸 행 1건
    assert mismatch_attempts[0].company_id == "00126380"


def test_item3_corp_code가_없는_행은_지금처럼_통과한다() -> None:
    """확인 못 함(필드 부재)이지 불일치가 아니다 — 동작이 나빠지면 안 된다."""
    row = _row("20250315000001", "사업보고서 (2025.03)")  # corp_code 기본값 ""
    fetcher = FakeFetcher(list_responses_by_pblntf_ty={
        "A": FilingListResult(state="OK", rows=(row,)),
    })

    result = select_related_filings(fetcher, "00126380")

    assert result.selected[0].rcept_no == "20250315000001"
    assert not any(a.reason_code == c.REASON_LIST_ROW_IDENTITY_MISMATCH for a in result.attempts)


# ══════════════════════════════════════════════════════════
# generation=8 후속 item 4 — 「행을 봤지만 전부 필터로 제외」와 「행이 아예
# 없음」을 다른 사유 코드로 구분
# ══════════════════════════════════════════════════════════


def test_item4_행은_있지만_이름_키워드로_전부_걸러지면_전용_사유_코드를_남긴다() -> None:
    fetcher = FakeFetcher(list_responses_by_pblntf_ty={
        "A": FilingListResult(state="OK", rows=(_row("20250315000001", "다른 종류의 공시"),)),
        "F": FilingListResult(state="OK", rows=()),
    })

    result = select_related_filings(fetcher, "00126380")

    business_attempt = [a for a in result.attempts if a.attempt_id == "list:dart_business_report"][0]
    assert business_attempt.state == c.ATTEMPT_STATE_MISSING
    assert business_attempt.reason_code == c.REASON_LIST_ROWS_ALL_FILTERED
    # item 2 재정의 — MISSING은 참인 확인이므로 REQUIRED+광역을 유지한다.
    assert business_attempt.requirement == c.REQUIREMENT_REQUIRED
    assert set(business_attempt.slot_ids) == set(c.SOURCE_KIND_SLOT_SCOPE[c.SOURCE_KIND_BUSINESS_REPORT])


def test_item4_행이_아예_없으면_기존_사유_코드를_그대로_쓴다() -> None:
    fetcher = FakeFetcher(list_responses_by_pblntf_ty={
        "A": FilingListResult(state="OK", rows=()),
        "F": FilingListResult(state="OK", rows=()),
    })

    result = select_related_filings(fetcher, "00126380")

    business_attempt = [a for a in result.attempts if a.attempt_id == "list:dart_business_report"][0]
    assert business_attempt.state == c.ATTEMPT_STATE_MISSING
    assert business_attempt.reason_code == c.REASON_LIST_QUERY_MISSING
    assert business_attempt.requirement == c.REQUIREMENT_REQUIRED
