"""수집 조율(collect_dart_evidence) 종단 시험 — 세 회사 유형 + 상한·중복 처리."""

from __future__ import annotations

import time

import pytest

from core.dart_client import (
    DartAuthenticationError,
    DartLimitReached,
    DartResponseError,
    DartTransportError,
)
from features.evidence_collection import collect as collect_module
from features.evidence_collection import constants as c
from features.evidence_collection.collect import collect_dart_evidence
from features.evidence_collection.filing_select import (
    DiscoveredDocumentUrl,
    DocumentFetchResult,
    FilingListResult,
    RawFilingRow,
)
from features.evidence_collection.models import EvidenceCollectionError
from features.evidence_collection.serialize import harvest_to_mapping
from features.evidence_collection.tests.fixtures.fake_fetcher import FakeFetcher
from features.evidence_collection.tests.fixtures.synthetic_documents import (
    AUDIT_ONLY_REPORT_TEXT,
    FINANCIAL_REPORT_TEXT,
    LISTED_BUSINESS_REPORT_TEXT,
)

_NOW = "2026-08-31T00:00:00+09:00"

#: 「채점되는 문단 1개 + 무신호 문단 1개」를 섞은 시험 전용 원문.
_MIXED_SIGNAL_TEXT = """\
I. 회사의 개요
당사는 정밀부품을 생산하는 주식회사이며 법인이다.

II. 아무 신호 없는 문단
오늘 날씨가 맑고 하늘이 파랗다는 이야기를 적어 둔 문단이다.
"""

#: 후보 문단은 있지만(채점 대상) 키워드 신호가 «전혀» 없는 시험 전용 원문.
_NO_SCORED_EVIDENCE_TEXT = """\
알림 사항
오늘 날씨가 맑고 하늘이 파랗다는 이야기를 적어 둔 문단이다.

참고 사항
이 문단도 아무 키워드 신호 없이 그저 문장을 채우기 위한 이야기다.
"""

_MULTI_SLOT_ONE_RANGE_TEXT = """\
II. 사업의 내용
주요 매출은 제품 판매에서 발생하며 주요 고객사에 서비스를 제공한다.
"""

#: 다른 계층 소관 슬롯(historical_performance·비교 4종)의 «옛» 트리거 낱말과
#: self_context 트리거 낱말을 한 문서에 일부러 섞은 시험 전용 원문 —
#: 산출 fragment에 그 슬롯이 0건임을 시험으로 고정하기 위한 것이다.
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
    covered = {
        slot_id
        for fragment in harvest.fragments
        for slot_id in fragment.covered_slot_ids
    }
    # 감사보고서의 「매출액 항목」 숫자 설명을 돈 버는 방식으로 오인하지 않는다.
    assert not any(slot_id.startswith("business_model:") for slot_id in covered)


@pytest.mark.parametrize(
    ("pblntf_ty", "report_nm", "rcept_no", "rcept_dt", "text", "source_kind", "expected"),
    (
        (
            "A",
            "사업보고서 (2024.02)",
            "20240229000001",
            "20240229",
            LISTED_BUSINESS_REPORT_TEXT,
            c.SOURCE_KIND_BUSINESS_REPORT,
            "2024-02-29",
        ),
        (
            "F",
            "감사보고서",
            "20250401000001",
            "20250401",
            AUDIT_ONLY_REPORT_TEXT,
            c.SOURCE_KIND_AUDIT_REPORT,
            "2025-04-01",
        ),
        (
            "A",
            "반기보고서 (2025.06)",
            "20250815000002",
            "20250815",
            "II. 위험관리\n원재료 가격 변동이 당면 과제이며 대응 대책을 추진하고 있다.",
            c.SOURCE_KIND_SEMIANNUAL_REPORT,
            "2025-08-15",
        ),
        (
            "A",
            "분기보고서 (2025.09)",
            "20251114000003",
            "20251114",
            "II. 위험관리\n공급망 변동이 당면 과제이며 대응 대책을 추진하고 있다.",
            c.SOURCE_KIND_QUARTERLY_REPORT,
            "2025-11-14",
        ),
    ),
)
def test_DART_네_자료종류의_접수일은_생산_문서부터_ISO로_고정된다(
    pblntf_ty: str,
    report_nm: str,
    rcept_no: str,
    rcept_dt: str,
    text: str,
    source_kind: str,
    expected: str,
) -> None:
    """OpenDART 원래 8자리는 유지하되 공개 경로에 들어갈 문서는 ISO다."""

    row = RawFilingRow(rcept_no, report_nm, rcept_dt)
    harvest = collect_dart_evidence(_fetcher(pblntf_ty, row, text), "00126380", now=_NOW)
    document = next(item for item in harvest.documents if item.source_kind == source_kind)

    assert row.rcept_dt == rcept_dt
    assert document.published_on == expected
    serialized = harvest_to_mapping(harvest)
    serialized_document = next(
        item for item in serialized["documents"] if item["source_kind"] == source_kind
    )
    assert serialized_document["published_on"] == expected


@pytest.mark.parametrize("rcept_dt", ["20250229", "20251301", "2025-03-15", ""])
def test_잘못된_DART_접수일은_문서를_받기_전에_REQUIRED_FAILED로_남긴다(
    rcept_dt: str,
) -> None:
    row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", rcept_dt)
    fetcher = _fetcher("A", row, LISTED_BUSINESS_REPORT_TEXT)

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    assert fetcher.document_calls == []
    assert harvest.documents == ()
    invalid = [
        attempt
        for attempt in harvest.attempts
        if attempt.reason_code == c.REASON_FILING_RECEIPT_DATE_INVALID
    ]
    assert len(invalid) == 1
    assert invalid[0].state == c.ATTEMPT_STATE_FAILED
    assert invalid[0].requirement == c.REQUIREMENT_REQUIRED
    assert invalid[0].documents_seen == 0


def test_한_DART_원문범위의_복수슬롯은_fragment_ID와_원문을_한번만_싣는다() -> None:
    row = RawFilingRow("20250315000011", "사업보고서 (2025.03)", "20250315")
    fetcher = _fetcher("A", row, _MULTI_SLOT_ONE_RANGE_TEXT)

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    assert len(harvest.fragments) == 1
    fragment = harvest.fragments[0]
    assert set(fragment.covered_slot_ids) == {
        "business_model:revenue_model",
        "business_model:customer_type",
        "business_model:value_exchange",
    }
    mapping = harvest_to_mapping(harvest)
    assert len(mapping["fragments"]) == 1
    assert set(mapping["fragments"][0]["covered_slot_ids"]) == set(
        fragment.covered_slot_ids
    )


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


def test_문단후보_상한에_닿은_문서는_부분근거가_있어도_OK로_위장하지_않는다(
    monkeypatch,
) -> None:
    monkeypatch.setattr(c, "MAX_LONG_FRAGMENT_CANDIDATES_PER_DOCUMENT", 1)
    row = RawFilingRow("20250315000021", "사업보고서", "20250315")
    company_id = "00126380"
    text = (
        "I. 회사의 개요\n"
        "당사는 정밀부품을 생산하는 주식회사이며 법인이다.\n\n"
        "II. 사업의 내용\n"
        "주요 매출은 제품 판매에서 발생하며 고객에게 서비스를 제공한다.\n"
    )

    harvest = collect_dart_evidence(
        _fetcher("A", row, text), company_id, now=_NOW
    )
    document_attempt = next(
        attempt
        for attempt in harvest.attempts
        if attempt.attempt_id.startswith("document:")
    )

    assert harvest.company_id == company_id
    assert harvest.fragments  # 상한 전까지의 실제 부분 근거는 버리지 않는다.
    assert document_attempt.state == c.ATTEMPT_STATE_TRUNCATED
    assert (
        document_attempt.reason_code
        == c.REASON_DOCUMENT_FRAGMENT_COUNT_EXCEEDED
    )
    assert not any(
        attempt.attempt_id.startswith("fragments:")
        for attempt in harvest.attempts
    )


@pytest.mark.parametrize(
    "external_error",
    [
        DartTransportError("가짜 DART 전송 실패"),
        DartResponseError("가짜 DART 응답 실패"),
        OSError("가짜 cache I/O 실패"),
    ],
)
def test_문서조회_예상외부실패는_FAILED로_보존한다(external_error) -> None:
    row = RawFilingRow("20250315000001", "사업보고서", "20250315")

    class ExternalFailureFetcher(FakeFetcher):
        def fetch_document_text(self, rcept_no: str) -> DocumentFetchResult:
            self.document_calls.append(rcept_no)
            raise external_error

    fetcher = ExternalFailureFetcher(
        list_responses_by_pblntf_ty={
            "A": FilingListResult(state="OK", rows=(row,)),
        }
    )

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    failed = [
        attempt
        for attempt in harvest.attempts
        if attempt.attempt_id.endswith(row.rcept_no)
    ]
    assert len(failed) == 1
    assert failed[0].state == c.ATTEMPT_STATE_FAILED
    assert failed[0].reason_code == c.REASON_DOCUMENT_FETCH_FAILED


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
def test_문서조회_코드계약오류를_자료실패로_위장하지_않는다(
    contract_error,
) -> None:
    row = RawFilingRow("20250315000001", "사업보고서", "20250315")

    class BrokenFetcher(FakeFetcher):
        def fetch_document_text(self, rcept_no: str) -> DocumentFetchResult:
            self.document_calls.append(rcept_no)
            raise contract_error

    fetcher = BrokenFetcher(
        list_responses_by_pblntf_ty={
            "A": FilingListResult(state="OK", rows=(row,)),
        }
    )

    with pytest.raises(type(contract_error)):
        collect_dart_evidence(fetcher, "00126380", now=_NOW)

    assert fetcher.document_calls == [row.rcept_no]


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


def test_평문이_중복이어도_정정_XML의_새_URL후보는_영수증과_함께_남는다() -> None:
    """본문 중복 제거가 raw href 발견까지 지우면 안 된다."""

    business_row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    semiannual_row = RawFilingRow("20250815000002", "반기보고서 (2025.06)", "20250815")
    first_url = "https://old-official.example/"
    corrected_url = "https://new-official.example/company"
    fetcher = FakeFetcher(
        list_responses_by_pblntf_ty={
            "A": FilingListResult(state="OK", rows=(business_row, semiannual_row)),
        },
        document_responses_by_rcept_no={
            business_row.rcept_no: DocumentFetchResult(
                state="OK",
                text=LISTED_BUSINESS_REPORT_TEXT,
                official_url_candidates=(
                    DiscoveredDocumentUrl(
                        url=first_url,
                        source_member_name="main.xml",
                        location="raw_xml_chars:10-39",
                        source_payload_sha256="a" * 64,
                    ),
                ),
            ),
            semiannual_row.rcept_no: DocumentFetchResult(
                state="OK",
                text=LISTED_BUSINESS_REPORT_TEXT,
                official_url_candidates=(
                    DiscoveredDocumentUrl(
                        url=corrected_url,
                        source_member_name="correction.xml",
                        location="raw_xml_chars:50-94",
                        source_payload_sha256="b" * 64,
                    ),
                ),
            ),
        },
    )

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    assert len(harvest.documents) == 1
    assert [candidate.url for candidate in harvest.official_url_candidates] == [
        first_url,
        corrected_url,
    ]
    corrected = harvest.official_url_candidates[1]
    assert corrected.source_receipt_no == semiannual_row.rcept_no
    assert corrected.source_document_id.endswith(semiannual_row.rcept_no)
    assert corrected.source_member_name == "correction.xml"
    assert corrected.source_location == "raw_xml_chars:50-94"
    assert corrected.source_payload_sha256 == "b" * 64


def test_다른_엔진_소유_슬롯은_실제_수집_산출물에도_0건이다() -> None:
    """산출 fragment에 이 슬롯이 0건임을 시험으로 고정한다.

    옛 트리거 낱말(매출액·영업이익·동종업계·점유율 등)을 일부러 섞은 문서를
    실제로 수집·분할·채점까지 전부 거쳐도 historical_performance·비교 4종
    슬롯은 fragment에 «절대» 달리지 않아야 한다(구조화 검증기 소관). 같은 문서가
    self_context는 실제로 만들어 내는지도 함께 확인한다.
    """
    row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    fetcher = _fetcher("A", row, _COMPETITIVE_AND_HISTORICAL_PROBE_TEXT)

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    produced_slot_ids = {
        slot_id
        for fragment in harvest.fragments
        for slot_id in fragment.covered_slot_ids
    }
    assert produced_slot_ids.isdisjoint(_EXCLUDED_SLOT_IDS)
    assert "competitive_position:self_context" in produced_slot_ids


# ══════════════════════════════════════════════════════════
# 무신호 조각이 계약 붕괴를 일으키던 결함
# ══════════════════════════════════════════════════════════


def test_P0_1_무신호_조각은_근거와_분리된_차선에_원문까지_남는다() -> None:
    row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    fetcher = _fetcher("A", row, _MIXED_SIGNAL_TEXT)

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    assert len(harvest.documents) == 1
    # 신호 문단은 가장 강한 회사정체성 장 안에서만 배정된다. 같은 문단의
    # 「생산」 한 단어를 다른 장으로 복제하지 않으며, 무신호 문단은 근거
    # fragments에 섞이지 않고 별도 무분류 차선에 보존된다.
    assert {fragment.slot_id for fragment in harvest.fragments} == {
        "identity:corporate_identity",
    }
    assert all(f.section_id and f.slot_id for f in harvest.fragments)
    assert len(harvest.unclassified_documents) == 1
    assert len(harvest.unclassified_fragments) == 1
    unclassified = harvest.unclassified_fragments[0]
    assert "오늘 날씨가 맑고 하늘이 파랗다는" in unclassified.text
    assert unclassified.section_id == ""
    assert unclassified.slot_id == ""
    assert unclassified.covered_slot_ids == ()
    assert unclassified.reason_codes == (c.REASON_NO_SIGNAL,)

    mapping = harvest_to_mapping(harvest)
    for fragment in mapping["fragments"]:
        assert fragment["section_id"] != ""
        assert fragment["slot_id"] != ""
    assert mapping["unclassified_fragments"] == [
        {
            "company_id": unclassified.company_id,
            "fragment_id": unclassified.fragment_id,
            "document_id": unclassified.document_id,
            "location": unclassified.location,
            "text_sha256": unclassified.text_sha256,
            "text": unclassified.text,
            "section_id": "",
            "slot_id": "",
            "covered_slot_ids": [],
            "score_millis": 0,
            "reason_codes": [c.REASON_NO_SIGNAL],
            "period_start": "",
            "period_end": "",
            "unit": "",
            "company_scope": "",
        }
    ]
    assert len(mapping["unclassified_documents"]) == 1
    assert mapping["unclassified_documents"][0]["exact_evidence_hashes"] == []

    observed = [
        a for a in harvest.attempts
        if a.attempt_id.startswith("fragments:") and a.reason_code == c.REASON_NO_SIGNAL
    ]
    assert len(observed) == 1
    assert observed[0].state == c.ATTEMPT_STATE_OK
    assert observed[0].documents_seen == 1  # 무신호 문단 관측 개수


def test_짧은_경쟁문장은_writer_근거로_승격하지_않고_정확_원문만_남긴다() -> None:
    sentence = "가나다전자는 베타전자와 경쟁합니다."
    row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    fetcher = _fetcher("A", row, f"{sentence}\n\n잡음\n")

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    assert all(sentence not in fragment.text for fragment in harvest.fragments)
    matches = [
        fragment
        for fragment in harvest.unclassified_fragments
        if fragment.text == sentence
    ]
    assert len(matches) == 1
    assert matches[0].section_id == ""
    assert matches[0].slot_id == ""
    assert matches[0].covered_slot_ids == ()
    assert matches[0].score_millis == 0
    start, end = (int(value) for value in matches[0].location.split("-", 1))
    assert f"{sentence}\n\n잡음\n"[start:end] == sentence
    mapping = harvest_to_mapping(harvest)
    assert all(
        document["exact_evidence_hashes"] == []
        for document in mapping["unclassified_documents"]
    )


@pytest.mark.parametrize("fatal_error", [DartLimitReached("한도"), DartAuthenticationError("인증")])
def test_문서조회_치명오류는_즉시_전파되어_다음_전송이_0회다(fatal_error) -> None:
    rows = (
        RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315"),
        RawFilingRow("20250815000002", "반기보고서 (2025.06)", "20250815"),
    )

    class FatalDocumentFetcher(FakeFetcher):
        def fetch_document_text(self, rcept_no: str) -> DocumentFetchResult:
            self.document_calls.append(rcept_no)
            raise fatal_error

    fetcher = FatalDocumentFetcher(
        list_responses_by_pblntf_ty={"A": FilingListResult(state="OK", rows=rows)}
    )

    with pytest.raises(type(fatal_error)):
        collect_dart_evidence(fetcher, "00126380", now=_NOW)

    assert fetcher.document_calls == ["20250315000001"]


# ══════════════════════════════════════════════════════════
# MISSING이 FAILED로 뭉개지던 결함
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


def test_P0_2_알_수_없는_state는_외부장애로_위장하지_않고_계약오류가_난다() -> None:
    with pytest.raises(ValueError, match="알 수 없는 DART fetch 결과 상태"):
        DocumentFetchResult(state="알수없음")


# ══════════════════════════════════════════════════════════
# 본문 없는 문서가 계약 붕괴를 일으키던 결함
# ══════════════════════════════════════════════════════════


def test_P0_3_scored_근거가_없는_문서는_documents에서_제외되고_attempt로_남는다() -> None:
    row = RawFilingRow("20250401000001", "감사보고서", "20250401")
    fetcher = _fetcher("F", row, _NO_SCORED_EVIDENCE_TEXT)

    harvest = collect_dart_evidence(fetcher, "00164788", now=_NOW)

    assert harvest.documents == ()
    assert harvest.fragments == ()
    assert len(harvest.unclassified_documents) == 1
    assert len(harvest.unclassified_fragments) == 2
    # 의미 분류 실패가 회사 유형 판정까지 지우지 않는다. 실제로 연 문서가
    # 감사보고서였다는 관측은 무분류 차선의 문서 신원으로 남아 있다.
    assert harvest.company_type == c.COMPANY_TYPE_AUDIT_ONLY
    no_evidence_attempts = [
        a for a in harvest.attempts if a.reason_code == c.REASON_DOCUMENT_NO_SCORED_EVIDENCE
    ]
    assert len(no_evidence_attempts) == 1
    assert no_evidence_attempts[0].state == c.ATTEMPT_STATE_OK
    assert no_evidence_attempts[0].documents_seen == 2  # 관측된 무신호 문단 개수


def test_정식_짧은관측_filter가_cap에_닿으면_문서attempt는_TRUNCATED다(
    monkeypatch,
) -> None:
    monkeypatch.setattr(c, "MAX_SHORT_OBSERVATION_CANDIDATES_PER_DOCUMENT", 1)
    row = RawFilingRow("20250401000001", "감사보고서", "20250401")
    text = (
        "I. 회사의 개요\n"
        "당사는 정밀부품을 생산하는 주식회사이며 법인이다.\n\n"
        "가나다는 나다라와 경쟁합니다.\n\n"
        "마바사는 사아자와 경쟁합니다.\n"
    )

    harvest = collect_dart_evidence(
        _fetcher("F", row, text),
        "00164788",
        now=_NOW,
        short_observation_filter=lambda candidate: "경쟁" in candidate,
    )

    document_attempt = next(
        attempt
        for attempt in harvest.attempts
        if attempt.attempt_id.startswith("document:")
    )
    assert document_attempt.state == c.ATTEMPT_STATE_TRUNCATED
    assert document_attempt.reason_code == c.REASON_DOCUMENT_FRAGMENT_COUNT_EXCEEDED
    assert any("나다라와 경쟁" in fragment.text for fragment in harvest.unclassified_fragments)


# ══════════════════════════════════════════════════════════
# exact_evidence_hashes(문서 1·조각 2 규모, 실제 실행)
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
    """회사 A 대상 수집 산출의 모든 fragment·
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


def test_반기와_분기_문단은_자기_소유_슬롯만_생산하고_허용_신호는_보존한다() -> None:
    """자료 종류별 슬롯 정본을 생산자가 지켜 앱의 뒤늦은 계약 오류를 막는다."""

    business_row = RawFilingRow(
        "20260315000001", "사업보고서 (2025.12)", "20260315"
    )
    semiannual_row = RawFilingRow(
        "20260815000002", "반기보고서 (2026.06)", "20260815"
    )
    quarterly_row = RawFilingRow(
        "20261115000003", "분기보고서 (2026.09)", "20261115"
    )
    semiannual_identity = (
        "I. 회사의 개요\n"
        "당사는 정밀부품을 생산하는 주식회사이며 법인이다."
    )
    semiannual_challenge = (
        "II. 위험관리\n"
        "원재료 가격 변동이 당면 과제이자 위험이며 대응 대책을 추진하고 있다."
    )
    quarterly_portfolio = (
        "II. 주요 제품\n"
        "대표 제품은 정밀 센서이며 핵심 제품의 매출 비중을 관리한다."
    )
    quarterly_change = (
        "III. 요약재무정보\n"
        "신규 생산라인 증설을 완료하여 공급 능력을 확대했다."
    )
    fetcher = FakeFetcher(
        list_responses_by_pblntf_ty={
            "A": FilingListResult(
                state="OK",
                rows=(business_row, semiannual_row, quarterly_row),
            ),
        },
        document_responses_by_rcept_no={
            business_row.rcept_no: DocumentFetchResult(
                state="OK",
                text=(
                    "II. 사업의 내용\n"
                    "주요 매출은 제품 판매에서 발생하며 고객사에 서비스를 제공한다."
                ),
            ),
            semiannual_row.rcept_no: DocumentFetchResult(
                state="OK",
                text=f"{semiannual_identity}\n\n{semiannual_challenge}",
            ),
            quarterly_row.rcept_no: DocumentFetchResult(
                state="OK",
                text=f"{quarterly_portfolio}\n\n{quarterly_change}",
            ),
        },
    )

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)
    supplements = [
        fragment
        for fragment in harvest.fragments
        if fragment.document_id.startswith(
            (c.SOURCE_KIND_SEMIANNUAL_REPORT, c.SOURCE_KIND_QUARTERLY_REPORT)
        )
    ]

    assert supplements
    for fragment in supplements:
        source_kind = fragment.document_id.split(":", 1)[0]
        assert set(fragment.covered_slot_ids) <= set(
            c.SOURCE_KIND_SLOT_SCOPE[source_kind]
        )
    assert any(
        fragment.document_id.startswith(c.SOURCE_KIND_SEMIANNUAL_REPORT)
        and fragment.section_id == "current_challenges"
        for fragment in supplements
    )
    assert any(
        fragment.document_id.startswith(c.SOURCE_KIND_QUARTERLY_REPORT)
        and fragment.section_id == "past_changes"
        for fragment in supplements
    )
    assert all(
        semiannual_identity not in fragment.text
        and quarterly_portfolio not in fragment.text
        for fragment in harvest.unclassified_fragments
    )
    supplement_attempts = [
        attempt
        for attempt in harvest.attempts
        if attempt.attempt_id.startswith("document:")
        and any(
            marker in attempt.attempt_id
            for marker in (
                c.SOURCE_KIND_SEMIANNUAL_REPORT,
                c.SOURCE_KIND_QUARTERLY_REPORT,
            )
        )
    ]
    assert len(supplement_attempts) == 2
    assert all(attempt.state == c.ATTEMPT_STATE_OK for attempt in supplement_attempts)


# ══════════════════════════════════════════════════════════
# generation=8 후속 item 2 재정의 — 기준은
# 「그 조회가 실제로 슬롯을 들여다봤는가」다. 문서 fetch·분할·채점을 실제로
# 거친 attempt(OK)는 광역 slot_ids + REQUIRED가 «정직하다»(그대로 둔다).
# 문제는 목록 조회뿐이다 — 「찾았다」(OK)는 사실만으로는 슬롯을 들여다본
# 게 아니므로 OPTIONAL로 내린다. MISSING(공시가 실제로 없다는 확인)은
# 목록·문서 어느 단계든 참인 확인이라 REQUIRED+광역을 유지한다.
# ══════════════════════════════════════════════════════════


def _count_list_ok_required(attempts) -> int:
    """「목록 조회 + OK + REQUIRED」가 0건임을 세는 시험용."""
    return sum(
        1 for a in attempts
        if a.attempt_id.startswith("list:")
        and a.state == c.ATTEMPT_STATE_OK
        and a.requirement == c.REQUIREMENT_REQUIRED
    )


def test_item2_문서_fetch_ok는_광역_slot_ids와_REQUIRED를_그대로_유지한다() -> None:
    """fetch·분할·채점을 실제로 거쳤으므로(전문을 훑었다) 좁히지 않는다."""
    row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    fetcher = _fetcher("A", row, LISTED_BUSINESS_REPORT_TEXT)

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    fetch_ok_attempt = [
        a for a in harvest.attempts
        if a.attempt_id.startswith("document:") and a.reason_code == c.REASON_DOCUMENT_FETCH_OK
    ][0]
    assert fetch_ok_attempt.requirement == c.REQUIREMENT_REQUIRED
    assert set(fetch_ok_attempt.slot_ids) == set(c.SOURCE_KIND_SLOT_SCOPE[c.SOURCE_KIND_BUSINESS_REPORT])


def test_item2_no_scored_evidence와_missing_fetch는_REQUIRED_광역을_유지하고_duplicate만_OPTIONAL이다() -> None:
    """「참인 확인」(문서를 다 읽었지만 없음 / 문서가
    실제로 없음)은 REQUIRED+광역을 유지한다. duplicate만 fetch·분할·채점을
    아예 건너뛰므로 여전히 OPTIONAL이다.
    """
    # 1) no scored evidence — 문서를 다 읽었지만 어떤 슬롯도 못 찾음(참인 확인)
    row1 = RawFilingRow("20250401000001", "감사보고서", "20250401")
    harvest1 = collect_dart_evidence(_fetcher("F", row1, _NO_SCORED_EVIDENCE_TEXT), "00164788", now=_NOW)
    no_evidence_attempt = [
        a for a in harvest1.attempts if a.reason_code == c.REASON_DOCUMENT_NO_SCORED_EVIDENCE
    ][0]
    assert no_evidence_attempt.requirement == c.REQUIREMENT_REQUIRED
    assert set(no_evidence_attempt.slot_ids) == set(c.SOURCE_KIND_SLOT_SCOPE[c.SOURCE_KIND_AUDIT_REPORT])

    # 2) duplicate — 분할·채점을 아예 건너뛰므로 여전히 OPTIONAL
    business_row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    semiannual_row = RawFilingRow("20250815000002", "반기보고서 (2025.06)", "20250815")
    dup_fetcher = FakeFetcher(
        list_responses_by_pblntf_ty={
            "A": FilingListResult(state="OK", rows=(business_row, semiannual_row)),
        },
        document_responses_by_rcept_no={
            business_row.rcept_no: DocumentFetchResult(state="OK", text=LISTED_BUSINESS_REPORT_TEXT),
            semiannual_row.rcept_no: DocumentFetchResult(state="OK", text=LISTED_BUSINESS_REPORT_TEXT),
        },
    )
    harvest2 = collect_dart_evidence(dup_fetcher, "00126380", now=_NOW)
    duplicate_attempt = [a for a in harvest2.attempts if a.reason_code == c.REASON_DOCUMENT_DUPLICATE][0]
    assert duplicate_attempt.requirement == c.REQUIREMENT_OPTIONAL

    # 3) missing document fetch — 「그 문서가 실제로 없다」는 참인 확인
    row3 = RawFilingRow("20250315000001", "사업보고서", "20250315")
    missing_fetcher = FakeFetcher(
        list_responses_by_pblntf_ty={"A": FilingListResult(state="OK", rows=(row3,))},
        document_responses_by_rcept_no={row3.rcept_no: DocumentFetchResult(state=c.ATTEMPT_STATE_MISSING)},
    )
    harvest3 = collect_dart_evidence(missing_fetcher, "00126380", now=_NOW)
    missing_attempt = [a for a in harvest3.attempts if a.reason_code == c.REASON_DOCUMENT_FETCH_MISSING][0]
    assert missing_attempt.requirement == c.REQUIREMENT_REQUIRED
    assert set(missing_attempt.slot_ids) == set(c.SOURCE_KIND_SLOT_SCOPE[c.SOURCE_KIND_BUSINESS_REPORT])


def test_item2_여러_시나리오에_걸쳐_목록_OK_REQUIRED_위반_0건이다() -> None:
    """「목록 조회 + OK + REQUIRED」가 0건임을 세는 시험.
    상장·감사·금융·무신호·목차 등 대표 시나리오를 전부 실제로 돌려 확인한다.
    """
    scenarios: list[tuple[str, str, str, str]] = [
        ("A", "사업보고서 (2025.03)", "00126380", LISTED_BUSINESS_REPORT_TEXT),
        ("F", "감사보고서", "00164788", AUDIT_ONLY_REPORT_TEXT),
        ("A", "사업보고서 (2025.03)", "00355758", FINANCIAL_REPORT_TEXT),
        ("A", "사업보고서 (2025.03)", "00126380", _MIXED_SIGNAL_TEXT),
        ("F", "감사보고서", "00164788", _NO_SCORED_EVIDENCE_TEXT),
    ]
    for pblntf_ty, report_nm, company_id, text in scenarios:
        row = RawFilingRow("20250315000001", report_nm, "20250315")
        harvest = collect_dart_evidence(_fetcher(pblntf_ty, row, text), company_id, now=_NOW)
        assert harvest.attempts  # 시나리오가 실제로 뭔가를 만들어냈는지(공허 통과 방지)
        assert _count_list_ok_required(harvest.attempts) == 0


def test_item2_목록_OK인데_문서_fetch가_FAILED면_그_source_kind에는_확인된_REQUIRED가_없다() -> None:
    """목록 OK 뒤 문서 fetch가 FAILED면 최종 판정 방향이
    UNKNOWN 쪽이어야 한다. 내 엔진 안에서 이를 대신 증명하는 방법: 이
    source_kind에 REQUIRED+OK/MISSING인 attempt가 «하나도» 없고, REQUIRED+
    FAILED인 attempt가 있어야 한다 — 소비 계약의 「REQUIRED가 전부
    OK/MISSING이면 자료부족」 조건을 깨서 UNKNOWN 쪽으로 이어지게 한다.
    """
    row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    fetcher = FakeFetcher(
        list_responses_by_pblntf_ty={"A": FilingListResult(state="OK", rows=(row,))},
        document_responses_by_rcept_no={},  # 응답 없음 → FakeFetcher 기본값 FAILED
    )

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    business_report_attempts = [
        a for a in harvest.attempts if a.source_kind == c.SOURCE_KIND_BUSINESS_REPORT
    ]
    assert business_report_attempts  # 실제로 attempt가 만들어졌는지
    confirmed_absent = [
        a for a in business_report_attempts
        if a.requirement == c.REQUIREMENT_REQUIRED and a.state in (c.ATTEMPT_STATE_OK, c.ATTEMPT_STATE_MISSING)
    ]
    assert confirmed_absent == []  # 「확인했는데 근거가 없다」로 읽힐 REQUIRED attempt가 없다
    blocking = [
        a for a in business_report_attempts
        if a.requirement == c.REQUIREMENT_REQUIRED and a.state == c.ATTEMPT_STATE_FAILED
    ]
    assert blocking  # 대신 REQUIRED+FAILED가 있어 UNKNOWN 쪽으로 이어진다
    # 목록 조회 자체는 OK였다(찾았다) — OPTIONAL로 내려가 있어야 위 결론이 성립한다.
    list_attempt = [a for a in business_report_attempts if a.attempt_id.startswith("list:")][0]
    assert list_attempt.state == c.ATTEMPT_STATE_OK
    assert list_attempt.requirement == c.REQUIREMENT_OPTIONAL


# ══════════════════════════════════════════════════════════
# item2가 과잉 교정되면(광역 slot_ids를
# 실제 확인된 슬롯으로 좁히면) 빈 슬롯을 덮는 REQUIRED 경로가 사라져
# 모든 회사가 UNKNOWN(TRANSIENT_FAILURE)으로 오판정될 수 있었다. 「위반
# 0건」 시험은 거짓 확인(false positive) 방향만 봤으므로, 반대 방향
# (거짓 미확인 — false negative, 빈 슬롯의 REQUIRED 경로 소실)도 함께
# 세는 시험을 추가한다. 계약을 import할 수 없으므로 계약이 보는 조건
# (REQUIRED+OK/MISSING·REQUIRED+FAILED의 slot_ids 합집합)을 직접 검사한다.
# ══════════════════════════════════════════════════════════

_COVERAGE_SCENARIOS: tuple[tuple[str, str, str, str, str], ...] = (
    ("A", "사업보고서 (2025.03)", "00126380", LISTED_BUSINESS_REPORT_TEXT, c.SOURCE_KIND_BUSINESS_REPORT),
    ("F", "감사보고서", "00164788", AUDIT_ONLY_REPORT_TEXT, c.SOURCE_KIND_AUDIT_REPORT),
    ("A", "사업보고서 (2025.03)", "00355758", FINANCIAL_REPORT_TEXT, c.SOURCE_KIND_BUSINESS_REPORT),
)


def test_gap_정상_수집시_그_source_kind의_collector_슬롯_전부가_REQUIRED_OK_MISSING로_덮인다() -> None:
    """정상 수집(문서 fetch OK)이면 빈 슬롯도 REQUIRED 경로로 덮여 자료부족
    (INSUFFICIENT) 방향이 되어야 한다 — UNKNOWN(TRANSIENT_FAILURE)로 새면
    안 된다. 상장·감사·금융 3개 시나리오에서 미커버 슬롯 0개를 확인한다.
    """
    for pblntf_ty, report_nm, company_id, text, source_kind in _COVERAGE_SCENARIOS:
        row = RawFilingRow("20250315000001", report_nm, "20250315")
        harvest = collect_dart_evidence(_fetcher(pblntf_ty, row, text), company_id, now=_NOW)

        full_scope = set(c.SOURCE_KIND_SLOT_SCOPE[source_kind])
        required_ok_or_missing = [
            a for a in harvest.attempts
            if a.source_kind == source_kind
            and a.requirement == c.REQUIREMENT_REQUIRED
            and a.state in (c.ATTEMPT_STATE_OK, c.ATTEMPT_STATE_MISSING)
        ]
        assert required_ok_or_missing, f"{company_id}: REQUIRED+OK/MISSING attempt가 하나도 없습니다"
        covered = set().union(*(a.slot_ids for a in required_ok_or_missing))
        uncovered = full_scope - covered
        assert uncovered == set(), f"{company_id}: 빈 슬롯이 REQUIRED 경로 밖으로 샙니다 — {uncovered}"


def test_gap_문서_fetch가_FAILED면_그_슬롯들이_REQUIRED_FAILED로_덮인다() -> None:
    """문서 fetch 자체가 실패하면(전송 장애) 그 source_kind의 슬롯 전부가
    REQUIRED+FAILED로 덮여야 UNKNOWN 방향이 끝까지 보존된다 — 슬롯이
    아무 REQUIRED attempt에도 안 걸려 조용히 자료부족으로 새면 안 된다.
    """
    for pblntf_ty, report_nm, company_id, _text, source_kind in _COVERAGE_SCENARIOS:
        row = RawFilingRow("20250315000001", report_nm, "20250315")
        fetcher = FakeFetcher(
            list_responses_by_pblntf_ty={pblntf_ty: FilingListResult(state="OK", rows=(row,))},
            document_responses_by_rcept_no={},  # 응답 없음 → FakeFetcher 기본값 FAILED
        )
        harvest = collect_dart_evidence(fetcher, company_id, now=_NOW)

        full_scope = set(c.SOURCE_KIND_SLOT_SCOPE[source_kind])
        required_failed = [
            a for a in harvest.attempts
            if a.source_kind == source_kind
            and a.requirement == c.REQUIREMENT_REQUIRED
            and a.state == c.ATTEMPT_STATE_FAILED
        ]
        assert required_failed, f"{company_id}: REQUIRED+FAILED attempt가 하나도 없습니다"
        covered = set().union(*(a.slot_ids for a in required_failed))
        uncovered = full_scope - covered
        assert uncovered == set(), f"{company_id}: FAILED가 못 덮는 슬롯이 있습니다 — {uncovered}"
        # 「확인했는데 근거가 없다」로 잘못 읽힐 REQUIRED+OK/MISSING은 없어야 한다.
        confirmed_absent = [
            a for a in harvest.attempts
            if a.source_kind == source_kind
            and a.requirement == c.REQUIREMENT_REQUIRED
            and a.state in (c.ATTEMPT_STATE_OK, c.ATTEMPT_STATE_MISSING)
        ]
        assert confirmed_absent == [], f"{company_id}: FAILED 상황인데 REQUIRED+OK/MISSING이 섞여 있습니다"
