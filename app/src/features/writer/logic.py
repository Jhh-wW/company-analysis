"""근거를 «하나의 글»로 잇는다 (문제로그 P-110).

★ 이 파일은 «쓰기»만 한다. **거짓말을 막는 것은 `verify.py`다.** 둘은 한 벌이다.
  여기만 쓰고 검증을 안 붙이면 **지어낸 문장이 그대로 화면에 나간다.**

★ 대부분이 순수 함수다. AI를 부르는 것은 `write_with_ai` 하나뿐이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from src.features.writer.constants import (
    CELL_GUIDE,
    EVIDENCE_CHARS,
    MAX_SENTENCES_PER_CELL,
    MIN_EVIDENCE_PER_CELL,
    PROMPT_CELL_HEAD,
    PROMPT_CONNECT,
    PROMPT_HEADER,
    PROMPT_RULES,
    PROMPT_TAIL,
    WRITTEN_CELLS,
)


@dataclass(frozen=True)
class Evidence:
    """작가에게 주는 근거 한 줄. **원문 그대로**다."""

    sid: str        #: 근거 번호 — 작가가 이 번호로 가리킨다
    text: str       #: 원문 문장
    cite: str       #: 출처 표기 (「조각 10·신규사업전망」 같은 것)

    @property
    def is_news(self) -> bool:
        """기사에서 온 근거인가 — 「방향 + 증거」로 이으라고 알려 줄 때 쓴다."""
        return "뉴스" in (self.cite or "")


@dataclass(frozen=True)
class Sentence:
    """작가가 쓴 문장 하나."""

    text: str       #: 작가가 쓴 글
    sid: str        #: 이 문장이 기댄 근거 번호


def collect_evidence(
    lines_by_cell: dict[str, list[tuple[str, str]]],
    cells: tuple[str, ...] = WRITTEN_CELLS,
) -> dict[str, list[Evidence]]:
    """칸별 원문 문장을 «근거 번호가 달린» 모양으로 바꾼다.

    Args:
        lines_by_cell: {칸: [(문장, 출처)…]} — 지금 보고서에 들어가는 원문들.
        cells: 작가에게 맡길 칸.

    Returns:
        {칸: [근거…]}. 근거가 모자란 칸은 **아예 안 담는다.**

    ★ 번호는 «칸-순번»이다 (예: `4-3-2`). 칸 이름에 이미 붙임표가 있어
      보기엔 낯설지만, 작가가 그대로 돌려주기만 하면 되므로 문제되지 않는다.
    """
    out: dict[str, list[Evidence]] = {}
    for cell in cells:
        # ★ 출처가 빈 원문은 작가에게도 주지 않는다. 사실 문장을 새로 쓴 뒤
        #   화면에 출처를 못 붙이면 W1과 같은 사고가 되기 때문이다(P-118).
        lines = [
            (t, c)
            for t, c in lines_by_cell.get(cell, [])
            if (t or "").strip() and (c or "").strip()
        ]
        if len(lines) < MIN_EVIDENCE_PER_CELL:
            continue
        out[cell] = [
            Evidence(sid=f"{cell}-{i}", text=text, cite=cite)
            for i, (text, cite) in enumerate(lines, start=1)
        ]
    return out


def build_prompt(
    company: str,
    job: str,
    evidence: dict[str, list[Evidence]],
    limit: int = MAX_SENTENCES_PER_CELL,
) -> str:
    """작가에게 줄 지시문."""
    부분 = [PROMPT_HEADER.format(company=company, job=job), PROMPT_RULES]
    부분.append(PROMPT_CONNECT.format(company=company))
    부분.append(PROMPT_CELL_HEAD)
    for cell, items in evidence.items():
        안내 = CELL_GUIDE.get(cell, "")
        부분.append(f"\n[{cell}] {안내}\n")
        for e in items:
            표 = "기사" if e.is_news else "공시·홈페이지"
            부분.append(f"  ({e.sid}) [{표}] {e.text[:EVIDENCE_CHARS]}\n")
    부분.append(PROMPT_TAIL.format(limit=limit))
    return "".join(부분)


def answer_schema(cells: list[str]) -> dict[str, Any]:
    """작가 답의 모양.

    ★ **근거 번호가 «필수»다.** 스키마에서 required로 못 박아
      「근거 없는 문장」이 애초에 만들어질 수 없게 한다.
    """
    return {
        "type": "object",
        "properties": {
            "칸": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "칸번호": {"type": "string", "enum": cells},
                        "문장들": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "글": {"type": "string"},
                                    "근거": {"type": "string"},
                                },
                                "required": ["글", "근거"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["칸번호", "문장들"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["칸"],
        "additionalProperties": False,
    }


def apply_written(
    payload: Optional[dict[str, Any]],
    evidence: dict[str, list[Evidence]],
    limit: int = MAX_SENTENCES_PER_CELL,
) -> tuple[dict[str, list[Sentence]], dict[str, int]]:
    """작가 답을 받아 «쓸 수 있는 것만» 남긴다.

    Args:
        payload: 작가 답. `None`이면 호출 실패.
        evidence: 작가에게 준 근거.
        limit: 칸당 문장 상한.

    Returns:
        ({칸: [문장…]}, 버린 사유별 개수).

    ★ **모르는 근거 번호를 대면 버린다.** 근거를 가리키지 못하는 문장은
      뒤에서 대조할 수 없고, 대조 못 하는 문장은 지어낸 것과 구별이 안 된다.
    ⚠️ 여기를 통과했다고 «사실»인 것은 아니다. 사실 판정은 `verify.py`가 한다.
    """
    버림 = {"모르는근거": 0, "빈글": 0, "상한초과": 0}
    if not payload:
        return {}, 버림
    out: dict[str, list[Sentence]] = {}
    for 칸 in payload.get("칸") or []:
        번호 = 칸.get("칸번호")
        if 번호 not in evidence:
            버림["모르는근거"] += 1
            continue
        # ★ 전역 번호 목록으로 검사하면 1번 문장이 4-3 근거를 대도 통과한다.
        #   사실이어도 잘못된 항목에 놓인 문장이므로 «그 칸의 번호»만 허용한다.
        아는번호 = {e.sid for e in evidence[번호]}
        문장들: list[Sentence] = []
        for 항목 in 칸.get("문장들") or []:
            글 = (항목.get("글") or "").strip()
            근거 = (항목.get("근거") or "").strip()
            if not 글:
                버림["빈글"] += 1
                continue
            if 근거 not in 아는번호:
                버림["모르는근거"] += 1
                continue
            if len(문장들) >= limit:
                버림["상한초과"] += 1
                continue
            문장들.append(Sentence(text=글, sid=근거))
        if 문장들:
            out[번호] = 문장들
    return out, 버림


def write_with_ai(
    ask: Callable[[str, dict[str, Any]], tuple[Optional[dict[str, Any]], dict[str, Any]]],
    *,
    company: str,
    job: str,
    evidence: dict[str, list[Evidence]],
    limit: int = MAX_SENTENCES_PER_CELL,
) -> tuple[dict[str, list[Sentence]], dict[str, Any]]:
    """근거를 주고 글을 받아 온다.

    ★ 근거가 하나도 없으면 **AI를 안 부른다** — 돈이 나가기 때문이다.
    """
    if not evidence:
        return {}, {"칸수": 0, "비고": "근거 없음 — AI 호출 안 함"}
    payload, usage = ask(
        build_prompt(company, job, evidence, limit), answer_schema(list(evidence))
    )
    written, 버림 = apply_written(payload, evidence, limit)
    기록: dict[str, Any] = {
        "칸수": len(evidence),
        "쓴칸": len(written),
        "쓴문장": sum(len(v) for v in written.values()),
        "버림": {k: v for k, v in 버림.items() if v},
        "usage": usage,
    }
    if payload is None:
        기록["오류"] = usage.get("error", "AI 답 없음")
    return written, 기록


def to_cited_lines(
    sentences: list[Sentence], evidence: list[Evidence]
) -> list[tuple[str, str]]:
    """검증된 작가 문장을 `(글, 실제 출처)`로 되돌린다.

    ★ 작가가 쓰는 `sid`는 대조용 내부 번호다. 화면에 그대로 내보내면 사용자가
      출처 목록에서 찾을 수 없다. `sid`가 가리킨 원문의 `cite`로 되돌려야 한다.
    ★ 이 칸의 근거에서 번호를 못 찾으면 버린다. 다른 칸 번호나 모르는 번호가
      검증 단계를 우연히 통과해도 잘못된 칸에 표시하지 않는 마지막 안전핀이다.
    """
    출처표 = {
        item.sid: item.cite.strip()
        for item in evidence
        if (item.cite or "").strip()
    }
    out: list[tuple[str, str]] = []
    for sentence in sentences:
        text = sentence.text.strip()
        if not text or sentence.sid not in 출처표:
            continue
        out.append((text, 출처표[sentence.sid]))
    return out
