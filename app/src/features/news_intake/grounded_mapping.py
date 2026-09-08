"""검증한 사건의 중복·다양성을 평가한 뒤 출처에 결속된 조각을 만든다."""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from difflib import SequenceMatcher

from src.features.news_intake import constants as c
from src.features.news_intake.models import GroundedNewsExcerpt, NewsCollectionPolicy, NewsEvidenceFragment
from src.shared.report_generation.models import exact_text_sha256


def _normalized(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def same_event(left: GroundedNewsExcerpt, right: GroundedNewsExcerpt) -> bool:
    a, b = _normalized(left.text), _normalized(right.text)
    if a == b:
        return True
    # 다른 금액·횟수·제품 번호는 새 사실일 수 있어 유사도만으로 합치지 않는다.
    if re.findall(r"\d+(?:[.,]\d+)*", a) != re.findall(r"\d+(?:[.,]\d+)*", b):
        return False
    if SequenceMatcher(None, a, b, autojunk=False).ratio() >= c.CONTENT_DUPLICATE_SIMILARITY:
        return True
    dates = (dt.date.fromisoformat(left.candidate.published_on), dt.date.fromisoformat(right.candidate.published_on))
    if left.topic != right.topic or abs((dates[0] - dates[1]).days) > c.DEFAULT_WINDOW_DAYS:
        return False
    event_a, event_b = _normalized(left.event_key), _normalized(right.event_key)
    return bool(event_a and event_b and event_a == event_b and
                SequenceMatcher(None, a, b, autojunk=False).ratio() >= c.EVENT_DUPLICATE_SIMILARITY)


def select_diverse_excerpts(excerpts: list[GroundedNewsExcerpt], policy: NewsCollectionPolicy
                           ) -> tuple[tuple[GroundedNewsExcerpt, ...], dict[str, int]]:
    excluded: Counter[str] = Counter()
    unique: list[GroundedNewsExcerpt] = []
    # 같은 내용이면 확인된 공식 발표, 최신 원문 순으로 대표 기사를 택한다.
    ordered = sorted(excerpts, key=lambda item: (
        item.candidate.source_category != "official_release",
        -dt.date.fromisoformat(item.candidate.published_on).toordinal(), item.candidate.source_url,
    ))
    for excerpt in ordered:
        if any(same_event(excerpt, previous) for previous in unique):
            excluded["duplicate_event"] += 1
        else:
            unique.append(excerpt)
    chosen: list[GroundedNewsExcerpt] = []
    topics: Counter[str] = Counter()
    publishers: Counter[str] = Counter()
    articles: Counter[str] = Counter()
    chars = 0
    while unique:
        best = min(unique, key=lambda item: (
            articles[item.candidate.source_url], topics[item.topic], publishers[item.candidate.publisher],
            -dt.date.fromisoformat(item.candidate.published_on).toordinal(), item.candidate.source_url, item.span_start,
        ))
        unique.remove(best)
        url = best.candidate.source_url
        if (len(chosen) >= policy.max_fragments or chars + len(best.text) > policy.max_fragment_chars
                or (url not in articles and len(articles) >= policy.max_articles)):
            excluded["fragment_budget"] += 1
            continue
        if articles[url] >= c.GROUNDED_EXCERPTS_PER_ARTICLE:
            excluded["article_excerpt_budget"] += 1
            continue
        chosen.append(best)
        articles[url] += 1
        publishers[best.candidate.publisher] += 1
        topics[best.topic] += 1
        chars += len(best.text)
    return tuple(chosen), dict(excluded)


def evidence_is_sufficient(excerpts: list[GroundedNewsExcerpt], policy: NewsCollectionPolicy) -> bool:
    chosen, _ = select_diverse_excerpts(excerpts, policy)
    return (len(chosen) >= policy.sufficient_events
            and len({item.candidate.source_url for item in chosen}) >= policy.sufficient_events
            and len({item.topic for item in chosen}) >= policy.sufficient_topics)


def to_evidence_fragment(excerpt: GroundedNewsExcerpt) -> NewsEvidenceFragment:
    candidate = excerpt.candidate
    text_hash = exact_text_sha256(excerpt.text)
    identity = exact_text_sha256("\n".join((candidate.source_url, text_hash, excerpt.section_id, excerpt.claim_slot)))
    return NewsEvidenceFragment(
        fragment_id="news-fragment-" + identity[:20], text=excerpt.text, text_sha256=text_hash,
        section_ids=(excerpt.section_id,), supported_claim_slots=(excerpt.claim_slot,),
        document_id=candidate.source_url, published_on=candidate.published_on,
        source_kind=c.SOURCE_KIND_NEWS, origin=c.ORIGIN_NEWS_INTAKE,
        publisher=candidate.publisher, title=candidate.title, url=candidate.source_url,
        # 검색 발행일을 발언일로 바꾸지 않는다. 명시 날짜를 검증한 발언만 보존한다.
        statement_on=excerpt.event_on if excerpt.claim_kind == "company_statement" else "",
        claim_kind=excerpt.claim_kind, temporal_status=excerpt.temporal_status, event_on=excerpt.event_on,
        topic=excerpt.topic, event_key=excerpt.event_key, source_category=candidate.source_category,
        span_start=excerpt.span_start, span_end=excerpt.span_end,
    )
