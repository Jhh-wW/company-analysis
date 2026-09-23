"""공식 근거의 정답 슬롯 회복 — 살릴 것과 막을 것을 짝으로 고정한다.

2026-09-23 4차 실측에서 감사보고서 「회사의 개요」 문단(사업정의·본점)과 수익인식
주석(수익 구성)이 각각 corporate_identity·value_exchange 하나만 받아, 작가가 그
문단으로 정확히 쓴 사업정의·소재지·수익 구성 문장이 검수 전에 탈락했다. 아래
원문은 모두 익명 합성 문장이다.
"""

from __future__ import annotations

from features.evidence_collection import constants as c
from features.evidence_collection import relevance
from features.evidence_collection.collect import collect_dart_evidence
from features.evidence_collection.filing_select import (
    DocumentFetchResult,
    FilingListResult,
    RawFilingRow,
)
from features.evidence_collection.tests.fixtures.fake_fetcher import FakeFetcher

_NOW = "2026-09-23T00:00:00+09:00"
_AUDIT_CANDIDATE_SLOTS = frozenset(c.SOURCE_KIND_CANDIDATE_SLOT_SCOPE[c.SOURCE_KIND_AUDIT_REPORT])
_AUDIT_COVERAGE_SLOTS = frozenset(c.SOURCE_KIND_SLOT_SCOPE[c.SOURCE_KIND_AUDIT_REPORT])

_OVERVIEW = (
    "1. 회사의 개요 주식회사 가나다랩(이하 \"회사\")은 2020년 3월 2일 설립되어 "
    "서울특별시 중구 예시로 10에 본점을 두고 있으며, 인공지능 소프트웨어 개발 및 "
    "공급을 주요 사업으로 영위하고 있습니다."
)
_REVENUE_COMPOSITION = (
    "(13) 수익인식 회사의 주된 영업수익의 형태는 소프트웨어 개발과 관련된 용역 매출, "
    "디지털 콘텐츠 매출 등으로 구성됩니다. 회사는 받았거나 받을 대가의 공정가치로 "
    "수익을 측정하고 있습니다."
)


def _covered(text: str, allowed: frozenset[str] = _AUDIT_CANDIDATE_SLOTS) -> set[str]:
    return {score.slot_id for score in relevance.score_fragment_slots(text, allowed_slot_ids=allowed)}


# ── 사업정의·소재지 ────────────────────────────────────────


def test_주요_사업으로_영위_문형은_사업정의와_소재지를_함께_지원한다() -> None:
    covered = _covered(_OVERVIEW)
    assert {
        "identity:corporate_identity",
        "identity:business_definition",
        "identity:official_location",
    } <= covered


def test_회사명만_있는_문단은_사업정의나_소재지를_받지_않는다() -> None:
    covered = _covered("주식회사 가나다랩 대표이사 홍길동")
    assert "identity:business_definition" not in covered
    assert "identity:official_location" not in covered


def test_웹_주소만_있는_문단은_법인_소재지가_아니다() -> None:
    covered = _covered("회사의 홈페이지 주소는 www.example.com 이며 전자우편 주소로 문의를 받습니다.")
    assert "identity:official_location" not in covered


def test_선택_소재지_칸은_필수_칸만_허용하면_채점되지_않는다() -> None:
    """선택 칸은 후보 범위에서만 열리고 필수 커버리지 범위를 넓히지 않는다."""

    assert "identity:official_location" not in _covered(_OVERVIEW, _AUDIT_COVERAGE_SLOTS)
    assert "identity:business_definition" in _covered(_OVERVIEW, _AUDIT_COVERAGE_SLOTS)


# ── 수익 구성 ──────────────────────────────────────────────


def test_영업수익의_형태_구성_문형은_수익모델을_지원한다() -> None:
    scores = relevance.score_fragment_slots(
        _REVENUE_COMPOSITION, allowed_slot_ids=_AUDIT_CANDIDATE_SLOTS
    )
    assert scores[0].slot_id == "business_model:revenue_model"
    assert "business_model:value_exchange" in {score.slot_id for score in scores}


def test_전사_연간_실적은_수익모델이_아니다() -> None:
    covered = _covered("당기 매출액은 471억원으로 전년 대비 1,432% 증가하였고 영업손실은 확대되었습니다.")
    assert "business_model:revenue_model" not in covered


# ── 공정가치 ≠ 고객 가치 ───────────────────────────────────


def test_공정가치만_있는_금융상품_주석은_고객가치_칸을_받지_않는다() -> None:
    text = (
        "(6) 금융상품 금융자산은 최초 인식시 공정가치로 측정하며, 공정가치의 변동을 "
        "당기손익으로 인식합니다. 현금성자산은 가치변동이 중요하지 않은 유가증권입니다."
    )
    assert "business_model:value_exchange" not in _covered(text)


def test_유형자산_자본적_지출_표준문형은_고객가치가_아니다() -> None:
    text = "유형자산의 내용연수를 연장시키거나 가치를 실질적으로 증가시키는 지출은 자본적 지출로 처리합니다."
    assert "business_model:value_exchange" not in _covered(text)


def test_복합어_밖의_가치는_계속_고객가치_신호다() -> None:
    text = "회사는 공정가치 평가와 별개로 고객에게 새로운 가치를 전달하는 구독 서비스를 운영합니다."
    assert "business_model:value_exchange" in _covered(text)


def test_가치사슬_문단은_2장_고객가치로_납치되지_않는다() -> None:
    scores = relevance.score_fragment_slots(
        "회사는 원재료 조달부터 판매까지 가치사슬 전반을 관리합니다.",
        allowed_slot_ids=_AUDIT_CANDIDATE_SLOTS,
    )
    assert scores[0].slot_id == "operations_partners:value_chain"
    assert all(score.slot_id != "business_model:value_exchange" for score in scores)


def test_제외_복합어_검사는_출현_위치별로_판정한다() -> None:
    assert relevance.keyword_has_direct_hit("가치", "공정가치와 고객 가치") is True
    assert relevance.keyword_has_direct_hit("가치", "공정가치와 현재가치") is False
    assert relevance.keyword_has_direct_hit("대가", "받을 대가") is True
    assert relevance.keyword_has_direct_hit("대가", "없음") is False


# ── 상수 계약 ──────────────────────────────────────────────


def test_선택_후보_칸은_필수_칸과_겹치지_않고_전문_공시에만_열린다() -> None:
    assert c.OPTIONAL_CANDIDATE_SLOT_IDS.isdisjoint(c.COLLECTOR_SLOT_IDS)
    assert c.OPTIONAL_CANDIDATE_SLOT_IDS <= c.ALL_SLOT_IDS
    for source_kind, coverage in c.SOURCE_KIND_SLOT_SCOPE.items():
        candidate = c.SOURCE_KIND_CANDIDATE_SLOT_SCOPE[source_kind]
        assert set(coverage) <= set(candidate)
        assert set(candidate) - set(coverage) == set(c.SOURCE_KIND_OPTIONAL_SLOT_SCOPE[source_kind])
    assert c.SOURCE_KIND_OPTIONAL_SLOT_SCOPE[c.SOURCE_KIND_SEMIANNUAL_REPORT] == ()
    assert c.SOURCE_KIND_OPTIONAL_SLOT_SCOPE[c.SOURCE_KIND_QUARTERLY_REPORT] == ()


# ── 수집 종단: 조각은 선택 칸을 싣고 조회 기록은 필수 칸만 주장한다 ──────


def test_감사보고서_수집은_선택_칸을_조각에만_싣고_조회기록에는_넣지_않는다() -> None:
    text = f"I. 회사의 개요\n{_OVERVIEW}\n\nII. 주석\n{_REVENUE_COMPOSITION}\n"
    row = RawFilingRow("20260414000001", "감사보고서 (2025.12)", "20260414")
    fetcher = FakeFetcher(
        list_responses_by_pblntf_ty={"F": FilingListResult(state="OK", rows=(row,))},
        document_responses_by_rcept_no={
            row.rcept_no: DocumentFetchResult(state="OK", text=text),
        },
    )

    harvest = collect_dart_evidence(fetcher, "00000001", now=_NOW)

    overview = next(fragment for fragment in harvest.fragments if "본점" in fragment.text)
    assert "identity:official_location" in overview.covered_slot_ids
    assert "identity:business_definition" in overview.covered_slot_ids
    revenue = next(fragment for fragment in harvest.fragments if "수익의 형태" in fragment.text)
    assert revenue.slot_id == "business_model:revenue_model"
    assert all(
        slot_id not in c.OPTIONAL_CANDIDATE_SLOT_IDS
        for attempt in harvest.attempts
        for slot_id in attempt.slot_ids
    )
