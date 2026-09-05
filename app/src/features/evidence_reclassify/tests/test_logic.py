from __future__ import annotations

import hashlib

from src.features.chapter_evidence.select import select_section_fragments
from src.features.evidence_reclassify.constants import (
    MAX_PROMPT_CHARS,
    REJECT_INVALID_SECTION_ID,
    REJECT_INVALID_SLOT_ID,
    REJECT_PARAGRAPH_SLOT_LIMIT,
    REJECT_PLAN_TERM_OUTSIDE_FUTURE,
    REJECT_QUOTE_NOT_FOUND,
)
from src.features.evidence_reclassify.logic import (
    apply_removals,
    build_reclassify_request,
    parse_and_verify,
    to_typed_fragments,
)
from src.shared.report_evidence.constants import SourceRequirement, SourceTier
from src.shared.report_evidence.models import (
    CollectedEvidenceDocument,
    DocumentTextRange,
    EvidenceFragment,
)


_TEXT = "회사는 산업용 센서를 생산해 고객사에 판매합니다."


def _candidate(
    paragraph_id: str = "p-1",
    *,
    text: str = _TEXT,
    section_id: str = "",
    score_millis: int = 0,
    heading: str = "II. 사업의 내용",
) -> dict[str, object]:
    return {
        "company_id": "corp-1",
        "fragment_id": paragraph_id,
        "document_id": "doc-1",
        "location": "100-" + str(100 + len(text)),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text": text,
        "section_id": section_id,
        "slot_id": "" if not section_id else f"{section_id}:business_definition",
        "score_millis": score_millis,
        "reason_codes": ("no_signal",),
        "covered_slot_ids": (),
        "section_heading": heading,
    }


def _response(*assignments: object, removals: list[object] | None = None) -> dict[str, object]:
    return {
        "assignments": list(assignments),
        "removals": removals or [],
    }


def _assignment(
    *,
    paragraph_id: str = "p-1",
    section_id: str = "portfolio",
    slot_id: str = "portfolio:product_role",
    quote: str = _TEXT,
) -> dict[str, str]:
    return {
        "paragraph_id": paragraph_id,
        "section_id": section_id,
        "slot_id": slot_id,
        "quote": quote,
    }


def test_정확한_연속_인용은_통과한다() -> None:
    result = parse_and_verify(_response(_assignment()), [_candidate()])

    assert len(result.assignments) == 1
    assert result.rejected == ()
    assert result.diagnostics.total_items == 1
    assert result.diagnostics.accepted_items == 1


def test_원문에_없는_인용은_사유코드와_함께_폐기한다() -> None:
    result = parse_and_verify(
        _response(_assignment(quote="원문에 없는 문장입니다.")),
        [_candidate()],
    )

    assert result.assignments == ()
    assert result.rejected[0].reason_code == REJECT_QUOTE_NOT_FOUND
    assert result.diagnostics.rejected_by_reason == {REJECT_QUOTE_NOT_FOUND: 1}


def test_띄어쓰기만_다른_연속_인용은_원문의_정확한_span으로_통과한다() -> None:
    text = "회사는  산업용 센서를\n고객사에 판매합니다."
    result = parse_and_verify(
        _response(_assignment(quote="회사는 산업용 센서를 고객사에 판매합니다.")),
        [_candidate(text=text)],
    )

    assert result.assignments[0].exact_quote == text
    assert result.assignments[0].quote_start == 0
    assert result.assignments[0].quote_end == len(text)


def test_닫힌_장과_슬롯_목록_밖의_항목은_각각_폐기한다() -> None:
    result = parse_and_verify(
        _response(
            _assignment(section_id="unknown"),
            _assignment(slot_id="portfolio:unknown"),
        ),
        [_candidate()],
    )

    assert [item.reason_code for item in result.rejected] == [
        REJECT_INVALID_SECTION_ID,
        REJECT_INVALID_SLOT_ID,
    ]


def test_같은_문단의_네번째_슬롯은_폐기한다() -> None:
    result = parse_and_verify(
        _response(
            _assignment(section_id="identity", slot_id="identity:corporate_identity"),
            _assignment(section_id="identity", slot_id="identity:business_definition"),
            _assignment(section_id="portfolio", slot_id="portfolio:product_role"),
            _assignment(section_id="portfolio", slot_id="portfolio:revenue_link"),
        ),
        [_candidate()],
    )

    assert len(result.assignments) == 3
    assert result.rejected[0].reason_code == REJECT_PARAGRAPH_SLOT_LIMIT


def test_계획_어휘_인용은_미래전략에만_배정한다() -> None:
    text = "회사는 향후 신규 서비스를 출시할 계획입니다."
    result = parse_and_verify(
        _response(
            _assignment(
                section_id="portfolio",
                slot_id="portfolio:product_role",
                quote=text,
            ),
            _assignment(
                section_id="future_strategy",
                slot_id="future_strategy:stated_plan",
                quote=text,
            ),
        ),
        [_candidate(text=text)],
    )

    assert result.assignments[0].section_id == "future_strategy"
    assert result.rejected[0].reason_code == REJECT_PLAN_TERM_OUTSIDE_FUTURE


def test_typed_조각은_실제_장선별의_회사_문서_hash_결속을_통과한다() -> None:
    candidate = _candidate()
    result = parse_and_verify(_response(_assignment()), [candidate])
    raw = to_typed_fragments(result, {})[0]
    fragment = EvidenceFragment(
        company_id=raw["company_id"],
        fragment_id=raw["fragment_id"],
        document_id=raw["document_id"],
        location=raw["location"],
        text_sha256=raw["text_sha256"],
        text=raw["text"],
        section_id=raw["section_id"],
        slot_id=raw["slot_id"],
        score_millis=raw["score_millis"],
        reason_codes=raw["reason_codes"],
        covered_slot_ids=raw["covered_slot_ids"],
    )
    document = CollectedEvidenceDocument(
        company_id="corp-1",
        document_id="doc-1",
        canonical_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260905000001",
        source_tier=SourceTier.TIER_1_OFFICIAL,
        source_kind="dart_business_report",
        publisher="예시회사",
        title="사업보고서",
        published_on="2026-03-01",
        collected_at="2026-09-05T00:00:00+00:00",
        content_sha256="a" * 64,
        exact_evidence_hashes=(raw["text_sha256"],),
        identity_binding="binding",
        usable_ranges=(DocumentTextRange(100, 100 + len(_TEXT)),),
        collector_version="collector-v1",
        parser_version="parser-v1",
        requirement=SourceRequirement.REQUIRED,
    )

    selected = select_section_fragments(
        section_id="portfolio",
        company_id="corp-1",
        documents=(document,),
        fragments=(fragment,),
    )

    assert selected.fragments == (fragment,)
    assert raw["section_ids"] == ("portfolio",)
    assert raw["supported_claim_slots"] == ("portfolio:product_role",)
    assert raw["origin"] == "ai_reclassified"


def test_빼기는_대상_장의_지원슬롯만_제거하고_조각은_유지한다() -> None:
    candidate = _candidate()
    result = parse_and_verify(
        _response(
            removals=[
                {
                    "paragraph_id": "p-1",
                    "section_id": "portfolio",
                    "reason": "일반 회계 상투문구",
                }
            ]
        ),
        [candidate],
    )
    fragments = [
        {
            "fragment_id": "p-1",
            "supported_claim_slots": (
                "portfolio:product_role",
                "identity:business_definition",
            ),
            "text": _TEXT,
        }
    ]

    updated = apply_removals(fragments, result)

    assert len(updated) == 1
    assert updated[0]["supported_claim_slots"] == (
        "identity:business_definition",
    )
    assert fragments[0]["supported_claim_slots"] == (
        "portfolio:product_role",
        "identity:business_definition",
    )


def test_프롬프트_상한을_넘는_뒤_문단은_자르고_진단에_기록한다() -> None:
    candidates = [
        _candidate(f"p-{index}", text=(str(index) * 24_000), heading="기타")
        for index in range(1, 4)
    ]

    request = build_reclassify_request(["portfolio"], candidates)

    assert len(request.prompt) <= MAX_PROMPT_CHARS
    assert request.diagnostics.prompt_chars == len(request.prompt)
    assert request.diagnostics.candidate_paragraphs_total == 3
    assert request.diagnostics.candidate_paragraphs_included == 2
    assert request.diagnostics.candidate_paragraphs_truncated == 1
    assert "[후보 문단 p-3]" not in request.prompt


def test_프롬프트는_무분류와_선호구간과_낮은점수_순서를_고정한다() -> None:
    candidates = [
        _candidate("assigned-low", section_id="identity", score_millis=100, heading="기타"),
        _candidate("unclassified-other", heading="기타"),
        _candidate("unclassified-preferred", heading="회사의 개요"),
    ]

    request = build_reclassify_request(["portfolio"], candidates)

    assert request.candidate_paragraph_ids == (
        "unclassified-preferred",
        "unclassified-other",
        "assigned-low",
    )
    assert request.schema["additionalProperties"] is False
