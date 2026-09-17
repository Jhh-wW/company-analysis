# -*- coding: utf-8 -*-
"""근거 결속 검사에서 탈락한 «확인» 문장을 AI 1회로 «묶어» 고쳐 쓴다.

이 파일이 하는 일은 하나다: 대상 문장들과 그 인용 원문·탈락 사유를 한 프롬프트에
담아 작가를 «정확히 한 번» 부르고(형식 실패 시 `PARSE_RETRY_LIMIT` 만큼만 재요청),
번호별 고쳐 쓴 문장을 돌려준다.

이 파일이 «하지 않는» 일 — 경계를 분명히 적는다:
  · 수치 검사·재검수를 하지 않는다. 그 둘은 부르는 쪽(`verify._semantic_review`)이
    기존 재작성 경로와 «같은» 함수로 돌려, 같은 잣대가 두 벌이 되지 않게 한다.
  · 진단 기록을 쓰지 않는다. 기록 칸은 공유 계약이라 부르는 쪽이 한곳에서 적는다.
  · AI 호출 실패를 삼키지 않는다. `AskFatalError` 는 그대로 올려 보내
    부르는 쪽이 «호출중단»으로 기록하고 나머지 보고서를 지키게 한다.

⚠️ 고쳐 쓴 문장을 그대로 믿지 않는다. 돌려준 글은 «후보»일 뿐이고, 수치 검사와
   재검수(근거 결속 검사 포함)를 다시 통과해야 보고서에 실린다.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from src.features.composer.constants import PARSE_RETRY_LIMIT
from src.features.composer.grounding_rewrite_constants import (
    GROUNDING_REWRITE_ABANDON_KEY,
    GROUNDING_REWRITE_EVIDENCE_HEAD,
    GROUNDING_REWRITE_MAX_PROMPT_CHARS,
    GROUNDING_REWRITE_NUMBER_KEY,
    GROUNDING_REWRITE_PROMPT_HEADER,
    GROUNDING_REWRITE_RESPONSE_KEY,
    GROUNDING_REWRITE_RETRY_GUIDE,
    GROUNDING_REWRITE_TAIL,
    GROUNDING_REWRITE_TARGET_HEAD,
    GROUNDING_REWRITE_TEXT_KEY,
    grounding_reason_text,
)
from src.features.composer.logic import (
    AskFn,
    extract_json_payload,
    _strip_inline_citation_markers,
)
from src.features.composer.port import AskFatalError, CollectedFragment
from src.shared.report_quality.composition_diagnostic_constants import (
    EMPTY_RECOVERY_SHAPE_CONTRACT,
    EMPTY_RECOVERY_SHAPE_NO_TARGET,
    EMPTY_RECOVERY_SHAPE_UNREADABLE,
    GROUNDING_REWRITE_STATE_DONE,
    GROUNDING_REWRITE_STATE_FORMAT_FAILED,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GroundingRewriteTarget:
    """고쳐 쓸 문장 하나 — 검수 번호·원문장·인용·탈락 사유."""

    number: int
    text: str
    citations: tuple[str, ...]
    reason_code: str


@dataclass
class GroundingRewriteOutcome:
    """묶음 재작성 한 번의 결과. 개수 칸은 부르는 쪽이 그대로 진단에 적는다."""

    #: 번호 → 고쳐 쓴 한 문장. 포기·빈 글·요청 밖 번호는 여기 없다.
    rewritten: dict[int, str] = field(default_factory=dict)
    #: 실제로 프롬프트에 실어 보낸 번호들(상한·글자 제한 적용 후).
    sent: tuple[int, ...] = ()
    #: 작가가 «포기»했거나 빈 글을 준 수. 요청 밖 번호는 세지 않는다.
    abandoned: int = 0
    #: 시도별 응답 꼴 코드. 성공하면 마지막 하나가 계약 꼴이다.
    shapes: tuple[str, ...] = ()
    #: `GROUNDING_REWRITE_STATE_DONE` 또는 `..._FORMAT_FAILED`.
    state: str = GROUNDING_REWRITE_STATE_DONE


def _fragment_lines(
    targets: Sequence[GroundingRewriteTarget],
    frag_by_id: Mapping[str, CollectedFragment],
) -> list[str]:
    """대상들이 인용한 조각 원문을 «한 번씩만» 나열한다.

    ★ 같은 조각을 여러 문장이 인용하는 일이 흔하다(한 장의 문장들은 대개 같은
      공시 조각에 기댄다). 대상마다 원문을 반복해 실으면 프롬프트가 조각 수가
      아니라 문장 수에 비례해 부풀어 글자 상한을 먼저 때린다.
    """

    lines: list[str] = []
    seen: set[str] = set()
    for target in targets:
        for citation in target.citations:
            if citation in seen:
                continue
            fragment = frag_by_id.get(citation)
            if fragment is None:
                continue
            seen.add(citation)
            lines.append(
                f"[조각 {citation}] 원문(JSON 문자열): "
                f"{json.dumps(fragment.text, ensure_ascii=False)}\n"
            )
    return lines


def _target_lines(targets: Sequence[GroundingRewriteTarget]) -> list[str]:
    """대상마다 «번호·원문장·탈락 사유·인용 조각 id»를 한 덩어리로 적는다."""

    lines: list[str] = []
    for target in targets:
        lines.append(
            f"번호 {target.number} · 인용 조각 "
            f"{', '.join(target.citations) if target.citations else '없음'}\n"
            f"  원문장(JSON 문자열): "
            f"{json.dumps(target.text, ensure_ascii=False)}\n"
            f"  탈락 사유: {grounding_reason_text(target.reason_code)}\n"
        )
    return lines


def build_grounding_rewrite_prompt(
    targets: Sequence[GroundingRewriteTarget],
    frag_by_id: Mapping[str, CollectedFragment],
) -> tuple[str, tuple[GroundingRewriteTarget, ...]]:
    """프롬프트와 «실제로 실린» 대상 목록을 만든다.

    글자 상한을 넘으면 뒤쪽 대상부터 빼고 다시 만든다 — 조각 원문도 함께 줄어야
    하므로 목록을 줄인 뒤 처음부터 다시 조립한다(남은 대상이 인용하지 않는
    조각은 더 이상 실리지 않는다).
    """

    kept = list(targets)
    while kept:
        parts = [GROUNDING_REWRITE_PROMPT_HEADER, GROUNDING_REWRITE_EVIDENCE_HEAD]
        parts.extend(_fragment_lines(kept, frag_by_id))
        parts.append(GROUNDING_REWRITE_TARGET_HEAD)
        parts.extend(_target_lines(kept))
        parts.append(GROUNDING_REWRITE_TAIL)
        prompt = "".join(parts)
        if len(prompt) <= GROUNDING_REWRITE_MAX_PROMPT_CHARS or len(kept) == 1:
            if len(prompt) > GROUNDING_REWRITE_MAX_PROMPT_CHARS:
                # 대상 하나만 남았는데도 상한을 넘는다 — 조각 원문 자체가 큰
                # 경우다. 자르면 근거가 잘린 채로 고쳐 쓰게 되므로 보내지 않는다.
                logger.warning(
                    "근거 결속 재작성 프롬프트가 대상 1개로도 상한(%d자)을 넘는다 — "
                    "보내지 않는다",
                    GROUNDING_REWRITE_MAX_PROMPT_CHARS,
                )
                return "", ()
            return prompt, tuple(kept)
        kept.pop()
    return "", ()


def _read_rewrites(
    raw: object, requested: frozenset[int],
) -> tuple[dict[int, str], int, str]:
    """작가 응답에서 «요청한 번호»의 고쳐 쓴 문장만 꺼낸다.

    Returns:
        (번호 → 고쳐 쓴 글, 포기·빈 글 수, 응답 꼴 코드)
    """

    if not isinstance(raw, Mapping):
        return {}, 0, EMPTY_RECOVERY_SHAPE_UNREADABLE
    rows = raw.get(GROUNDING_REWRITE_RESPONSE_KEY)
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return {}, 0, EMPTY_RECOVERY_SHAPE_NO_TARGET
    rewritten: dict[int, str] = {}
    abandoned = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        number = row.get(GROUNDING_REWRITE_NUMBER_KEY)
        if not isinstance(number, int) or isinstance(number, bool):
            continue
        if number not in requested or number in rewritten:
            # 요청하지 않은 번호는 버린다. 같은 번호를 두 번 주면 첫 답만 쓴다 —
            # 뒤의 답으로 덮으면 같은 응답에서 «어느 쪽이 쓰였는지»가 응답 순서에
            # 좌우된다.
            continue
        if bool(row.get(GROUNDING_REWRITE_ABANDON_KEY)):
            abandoned += 1
            continue
        text = row.get(GROUNDING_REWRITE_TEXT_KEY)
        if not isinstance(text, str):
            abandoned += 1
            continue
        # 재작성 응답도 작가 응답과 같은 «흉내낸 인용 대괄호» 위험이 있다
        # (`verify._ask_rewrite` 와 같은 정리).
        cleaned = _strip_inline_citation_markers(text)
        if not cleaned:
            abandoned += 1
            continue
        rewritten[number] = cleaned
    return rewritten, abandoned, EMPTY_RECOVERY_SHAPE_CONTRACT


def rewrite_grounding_rejected(
    ask: AskFn,
    targets: Sequence[GroundingRewriteTarget],
    frag_by_id: Mapping[str, CollectedFragment],
) -> GroundingRewriteOutcome:
    """대상 문장들을 AI «한 번»으로 묶어 고쳐 쓴다.

    호출 수: 작성 1회 + 형식 실패 시 재요청 `PARSE_RETRY_LIMIT` 회. 그게 전부다.

    Raises:
        AskFatalError: 요청 전역 장애(호출 한도·예약액 소진·과금 불확실). 삼키지
            않는다 — 부르는 쪽이 «호출중단»으로 기록하고 대상을 제거한다.
    """

    if not targets:
        return GroundingRewriteOutcome()
    prompt, sent_targets = build_grounding_rewrite_prompt(targets, frag_by_id)
    if not sent_targets:
        return GroundingRewriteOutcome(
            state=GROUNDING_REWRITE_STATE_FORMAT_FAILED,
            shapes=(EMPTY_RECOVERY_SHAPE_NO_TARGET,),
        )
    requested = frozenset(target.number for target in sent_targets)
    sent = tuple(target.number for target in sent_targets)
    shapes: list[str] = []
    attempts = 0
    rewritten: dict[int, str] = {}
    abandoned = 0
    while True:
        attempts += 1
        answer = _ask_once(
            ask, prompt if attempts == 1 else prompt + GROUNDING_REWRITE_RETRY_GUIDE,
        )
        rewritten, abandoned, shape = _read_rewrites(
            extract_json_payload(answer or ""), requested,
        )
        shapes.append(shape)
        if shape == EMPTY_RECOVERY_SHAPE_CONTRACT or attempts > PARSE_RETRY_LIMIT:
            break
    if shapes[-1] != EMPTY_RECOVERY_SHAPE_CONTRACT:
        logger.warning(
            "근거 결속 재작성 응답을 %d번 다 읽지 못했다 — 대상 %d개를 제거한다",
            attempts, len(targets),
        )
        return GroundingRewriteOutcome(
            sent=sent,
            shapes=tuple(shapes),
            state=GROUNDING_REWRITE_STATE_FORMAT_FAILED,
        )
    return GroundingRewriteOutcome(
        rewritten=rewritten,
        sent=sent,
        abandoned=abandoned,
        shapes=tuple(shapes),
        state=GROUNDING_REWRITE_STATE_DONE,
    )


def _ask_once(ask: AskFn, prompt: str) -> str | None:
    """작가를 한 번 부른다. 호출이 죽으면 None — 요청 전역 장애만 올려 보낸다.

    `verify._safe_ask` 와 같은 규칙이다. 그 함수를 쓰지 않는 이유는 순환 import다
    (`verify` 가 이 모듈을 부른다).
    """

    try:
        return str(ask(prompt))
    except AskFatalError:
        raise
    except Exception:  # noqa: BLE001 - 다듬기 호출 실패는 «고쳐 쓰지 못함»이다
        logger.warning("근거 결속 재작성 호출이 실패했다 — 대상을 제거한다")
        return None


__all__ = [
    "GroundingRewriteOutcome",
    "GroundingRewriteTarget",
    "build_grounding_rewrite_prompt",
    "rewrite_grounding_rejected",
]
