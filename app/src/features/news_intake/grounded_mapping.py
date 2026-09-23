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


def _same_claim_context(left: GroundedNewsExcerpt, right: GroundedNewsExcerpt) -> bool:
    """문장이 같아도 발행일·주장 종류·시제·사건일이 같아야 같은 사실이다.

    ★ 발행일이 다르면 합치지 않는다. 지금 모델은 주장 절마다 기간이 어디에 결속되는지
      증명하지 못한다 — «지난해»는 발행일에 기대고, «2010년에 설립된 … 당기 매출»처럼
      문장 안 연도가 다른 절의 기간을 뜻하지도 않는다. 연도 발견·상대 표현 목록으로는
      같은 실패가 반복된다. 다른 날짜 재보도는 지우지 않고 선택 순서(may_be_same_event)와
      기존 충분성(_legacy_same_event)으로 다룬다. 엄밀한 기간 결속이 생기면 그때 넓힌다.
    """
    return (left.candidate.published_on == right.candidate.published_on
            and left.claim_kind == right.claim_kind and left.temporal_status == right.temporal_status
            and not (left.event_on and right.event_on and left.event_on != right.event_on))


def _claim_key(text: str) -> str:
    """문장별 비교 키를 이은 값 — 허용된 통계 종결형 정규화 말고는 글자 그대로다."""
    return "".join(_sentence_key(sentence) for sentence in _sentences(text))


def same_event(left: GroundedNewsExcerpt, right: GroundedNewsExcerpt) -> bool:
    """지워도 정보가 사라지지 않는 «같은 사실»인지 — 중복 삭제는 이 판정만 쓴다.

    ① 순수 부분 인용: 발행처·발행일·주제·주장 신원이 같고 짧은 쪽 문장이 긴 쪽
       문장들과 순서대로 정확히 대응한다(대표는 긴 원문).
    ② 주장 문장 전체가 같고(허용된 통계 종결형 정규화만) 주장 문맥도 같다.

    숫자 배열·유사도·접두사·같은 event_key는 같은 사실의 증명이 아니다 — 제품·
    지역이 첫 수치 뒤에 오거나 한 글자만 달라도 유사도는 높다. 그런 쌍은 지우지
    않고 ``may_be_same_event``로 선택 순서에만 묶는다(충분성은 ``_legacy_same_event``).
    """
    if _is_pure_partial_quote(left, right):
        return True
    return _claim_key(left.text) == _claim_key(right.text) and _same_claim_context(left, right)


def _similar_report(left: GroundedNewsExcerpt, right: GroundedNewsExcerpt) -> bool:
    """기존(HEAD) 유사 보도 휴리스틱 — 삭제가 아니라 «같은 사건일 수 있음» 표시에만 쓴다."""
    a, b = _normalized(left.text), _normalized(right.text)
    if a == b:
        return True
    if _numbers(a) != _numbers(b):
        return False
    ratio = SequenceMatcher(None, a, b, autojunk=False).ratio()
    if ratio >= c.CONTENT_DUPLICATE_SIMILARITY:
        return True
    dates = (dt.date.fromisoformat(left.candidate.published_on), dt.date.fromisoformat(right.candidate.published_on))
    if left.topic != right.topic or abs((dates[0] - dates[1]).days) > c.DEFAULT_WINDOW_DAYS:
        return False
    event_a, event_b = _normalized(left.event_key), _normalized(right.event_key)
    return bool(event_a and event_b and event_a == event_b and ratio >= c.EVENT_DUPLICATE_SIMILARITY)


def may_be_same_event(left: GroundedNewsExcerpt, right: GroundedNewsExcerpt) -> bool:
    """같은 사건«일 수도» 있는지 — 선택 순서(재보도 뒤로 미루기)에만 쓴다. 삭제에는 쓰지 않는다."""
    return same_event(left, right) or _similar_report(left, right)


def _legacy_same_event(left: GroundedNewsExcerpt, right: GroundedNewsExcerpt) -> bool:
    """기존(HEAD) 동일 사건 판정 그대로 — 수집 충분성 계산 전용이다.

    판정 셋의 역할: ``same_event``=삭제, ``may_be_same_event``=선택 순서,
    이것=충분성. 삭제를 좁혀도 충분성은 이 판정·대표·예산으로 기존과 똑같이 센다.
    (관계를 넓힌 상위집합도 탐욕 묶음의 대표가 바뀌면 묶음 수가 늘 수 있어 쓰지 않는다.)
    """
    a, b = _normalized(left.text), _normalized(right.text)
    if a != b and _numbers(a) != _numbers(b):
        return _is_pure_partial_quote(left, right)
    return _similar_report(left, right)


def _prefer_original_on_pure_partial_quote(
    current: GroundedNewsExcerpt, kept: GroundedNewsExcerpt,
) -> GroundedNewsExcerpt:
    """충분성 계산용 묶음의 대표 — 기존(HEAD) 규칙 그대로라 사건 수를 기존과 같게 센다.

    숫자 배열이 같은 경로(문구만 다른 일반 중복)는 정렬 순서상 먼저 온 대표를
    유지하고, 숫자 배열이 다르면(순수 부분 인용) 문장 수가 더 많은 원문을 남긴다.
    """
    if _numbers(_normalized(current.text)) == _numbers(_normalized(kept.text)):
        return kept
    return current if len(_sentences(current.text)) > len(_sentences(kept.text)) else kept


def _prefer_richer_quote(current: GroundedNewsExcerpt, kept: GroundedNewsExcerpt) -> GroundedNewsExcerpt:
    """확실한 같은 사실의 대표 — 문장이 더 많은 원문 객체를 글자 그대로 남긴다.

    숫자 배열이 같아도 긴 쪽에만 있는 문장(수치 없는 사실)을 잃지 않는다. 문장
    수가 같으면(같은 문장) 정렬 순서상 먼저 온 대표를 유지한다.
    """
    return current if len(_sentences(current.text)) > len(_sentences(kept.text)) else kept


def _merge_duplicates(excerpts, same, prefer) -> tuple[list[GroundedNewsExcerpt], int]:
    unique: list[GroundedNewsExcerpt] = []
    merged = 0
    # 같은 내용이면 확인된 공식 발표, 최신 원문 순으로 대표 기사를 택한다.
    ordered = sorted(excerpts, key=lambda item: (
        item.candidate.source_category != "official_release",
        -dt.date.fromisoformat(item.candidate.published_on).toordinal(), item.candidate.source_url,
    ))
    for excerpt in ordered:
        match_index = next(
            (index for index, previous in enumerate(unique) if same(excerpt, previous)), None,
        )
        if match_index is None:
            unique.append(excerpt)
            continue
        merged += 1
        unique[match_index] = prefer(excerpt, unique[match_index])
    return unique, merged


def _possible_duplicate_links(unique: list[GroundedNewsExcerpt]) -> list[set[int]]:
    links: list[set[int]] = [set() for _ in unique]
    for first in range(len(unique)):
        for second in range(first + 1, len(unique)):
            if may_be_same_event(unique[first], unique[second]):
                links[first].add(second)
                links[second].add(first)
    return links


def select_diverse_excerpts(excerpts: list[GroundedNewsExcerpt], policy: NewsCollectionPolicy
                           ) -> tuple[tuple[GroundedNewsExcerpt, ...], dict[str, int]]:
    excluded: Counter[str] = Counter()
    unique, merged = _merge_duplicates(excerpts, same_event, _prefer_richer_quote)
    if merged:
        excluded["duplicate_event"] += merged
    chosen = _within_budget(unique, policy, excluded, _possible_duplicate_links(unique))
    return tuple(chosen), dict(excluded)


def _within_budget(unique: list[GroundedNewsExcerpt], policy: NewsCollectionPolicy,
                   excluded: Counter[str], echo_links: list[set[int]] | None = None,
                   ) -> list[GroundedNewsExcerpt]:
    """기사·주제·발행처가 고르게 섞이도록 예산 안에서 고른다.

    ``echo_links[i]``는 i와 같은 사건일 수 있는 조각 번호다. 이미 뽑힌 조각의
    재보도일 수 있는 조각은 지우지 않고 뒤로 미룬다 — 분명히 다른 사실이 예산을
    먼저 쓰고, 남는 예산 안에서만 보존된다.
    """
    chosen: list[GroundedNewsExcerpt] = []
    chosen_indexes: set[int] = set()
    topics: Counter[str] = Counter()
    publishers: Counter[str] = Counter()
    articles: Counter[str] = Counter()
    # 조각 예산의 단위는 «모델이 고른 한 범위»다. 주장 역할로 나눈 부분들은
    # 원래 한 범위라 한 몫으로 센다 — 나눴다는 이유로 같은 기사나 다른 기사의
    # 별도 사실이 예산에서 밀려나지 않게. 글자 예산은 실제 글자로 그대로 잰다.
    units: set[tuple[str, tuple[int, ...]]] = set()
    units_by_article: dict[str, set[tuple[int, ...]]] = {}
    chars = 0

    def rank(index: int) -> tuple[object, ...]:
        item = unique[index]
        echoes_chosen = bool(echo_links and echo_links[index] & chosen_indexes)
        return (
            echoes_chosen, articles[item.candidate.source_url], topics[item.topic],
            publishers[item.candidate.publisher], -dt.date.fromisoformat(item.candidate.published_on).toordinal(),
            item.candidate.source_url, item.span_start,
        )

    remaining = list(range(len(unique)))
    while remaining:
        best_index = min(remaining, key=rank)
        remaining.remove(best_index)
        best = unique[best_index]
        url = best.candidate.source_url
        unit = ((best.split_from,) if best.split_from is not None
                else (best.span_start, best.span_end))
        new_unit = (url, unit) not in units
        if ((new_unit and len(units) >= policy.max_fragments) or chars + len(best.text) > policy.max_fragment_chars
                or (url not in articles and len(articles) >= policy.max_articles)):
            excluded["fragment_budget"] += 1
            continue
        if new_unit and len(units_by_article.get(url, ())) >= c.GROUNDED_EXCERPTS_PER_ARTICLE:
            excluded["article_excerpt_budget"] += 1
            continue
        chosen.append(best)
        chosen_indexes.add(best_index)
        units.add((url, unit))
        units_by_article.setdefault(url, set()).add(unit)
        articles[url] += 1
        publishers[best.candidate.publisher] += 1
        topics[best.topic] += 1
        chars += len(best.text)
    return chosen


def _legacy_selection(excerpts: list[GroundedNewsExcerpt], policy: NewsCollectionPolicy
                      ) -> list[GroundedNewsExcerpt]:
    """충분성 계산 전용 선택 — 기존(HEAD) 동일 사건 판정·대표·예산 그대로다. 출력 조각에는 쓰지 않는다."""
    unique, _ = _merge_duplicates(excerpts, _legacy_same_event, _prefer_original_on_pure_partial_quote)
    return _within_budget(unique, policy, Counter())


def _meets_policy(chosen: list[GroundedNewsExcerpt], policy: NewsCollectionPolicy) -> bool:
    return (len(chosen) >= policy.sufficient_events
            and len({item.candidate.source_url for item in chosen}) >= policy.sufficient_events
            and len({item.topic for item in chosen}) >= policy.sufficient_topics)


def evidence_is_sufficient(excerpts: list[GroundedNewsExcerpt], policy: NewsCollectionPolicy,
                           uncounted_urls: frozenset[str] = frozenset()) -> bool:
    """수집 충분성 — 조기 중단과 최종 판정이 같은 입력으로 이 함수 하나를 쓴다.

    두 조건이 모두 참이어야 한다.
    ① 기존(HEAD) 판정: 기존 선택을 두 번 적용(HEAD 최종 판정과 같은 계산)해도 충분 —
       보존을 넓힌 재보도·유사 보도를 새 사건으로 세어 기존보다 느슨해지지 않게.
    ② 실제 최종 선택: ``select_diverse_excerpts``가 실제로 남기는 조각의 사건·기사·주제
       수가 충분 — 대표(긴 원문)·글자 예산 차이로 출력이 한 기사뿐인데 충분이라 하지 않게.
    ``uncounted_urls``(다른 날짜 재게시본)는 두 조건 모두에서 새 기사로 세지 않는다.
    """
    counted = [item for item in excerpts if item.candidate.source_url not in uncounted_urls]
    if not _meets_policy(_legacy_selection(_legacy_selection(counted, policy), policy), policy):
        return False
    chosen, _ = select_diverse_excerpts(list(excerpts), policy)
    return _meets_policy([item for item in chosen if item.candidate.source_url not in uncounted_urls], policy)


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
