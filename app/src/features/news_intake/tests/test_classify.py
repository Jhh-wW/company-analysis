from __future__ import annotations

import json

from src.features.news_intake import constants as c
from src.features.news_intake.classify import (
    build_classification_prompt,
    classify_and_read,
    classify_candidates,
)
from src.features.news_intake.models import NewsCandidate
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS


_ALL_SECTIONS = frozenset(REQUIRED_EVIDENCE_SECTION_IDS)


def _eligible(*, ready: tuple[str, ...] = ()) -> frozenset[str]:
    """READY 장을 뺀 뉴스 대상 장. 정본은 ``select.news_eligible_sections``다."""

    return _ALL_SECTIONS - frozenset(ready)


def _candidate(number: int) -> NewsCandidate:
    return NewsCandidate(
        id=f"news-{number:03d}",
        title=f"인이지 기사 {number}",
        description=f"인이지 요약 {number}",
        originallink=f"https://media.example/{number}",
        link="",
        published_on="2026-08-01",
        publisher="media.example",
        priority=4,
        source_url=f"https://media.example/{number}",
    )


def test_프롬프트는_제목과_요약만_담고_strict_JSON을_요구한다() -> None:
    prompt = build_classification_prompt((_candidate(1),))

    assert "인이지 기사 1" in prompt
    assert "인이지 요약 1" in prompt
    assert "JSON 객체 하나만" in prompt
    assert "요약하거나 새로 쓰지 마세요" in prompt


def test_닫힌목록_밖_장은_버리고_유효한_장만_남긴다() -> None:
    response = json.dumps(
        {
            "items": [
                {
                    "id": "news-001",
                    "sections": ["portfolio", "not_a_section"],
                    "kind": "press_release",
                }
            ]
        }
    )

    result = classify_candidates(
        (_candidate(1),),
        classify=lambda _prompt: response,
        eligible_sections=_eligible(),
    )

    assert result.classified[0].section_ids == ("portfolio",)
    assert result.exclusion_counts[c.EXCLUDED_UNKNOWN_SECTION] == 1


def test_후보당_세장초과와_없는_ID와_모르는_종류를_폐기한다() -> None:
    response = json.dumps(
        {
            "items": [
                {
                    "id": "news-001",
                    "sections": ["identity", "portfolio", "culture", "past_changes"],
                    "kind": "other",
                },
                {"id": "news-999", "sections": ["identity"], "kind": "other"},
                {"id": "news-002", "sections": ["identity"], "kind": "guess"},
            ]
        }
    )

    result = classify_candidates(
        (_candidate(1), _candidate(2)),
        classify=lambda _prompt: response,
        eligible_sections=_eligible(),
    )

    assert result.classified == ()
    assert result.exclusion_counts[c.EXCLUDED_TOO_MANY_SECTIONS] == 1
    assert result.exclusion_counts[c.EXCLUDED_UNKNOWN_ID] == 1
    assert result.exclusion_counts[c.EXCLUDED_UNKNOWN_KIND] == 1


def test_대상이_아닌_장만_고른_후보는_본문을_호출하지_않는다() -> None:
    calls: list[str] = []
    response = json.dumps(
        {
            "items": [
                {"id": "news-001", "sections": ["portfolio"], "kind": "event"},
                {
                    "id": "news-002",
                    "sections": ["portfolio", "culture"],
                    "kind": "interview",
                },
            ]
        }
    )

    result = classify_and_read(
        (_candidate(1), _candidate(2)),
        classify=lambda _prompt: response,
        fetch_text=lambda url: calls.append(url) or "대표는 「현장을 본다」고 말했다.",
        eligible_sections=_eligible(ready=("portfolio",)),
    )

    assert [item.candidate.id for item in result.classified] == ["news-002"]
    assert result.classified[0].section_ids == ("culture",)
    assert calls == ["https://media.example/2"]
    assert result.fetched_count <= result.classified_count


def test_깨진_JSON은_본문호출_없이_진단으로_남긴다() -> None:
    calls: list[str] = []
    result = classify_and_read(
        (_candidate(1),),
        classify=lambda _prompt: "```json\n{}\n```",
        fetch_text=lambda url: calls.append(url) or "본문",
        eligible_sections=_eligible(),
    )

    assert result.classified == ()
    assert result.articles == ()
    assert calls == []
    assert result.exclusion_counts[c.EXCLUDED_INVALID_JSON] == 1


def test_대상_장이_하나도_없으면_분류기도_본문함수도_부르지_않는다() -> None:
    calls: list[str] = []

    result = classify_and_read(
        (_candidate(1),),
        classify=lambda _prompt: calls.append("classify") or '{"items":[]}',
        fetch_text=lambda _url: calls.append("fetch") or "본문",
        eligible_sections=frozenset(),
    )

    assert result.classified == ()
    assert calls == []
