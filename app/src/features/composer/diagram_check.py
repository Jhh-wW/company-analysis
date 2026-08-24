"""도식 검증 — 그림이 근거보다 앞서 나가지 못하게 막는다.

★ 왜 필요한가 (도식 적대 검증 실측) — 도식 10개를 독립 검증했더니 결함이
  **수치 도식 0건 / 관계 도식 7건**으로 완전히 갈렸다. 이유는 분명하다:
  수치 도식은 틀리면 숫자가 안 맞아 걸리는데, **관계 도식은 틀려도 아무도
  안 걸린다.** 실제로 잡힌 것 중 하나는 원문에 없는 관계를 그린 것이었다 —
  제조를 돕는 기술 파트너에서 「고객」으로 화살표를 그었는데, 원문은
  "운영 효율성·제조 경쟁력 강화"라고만 했다.

★ 그래서 이 파일은 «관계 도식(경로표)»만 본다. 검사는 하나다:
      경로표의 각 칸이 그 줄이 인용한 조각 원문에 실제로 «있는가».
  원문에 없는 회사·단계·고객이 그림에 등장하는 것을 막는다.

★ 닫힌 목록 게이트가 아니다 (01_원칙과_금지.md).
  - 어휘 목록·업종 목록·관계 종류 목록을 만들지 않는다.
  - 칸 내용의 좋고 나쁨을 판단하지 않는다. «인용한 원문 안에 있는 말인가»만 본다.
  - 문장을 거절하지 않는다. 근거 없는 «줄»만 뺀다. 줄이 다 빠지면 도식을
    안 그릴 뿐, 장은 그대로 남는다.

★ 글자가 똑같이 일치할 것을 요구하지 않는다. 작가는 원문을 줄여 쓴다
  (원문 "시트를 가공해" → 칸 "시트·필름 가공"). 그래서 글자 3-그램 겹침으로
  본다 — 조사·어미가 달라도 같은 말이면 통과하고, 지어낸 이름은 겹침이
  거의 0이라 걸린다. 형태소 사전이 필요 없다.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Final

from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    FlowRow,
)

logger = logging.getLogger(__name__)

#: 글자 n-그램 길이. dedupe.py와 같은 값을 쓴다 — 두 곳이 다른 잣대를 쓰면
#: 「여기선 같은 말, 저기선 다른 말」이 되어 설명할 수 없는 결과가 나온다.
_NGRAM_SIZE: Final[int] = 3

#: 칸이 «원문에 있다»고 볼 최소 겹침 비율 (칸 기준).
#: 0.5는 느슨한 편이다 — 잘못 지우는 쪽이 더 나쁘기 때문에 의심스러우면 남긴다.
_GROUNDED_THRESHOLD: Final[float] = 0.5

#: 이 길이 미만의 칸은 검사하지 않는다. 짧은 말은 우연히 겹치거나 우연히
#: 안 겹친다 — 어느 쪽으로도 판단 근거가 못 된다.
_MIN_CHECK_CHARS: Final[int] = 3

_KEEP_CHARS_RE: Final[re.Pattern[str]] = re.compile(r"[^0-9A-Za-z가-힣]+")


def _ngrams(text: str) -> frozenset[str]:
    """글자 3-그램 집합. 공백·문장부호는 표기 차이라 지운다."""
    condensed = _KEEP_CHARS_RE.sub(
        "", unicodedata.normalize("NFKC", text or "").lower()
    )
    if len(condensed) < _NGRAM_SIZE:
        return frozenset()
    return frozenset(
        condensed[index : index + _NGRAM_SIZE]
        for index in range(len(condensed) - _NGRAM_SIZE + 1)
    )


def _fragment_texts(
    fragments: Sequence[CollectedFragment],
) -> dict[str, str]:
    return {str(fragment.fragment_id): fragment.text for fragment in fragments}


def _is_grounded(cell: str, source_text: str) -> bool:
    """칸이 인용 원문 안에 있는 말인가 (글자 3-그램 겹침)."""
    condensed = _KEEP_CHARS_RE.sub("", cell)
    if len(condensed) < _MIN_CHECK_CHARS:
        return True  # 판단 근거가 없으면 남긴다
    cell_grams = _ngrams(cell)
    if not cell_grams:
        return True
    source_grams = _ngrams(source_text)
    if not source_grams:
        return False
    return len(cell_grams & source_grams) / len(cell_grams) >= _GROUNDED_THRESHOLD


def _grounded_rows(
    rows: Sequence[FlowRow], texts: Mapping[str, str]
) -> tuple[tuple[FlowRow, ...], list[str]]:
    """근거 원문에 실제로 있는 줄만 남긴다. 뺀 줄의 사유도 함께 돌려준다."""
    kept: list[FlowRow] = []
    dropped: list[str] = []
    for row in rows:
        source_text = " ".join(
            texts.get(str(citation).strip(), "") for citation in row.citations
        )
        ungrounded = [
            cell for cell in row.cells if not _is_grounded(cell, source_text)
        ]
        if ungrounded:
            dropped.append(
                "경로 «"
                + " → ".join(row.cells)
                + "»: 인용 원문에서 확인되지 않는 칸 "
                + ", ".join(f"「{cell}」" for cell in ungrounded)
            )
            continue
        kept.append(row)
    return tuple(kept), dropped


def check_diagrams(
    report: ComposedReport, fragments: Sequence[CollectedFragment]
) -> tuple[ComposedReport, tuple[str, ...]]:
    """관계 도식의 각 줄이 인용 원문에 근거하는지 보고, 근거 없는 줄을 뺀다.

    Args:
        report: 검증까지 끝난 보고서.
        fragments: 수집 조각 — 칸을 대조할 원문.

    Returns:
        (근거 없는 줄이 빠진 보고서, 뺀 사유 목록).
        사유 목록은 운영 기록용이다 — 원문을 담지 않는다.

    ★ 장을 지우지 않는다. 도식이 사라져도 본문 문장은 그대로다.
    """
    texts = _fragment_texts(fragments)
    problems: list[str] = []
    rebuilt: list[ComposedSection] = []
    changed = False
    for section in report.sections:
        if not section.flow_rows:
            rebuilt.append(section)
            continue
        kept, dropped = _grounded_rows(section.flow_rows, texts)
        if dropped:
            changed = True
            problems.extend(f"[{section.section_id}] {reason}" for reason in dropped)
        rebuilt.append(
            ComposedSection(
                section_id=section.section_id,
                sentences=section.sentences,
                notice=section.notice,
                flow_rows=kept,
            )
        )
    if not changed:
        return report, ()
    logger.info("도식 검증: 근거 없는 경로 %d줄을 뺐습니다", len(problems))
    return (
        ComposedReport(sections=tuple(rebuilt), summary=report.summary),
        tuple(problems),
    )
