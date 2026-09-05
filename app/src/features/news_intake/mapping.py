"""기사 본문 문장을 장별 뉴스 근거 조각으로 결정론 변환한다."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable

from src.features.news_intake import constants as c
from src.features.news_intake.models import (
    FetchedNewsArticle,
    NewsEvidenceFragment,
    NewsMappingResult,
)
from src.features.news_intake.select import normalize_company_name
from src.shared.report_evidence.policy import collector_slots_for
from src.shared.report_generation.models import exact_text_sha256
from src.shared.report_quality.source_identity import canonical_url


_NUMBER_RE = re.compile(c.NUMERIC_VALUE_PATTERN)
_SENTENCE_ENDINGS = frozenset(".!?。！？")
_CLOSING_AFTER_SENTENCE = frozenset("”」’'\"）)]}")


def split_sentences(text: str) -> tuple[str, ...]:
    """줄바꿈과 문장부호에서만 잘라 원문 글자를 보존한다."""

    raw = str(text or "")
    sentences: list[str] = []
    start = 0
    index = 0
    active_quote = ""
    quote_closers = {opening: closing for opening, closing in c.QUOTE_PAIRS}
    while index < len(raw):
        char = raw[index]
        if active_quote:
            if char == active_quote:
                active_quote = ""
        elif char in quote_closers:
            active_quote = quote_closers[char]
        if char in "\r\n" and not active_quote:
            candidate = raw[start:index].strip()
            if candidate:
                sentences.append(candidate)
            while index + 1 < len(raw) and raw[index + 1] in "\r\n":
                index += 1
            start = index + 1
        elif char in _SENTENCE_ENDINGS and not active_quote:
            # 소수점은 어차피 숫자 규칙에서 버리지만 문장 경계까지 훼손하지 않는다.
            if (
                char == "."
                and index > 0
                and index + 1 < len(raw)
                and raw[index - 1].isdigit()
                and raw[index + 1].isdigit()
            ):
                index += 1
                continue
            end = index + 1
            while end < len(raw) and raw[end] in _CLOSING_AFTER_SENTENCE:
                end += 1
            if end == len(raw) or raw[end].isspace():
                candidate = raw[start:end].strip()
                if candidate:
                    sentences.append(candidate)
                while end < len(raw) and raw[end].isspace() and raw[end] not in "\r\n":
                    end += 1
                start = end
                index = end - 1
        index += 1
    tail = raw[start:].strip()
    if tail:
        sentences.append(tail)
    return tuple(sentences)


def _quote_spans(text: str) -> tuple[tuple[int, int], ...]:
    spans: list[tuple[int, int]] = []
    for opening, closing in c.QUOTE_PAIRS:
        start = 0
        while True:
            left = text.find(opening, start)
            if left < 0:
                break
            right = text.find(closing, left + len(opening))
            if right < 0:
                break
            spans.append((left, right + len(closing)))
            start = right + len(closing)
    return tuple(sorted(set(spans)))


def _has_attributed_quote(sentence: str, company_names: tuple[str, ...]) -> bool:
    for start, end in _quote_spans(sentence):
        context = (
            sentence[max(0, start - c.ATTRIBUTION_CONTEXT_CHARS) : start]
            + sentence[end : min(len(sentence), end + c.ATTRIBUTION_CONTEXT_CHARS)]
        )
        normalized_context = normalize_company_name(context)
        if any(name in normalized_context for name in company_names) or any(
            marker in context.casefold() for marker in c.ATTRIBUTION_ROLE_MARKERS
        ):
            return True
    return False


def _supported_slots(section_ids: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            slot_id
            for section_id in section_ids
            for slot_id in collector_slots_for(section_id)
        )
    )


def _allowed_sections(
    *,
    sentence: str,
    requested: tuple[str, ...],
    company_names: tuple[str, ...],
    published_on: str,
    excluded: Counter[str],
) -> tuple[str, ...]:
    has_quote = bool(_quote_spans(sentence))
    attributed_quote = has_quote and _has_attributed_quote(sentence, company_names)
    allowed: list[str] = []
    for section_id in requested:
        if section_id in c.JOURNALIST_NARRATION_SECTIONS:
            allowed.append(section_id)
        elif section_id in c.ATTRIBUTED_QUOTE_ONLY_SECTIONS:
            if not has_quote:
                excluded[c.EXCLUDED_QUOTE_REQUIRED] += 1
            elif not attributed_quote:
                excluded[c.EXCLUDED_ATTRIBUTION_REQUIRED] += 1
            else:
                allowed.append(section_id)
        elif section_id in c.DATED_QUOTE_ONLY_SECTIONS:
            if not has_quote:
                excluded[c.EXCLUDED_QUOTE_REQUIRED] += 1
            elif not published_on:
                excluded[c.EXCLUDED_PUBLISHED_ON_REQUIRED] += 1
            else:
                allowed.append(section_id)
    return tuple(allowed)


def map_news_fragments(
    article: FetchedNewsArticle,
    *,
    company_name: str,
    aliases: Iterable[str] = (),
) -> NewsMappingResult:
    """기사 한 건의 문장을 분류된 장 중 허용된 장에만 배정한다."""

    names = tuple(
        dict.fromkeys(
            name
            for name in (
                normalize_company_name(company_name),
                *(normalize_company_name(alias) for alias in aliases),
            )
            if name
        )
    )
    if not names:
        raise ValueError("회사 정식 명칭 또는 별칭이 하나 이상 필요합니다")
    candidate = article.candidate
    document_id = canonical_url(candidate.source_url)
    if not document_id:
        raise ValueError("기사 URL을 문서 식별자로 정규화할 수 없습니다")

    excluded: Counter[str] = Counter()
    fragments: list[NewsEvidenceFragment] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for sentence in split_sentences(article.text):
        if any(marker in sentence for marker in c.UNVERIFIED_MARKERS):
            excluded[c.EXCLUDED_UNVERIFIED_SENTENCE] += 1
            continue
        if _NUMBER_RE.search(sentence):
            excluded[c.EXCLUDED_NUMERIC_SENTENCE] += 1
            continue
        section_ids = _allowed_sections(
            sentence=sentence,
            requested=article.section_ids,
            company_names=names,
            published_on=candidate.published_on,
            excluded=excluded,
        )
        if not section_ids:
            continue
        key = (sentence, section_ids)
        if key in seen:
            excluded[c.EXCLUDED_DUPLICATE_SENTENCE] += 1
            continue
        seen.add(key)
        text_sha256 = exact_text_sha256(sentence)
        fragment_identity = exact_text_sha256(
            "\n".join((document_id, text_sha256, *section_ids))
        )
        fragments.append(
            NewsEvidenceFragment(
                fragment_id=f"news-fragment-{fragment_identity[:20]}",
                text=sentence,
                text_sha256=text_sha256,
                section_ids=section_ids,
                supported_claim_slots=_supported_slots(section_ids),
                document_id=document_id,
                published_on=candidate.published_on,
                source_kind=c.SOURCE_KIND_NEWS,
                origin=c.ORIGIN_NEWS_INTAKE,
                publisher=candidate.publisher,
                title=candidate.title,
                url=document_id,
                statement_on=(
                    candidate.published_on if "culture" in section_ids else ""
                ),
            )
        )
    return NewsMappingResult(fragments=tuple(fragments), exclusion_counts=dict(excluded))


def map_article_to_fragments(
    article: FetchedNewsArticle,
    *,
    company_name: str,
    aliases: Iterable[str] = (),
) -> NewsMappingResult:
    """호출부에서 읽기 쉬운 ``map_news_fragments`` 별칭."""

    return map_news_fragments(article, company_name=company_name, aliases=aliases)


def map_articles_to_fragments(
    articles: Iterable[FetchedNewsArticle],
    *,
    company_name: str,
    aliases: Iterable[str] = (),
) -> NewsMappingResult:
    """여러 기사 결과를 입력 순서대로 합치고 제외 사유를 더한다."""

    fragments: list[NewsEvidenceFragment] = []
    excluded: Counter[str] = Counter()
    for article in articles:
        result = map_news_fragments(
            article,
            company_name=company_name,
            aliases=aliases,
        )
        fragments.extend(result.fragments)
        excluded.update(result.exclusion_counts)
    return NewsMappingResult(fragments=tuple(fragments), exclusion_counts=dict(excluded))
