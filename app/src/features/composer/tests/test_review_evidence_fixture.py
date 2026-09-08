"""JYP 검수 fixture의 proof가 실제 인용 조각을 벗어나지 않는지 검증한다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.features.composer.tests import review_evidence_fixture as fixture
from src.features.composer.grounding import grounding_problem
from src.shared.report_quality.assessment import has_public_numeric_token


_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
_FRAGMENTS: dict[str, dict[str, str]] = json.loads(
    (_FIXTURE_DIR / "jyp_fragments.json").read_text(encoding="utf-8")
)
_RESPONSES: dict[str, Any] = json.loads(
    (_FIXTURE_DIR / "jyp_ask_responses.json").read_text(encoding="utf-8")
)


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
