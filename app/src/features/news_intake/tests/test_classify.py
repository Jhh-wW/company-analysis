from __future__ import annotations

import json

from src.features.news_intake import constants as c
from src.features.news_intake.classify import (
    build_classification_prompt,
    classify_and_read,
    classify_candidates,
)
from src.features.news_intake.models import NewsBodyFetchResult, NewsCandidate
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


# --------------------------------------- 주소 폴백과 실패 사유 코드 (n12)


_ONE_PORTFOLIO = (
    '{"items":[{"id":"news-001","sections":["portfolio"],"kind":"press_release"}]}'
)


def _two_url_candidate() -> NewsCandidate:
    """언론사 원문과 검색 서비스 주소가 «서로 다른» 기사 한 건."""

    return NewsCandidate(
        id="news-001",
        title="인이지 기사 1",
        description="인이지 요약 1",
        originallink="https://media.example/1",
        link="https://search.example/1",
        published_on="2026-08-01",
        publisher="media.example",
        priority=4,
        source_url="https://media.example/1",
    )


def test_언론사_원문이_robots에_막히면_검색_서비스_주소로_넘어간다() -> None:
    calls: list[str] = []

    def fetch_text(url: str) -> NewsBodyFetchResult:
        calls.append(url)
        if url == "https://media.example/1":
            return NewsBodyFetchResult(reason_code=c.EXCLUDED_FETCH_ROBOTS_BLOCKED)
        return NewsBodyFetchResult(
            text="대표는 「현장을 본다」고 말했다.", stage=c.BODY_STAGE_JSON_LD
        )

    result = classify_and_read(
        (_two_url_candidate(),),
        classify=lambda _prompt: _ONE_PORTFOLIO,
        fetch_text=fetch_text,
        eligible_sections=_eligible(),
    )

    assert calls == ["https://media.example/1", "https://search.example/1"]
    assert result.fetched_count == 1
    # 넘어갔다는 사실 자체가 진단에 남아야 한다 — 성공했다고 지우지 않는다.
    assert result.exclusion_counts[c.EXCLUDED_FETCH_ROBOTS_BLOCKED] == 1
    assert result.body_stage_counts == {c.BODY_STAGE_JSON_LD: 1}


def test_첫_주소가_성공하면_두_번째_주소는_부르지_않는다() -> None:
    calls: list[str] = []

    def fetch_text(url: str) -> str:
        calls.append(url)
        return "대표는 「현장을 본다」고 말했다."

    result = classify_and_read(
        (_two_url_candidate(),),
        classify=lambda _prompt: _ONE_PORTFOLIO,
        fetch_text=fetch_text,
        eligible_sections=_eligible(),
    )

    assert calls == ["https://media.example/1"]
    assert result.exclusion_counts.get(c.EXCLUDED_FETCH_ROBOTS_BLOCKED, 0) == 0


def test_모든_주소가_실패하면_사유가_주소마다_남는다() -> None:
    사유 = {
        "https://media.example/1": c.EXCLUDED_FETCH_ROBOTS_BLOCKED,
        "https://search.example/1": c.EXCLUDED_FETCH_EMPTY_BODY,
    }

    result = classify_and_read(
        (_two_url_candidate(),),
        classify=lambda _prompt: _ONE_PORTFOLIO,
        fetch_text=lambda url: NewsBodyFetchResult(reason_code=사유[url]),
        eligible_sections=_eligible(),
    )

    assert result.fetched_count == 0
    assert result.exclusion_counts[c.EXCLUDED_FETCH_ROBOTS_BLOCKED] == 1
    assert result.exclusion_counts[c.EXCLUDED_FETCH_EMPTY_BODY] == 1
    # 옛 뭉뚱그린 코드로 되돌아가지 않는다.
    assert c.EXCLUDED_FETCH_FAILED not in result.exclusion_counts


def test_본문함수가_터져도_다음_주소를_시도하고_사유를_남긴다() -> None:
    calls: list[str] = []

    def fetch_text(url: str) -> str:
        calls.append(url)
        raise RuntimeError("네트워크가 끊겼습니다")

    result = classify_and_read(
        (_two_url_candidate(),),
        classify=lambda _prompt: _ONE_PORTFOLIO,
        fetch_text=fetch_text,
        eligible_sections=_eligible(),
    )

    assert calls == ["https://media.example/1", "https://search.example/1"]
    assert result.exclusion_counts[c.EXCLUDED_FETCH_FAILED] == 2


def test_음성대조_주소칸_순서에서_link를_빼면_두_번째_주소를_못_쓴다(
    monkeypatch,
) -> None:
    """주소 폴백의 정본은 ``BODY_FETCH_URL_FIELD_ORDER``다.

    ``link``를 순서에서 빼면 언론사 원문이 막혔을 때 되살릴 길이 사라진다.
    """

    calls: list[str] = []

    def fetch_text(url: str) -> NewsBodyFetchResult:
        calls.append(url)
        if url == "https://media.example/1":
            return NewsBodyFetchResult(reason_code=c.EXCLUDED_FETCH_ROBOTS_BLOCKED)
        return NewsBodyFetchResult(text="대표는 「현장을 본다」고 말했다.", stage="x")

    monkeypatch.setattr(c, "BODY_FETCH_URL_FIELD_ORDER", ("source_url", "originallink"))

    result = classify_and_read(
        (_two_url_candidate(),),
        classify=lambda _prompt: _ONE_PORTFOLIO,
        fetch_text=fetch_text,
        eligible_sections=_eligible(),
    )

    assert calls == ["https://media.example/1"]
    assert result.fetched_count == 0


def test_본문_단계별_수는_얻은_겹마다_따로_센다() -> None:
    response = json.dumps(
        {
            "items": [
                {"id": "news-001", "sections": ["portfolio"], "kind": "event"},
                {"id": "news-002", "sections": ["portfolio"], "kind": "event"},
            ]
        }
    )
    단계 = {
        "https://media.example/1": c.BODY_STAGE_USABLE_RANGES,
        "https://media.example/2": c.BODY_STAGE_META_DESCRIPTION,
    }

    result = classify_and_read(
        (_candidate(1), _candidate(2)),
        classify=lambda _prompt: response,
        fetch_text=lambda url: NewsBodyFetchResult(
            text="인이지가 새 제품을 공개했다고 밝혔다.", stage=단계[url]
        ),
        eligible_sections=_eligible(),
    )

    assert result.body_stage_counts == {
        c.BODY_STAGE_USABLE_RANGES: 1,
        c.BODY_STAGE_META_DESCRIPTION: 1,
    }


def test_다른_주소에서_읽은_기사는_그_주소를_출처로_남긴다() -> None:
    """언론사 원문이 막혀 검색 서비스 주소에서 읽었으면 출처도 그쪽이다."""

    def fetch_text(url: str) -> NewsBodyFetchResult:
        if url == "https://media.example/1":
            return NewsBodyFetchResult(reason_code=c.EXCLUDED_FETCH_ROBOTS_BLOCKED)
        return NewsBodyFetchResult(
            text="대표는 「현장을 본다」고 말했다.", stage=c.BODY_STAGE_JSON_LD
        )

    result = classify_and_read(
        (_two_url_candidate(),),
        classify=lambda _prompt: _ONE_PORTFOLIO,
        fetch_text=fetch_text,
        eligible_sections=_eligible(),
    )

    기사 = result.articles[0]
    assert 기사.candidate.source_url == "https://search.example/1"
    assert 기사.candidate.publisher == "search.example"


def test_첫_주소에서_읽었으면_출처는_그대로다() -> None:
    result = classify_and_read(
        (_two_url_candidate(),),
        classify=lambda _prompt: _ONE_PORTFOLIO,
        fetch_text=lambda _url: "대표는 「현장을 본다」고 말했다.",
        eligible_sections=_eligible(),
    )

    assert result.articles[0].candidate.source_url == "https://media.example/1"
    assert result.articles[0].candidate.publisher == "media.example"
