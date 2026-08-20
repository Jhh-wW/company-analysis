"""canonical 본문에서 숫자 없는 핵심 결론 3~5개를 만든다.

요약 AI는 새 사실을 쓸 수 없고, 본문 섹션 ID와 문장만 답한다. 결과는 별도
근거 대조 AI가 해당 섹션 전체와 다시 비교하며, 규칙을 통과한 문장만 남는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

from src.features.writer.logic import Evidence, Sentence
from src.features.writer.verify import verify_with_ai


SUMMARY_MIN = 3
SUMMARY_MAX = 5
SUMMARY_MAX_TOKENS = 900
SUMMARY_STEP = "12_핵심요약"
SUMMARY_VERIFY_STEP = "12_핵심요약_근거대조"

_NUMBER_OR_CITATION_RE = re.compile(r"\d|\[[0-9]+\]|〔[^〕]+〕")
_META_OR_SCOPE_MARKERS = (
    "AI",
    "인공지능",
    "레퍼런스",
    "검증 절차",
    "정리했다",
    "제시한다",
    "자기소개서",
    "면접 답변",
    "지원 직무",
    "KPI",
    "급여",
    "근속",
)


@dataclass(frozen=True)
class SummaryDraft:
    section_id: str
    text: str


def _prompt(company: str, sections: dict[str, list[str]]) -> str:
    body = []
    for section_id, lines in sections.items():
        body.append(f"\n[{section_id}]\n" + "\n".join(f"- {line}" for line in lines))
    return (
        "취업준비생용 회사분석 보고서의 첫 장 핵심 요약을 만든다.\n"
        f"회사: {company}\n"
        "아래 본문에서 반드시 알아야 할 회사 고유 결론 3~5개를 고른다.\n"
        "규칙:\n"
        "1. 본문에 없는 사실·원인·평가를 추가하지 않는다.\n"
        "2. 숫자·날짜·구체 사례를 반복하지 않고 결론만 한 문장으로 쓴다.\n"
        "3. 각 결론은 근거가 있는 section_id 하나만 가리킨다.\n"
        "4. 같은 섹션·같은 결론을 반복하지 않는다.\n"
        "5. 작성·검증 방법, 직무·자소서·면접 조언은 쓰지 않는다.\n"
        + "".join(body)
    )


def _schema(section_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": SUMMARY_MIN,
                "maxItems": SUMMARY_MAX,
                "items": {
                    "type": "object",
                    "properties": {
                        "section_id": {"type": "string", "enum": section_ids},
                        "text": {"type": "string"},
                    },
                    "required": ["section_id", "text"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }


def _apply(payload: object, section_ids: set[str]) -> list[SummaryDraft]:
    if not isinstance(payload, dict):
        return []
    out: list[SummaryDraft] = []
    used_sections: set[str] = set()
    used_text: set[str] = set()
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        section_id = str(item.get("section_id") or "").strip()
        text = " ".join(str(item.get("text") or "").split())
        if (
            section_id not in section_ids
            or section_id in used_sections
            or not text
            or text in used_text
            or _NUMBER_OR_CITATION_RE.search(text)
            or any(marker in text for marker in _META_OR_SCOPE_MARKERS)
        ):
            continue
        used_sections.add(section_id)
        used_text.add(text)
        out.append(SummaryDraft(section_id=section_id, text=text))
        if len(out) >= SUMMARY_MAX:
            break
    return out


def build_summary_with_ai(
    ask: Callable[[str, dict[str, Any], int], tuple[Optional[dict[str, Any]], dict[str, Any]]],
    *,
    company: str,
    sections: dict[str, list[str]],
) -> tuple[list[SummaryDraft], list[dict[str, Any]]]:
    """요약 작성과 독립 근거 대조를 실행한다. 3개 미만이면 빈 목록이다."""

    usable = {
        section_id: [line for line in lines if line.strip()]
        for section_id, lines in sections.items()
        if any(line.strip() for line in lines)
    }
    if len(usable) < SUMMARY_MIN:
        return [], [{"step": SUMMARY_STEP, "비고": "요약 가능한 본문 섹션 부족"}]

    payload, usage = ask(
        _prompt(company, usable),
        _schema(list(usable)),
        SUMMARY_MAX_TOKENS,
    )
    drafts = _apply(payload, set(usable))
    steps: list[dict[str, Any]] = [
        {
            "step": SUMMARY_STEP,
            "후보": len((payload or {}).get("items", [])) if isinstance(payload, dict) else 0,
            "규칙통과": len(drafts),
            "usage": usage,
        }
    ]
    if len(drafts) < SUMMARY_MIN:
        return [], steps

    evidence = {
        section_id: [
            Evidence(
                sid=f"summary-{section_id}",
                text=" ".join(lines),
                cite=section_id,
            )
        ]
        for section_id, lines in usable.items()
    }
    written = {
        draft.section_id: [
            Sentence(text=draft.text, sid=f"summary-{draft.section_id}")
        ]
        for draft in drafts
    }

    def verify_ask(prompt: str, schema: dict[str, Any]):
        return ask(prompt, schema, 1200)

    passed, verify_step = verify_with_ai(
        verify_ask,
        written=written,
        evidence=evidence,
    )
    steps.append({"step": SUMMARY_VERIFY_STEP, **verify_step})
    verified = [
        draft
        for draft in drafts
        if passed.get(draft.section_id)
        and any(sentence.text == draft.text for sentence in passed[draft.section_id])
    ]
    return (verified if len(verified) >= SUMMARY_MIN else []), steps
