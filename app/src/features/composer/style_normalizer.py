"""검증 원문을 보존하고 최종 공개 산문에만 문체·시점 표기를 적용한다."""

from __future__ import annotations

from calendar import monthrange
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date
from re import Match

from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSentence, fragments_from_raw,
)
from src.features.composer.style_normalizer_constants import (
    DATED_PLAN_RE, DISCLOSURE_ORIGINAL_MARKER, FUTURE_CLAUSE_RE,
    PAST_DATED_FUTURE_TENSE, QUOTE_PAIRS, SENTENCE_ENDING_RE,
    SENTENCE_ENDING_REPLACEMENTS,
)
from src.features.composer.verbatim_news import verbatim_news_source


def _unquoted_text(text: str) -> str:
    """위치를 유지한 채 인용을 가려 인용 밖의 문자만 치환하게 한다."""
    closers: list[str] = []
    visible: list[str] = []
    for char in text:
        if closers and char == closers[-1]:
            closers.pop()
            visible.append(" ")
        elif char in QUOTE_PAIRS:
            closers.append(QUOTE_PAIRS[char])
            visible.append(" ")
        else:
            visible.append(" " if closers else char)
    return "".join(visible)


def normalize_sentence_style(text: str) -> str:
    """따옴표 밖의 마침표 종결 어미만 상수 치환표대로 바꾼다."""
    matches = list(SENTENCE_ENDING_RE.finditer(_unquoted_text(text)))
    for match in reversed(matches):
        text = (
            text[:match.start()]
            + SENTENCE_ENDING_REPLACEMENTS[match.group()]
            + text[match.end():]
        )
    return text


def _dated_plan_is_past(match: Match[str], baseline: date) -> bool:
    try:
        year, month = int(match["year"]), int(match["month"])
        # 일자가 없으면 같은 달의 일정이 이미 지났다고 추정하지 않는다.
        day = int(match["day"]) if match["day"] else monthrange(year, month)[1]
        return date(year, month, day) < baseline
    except ValueError:
        return False


def annotate_past_dated_future(text: str, as_of_date: str) -> tuple[str, int]:
    """지난 일정의 미래형은 유지하고 원문 기준 표기와 절 개수만 돌려준다."""
    try:
        baseline = date.fromisoformat(as_of_date)
    except (TypeError, ValueError):
        return text, 0
    matches = [
        match for match in FUTURE_CLAUSE_RE.finditer(_unquoted_text(text))
        if any(_dated_plan_is_past(plan, baseline)
               for plan in DATED_PLAN_RE.finditer(match["clause"]))
        and not text[match.end():].lstrip().startswith(DISCLOSURE_ORIGINAL_MARKER)
    ]
    for match in reversed(matches):
        text = (
            text[:match.end()] + " " + DISCLOSURE_ORIGINAL_MARKER
            + text[match.end():]
        )
    return text, len(matches)


def _sentence_key(sentence: ComposedSentence) -> tuple[str, tuple[str, ...], str]:
    return sentence.text, sentence.citations, sentence.planned_claim_slot


class SentenceStyleNormalizer:
    """본문과 요약의 같은 문장을 한 번만 세고 원문 인용 자격을 보존한다."""

    def __init__(
        self,
        report: ComposedReport,
        fragments: Mapping | Sequence[CollectedFragment],
        *,
        as_of_date: str,
    ) -> None:
        collected = fragments_from_raw(fragments) if isinstance(fragments, Mapping) else fragments
        self._fragments = {fragment.fragment_id: fragment for fragment in collected}
        self._section_ids = tuple(section.section_id for section in report.sections)
        self._as_of_date = as_of_date
        self._ai_keys = {
            _sentence_key(sentence)
            for section in report.sections for sentence in section.sentences
            if sentence.structured_claim is None and not sentence.verified_fact_id
        }
        self._cache: dict[tuple[str, tuple[str, ...], str], str] = {}
        self.diagnostics: dict[str, int] = {}

    def normalize(self, sentence: ComposedSentence) -> ComposedSentence:
        """검증 객체를 수정하지 않고 표시용 사본만 반환한다."""
        key = _sentence_key(sentence)
        if sentence.structured_claim is not None or (
            sentence.verified_fact_id and key not in self._ai_keys
        ):
            return sentence
        if key not in self._cache:
            # 작가 표식 대신 수집 조각과 장 소유권으로 원문 인용 자격을 재확인한다.
            if any(verbatim_news_source(
                sentence.text, sentence.citations, self._fragments, section_id=section_id,
            ) is not None for section_id in self._section_ids):
                self._cache[key] = sentence.text
            else:
                text, count = annotate_past_dated_future(sentence.text, self._as_of_date)
                self._cache[key] = normalize_sentence_style(text)
                if count:
                    self.diagnostics[PAST_DATED_FUTURE_TENSE] = (
                        self.diagnostics.get(PAST_DATED_FUTURE_TENSE, 0) + count
                    )
        return replace(sentence, text=self._cache[key])
