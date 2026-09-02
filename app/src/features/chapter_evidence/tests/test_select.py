from __future__ import annotations

import hashlib
import math

from src.features.chapter_evidence.constants import CHARS_PER_ESTIMATED_TOKEN
from src.features.chapter_evidence.select import select_section_fragments
from src.shared.report_evidence.constants import SourceRequirement, SourceTier
from src.shared.report_evidence.models import (
    CollectedEvidenceDocument,
    DocumentTextRange,
    EvidenceFragment,
)


def _document(
    *,
    company_id: str = "corp-1",
    document_id: str = "doc-1",
    exact_evidence_hashes: tuple[str, ...] | None = None,
) -> CollectedEvidenceDocument:
    """generation=7 문서를 만든다.

    ``exact_evidence_hashes``를 안 주면(이 시험에서 문서-조각 결속 자체를
    검증하지 않는 경우) 자리표시자 해시 하나로 유효성만 채운다. 결속을
    검증하는 시험은 실제로 쓸 조각들의 text_sha256을 명시적으로 넘긴다.
    """

    hashes = (
        exact_evidence_hashes
        if exact_evidence_hashes is not None
        else (hashlib.sha256(f"placeholder:{document_id}".encode()).hexdigest(),)
    )
    return CollectedEvidenceDocument(
        company_id=company_id,
        document_id=document_id,
        canonical_url="https://example.com/x",
        source_tier=SourceTier.TIER_1_OFFICIAL,
        source_kind="dart_business_report",
        publisher="예시회사",
        title="문서",
        published_on="2026-03-01",
        collected_at="2026-08-31T00:00:00+00:00",
        content_sha256="a" * 64,
        exact_evidence_hashes=hashes,
        identity_binding="binding",
        usable_ranges=(DocumentTextRange(0, 500),),
        collector_version="collector-v1",
        parser_version="parser-v1",
        requirement=SourceRequirement.REQUIRED,
    )


def _fragment(
    *,
    company_id: str = "corp-1",
    fragment_id: str,
    document_id: str = "doc-1",
    section_id: str = "business_model",
    slot_id: str = "business_model:revenue_model",
    text: str = "회사는 제품을 직접 판매해 수익을 얻습니다.",
    score_millis: int = 800,
) -> EvidenceFragment:
    return EvidenceFragment(
        company_id=company_id,
        fragment_id=fragment_id,
        document_id=document_id,
        location="본문",
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text=text,
        section_id=section_id,
        slot_id=slot_id,
        score_millis=score_millis,
        reason_codes=("official_direct_statement",),
    )


def test_다른_장의_조각은_제외한다() -> None:
    fragment = _fragment(fragment_id="f1", section_id="identity", slot_id="identity:corporate_identity")

    selection = select_section_fragments(
        section_id="business_model",
        company_id="corp-1",
        documents=(_document(),),
        fragments=(fragment,),
    )

    assert selection.fragments == ()


def test_수집슬롯이_아닌_슬롯의_조각은_제외한다() -> None:
    # competitive_position 의 비교 5칸은 Codex 주입 몫이라 수집 슬롯이 아니다.
    fragment = _fragment(
        fragment_id="f1",
        section_id="competitive_position",
        slot_id="competitive_position:comparison_target",
        text="경쟁사 비교 대상 서술",
    )

    selection = select_section_fragments(
        section_id="competitive_position",
        company_id="corp-1",
        documents=(_document(),),
        fragments=(fragment,),
    )

    assert selection.fragments == ()


def test_다른_회사_문서를_가리키는_조각은_제외한다() -> None:
    other_company_document = _document(company_id="corp-2", document_id="doc-x")
    fragment = _fragment(fragment_id="f1", document_id="doc-x")

    selection = select_section_fragments(
        section_id="business_model",
        company_id="corp-1",
        documents=(_document(), other_company_document),
        fragments=(fragment,),
    )

    assert selection.fragments == ()


def test_같은_원문_해시는_최고점만_남긴다() -> None:
    text = "같은 원문 내용입니다."
    low = _fragment(fragment_id="f-low", text=text, score_millis=300)
    high = _fragment(fragment_id="f-high", text=text, score_millis=900)
    document = _document(exact_evidence_hashes=(low.text_sha256,))

    selection = select_section_fragments(
        section_id="business_model",
        company_id="corp-1",
        documents=(document,),
        fragments=(low, high),
    )

    assert [fragment.fragment_id for fragment in selection.fragments] == ["f-high"]
    assert "duplicate_fragments_removed:1" in selection.reason_codes


def test_다른_슬롯의_같은_원문은_ID_하나로_합치고_두_슬롯을_보존한다() -> None:
    text = "구독형 SaaS를 B2B 고객에게 판매합니다."
    revenue_fragment = _fragment(
        fragment_id="f-revenue",
        slot_id="business_model:revenue_model",
        text=text,
        score_millis=800,
    )
    customer_fragment = _fragment(
        fragment_id="f-customer",
        slot_id="business_model:customer_type",
        text=text,
        score_millis=800,
    )
    document = _document(exact_evidence_hashes=(revenue_fragment.text_sha256,))

    selection = select_section_fragments(
        section_id="business_model",
        company_id="corp-1",
        documents=(document,),
        fragments=(revenue_fragment, customer_fragment),
    )

    assert len(selection.fragments) == 1
    assert set(selection.fragments[0].covered_slot_ids) == {
        "business_model:revenue_model", "business_model:customer_type"
    }
    assert selection.estimated_tokens == math.ceil(
        len(text) / CHARS_PER_ESTIMATED_TOKEN
    )
    assert "duplicate_fragments_removed:1" in selection.reason_codes


def test_문서에_결속되지_않은_조각은_사유코드와_함께_제외된다() -> None:
    # 조각의 text_sha256이 원본 문서의 exact_evidence_hashes 목록에 없으면
    # ChapterEvidenceCandidates 계약이 예외로 죽기 전에 선별 단계가 먼저
    # fail-closed로 걸러낸다.
    fragment = _fragment(fragment_id="f1", text="문서가 내보내지 않은 원문.")
    document = _document(
        exact_evidence_hashes=(hashlib.sha256(b"other-text").hexdigest(),)
    )

    selection = select_section_fragments(
        section_id="business_model",
        company_id="corp-1",
        documents=(document,),
        fragments=(fragment,),
    )

    assert selection.fragments == ()
    assert "fragment_not_bound_to_document:1" in selection.reason_codes


def test_다른_회사_조각은_해시가_결속돼도_회사_결속으로_제외된다() -> None:
    # 겹마다 따로 확인한다 — text_sha256 결속(2층)이 뚫려도(우연히 같은 원문)
    # company_id 결속(1층)이 혼자서 이 조각을 잡아야 한다. 두 층을 한꺼번에
    # 어기는 입력으로만 시험하면 어느 층이 실제로 막았는지 알 수 없다.
    fragment = _fragment(company_id="corp-2", fragment_id="f1", text="같은 원문")
    document = _document(exact_evidence_hashes=(fragment.text_sha256,))

    selection = select_section_fragments(
        section_id="business_model",
        company_id="corp-1",
        documents=(document,),
        fragments=(fragment,),
    )

    assert selection.fragments == ()
    assert "fragment_company_mismatch:1" in selection.reason_codes
    # 회사 결속에서 이미 걸렀으므로 해시 결속 사유까지 중복으로 세지 않는다.
    assert not any(
        code.startswith("fragment_not_bound_to_document:") for code in selection.reason_codes
    )


def test_슬롯_커버리지가_점수보다_우선한다() -> None:
    # revenue_model 슬롯은 저점(600)짜리 대표 하나뿐이고, customer_type 슬롯은
    # 고점(950)짜리 조각이 둘이다. 예산이 세 조각을 다 담을 만큼 넉넉하면
    # revenue_model 대표도 반드시 살아남아야 한다(슬롯 커버리지 우선).
    revenue = _fragment(
        fragment_id="f-revenue",
        slot_id="business_model:revenue_model",
        text="revenue 슬롯 대표 조각 텍스트.",
        score_millis=600,
    )
    customer_best = _fragment(
        fragment_id="f-customer-best",
        slot_id="business_model:customer_type",
        text="customer 슬롯 최고점 조각 텍스트.",
        score_millis=950,
    )
    customer_extra = _fragment(
        fragment_id="f-customer-extra",
        slot_id="business_model:customer_type",
        text="customer 슬롯 여분 조각 텍스트 조금 더 길게.",
        score_millis=940,
    )
    document = _document(
        exact_evidence_hashes=(
            revenue.text_sha256,
            customer_best.text_sha256,
            customer_extra.text_sha256,
        )
    )

    selection = select_section_fragments(
        section_id="business_model",
        company_id="corp-1",
        documents=(document,),
        fragments=(customer_best, customer_extra, revenue),
        max_chars=10_000,
        max_estimated_tokens=10_000,
    )

    included_ids = {fragment.fragment_id for fragment in selection.fragments}
    assert "f-revenue" in included_ids
    assert "f-customer-best" in included_ids


def test_예산이_빠듯하면_남는_예산은_점수_내림차순으로_버려진다() -> None:
    revenue = _fragment(
        fragment_id="f-revenue",
        slot_id="business_model:revenue_model",
        text="가" * 100,
        score_millis=700,
    )
    value_exchange = _fragment(
        fragment_id="f-value",
        slot_id="business_model:value_exchange",
        text="나" * 100,
        score_millis=700,
    )
    low_score_extra = _fragment(
        fragment_id="f-extra-low",
        slot_id="business_model:revenue_model",
        text="다" * 100,
        score_millis=100,
    )
    document = _document(
        exact_evidence_hashes=(
            revenue.text_sha256,
            value_exchange.text_sha256,
            low_score_extra.text_sha256,
        )
    )

    selection = select_section_fragments(
        section_id="business_model",
        company_id="corp-1",
        documents=(document,),
        fragments=(revenue, value_exchange, low_score_extra),
        max_chars=200,
        max_estimated_tokens=1000,
    )

    included_ids = {fragment.fragment_id for fragment in selection.fragments}
    assert included_ids == {"f-revenue", "f-value"}
    assert any(code.startswith("budget_truncated_fragments:") for code in selection.reason_codes)


def test_단독으로_예산을_넘는_조각은_슬롯미달_사유를_남기고_제외된다() -> None:
    oversized = _fragment(
        fragment_id="f-oversized",
        slot_id="business_model:revenue_model",
        text="가" * 500,
        score_millis=900,
    )
    document = _document(exact_evidence_hashes=(oversized.text_sha256,))

    selection = select_section_fragments(
        section_id="business_model",
        company_id="corp-1",
        documents=(document,),
        fragments=(oversized,),
        max_chars=100,
        max_estimated_tokens=1000,
    )

    assert selection.fragments == ()
    assert "fragment_exceeds_budget:business_model:revenue_model" in selection.reason_codes
    # 오버사이즈 대표 조각은 2단계에서 다시 세지 않는다 — 세면 사유가 중복된다.
    assert not any(
        code.startswith("budget_truncated_fragments:") for code in selection.reason_codes
    )


def test_추정토큰은_문자수_비율로_결정론적으로_계산된다() -> None:
    text = "가" * 220
    fragment = _fragment(fragment_id="f1", text=text, score_millis=900)
    document = _document(exact_evidence_hashes=(fragment.text_sha256,))

    selection = select_section_fragments(
        section_id="business_model",
        company_id="corp-1",
        documents=(document,),
        fragments=(fragment,),
        max_chars=1000,
        max_estimated_tokens=1000,
    )

    assert selection.estimated_tokens == math.ceil(len(text) / CHARS_PER_ESTIMATED_TOKEN)


def test_최종_조각_순서는_fragment_id로_결정론적이다() -> None:
    a = _fragment(fragment_id="f-b", slot_id="business_model:revenue_model", text="A")
    b = _fragment(fragment_id="f-a", slot_id="business_model:customer_type", text="B")
    document = _document(exact_evidence_hashes=(a.text_sha256, b.text_sha256))

    selection = select_section_fragments(
        section_id="business_model",
        company_id="corp-1",
        documents=(document,),
        fragments=(a, b),
    )

    assert [fragment.fragment_id for fragment in selection.fragments] == ["f-a", "f-b"]
