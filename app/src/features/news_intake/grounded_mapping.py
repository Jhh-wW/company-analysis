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


def _numbers(normalized_text: str) -> list[str]:
    # 순서를 보존한다 — 배열이 다르면(순서가 바뀌어도) 새 사실일 수 있다.
    return c.NEWS_EVENT_NUMBER_TOKEN_RE.findall(normalized_text)


def _sentences(text: str) -> tuple[str, ...]:
    """«순수 부분 인용» 판정에서만 쓰는 문장 분리 — 다른 경로엔 안 쓴다."""
    return tuple(part for part in c.NEWS_SENTENCE_SPLIT_RE.split(text.strip()) if part.strip())


def _sentence_key(sentence: str) -> str:
    """문장 하나를 공백 제거·대소문자 통일 후 비교용 키로 만든다.

    유일하게 허용한 추가 정규화는 문장 «끝»의 통계 서술 종결형 하나뿐이다
    (constants의 ``NEWS_STAT_SENTENCE_ENDING_EQUIVALENTS``). 대상명·부정·
    계획/현재·숫자·단위·기간·중간 문구는 절대 지우거나 바꾸지 않는다 —
    문장 끝의 그 좁은 자리 하나만 표준형으로 맞춘다.
    """
    key = _normalized(sentence)
    for canonical, alternate in c.NEWS_STAT_SENTENCE_ENDING_EQUIVALENTS:
        canonical_key, alternate_key = _normalized(canonical), _normalized(alternate)
        if not key.endswith(alternate_key):
            continue
        head = key[: -len(alternate_key)]
        # ★ 계약은 «숫자 + 단위» 뒤 문장 끝이다. 접미사만 보면 단위가 아니라
        #   낱말의 끝글자가 걸린다 — "거래유형은 임대로 집계됐다."가
        #   "거래유형은 임대였다."로 바뀌어 다른 사건이 하나로 합쳐졌다.
        #   그래서 접미사 «바로 앞»이 실제 숫자의 끝인지 한 번 더 본다.
        #   ``isdecimal()``은 숫자 토큰 정규식의 ``\d``(Unicode Nd)와 같은 집합이다.
        if not head or not head[-1].isdecimal():
            continue
        key = head + canonical_key
        break
    return key


def _is_sentence_subsequence(shorter: tuple[str, ...], longer: tuple[str, ...]) -> bool:
    """더 짧은 쪽의 모든 문장이, 순서 그대로, 더 긴 쪽 문장들과 하나씩 정확히 대응하는지.

    대응은 문장 «전체»가 (허용된 종결형 정규화 후) 완전히 같을 때만 인정한다
    — 어간 일부나 유사도로는 대응시키지 않는다.
    """
    if not shorter:
        return False
    long_index = 0
    for short_sentence in shorter:
        short_key = _sentence_key(short_sentence)
        matched = False
        while long_index < len(longer):
            if _sentence_key(longer[long_index]) == short_key:
                long_index += 1
                matched = True
                break
            long_index += 1
        if not matched:
            return False
    return True


def _is_pure_partial_quote(left: GroundedNewsExcerpt, right: GroundedNewsExcerpt) -> bool:
    """숫자 배열이 다른 두 인용이 «순수 부분 인용» 관계인지만 본다.

    아래를 전부 만족할 때만 참이다:
      · 출판사가 같고 비어 있지 않다.
      · 발행일이 «정확히» 같다(창이 아니라 같은 날짜).
      · 주제·claim_kind·temporal_status·event_on이 모두 같다.
      · 더 짧은 인용의 모든 문장이, 순서 그대로, 더 긴 인용의 문장들과
        (허용된 통계 종결형 정규화만 적용해) 정확히 대응한다.

    대상명·부정·계획/현재·숫자·단위·기간·중간 문구는 절대 지우지 않는다 —
    허용된 정규화는 문장 끝 통계 서술 종결형 하나뿐이다.
    """
    publisher = left.candidate.publisher
    if (
        not publisher
        or publisher != right.candidate.publisher
        or left.candidate.published_on != right.candidate.published_on
        or left.topic != right.topic
        or left.claim_kind != right.claim_kind
        or left.temporal_status != right.temporal_status
        or left.event_on != right.event_on
    ):
        return False
    left_sentences, right_sentences = _sentences(left.text), _sentences(right.text)
    if not left_sentences or not right_sentences:
        return False
    shorter, longer = (
        (left_sentences, right_sentences) if len(left_sentences) <= len(right_sentences)
        else (right_sentences, left_sentences)
    )
    return _is_sentence_subsequence(shorter, longer)


def same_event(left: GroundedNewsExcerpt, right: GroundedNewsExcerpt) -> bool:
    a, b = _normalized(left.text), _normalized(right.text)
    if a == b:
        return True
    if _numbers(a) != _numbers(b):
        # 다른 금액·횟수·제품 번호는 새 사실일 수 있어 유사도만으로 합치지
        # 않는다. 다만 발행처·발행일·주제 등 신원이 전부 같고, 짧은 쪽이 긴
        # 쪽의 «순수 부분 인용»(문장 단위로 순서대로 그대로 들어 있음)일
        # 때만 예외로 같은 사건으로 본다.
        return _is_pure_partial_quote(left, right)
    if SequenceMatcher(None, a, b, autojunk=False).ratio() >= c.CONTENT_DUPLICATE_SIMILARITY:
        return True
    dates = (dt.date.fromisoformat(left.candidate.published_on), dt.date.fromisoformat(right.candidate.published_on))
    if left.topic != right.topic or abs((dates[0] - dates[1]).days) > c.DEFAULT_WINDOW_DAYS:
        return False
    event_a, event_b = _normalized(left.event_key), _normalized(right.event_key)
    return bool(event_a and event_b and event_a == event_b and
                SequenceMatcher(None, a, b, autojunk=False).ratio() >= c.EVENT_DUPLICATE_SIMILARITY)


def _prefer_original_on_pure_partial_quote(
    current: GroundedNewsExcerpt, kept: GroundedNewsExcerpt,
) -> GroundedNewsExcerpt:
    """같은 사건 대표를 고른다 — 기존 객체를 그대로 돌려주며 글자를 바꾸지 않는다.

    숫자 배열이 같은 기존 경로(문구만 다른 일반 중복)는 손대지 않는다 — 정렬
    순서상 먼저 온 대표를 그대로 유지한다(기존 동작 그대로). 숫자 배열이
    다르면 ``same_event``가 참이었던 이유는 «순수 부분 인용» 경로뿐이다 —
    그때만 문장 수가 더 많은(더 긴) 원문 객체를 대표로 남긴다.
    """
    if _numbers(_normalized(current.text)) == _numbers(_normalized(kept.text)):
        return kept
    return current if len(_sentences(current.text)) > len(_sentences(kept.text)) else kept


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
        match_index = next(
            (index for index, previous in enumerate(unique) if same_event(excerpt, previous)), None,
        )
        if match_index is None:
            unique.append(excerpt)
            continue
        excluded["duplicate_event"] += 1
        unique[match_index] = _prefer_original_on_pure_partial_quote(excerpt, unique[match_index])
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
