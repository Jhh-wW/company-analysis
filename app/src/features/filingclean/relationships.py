"""사업보고서에서 실명이 붙은 파트너·계약 문장만 보충한다.

절 첫머리 1,200자만 잘라 쓰면 뒤에 있는 유통사·공연사·합작사 문장을 놓칠 수
있다. 그렇다고 절 전체를 넣으면 표와 일반론이 후보를 압도한다. 이 모듈은 원문을
요약하지 않고, 관계 표현과 외부 주체 실명이 함께 있는 문장만 소수 보충한다.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Final

from src.features.filingclean.logic import find_best_body_chunk


MAX_RELATIONSHIP_FRAGMENTS: Final[int] = 3
MIN_SENTENCE_CHARS: Final[int] = 35
MAX_SENTENCE_CHARS: Final[int] = 700

_RELATION_MARKERS: Final[tuple[str, ...]] = (
    "파트너십",
    "전략적 파트너",
    "유통 계약",
    "공급 계약",
    "라이선스 계약",
    "라이센스 계약",
    "합작",
    "업무협약",
    "제휴",
    "협업",
)
_PLAN_MARKERS: Final[tuple[str, ...]] = (
    "계획",
    "예정",
    "추진",
    "목표",
    "확대해갈",
    "강화할",
    "구축할",
)
_CONTINUATION_MARKERS: Final[tuple[str, ...]] = (
    "양사",
    "이를",
    "이번",
    "해당",
    "파트너십",
    "계약",
)
_LATIN_NAME_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9&.+:-]*(?:\s+[A-Za-z][A-Za-z0-9&.+:-]*)*(?![A-Za-z0-9])"
)
_KOREAN_PARTY_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:주식회사\s*|㈜\s*|\(주\)\s*)?([가-힣][가-힣A-Za-z0-9&.·]{1,24})"
    r"(?:와|과|와의|과의)\s*(?:전략적\s*)?(?:파트너십|계약|제휴|협업|합작|업무협약)"
)
_LATIN_STOP: Final[set[str]] = {
    "agency",
    "ai",
    "api",
    "b2b",
    "b2c",
    "business",
    "cd",
    "ceo",
    "company",
    "content",
    "contents",
    "dart",
    "digital",
    "entertainment",
    "esg",
    "global",
    "group",
    "ip",
    "ir",
    "it",
    "k-pop",
    "kpop",
    "md",
    "media",
    "music",
    "oem",
    "odm",
    "online",
    "offline",
    "partner",
    "partners",
    "partnership",
    "pd",
    "platform",
    "r&d",
    "record",
    "records",
    "service",
    "services",
    "sns",
    "strategic",
    "technology",
    "tv",
    "distribution",
    "network",
    "worldwide",
    "advanced",
    "alliance",
    "consortium",
    "creative",
    "innovative",
    "integrated",
    "international",
    "leading",
    "solutions",
    "universal",
    "premier",
    "commerce",
    "collective",
}
_KOREAN_STOP: Final[set[str]] = {
    "당사",
    "회사",
    "기업",
    "기업들",
    "업체",
    "업체들",
    "회사들",
    "고객",
    "양사",
    "파트너",
    "파트너들",
    "협력사",
    "플랫폼기업",
    "글로벌음악플랫폼",
    "음악플랫폼",
    "디지털플랫폼",
    "온라인플랫폼",
}
_GENERIC_PARTY_MARKERS: Final[tuple[str, ...]] = (
    "플랫폼기업",
    "글로벌파트너",
    "유관기업",
    "관계사",
    "협력사",
    "고객사",
    "공급업체",
    "사업자",
)
_GENERIC_KOREAN_ORG_RE: Final[re.Pattern[str]] = re.compile(
    r"[가-힣]{2,}(?:조직|팀|센터|본부|부서|실)$"
)
_GENERIC_KOREAN_NAMED_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:글로벌|디지털|온라인|콘텐츠|음악|아티스트|신인|인재|사업|전략|연구|개발|"
    r"마케팅|유통|공연|제작|운영|해외|국내)+(?:스튜디오|레이블|플랫폼|파트너스|"
    r"펀드|뮤직|레코드|레코딩|샵|법인|그룹)$"
)
_SENTENCE_RE: Final[re.Pattern[str]] = re.compile(r"[^.!?]+(?:[.!?]+|$)")
_BUSINESS_HEADS: Final[tuple[str, ...]] = (
    "사업의 내용",
    "사업의내용",
    "사업의 개요",
    "사업의개요",
)
_FINANCIAL_HEAD_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:III\.?\s*)?재무에\s*관한\s*사항"
)
_BUSINESS_SCOPE_PROBE_CHARS: Final[int] = 2400
_BUSINESS_SCOPE_MAX_CHARS: Final[int] = 50_000
_BUSINESS_SCOPE_MIN_CHARS: Final[int] = 200


def _plain(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def _appears_in_distinct_sentences(value: str, sentences: list[str]) -> bool:
    needle = _plain(value).casefold()
    cores: list[str] = []
    for sentence in sentences:
        plain = _plain(sentence)
        index = plain.casefold().find(needle)
        if index < 0:
            continue
        core = re.sub(r"[^0-9a-z가-힣]+", "", plain[index:].casefold())
        if not core or any(core in prior or prior in core for prior in cores):
            continue
        cores.append(core)
    return len(cores) >= 2


def _strong_latin_word(word: str) -> bool:
    token = word.strip(".")
    return (
        len(token) >= 2
        and token.casefold() not in _LATIN_STOP
        and (
            token.isupper()
            or any(char.isupper() for char in token[1:])
            or any(char.isdigit() for char in token)
        )
    )


def _has_named_party(sentence: str, corpus_sentences: list[str]) -> bool:
    for match in _LATIN_NAME_RE.finditer(sentence):
        raw_words = [part.strip(".") for part in match.group(0).split()]
        eligible_words = [word for word in raw_words if word.casefold() not in _LATIN_STOP]
        if any(_strong_latin_word(word) for word in eligible_words) or (
            len(raw_words) >= 2
            and bool(eligible_words)
            and _appears_in_distinct_sentences(match.group(0), corpus_sentences)
        ):
            return True
    for match in _KOREAN_PARTY_RE.finditer(sentence):
        party = re.sub(r"\s+", "", match.group(1))
        if party not in _KOREAN_STOP and not any(
            marker in party for marker in _GENERIC_PARTY_MARKERS
        ) and not _GENERIC_KOREAN_ORG_RE.fullmatch(
            party
        ) and not _GENERIC_KOREAN_NAMED_RE.fullmatch(party):
            if re.search(r"[A-Z0-9]", party) or _appears_in_distinct_sentences(
                party, corpus_sentences
            ):
                return True
    return False


def collect_relationship_fragments(
    filing_text: str,
    *,
    limit: int = MAX_RELATIONSHIP_FRAGMENTS,
) -> list[str]:
    """외부 주체 실명과 관계가 함께 있는 원문 문장을 순서대로 돌려준다."""

    if not filing_text or limit <= 0:
        return []
    out: list[str] = []
    seen: set[str] = set()
    sentences = [_plain(match.group(0)) for match in _SENTENCE_RE.finditer(filing_text)]
    for index, sentence in enumerate(sentences):
        if not (MIN_SENTENCE_CHARS <= len(sentence) <= MAX_SENTENCE_CHARS):
            continue
        if not any(marker in sentence for marker in _RELATION_MARKERS):
            continue
        if not _has_named_party(sentence, sentences):
            continue
        if index + 1 < len(sentences):
            continuation = sentences[index + 1]
            if (
                any(marker in continuation for marker in _PLAN_MARKERS)
                and any(marker in continuation for marker in _CONTINUATION_MARKERS)
                and len(sentence) + 1 + len(continuation) <= MAX_SENTENCE_CHARS
            ):
                sentence = f"{sentence} {continuation}"
        key = re.sub(r"[^0-9a-z가-힣]+", "", sentence.casefold())
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(sentence)
        if len(out) >= limit:
            break
    return out


def business_scope(filing_text: str) -> str:
    """목차가 아닌 실제 ``사업의 내용`` 절만 관계 탐색 범위로 돌려준다."""

    body = find_best_body_chunk(
        filing_text,
        _BUSINESS_HEADS,
        _BUSINESS_SCOPE_PROBE_CHARS,
        "사업내용",
    )
    if not body:
        return ""
    start = filing_text.find(body)
    if start < 0:
        return ""
    hard_end = min(len(filing_text), start + _BUSINESS_SCOPE_MAX_CHARS)
    financial = _FINANCIAL_HEAD_RE.search(
        filing_text,
        min(hard_end, start + _BUSINESS_SCOPE_MIN_CHARS),
        hard_end,
    )
    end = financial.start() if financial else hard_end
    return filing_text[start:end]


def add_to(
    frags: dict[int, dict[str, Any]],
    filing_text: str,
    *,
    limit: int = MAX_RELATIONSHIP_FRAGMENTS,
) -> tuple[dict[int, dict[str, Any]], int]:
    """기존 조각에 관계 문장을 ``사업내용`` 조각으로 보충한다."""

    out = dict(frags)
    existing = {
        re.sub(r"[^0-9a-z가-힣]+", "", _plain(frag.get("원문", "")).casefold())
        for frag in frags.values()
    }
    next_id = max(frags, default=0) + 1
    added = 0
    scoped_text = business_scope(filing_text)
    if not scoped_text:
        return out, 0
    for sentence in collect_relationship_fragments(scoped_text, limit=limit):
        key = re.sub(r"[^0-9a-z가-힣]+", "", sentence.casefold())
        if any(key and key in prior for prior in existing):
            continue
        out[next_id] = {"종류": "사업내용", "원문": sentence}
        existing.add(key)
        next_id += 1
        added += 1
    return out, added
