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

#: Codex 소관 슬롯(historical_performance·비교 4종)의 «옛» 트리거 낱말과
#: self_context 트리거 낱말을 한 문서에 일부러 섞은 시험 전용 원문
#: (2026-08-31 team-lead 통보 — 「산출 fragment에 이 슬롯이 0건임을
#: 시험으로 고정하라」).
_COMPETITIVE_AND_HISTORICAL_PROBE_TEXT = """\
I. 회사의 개요
당사는 반도체 부품을 생산하는 법인이다.

II. 재무에 관한 사항
최근 3개년 매출액과 영업이익, 당기순이익은 아래와 같이 늘었다.
동종업계 경쟁사 대비 업계는 과점 구조이며 점유율과 순위, 규모는 공시 기준으로 확인된다.

III. 시장 현황
당사는 특허를 다수 보유해 경쟁력을 갖추고 있으며 시장점유율에서 업계를 선도한다.
다만 이 경쟁력과 강점, 우위는 상대 회사 이름을 밝히지 않아 확인되지 않는다, 한계와 제약이 있다.
"""

_EXCLUDED_SLOT_IDS = frozenset({
    "past_changes:historical_performance",
    "competitive_position:comparison_target",
    "competitive_position:comparison_metric",
    "competitive_position:comparison_basis",
    "competitive_position:comparison_judgment",
    "competitive_position:limitation",
})


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


def test_다른_엔진_소유_슬롯은_실제_수집_산출물에도_0건이다() -> None:
    """team-lead 통보(2026-08-31) — 산출 fragment에 이 슬롯이 0건임을 시험으로 고정.

    옛 트리거 낱말(매출액·영업이익·동종업계·점유율 등)을 일부러 섞은 문서를
    실제로 수집·분할·채점까지 전부 거쳐도 historical_performance·비교 4종
    슬롯은 fragment에 «절대» 달리지 않아야 한다(Codex 소관). 같은 문서가
    self_context는 실제로 만들어 내는지도 함께 확인한다.
    """
    row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    fetcher = _fetcher("A", row, _COMPETITIVE_AND_HISTORICAL_PROBE_TEXT)

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    produced_slot_ids = {f.slot_id for f in harvest.fragments if f.slot_id}
    assert produced_slot_ids.isdisjoint(_EXCLUDED_SLOT_IDS)
    assert "competitive_position:self_context" in produced_slot_ids
