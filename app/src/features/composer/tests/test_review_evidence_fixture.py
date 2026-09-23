"""JYP 검수 fixture의 proof가 실제 인용 조각을 벗어나지 않는지 검증한다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.features.composer.tests import review_evidence_fixture as fixture
from src.features.composer.grounding import grounding_problem
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.verify import (
    _GroupedReviewItem,
    _build_grouped_review_prompt,
    verify_report,
)
from src.shared.report_quality.assessment import has_public_numeric_token


_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
_FRAGMENTS: dict[str, dict[str, str]] = json.loads(
    (_FIXTURE_DIR / "jyp_fragments.json").read_text(encoding="utf-8")
)
_RESPONSES: dict[str, Any] = json.loads(
    (_FIXTURE_DIR / "jyp_ask_responses.json").read_text(encoding="utf-8")
)


def test_실제_묶음검수_프롬프트의_등급줄_뒤_본문도_빠짐없이_읽는다() -> None:
    sentence = ComposedSentence(
        text="회사는 고객에게 분석 서비스를 제공한다.",
        citations=("1",),
        grade="확인",
        planned_claim_slot="business_model:value_exchange",
    )
    prompt = _build_grouped_review_prompt(
        [_GroupedReviewItem(number=1, section_id="business_model", kind="문장",
                            citations=sentence.citations, sentence=sentence)],
        {"1": CollectedFragment(fragment_id="1", kind="사업내용", text=sentence.text)},
        None,
    )
    items = fixture.review_items(prompt)
    assert len(items) == 1
    assert items[0].text == sentence.text
    assert items[0].section == "business_model"
    assert items[0].citations == ("1",)
    assert json.loads(fixture.grounded_review_response(prompt))["판정"] == [
        {"번호": 1, "장": "business_model", "근거": ["1"], "결과": "참"}
    ]


_MULTI_CITED_TEXT = "회사는 고객에게 분석 서비스를 제공하고 구독료를 받는다."
_MULTI_CITED_FRAGMENTS = (
    CollectedFragment(fragment_id="1", kind="사업내용", text="회사는 고객에게 분석 서비스를 제공한다."),
    CollectedFragment(
        fragment_id="2", kind="사업내용", text="회사는 분석 서비스 이용 고객에게 구독료를 받는다."
    ),
)


def _multi_cited_sentence() -> ComposedSentence:
    return ComposedSentence(
        text=_MULTI_CITED_TEXT,
        citations=("1", "2"),
        grade="확인",
        planned_claim_slot="business_model:value_exchange",
    )


def test_실제_묶음검수_프롬프트의_두번째_인용도_조각_접두어_없이_읽는다() -> None:
    """인용 칸 「조각 1, 조각 2」의 두 번째 id에 「조각 」이 남으면 안 된다."""

    sentence = _multi_cited_sentence()
    prompt = _build_grouped_review_prompt(
        [_GroupedReviewItem(number=1, section_id="business_model", kind="문장",
                            citations=sentence.citations, sentence=sentence)],
        {fragment.fragment_id: fragment for fragment in _MULTI_CITED_FRAGMENTS},
        None,
    )
    assert "인용: 조각 1, 조각 2)" in prompt

    (item,) = fixture.review_items(prompt)
    assert item.citations == ("1", "2")
    (entry,) = json.loads(fixture.grounded_review_response(prompt))["판정"]
    assert entry["근거"] == ["1", "2"]


def test_옛_단일형식도_같은_규칙으로_다중_인용을_읽는다() -> None:
    prompt = (
        "\n[1] (등급: 확인, 인용: 조각 3, 조각 10)\n"
        f"  문장(JSON 문자열): {json.dumps('첫 문장이다.', ensure_ascii=False)}"
    )

    (item,) = fixture.review_items(prompt)
    assert item.citations == ("3", "10")


def test_다중_인용_참_응답은_장별_허용근거_검수에서_정상_후보를_지우지_않는다() -> None:
    """장별 허용 근거를 넘기는 검수는 응답 «근거»가 인용과 다르면 후보를 뺀다.

    도우미가 두 번째 id를 「조각 2」로 되돌리면 정상 다중 인용 후보가 진단 없이
    사라진다 — 가짜 검수가 생산 경계의 실패를 만들어 내는 반례다.
    """

    report = ComposedReport((ComposedSection("business_model", (_multi_cited_sentence(),)),))
    diagnostics: list[dict] = []

    checked = verify_report(
        report,
        _MULTI_CITED_FRAGMENTS,
        None,
        fixture.grounded_review_response,
        allowed_fragment_ids_by_section={"business_model": frozenset({"1", "2"})},
        diagnostics=diagnostics,
    )

    assert [sentence.text for sentence in checked.sections[0].sentences] == [
        _MULTI_CITED_TEXT
    ]
    assert diagnostics == []


def _proof_entries(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        found = [value] if "원문" in value else []
        return found + [entry for child in value.values() for entry in _proof_entries(child)]
    if isinstance(value, list):
        return [entry for child in value for entry in _proof_entries(child)]
    return []


def test_모든_proof_원문은_인용한_실제_조각의_정확한_substring이다() -> None:
    sentences = [
        sentence
        for section in _RESPONSES["장별_응답"].values()
        for sentence in section["문장들"]
    ] + _RESPONSES["핵심요약_응답"]["문장들"]

    for sentence in sentences:
        item = fixture.ReviewItem(
            number=1,
            section="",
            kind="문장",
            citations=tuple(str(citation) for citation in sentence["인용"]),
            text=str(sentence["글"]),
        )
        proof = fixture._proof_for_jyp_sentence(item)
        for entry in _proof_entries(proof):
            source_id = entry.get("근거")
            quote = entry.get("원문")
            assert isinstance(source_id, str) and source_id in _FRAGMENTS
            assert isinstance(quote, str) and quote in _FRAGMENTS[source_id]["원문"]


def test_정상_JYP_수치_문장은_실제_인용과_proof로_grounding을_통과한다() -> None:
    sentences = [
        sentence
        for section in _RESPONSES["장별_응답"].values()
        for sentence in section["문장들"]
    ] + _RESPONSES["핵심요약_응답"]["문장들"]

    for sentence in sentences:
        text = str(sentence["글"])
        if not has_public_numeric_token(text):
            continue
        citations = tuple(str(citation) for citation in sentence["인용"])
        item = fixture.ReviewItem(
            number=1,
            section="",
            kind="문장",
            citations=citations,
            text=text,
        )
        proof = fixture._proof_for_jyp_sentence(item) or {}
        sources = {
            citation: _FRAGMENTS[citation]["원문"]
            for citation in citations
        }
        assert grounding_problem(text, sources, proof) == "", text


def test_근거가_불필요한_정상_후보는_참을_유지하고_proof를_꾸미지_않는다() -> None:
    text = "2026년 2분기에는 5개 신보와 Stray Kids 구보 출하량 약 42만 장이 확인됐다."
    prompt = (
        "\n[1] (등급: 확인, 인용: 조각 10)\n"
        f"  문장(JSON 문자열): {json.dumps(text, ensure_ascii=False)}"
    )

    response = json.loads(fixture.grounded_review_response(prompt))
    entry = response["판정"][0]
    assert entry["결과"] == "참"
    assert "검증근거" not in entry
