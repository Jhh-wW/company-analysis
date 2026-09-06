"""뉴스 검색 결과의 기계 필터·중복 제거·우선순위 선별."""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Protocol
from urllib.parse import urlsplit

from src.features.news_intake import constants as c
from src.features.news_intake.models import NewsCandidate, NewsSelectionResult
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.report_quality.source_identity import canonical_url


class NewsItemInput(Protocol):
    """NAVER ``NewsItem``과 가짜 시험 객체가 공유하는 최소 계약."""

    title: str
    originallink: str
    link: str
    description: str
    pubDate: str


@dataclass(frozen=True)
class _CandidateSeed:
    title: str
    description: str
    originallink: str
    link: str
    published_on: str
    published_date: dt.date
    publisher: str
    priority: int
    source_url: str
    input_index: int
    company_host: bool
    newsroom_path: bool


_SPACE_RE = re.compile(r"\s+")
_SYMBOL_RE = re.compile(r"[^0-9a-z가-힣]+", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+|[\r\n]+")


def normalize_company_name(value: str) -> str:
    """법인 표지와 공백 차이를 없앤 회사명 비교값."""

    clean = unicodedata.normalize("NFKC", str(value or "")).casefold()
    for marker in c.CORPORATE_DESIGNATORS:
        clean = clean.replace(unicodedata.normalize("NFKC", marker).casefold(), "")
    return _SPACE_RE.sub("", clean)


def _normalized_names(company_name: str, aliases: Iterable[str]) -> tuple[str, ...]:
    values = (company_name, *tuple(aliases))
    normalized = tuple(dict.fromkeys(normalize_company_name(item) for item in values))
    return tuple(item for item in normalized if item)


def _mentions_company(text: str, names: tuple[str, ...]) -> bool:
    normalized = normalize_company_name(text)
    return any(name in normalized for name in names)


def _raw_items(items: Iterable[NewsItemInput] | object) -> tuple[NewsItemInput, ...]:
    """``NewsSearchResult``와 그 ``items`` 목록을 모두 입력으로 받는다."""

    candidate = getattr(items, "items", items)
    try:
        return tuple(candidate)  # type: ignore[arg-type]
    except TypeError as error:
        raise TypeError("뉴스 입력은 NewsSearchResult 또는 NewsItem iterable이어야 합니다") from error


def _parse_date(value: object) -> dt.date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError:
        return None


def _normalized_host(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlsplit(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").rstrip(".").casefold()
    return host[4:] if host.startswith("www.") else host


def _domain_hosts(values: Iterable[str]) -> frozenset[str]:
    return frozenset(host for host in (_normalized_host(value) for value in values) if host)


def _host_matches(host: str, company_hosts: frozenset[str]) -> bool:
    clean = _normalized_host(host)
    return any(clean == expected or clean.endswith(f".{expected}") for expected in company_hosts)


def _source_url(item: NewsItemInput) -> str:
    return canonical_url(str(getattr(item, "originallink", "") or "")) or canonical_url(
        str(getattr(item, "link", "") or "")
    )


def _publisher(url: str) -> str:
    return (urlsplit(url).hostname or "출처미상").casefold()


def _is_rumor_only(description: str) -> bool:
    sentences = tuple(
        sentence.strip()
        for sentence in _SENTENCE_SPLIT_RE.split(description)
        if sentence.strip()
    )
    return bool(sentences) and all(
        any(marker in sentence for marker in c.RUMOR_ONLY_MARKERS)
        for sentence in sentences
    )


def _newsroom_path(url: str) -> bool:
    path = urlsplit(url).path.casefold()
    return any(marker in path for marker in c.NEWSROOM_PATH_MARKERS)


def _priority(
    *,
    title: str,
    company_names: tuple[str, ...],
    executive_names: tuple[str, ...],
    company_host: bool,
) -> int:
    if company_host:
        return c.PRIORITY_NEWSROOM
    if _mentions_company(title, company_names) and any(
        marker in title for marker in c.PRESS_RELEASE_KEYWORDS
    ):
        return c.PRIORITY_PRESS_RELEASE
    if any(marker.casefold() in title.casefold() for marker in c.INTERVIEW_KEYWORDS) or any(
        name and name.casefold() in title.casefold() for name in executive_names
    ):
        return c.PRIORITY_INTERVIEW
    return c.PRIORITY_OTHER


def _title_words(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    for marker in c.CORPORATE_DESIGNATORS:
        normalized = normalized.replace(
            unicodedata.normalize("NFKC", marker).casefold(), " "
        )
    words: list[str] = []
    for raw_word in _SYMBOL_RE.sub(" ", normalized).split():
        word = raw_word
        for particle in c.TITLE_PARTICLES:
            if len(word) > len(particle) + 1 and word.endswith(particle):
                word = word[: -len(particle)]
                break
        if word and word not in c.TITLE_PARTICLES:
            words.append(word)
    return tuple(words)


def _titles_are_duplicate(left: str, right: str) -> bool:
    if not any(marker in f"{left} {right}" for marker in c.PRESS_RELEASE_KEYWORDS):
        return False
    left_words = _title_words(left)
    right_words = _title_words(right)
    left_text = "".join(left_words)
    right_text = "".join(right_words)
    if min(len(left_text), len(right_text)) < c.TITLE_DUPLICATE_MIN_CHARS:
        return False
    ratio = SequenceMatcher(None, left_text, right_text).ratio()
    union = set(left_words) | set(right_words)
    overlap = len(set(left_words) & set(right_words)) / len(union) if union else 0.0
    return ratio >= c.TITLE_DUPLICATE_SIMILARITY or overlap >= c.TITLE_DUPLICATE_SIMILARITY


def _duplicate_preference(seed: _CandidateSeed) -> tuple[int, int, int, int]:
    """자사 뉴스룸 → 회사 원문 → 최신 → 입력 순서."""

    return (
        0 if seed.newsroom_path and seed.company_host else 1,
        0 if seed.company_host else 1,
        -seed.published_date.toordinal(),
        seed.input_index,
    )


def _deduplicate(seeds: list[_CandidateSeed]) -> tuple[list[_CandidateSeed], int]:
    groups: list[list[_CandidateSeed]] = []
    for seed in seeds:
        for group in groups:
            if any(
                seed.source_url == other.source_url
                or _titles_are_duplicate(seed.title, other.title)
                for other in group
            ):
                group.append(seed)
                break
        else:
            groups.append([seed])
    kept = [min(group, key=_duplicate_preference) for group in groups]
    return kept, sum(len(group) - 1 for group in groups)


def select_candidates(
    company_name: str,
    aliases: Iterable[str],
    items: Iterable[NewsItemInput] | object,
    as_of: dt.date,
    *,
    company_domains: Iterable[str] = (),
    company_domain: str = "",
    executive_names: Iterable[str] = (),
    window_days: int = c.DEFAULT_WINDOW_DAYS,
    extended_window: bool = False,
    limit: int = c.MAX_CANDIDATES,
) -> NewsSelectionResult:
    """검색 결과를 싼 규칙부터 거르고 우선순위대로 최대 20건 고른다."""

    if not isinstance(as_of, dt.date):
        raise TypeError("기준일은 datetime.date여야 합니다")
    effective_window = c.EXTENDED_WINDOW_DAYS if extended_window else window_days
    if isinstance(effective_window, bool) or effective_window <= 0:
        raise ValueError("기사 기간 창은 1일 이상이어야 합니다")
    if isinstance(limit, bool) or not 1 <= limit <= c.MAX_CANDIDATES:
        raise ValueError(f"기사 상한은 1부터 {c.MAX_CANDIDATES} 사이여야 합니다")
    names = _normalized_names(company_name, aliases)
    if not names:
        raise ValueError("회사 정식 명칭 또는 별칭이 하나 이상 필요합니다")
    domains = tuple(company_domains) + ((company_domain,) if company_domain else ())
    company_hosts = _domain_hosts(domains)
    clean_executives = tuple(str(item).strip() for item in executive_names if str(item).strip())
    raw_items = _raw_items(items)
    excluded: Counter[str] = Counter()
    seeds: list[_CandidateSeed] = []

    for index, item in enumerate(raw_items):
        title = str(getattr(item, "title", "") or "").strip()
        description = str(getattr(item, "description", "") or "").strip()
        combined = f"{title}\n{description}"
        if not _mentions_company(combined, names):
            excluded[c.EXCLUDED_COMPANY_NOT_MENTIONED] += 1
            continue
        if _is_rumor_only(description):
            excluded[c.EXCLUDED_RUMOR_ONLY] += 1
            continue
        if any(marker in combined for marker in c.STOCK_KEYWORDS):
            excluded[c.EXCLUDED_STOCK_ARTICLE] += 1
            continue
        published_date = _parse_date(getattr(item, "pubDate", ""))
        if published_date is None:
            excluded[c.EXCLUDED_INVALID_DATE] += 1
            continue
        age = (as_of - published_date).days
        if age < 0 or age > effective_window:
            excluded[c.EXCLUDED_OUTSIDE_WINDOW] += 1
            continue
        source_url = _source_url(item)
        if not source_url:
            excluded[c.EXCLUDED_INVALID_URL] += 1
            continue
        originallink = str(getattr(item, "originallink", "") or "").strip()
        link = str(getattr(item, "link", "") or "").strip()
        origin_host = urlsplit(canonical_url(originallink)).hostname or ""
        is_company_host = _host_matches(origin_host, company_hosts)
        seeds.append(
            _CandidateSeed(
                title=title,
                description=description,
                originallink=originallink,
                link=link,
                published_on=published_date.isoformat(),
                published_date=published_date,
                publisher=_publisher(source_url),
                priority=_priority(
                    title=title,
                    company_names=names,
                    executive_names=clean_executives,
                    company_host=is_company_host,
                ),
                source_url=source_url,
                input_index=index,
                company_host=is_company_host,
                newsroom_path=_newsroom_path(source_url),
            )
        )

    deduplicated, duplicate_count = _deduplicate(seeds)
    excluded[c.EXCLUDED_DUPLICATE_RELEASE] += duplicate_count
    ordered = sorted(
        deduplicated,
        key=lambda item: (item.priority, -item.published_date.toordinal(), item.input_index),
    )
    if len(ordered) > limit:
        excluded[c.EXCLUDED_CANDIDATE_LIMIT] += len(ordered) - limit
        ordered = ordered[:limit]
    candidates = tuple(
        NewsCandidate(
            id=f"news-{index:03d}",
            title=item.title,
            description=item.description,
            originallink=item.originallink,
            link=item.link,
            published_on=item.published_on,
            publisher=item.publisher,
            priority=item.priority,
            source_url=item.source_url,
        )
        for index, item in enumerate(ordered, start=1)
    )
    return NewsSelectionResult(
        candidates=candidates,
        searched_count=len(raw_items),
        exclusion_counts=dict(excluded),
    )


def select_news_items(
    company_name: str,
    aliases: Iterable[str],
    items: Iterable[NewsItemInput] | object,
    as_of: dt.date,
    **kwargs: object,
) -> NewsSelectionResult:
    """호출부에서 읽기 쉬운 ``select_candidates`` 별칭."""

    return select_candidates(company_name, aliases, items, as_of, **kwargs)


def needs_extended_window(section_ready: Mapping[str, bool]) -> bool:
    """5·6장을 제외한 장 하나라도 READY가 아니면 3년 확장을 요청한다."""

    if not isinstance(section_ready, Mapping):
        raise TypeError("장별 READY 상태는 Mapping이어야 합니다")
    return any(
        not bool(section_ready.get(section_id, False))
        for section_id in REQUIRED_EVIDENCE_SECTION_IDS
        if section_id not in c.NON_EXTENDABLE_SECTIONS
    )
