"""수집 조율(collect_dart_evidence) 종단 시험 — 세 회사 유형 + 상한·중복 처리."""

from __future__ import annotations

import time

from features.evidence_collection import collect as collect_module
from features.evidence_collection import constants as c
from features.evidence_collection.collect import collect_dart_evidence
from features.evidence_collection.filing_select import DocumentFetchResult, FilingListResult, RawFilingRow
from features.evidence_collection.models import EvidenceCollectionError
from features.evidence_collection.serialize import harvest_to_mapping
from features.evidence_collection.tests.fixtures.fake_fetcher import FakeFetcher
from features.evidence_collection.tests.fixtures.synthetic_documents import (
    AUDIT_ONLY_REPORT_TEXT,
    FINANCIAL_REPORT_TEXT,
    LISTED_BUSINESS_REPORT_TEXT,
)

_NOW = "2026-08-31T00:00:00+09:00"

#: 「채점되는 문단 1개 + 무신호 문단 1개」를 섞은 시험 전용 원문(P0-1).
_MIXED_SIGNAL_TEXT = """\
I. 회사의 개요
당사는 정밀부품을 생산하는 주식회사이며 법인이다.

II. 아무 신호 없는 문단
오늘 날씨가 맑고 하늘이 파랗다는 이야기를 적어 둔 문단이다.
"""

#: 후보 문단은 있지만(채점 대상) 키워드 신호가 «전혀» 없는 시험 전용 원문(P0-3).
_NO_SCORED_EVIDENCE_TEXT = """\
알림 사항
오늘 날씨가 맑고 하늘이 파랗다는 이야기를 적어 둔 문단이다.

참고 사항
이 문단도 아무 키워드 신호 없이 그저 문장을 채우기 위한 이야기다.
"""

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


def test_deadline이_처음부터_지난_상태면_목록_조회조차_시작하지_않는다() -> None:
    """P1-3 수정 후 — 예전에는 목록 조회 2건을 다 돌리고 나서야 걸렀지만,
    지금은 새 목록 조회를 시작하기 «직전»에 걸러 아예 시작하지 않는다.
    """
    row = RawFilingRow("20250315000001", "사업보고서", "20250315")
    fetcher = _fetcher("A", row, LISTED_BUSINESS_REPORT_TEXT)

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW, deadline_seconds=-1.0)

    assert harvest.documents == ()
    truncated = [a for a in harvest.attempts if a.reason_code == c.REASON_DEADLINE_EXCEEDED]
    assert truncated  # 목록 조회 단계에서 최소 1건 이상 TRUNCATED로 남는다
    assert all(a.state == c.ATTEMPT_STATE_TRUNCATED for a in truncated)
    assert all(a.attempt_id.startswith("list:") for a in truncated)


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


# ══════════════════════════════════════════════════════════
# P0-1 — 무신호 조각이 계약 붕괴를 일으키던 결함
# ══════════════════════════════════════════════════════════


def test_P0_1_무신호_조각은_harvest_fragments에도_직렬화_출력에도_안_남는다() -> None:
    row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    fetcher = _fetcher("A", row, _MIXED_SIGNAL_TEXT)

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    assert len(harvest.documents) == 1
    assert len(harvest.fragments) == 1  # 무신호 문단은 fragments에 없다
    assert all(f.section_id and f.slot_id for f in harvest.fragments)

    mapping = harvest_to_mapping(harvest)
    for fragment in mapping["fragments"]:
        assert fragment["section_id"] != ""
        assert fragment["slot_id"] != ""

    observed = [
        a for a in harvest.attempts
        if a.attempt_id.startswith("fragments:") and a.reason_code == c.REASON_NO_SIGNAL
    ]
    assert len(observed) == 1
    assert observed[0].state == c.ATTEMPT_STATE_OK
    assert observed[0].documents_seen == 1  # 무신호 문단 관측 개수


# ══════════════════════════════════════════════════════════
# P0-2 — MISSING이 FAILED로 뭉개지던 결함
# ══════════════════════════════════════════════════════════


def test_P0_2_확인된_부재는_MISSING으로_전송_장애는_FAILED로_끝까지_보존된다() -> None:
    missing_row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    failed_row = RawFilingRow("20250815000002", "반기보고서 (2025.06)", "20250815")
    fetcher = FakeFetcher(
        list_responses_by_pblntf_ty={
            "A": FilingListResult(state="OK", rows=(missing_row, failed_row)),
        },
        document_responses_by_rcept_no={
            missing_row.rcept_no: DocumentFetchResult(state=c.ATTEMPT_STATE_MISSING),
            failed_row.rcept_no: DocumentFetchResult(state=c.ATTEMPT_STATE_FAILED),
        },
    )

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    missing_attempts = [a for a in harvest.attempts if a.attempt_id.endswith(missing_row.rcept_no)]
    failed_attempts = [a for a in harvest.attempts if a.attempt_id.endswith(failed_row.rcept_no)]
    assert missing_attempts[0].state == c.ATTEMPT_STATE_MISSING
    assert missing_attempts[0].reason_code == c.REASON_DOCUMENT_FETCH_MISSING
    assert failed_attempts[0].state == c.ATTEMPT_STATE_FAILED
    assert failed_attempts[0].reason_code == c.REASON_DOCUMENT_FETCH_FAILED


def test_P0_2_알_수_없는_state는_fail_closed로_FAILED_처리한다() -> None:
    row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    fetcher = FakeFetcher(
        list_responses_by_pblntf_ty={"A": FilingListResult(state="OK", rows=(row,))},
        document_responses_by_rcept_no={row.rcept_no: DocumentFetchResult(state="알수없음")},
    )

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    document_attempts = [a for a in harvest.attempts if a.attempt_id.startswith("document:")]
    assert len(document_attempts) == 1
    assert document_attempts[0].state == c.ATTEMPT_STATE_FAILED
    assert document_attempts[0].reason_code == c.REASON_DOCUMENT_FETCH_FAILED


# ══════════════════════════════════════════════════════════
# P0-3 — 본문 없는 문서가 계약 붕괴를 일으키던 결함
# ══════════════════════════════════════════════════════════


def test_P0_3_scored_근거가_없는_문서는_documents에서_제외되고_attempt로_남는다() -> None:
    row = RawFilingRow("20250401000001", "감사보고서", "20250401")
    fetcher = _fetcher("F", row, _NO_SCORED_EVIDENCE_TEXT)

    harvest = collect_dart_evidence(fetcher, "00164788", now=_NOW)

    assert harvest.documents == ()
    assert harvest.fragments == ()
    no_evidence_attempts = [
        a for a in harvest.attempts if a.reason_code == c.REASON_DOCUMENT_NO_SCORED_EVIDENCE
    ]
    assert len(no_evidence_attempts) == 1
    assert no_evidence_attempts[0].state == c.ATTEMPT_STATE_OK
    assert no_evidence_attempts[0].documents_seen == 2  # 관측된 무신호 문단 개수


# ══════════════════════════════════════════════════════════
# P0-5 — exact_evidence_hashes(문서 1·조각 2 규모, 실제 실행)
# ══════════════════════════════════════════════════════════


def test_P0_5_실제_수집_산출물의_exact_evidence_hashes는_scored_fragment_해시_전체다() -> None:
    row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    fetcher = _fetcher("A", row, _MIXED_SIGNAL_TEXT)

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)
    mapping = harvest_to_mapping(harvest)

    assert len(mapping["documents"]) == 1
    document_mapping = mapping["documents"][0]
    fragment_hashes = sorted({f["text_sha256"] for f in mapping["fragments"]})
    assert document_mapping["exact_evidence_hashes"] == fragment_hashes
    assert document_mapping["exact_evidence_hashes"] != []


# ══════════════════════════════════════════════════════════
# P1-1 — attempts를 classify에 넘겨 undecided를 실제로 만들어내는지
# ══════════════════════════════════════════════════════════


def test_P1_1_필수_목록_조회가_모두_FAILED면_undecided를_돌려준다() -> None:
    fetcher = FakeFetcher(
        list_responses_by_pblntf_ty={
            "A": FilingListResult(state=c.ATTEMPT_STATE_FAILED),
            "F": FilingListResult(state=c.ATTEMPT_STATE_FAILED),
        },
    )

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    assert harvest.company_type == c.COMPANY_TYPE_UNDECIDED
    assert harvest.documents == ()


# ══════════════════════════════════════════════════════════
# P1-2 — 자료형 예외가 수집 전체를 날리던 결함
# ══════════════════════════════════════════════════════════


def test_P1_2_모델_생성_예외는_그_문서만_버리고_나머지는_계속_처리한다(monkeypatch) -> None:
    bad_row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    good_row = RawFilingRow("20250815000002", "반기보고서 (2025.06)", "20250815")
    fetcher = FakeFetcher(
        list_responses_by_pblntf_ty={
            "A": FilingListResult(state="OK", rows=(bad_row, good_row)),
        },
        document_responses_by_rcept_no={
            bad_row.rcept_no: DocumentFetchResult(state="OK", text=LISTED_BUSINESS_REPORT_TEXT),
            good_row.rcept_no: DocumentFetchResult(state="OK", text=LISTED_BUSINESS_REPORT_TEXT[:200] + "\n\n" + LISTED_BUSINESS_REPORT_TEXT[200:]),
        },
    )
    real_collected_document = collect_module.CollectedDocument

    def _flaky_collected_document(*args: object, **kwargs: object) -> object:
        if kwargs.get("source_kind") == c.SOURCE_KIND_BUSINESS_REPORT:
            raise EvidenceCollectionError("시험 전용 강제 실패")
        return real_collected_document(*args, **kwargs)

    monkeypatch.setattr(collect_module, "CollectedDocument", _flaky_collected_document)

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    assert all(doc.source_kind != c.SOURCE_KIND_BUSINESS_REPORT for doc in harvest.documents)
    invalid_attempts = [a for a in harvest.attempts if a.reason_code == c.REASON_DOCUMENT_MODEL_INVALID]
    assert len(invalid_attempts) == 1
    assert invalid_attempts[0].state == c.ATTEMPT_STATE_FAILED
    # 반기보고서(다른 문서)는 정상적으로 살아남았다 — harvest 전체가 무너지지 않았다.
    assert any(doc.source_kind == c.SOURCE_KIND_SEMIANNUAL_REPORT for doc in harvest.documents)


# ══════════════════════════════════════════════════════════
# P1-3 — deadline이 목록 조회·분할·채점에는 적용되지 않던 결함
# ══════════════════════════════════════════════════════════


class _SlowDocumentFetcher:
    """fetch_document_text가 실제로 시간이 걸리는 가짜 fetcher(P1-3 재검사 시험 전용).

    네트워크 접근은 없다 — 로컬 sleep으로 「조회 도중 deadline을 막 넘긴
    시점」을 재현할 뿐이다.
    """

    def __init__(
        self, list_result: FilingListResult, document_result: DocumentFetchResult, delay_seconds: float,
    ) -> None:
        self._list_result = list_result
        self._document_result = document_result
        self._delay_seconds = delay_seconds

    def fetch_filing_list(self, company_id: str, pblntf_ty: str) -> FilingListResult:
        return self._list_result

    def fetch_document_text(self, rcept_no: str) -> DocumentFetchResult:
        time.sleep(self._delay_seconds)
        return self._document_result


def test_P1_3_deadline을_조회_직후_다시_확인해_분할_채점을_건너뛴다() -> None:
    row = RawFilingRow("20250315000001", "사업보고서", "20250315")
    fetcher = _SlowDocumentFetcher(
        list_result=FilingListResult(state="OK", rows=(row,)),
        document_result=DocumentFetchResult(state="OK", text=LISTED_BUSINESS_REPORT_TEXT),
        delay_seconds=0.35,
    )

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW, deadline_seconds=0.2)

    assert harvest.documents == ()
    assert harvest.fragments == ()
    truncated = [
        a for a in harvest.attempts
        if a.attempt_id.startswith("document:") and a.reason_code == c.REASON_DEADLINE_EXCEEDED
    ]
    assert len(truncated) == 1
    assert truncated[0].state == c.ATTEMPT_STATE_TRUNCATED


# ══════════════════════════════════════════════════════════
# P1-4 — 다른 회사 문서 혼입 차단
# ══════════════════════════════════════════════════════════


def test_P1_4_fetcher_메타의_corp_code가_다르면_문서를_버리고_FAILED를_남긴다() -> None:
    row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    fetcher = FakeFetcher(
        list_responses_by_pblntf_ty={"A": FilingListResult(state="OK", rows=(row,))},
        document_responses_by_rcept_no={
            row.rcept_no: DocumentFetchResult(
                state="OK", text=LISTED_BUSINESS_REPORT_TEXT, corp_code="99999999",
            ),
        },
    )

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    assert harvest.documents == ()
    mismatch_attempts = [
        a for a in harvest.attempts if a.reason_code == c.REASON_DOCUMENT_IDENTITY_MISMATCH
    ]
    assert len(mismatch_attempts) == 1
    assert mismatch_attempts[0].state == c.ATTEMPT_STATE_FAILED


def test_P1_4_corp_code가_일치하면_identity_binding에_검증됐다고_정직하게_남는다() -> None:
    row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    fetcher = FakeFetcher(
        list_responses_by_pblntf_ty={"A": FilingListResult(state="OK", rows=(row,))},
        document_responses_by_rcept_no={
            row.rcept_no: DocumentFetchResult(
                state="OK", text=LISTED_BUSINESS_REPORT_TEXT, corp_code="00126380",
            ),
        },
    )

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    assert len(harvest.documents) == 1
    assert f"identity_check={c.IDENTITY_CHECK_VERIFIED}" in harvest.documents[0].identity_binding


def test_P1_4_메타가_없으면_검증했다고_거짓_주장하지_않는다() -> None:
    row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    fetcher = _fetcher("A", row, LISTED_BUSINESS_REPORT_TEXT)  # corp_code 기본값 "" (메타 없음)

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    assert len(harvest.documents) == 1
    assert f"identity_check={c.IDENTITY_CHECK_UNVERIFIED}" in harvest.documents[0].identity_binding


# ══════════════════════════════════════════════════════════
# P1-5 — 누적 바이트 상한 TRUNCATED 경로 + 중복 유령 소비
# ══════════════════════════════════════════════════════════


def test_P1_5_누적_바이트_상한을_넘으면_TRUNCATED로_기록한다(monkeypatch) -> None:
    business_row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    semiannual_row = RawFilingRow("20250815000002", "반기보고서 (2025.06)", "20250815")
    business_bytes = len(LISTED_BUSINESS_REPORT_TEXT.encode("utf-8"))
    # 첫 문서는 통과하지만 둘째 문서까지 더하면 넘치도록 총합 상한을 좁힌다.
    monkeypatch.setattr(c, "MAX_TOTAL_TEXT_BYTES", business_bytes + 10)
    fetcher = FakeFetcher(
        list_responses_by_pblntf_ty={
            "A": FilingListResult(state="OK", rows=(business_row, semiannual_row)),
        },
        document_responses_by_rcept_no={
            business_row.rcept_no: DocumentFetchResult(state="OK", text=LISTED_BUSINESS_REPORT_TEXT),
            # 다른 내용(중복이 아님)이면서 상한을 넘기는 두번째 문서.
            semiannual_row.rcept_no: DocumentFetchResult(
                state="OK", text=FINANCIAL_REPORT_TEXT + "\n" + LISTED_BUSINESS_REPORT_TEXT,
            ),
        },
    )

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    assert len(harvest.documents) == 1
    assert harvest.documents[0].source_kind == c.SOURCE_KIND_BUSINESS_REPORT
    truncated = [a for a in harvest.attempts if a.reason_code == c.REASON_TOTAL_BYTES_EXCEEDED]
    assert len(truncated) == 1
    assert truncated[0].state == c.ATTEMPT_STATE_TRUNCATED


def test_P1_5_중복_문서는_누적_바이트에_가산되지_않는다(monkeypatch) -> None:
    """중복 판정 «전» 가산이면 유령 소비로 다음 서로 다른 문서가 부당하게 잘린다."""
    business_row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    duplicate_row = RawFilingRow("20250815000002", "반기보고서 (2025.06)", "20250815")
    business_bytes = len(LISTED_BUSINESS_REPORT_TEXT.encode("utf-8"))
    # 상한을 "문서 1개 분량 + 여유"로 좁힌다 — 중복이 가산되면 다음 서로
    # 다른 내용의 문서가 들어올 자리가 없어야(잘못 TRUNCATED) 하지만,
    # 중복은 가산되지 않아야 하므로 실제로는 자리가 남아 있어야 한다.
    monkeypatch.setattr(c, "MAX_TOTAL_TEXT_BYTES", business_bytes + 10)
    fetcher = FakeFetcher(
        list_responses_by_pblntf_ty={
            "A": FilingListResult(state="OK", rows=(business_row, duplicate_row)),
        },
        document_responses_by_rcept_no={
            business_row.rcept_no: DocumentFetchResult(state="OK", text=LISTED_BUSINESS_REPORT_TEXT),
            duplicate_row.rcept_no: DocumentFetchResult(state="OK", text=LISTED_BUSINESS_REPORT_TEXT),
        },
    )

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    duplicate_attempts = [a for a in harvest.attempts if a.reason_code == c.REASON_DOCUMENT_DUPLICATE]
    truncated = [a for a in harvest.attempts if a.reason_code == c.REASON_TOTAL_BYTES_EXCEEDED]
    assert len(duplicate_attempts) == 1
    assert truncated == []  # 유령 소비가 없으면 TRUNCATED가 생기지 않는다


# ══════════════════════════════════════════════════════════
# generation=8 — fragment·attempt도 회사 소유권을 직접 운반한다
# ══════════════════════════════════════════════════════════


def test_gen8_회사_A_수집_산출의_모든_fragment_attempt는_A의_company_id를_갖는다() -> None:
    """team-lead 통보(2026-08-31) — 회사 A 대상 수집 산출의 모든 fragment·
    attempt가 A의 company_id를 갖는지 고정한다. list 단계 attempts·문서
    단계 attempts·scored fragments를 전부 만들어내도록 사업보고서+반기보고서
    두 건을 함께 수집한다(list attempt 여러 종류 + document attempt +
    fragments attempt까지 골고루 나오게).
    """
    company_a = "00126380"
    business_row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    semiannual_row = RawFilingRow("20250815000002", "반기보고서 (2025.06)", "20250815")
    fetcher = FakeFetcher(
        list_responses_by_pblntf_ty={
            "A": FilingListResult(state="OK", rows=(business_row, semiannual_row)),
        },
        document_responses_by_rcept_no={
            business_row.rcept_no: DocumentFetchResult(state="OK", text=LISTED_BUSINESS_REPORT_TEXT),
            semiannual_row.rcept_no: DocumentFetchResult(state="OK", text=_MIXED_SIGNAL_TEXT),
        },
    )

    harvest = collect_dart_evidence(fetcher, company_a, now=_NOW)

    assert harvest.documents  # 문서가 실제로 만들어졌는지(공허하게 통과하지 않도록)
    assert harvest.fragments
    assert harvest.attempts
    assert all(document.company_id == company_a for document in harvest.documents)
    assert all(fragment.company_id == company_a for fragment in harvest.fragments)
    assert all(attempt.company_id == company_a for attempt in harvest.attempts)

    # 직렬화 산출에서도 동일하게 성립해야 한다.
    mapping = harvest_to_mapping(harvest)
    assert all(f["company_id"] == company_a for f in mapping["fragments"])
    assert all(a["company_id"] == company_a for a in mapping["attempts"])
