"""5장 대응 칸이 «그 줄이 인용한 원문»의 뒷받침을 받는지 기계로 확인한다.

빈 대응 칸은 `challenge_guard.challenge_response_problem` 이 이미 막는다. 이
모듈이 막는 것은 그 다음 모양이다 — 칸이 «비어 있지는 않은데» 그 줄이 인용한
조각 원문에 없는 말로 적힌 경우다. 표는 본문보다 먼저 읽히고 배지도 없어서,
근거 없는 대응이 한 줄이라도 실리면 독자는 그것을 회사가 밝힌 사실로 읽는다.

★ 왜 새 잣대를 만들지 않았나 — 산문이 이미 쓰는 «자기 인용 지지어» 계산
  (`prose_own_source.own_source_support_terms`)과 최소 개수 계약
  (`report_quality.evidence_support.MIN_PROSE_EVIDENCE_SUPPORT_TERMS`)을 그대로
  가져온다. 표와 산문이 각자 세면 「본문에서는 막힌 말이 표에서는 통과하는」
  어긋남이 다시 생긴다.

★ 짧은 칸을 벌하지 않는다 — 대응표 지침이 칸을 «짧은 이름·구»로 쓰라고
  요구하므로 지지어가 한 개뿐인 정상 칸이 있을 수 있다. 칸 전체가 원문에
  글자 그대로 있으면 지지어 개수와 무관하게 통과시킨다.

⚠️ 이 검사가 «하지 않는» 것 — 정직하게 적는다.
  · 대응의 참·거짓을 판정하지 않는다. 낱말이 겹친다는 것은 대상 결속의
    증명이 아니다. 관계 판정은 기존 의미 검수(AI)와 수치·시점 검사가 맡는다.
  · 인용 원문이 하나도 없으면 «없음»이 아니라 «판단 불가»로 보고 물러난다
    (`diagram_check._numbers_are_grounded` 와 같은 원칙).
  · 다른 줄의 인용 원문으로 메우지 않는다. 그 줄이 스스로 단 인용만 본다.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence

from src.features.composer.challenge_constants import (
    CHALLENGE_RESPONSE_CELL_COUNT,
    CHALLENGE_RESPONSE_CELL_INDEX,
    CHALLENGE_RESPONSE_NOT_IN_SOURCE,
)
from src.features.composer.prose_own_source import own_source_support_terms
from src.shared.report_quality.evidence_support import (
    MIN_PROSE_EVIDENCE_SUPPORT_TERMS,
    normalized_support_terms,
)


def _compact(value: object) -> str:
    """공백·호환문자 차이만 없앤다. 글자와 숫자는 하나도 버리지 않는다."""

    return "".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def challenge_response_evidence_problem(
    cells: Sequence[str], sources_mapping: Mapping[str, str]
) -> str:
    """대응 칸이 자기 인용 원문의 뒷받침을 못 받으면 사유 코드를 돌려준다.

    Args:
        cells: 대응표 한 줄의 칸. «지금 겪는 과제 / 회사가 밝힌 대응» 두 칸이
            아니면 이 검사는 물러난다 — 칸 수 계약은 파서가 이미 지킨다.
        sources_mapping: 그 줄이 인용한 조각 id → 원문. 다른 줄의 원문을
            섞어 넣으면 안 된다.

    Returns:
        문제가 없으면 빈 문자열, 있으면 `CHALLENGE_RESPONSE_NOT_IN_SOURCE`.
    """

    if isinstance(cells, str) or len(cells) != CHALLENGE_RESPONSE_CELL_COUNT:
        return ""
    response = str(cells[CHALLENGE_RESPONSE_CELL_INDEX] or "").strip()
    if not response:
        # 빈 칸은 `challenge_response_problem` 의 몫이다. 두 검사가 같은 줄에
        # 각자 사유를 남기면 진단에서 원인이 두 개로 보인다.
        return ""
    source_texts = [
        str(text) for text in sources_mapping.values() if str(text or "").strip()
    ]
    if not source_texts:
        return ""
    joined_sources = " ".join(source_texts)
    compact_response = _compact(response)
    if compact_response and compact_response in _compact(joined_sources):
        return ""
    support_terms = normalized_support_terms(
        own_source_support_terms(response, source_texts)
    )
    if len(support_terms) >= MIN_PROSE_EVIDENCE_SUPPORT_TERMS:
        return ""
    return CHALLENGE_RESPONSE_NOT_IN_SOURCE


__all__ = ["challenge_response_evidence_problem"]
