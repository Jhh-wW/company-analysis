# -*- coding: utf-8 -*-
"""렌더 직전에 «인용이 아닌 대괄호 숫자»를 본문 글자로 확정한다.

★ 무엇을 막나 — 출고 검사(`shared.report_quality.output_validation`)는 화면
  문장 안의 모든 ``[숫자]``를 부록 인용 번호로 읽는다. 그래서 문장에 우연히
  들어온 대괄호 숫자(종목코드·법령 조항 번호·기사 원문의 괄호 표기 등)가
  «부록에 없는 인용»으로 잡혀 보고서 «전체»가 차단됐다. 한 문장의 글자 하나가
  요청 전체를 죽이는 자리라, 검사 쪽을 풀지 않고 «들어오는 글자»를 정리한다.

★ 규칙 하나 — 문장 글자 속 ``[숫자]``는 «그 문장이 실제로 인용한 조각의 공개
  번호»와 같을 때만 인용이다. 그 외는 전부 본문 글자다. 번호는 렌더러가 쓰는
  정본(`render.citation_numbers_for_fragments`)을 그대로 읽어 계산하므로,
  여기서 «인용»으로 본 번호와 화면에 찍히는 번호가 갈릴 수 없다.

★ 두 갈래 —
  ① 일반 산문: 대괄호만 전각으로 바꾼다. 숫자와 낱말은 한 글자도 지우지
     않으므로 독자가 보는 내용이 보존된다.
  ② 원문을 «글자 그대로» 옮긴 문장(뉴스 축자 후보 등): 글자를 바꾸면 원문
     sha256 대조가 깨진다. 그래서 바꾸지 않고 그 문장만 빼고, 원문 없는 닫힌
     사유 코드를 검수 진단에 남긴다.

⚠️ 표 칸·보도표 행은 여기서 건드리지 않는다. 출고 검사가 표 «칸 글자»를 인용
  번호로 읽지 않고(`_cited_numbers_in_body`는 표의 구조화된 출처 번호만 본다),
  보도표 행은 Source 해시와 글자 그대로 대조하는 자리이기 때문이다.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Optional

from src.features.composer.logic import FragmentsInput, _normalize_fragments
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.render import citation_numbers_for_fragments
from src.features.composer.stray_citation_marker_constants import (
    CITATION_MARKER_ITEM,
    CITATION_MARKER_NOT_IN_CITATIONS,
    FULLWIDTH_LEFT_SQUARE_BRACKET,
    FULLWIDTH_RIGHT_SQUARE_BRACKET,
    MAX_CITATION_NUMBER_DIGITS,
)

# ★ 출고 검사와 «같은» 정규식을 그대로 읽는다. 여기서 따로 적으면 두 규칙이
#   갈려 이번 결함이 다시 난다(검사 쪽은 자릿수 제한이 없는데 정리 쪽만 1~3자리를
#   보던 것이 이번 사고의 원인이다).
from src.shared.report_quality.output_validation import CITATION_MARKER_RE

logger = logging.getLogger(__name__)

#: 진단의 후보지문 계산 — 검수 진단과 같은 값(UTF-8 SHA-256).
_BODY_KIND = "본문"
_SUMMARY_KIND = "요약"
_SUMMARY_SECTION_ID = "summary"


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _own_citation_numbers(
    sentence: ComposedSentence, numbers: Mapping[str, int]
) -> frozenset[int]:
    """이 문장이 실제로 인용한 조각의 «공개 번호»."""

    return frozenset(
        number
        for number in (
            numbers.get(str(citation).strip()) for citation in sentence.citations
        )
        if isinstance(number, int)
    )


def _stray_marker_spans(text: str, own_numbers: frozenset[int]) -> list[tuple[int, int]]:
    """``[숫자]`` 중 «자기 인용 번호가 아닌» 것의 (시작, 끝) 자리들."""

    spans: list[tuple[int, int]] = []
    for match in CITATION_MARKER_RE.finditer(text):
        digits = match.group(1)
        if len(digits) > MAX_CITATION_NUMBER_DIGITS:
            # 부록 번호가 될 수 없는 자릿수 — 인용이 아니다(int 변환도 피한다).
            spans.append(match.span())
            continue
        if int(digits) in own_numbers:
            continue
        spans.append(match.span())
    return spans


def _widen_brackets(text: str, spans: Sequence[tuple[int, int]]) -> str:
    """찾은 자리의 대괄호«만» 전각으로 바꾼다. 숫자·다른 글자는 그대로 둔다."""

    out: list[str] = []
    cursor = 0
    for start, end in spans:
        out.append(text[cursor:start])
        out.append(FULLWIDTH_LEFT_SQUARE_BRACKET)
        out.append(text[start + 1:end - 1])
        out.append(FULLWIDTH_RIGHT_SQUARE_BRACKET)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def _is_verbatim_copy(sentence: ComposedSentence, source_texts: Mapping[str, str]) -> bool:
    """이 문장이 인용 조각의 원문을 «글자 그대로» 옮긴 것인가.

    ★ 뉴스 축자 후보(`news_usage.supplement_news_candidates`)는 「출처 접두사 +
      조각 원문」 모양이라 원문이 문장 «끝»에 통째로 붙는다. 이 문장의 글자를
      고치면 `verbatim_news.VerbatimNewsSource.matches`·보도표 원문 해시 대조가
      깨지므로, 고치는 대신 빼야 하는 자리를 여기서 가른다.
    ⚠️ 회사·매체를 가리지 않는 구조 판정이다 — 특정 값을 적지 않는다.
    """

    body = sentence.text.strip()
    if not body:
        return False
    for citation in sentence.citations:
        source = str(source_texts.get(str(citation).strip(), "") or "").strip()
        if source and body.endswith(source):
            return True
    return False


def _diagnostic(section_id: str, kind: str, sentence: ComposedSentence) -> dict:
    """원문 없이 장·종류·사유·후보지문만 남긴 닫힌 진단 한 줄."""

    return {
        "section_id": section_id,
        "kind": kind,
        "reason_code": CITATION_MARKER_NOT_IN_CITATIONS,
        "candidate_sha256": _sha256(sentence.text),
        "verification_items": (CITATION_MARKER_ITEM,),
    }


def _sanitize_sentences(
    sentences: Sequence[ComposedSentence],
    *,
    section_id: str,
    kind: str,
    numbers: Mapping[str, int],
    source_texts: Mapping[str, str],
    diagnostics: Optional[list[dict]],
) -> tuple[ComposedSentence, ...]:
    kept: list[ComposedSentence] = []
    for sentence in sentences:
        spans = _stray_marker_spans(
            sentence.text, _own_citation_numbers(sentence, numbers)
        )
        if not spans:
            kept.append(sentence)
            continue
        if _is_verbatim_copy(sentence, source_texts):
            # 글자를 바꾸면 원문 대조가 깨진다 — 그 문장만 뺀다.
            if diagnostics is not None:
                diagnostics.append(_diagnostic(section_id, kind, sentence))
            logger.info(
                "인용이 아닌 대괄호 숫자가 든 축자 문장을 뺐습니다 (장 %s, %s)",
                section_id,
                kind,
            )
            continue
        kept.append(replace(sentence, text=_widen_brackets(sentence.text, spans)))
    return tuple(kept)


def sanitize_stray_citation_markers(
    report: ComposedReport,
    fragments: FragmentsInput,
    *,
    diagnostics: Optional[list[dict]] = None,
) -> ComposedReport:
    """렌더로 넘기기 전에 인용 아닌 ``[숫자]``를 본문 글자로 확정한다.

    Args:
        report: 검수·근거 결속까지 끝난 보고서.
        fragments: 그 보고서의 수집 조각 — 공개 번호와 원문 대조에 쓴다.
        diagnostics: 축자 문장을 «뺀» 기록을 담을 검수 진단 수집기. None이면
            기록하지 않는다(기존 호출 모양 보존).

    Returns:
        같은 장 개수·순서의 ComposedReport. 장을 지우지 않으며, 인용 아닌
        대괄호 숫자가 없으면 «입력과 같은 값»을 돌려준다(멱등).
    """

    numbers = citation_numbers_for_fragments(fragments)
    source_texts = {
        fragment.fragment_id: str(fragment.text or "")
        for fragment in _normalize_fragments(fragments)
    }
    sections: list[ComposedSection] = []
    for section in report.sections:
        sentences = _sanitize_sentences(
            section.sentences,
            section_id=section.section_id,
            kind=_BODY_KIND,
            numbers=numbers,
            source_texts=source_texts,
            diagnostics=diagnostics,
        )
        notice_spans = _stray_marker_spans(section.notice, frozenset())
        notice = (
            _widen_brackets(section.notice, notice_spans)
            if notice_spans
            else section.notice
        )
        sections.append(replace(section, sentences=sentences, notice=notice))
    summary = _sanitize_sentences(
        report.summary,
        section_id=_SUMMARY_SECTION_ID,
        kind=_SUMMARY_KIND,
        numbers=numbers,
        source_texts=source_texts,
        diagnostics=diagnostics,
    )
    return replace(report, sections=tuple(sections), summary=summary)
