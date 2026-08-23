"""canonical 본문에서 검증 완료 핵심 결론 3~5개를 고른다.

실제 출고 경로는 Reviewer 또는 원문 완전일치 코드 검증을 통과한 문장을 그대로
고른다. 새 문장을 만들지 않으므로 요약 작성·재검수 AI를 다시 부를 필요가 없다.
기존 AI 함수는 과거 저장본과 단위 테스트 호환을 위해 남겨 둔다.
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
_NUMBER_RE = re.compile(r"\d|[%％]")
_CITATION_RE = re.compile(r"\[[0-9]+\]|〔[^〕]+〕")
_META_OR_SCOPE_MARKERS = (
    "AI",
    "인공지능",
    "레퍼런스",
    "검증 절차",
    "정리했다",
    "자기소개서",
    "면접 답변",
    "지원 직무",
    "KPI",
    "급여",
    "근속",
)
_DETERMINISTIC_META_MARKERS = tuple(
    marker for marker in _META_OR_SCOPE_MARKERS if marker not in {"AI", "인공지능"}
)


@dataclass(frozen=True)
class SummaryDraft:
    section_id: str
    text: str
    fact_ids: tuple[str, ...] = ()
    support_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class VerifiedSummarySource:
    """Reviewer 또는 원문 완전일치 검증을 통과한 요약 후보의 최소 계약."""

    section_id: str
    text: str
    fact_id: str
    support_terms: tuple[str, ...] = ()


def build_summary_from_verified_claims(
    candidates: list[VerifiedSummarySource],
) -> tuple[list[SummaryDraft], list[dict[str, Any]]]:
    """검수된 본문 문장을 바꾸지 않고 최대 한 장당 한 문장만 고른다.

    각 장에서는 숫자 없는 문장을 먼저 고르되, 검증 후보가 숫자 문장뿐이면 내용을
    바꾸지 않고 그대로 허용한다. 제작 메타·각주는 제외하고, 근거어는 후보에 이미
    결속된 것 중 문장에도 나타나는 서로 다른 두 개 이상만 사용한다. 의미를 추측하거나
    문장을 다시 쓰는 단계가 없으므로 이 함수의 AI 호출 수는 항상 0이다.
    """

    out: list[SummaryDraft] = []
    used_text: set[str] = set()
    section_order: list[str] = []
    by_section: dict[str, list[VerifiedSummarySource]] = {}
    for candidate in candidates:
        section_id = str(candidate.section_id or "").strip()
        if not section_id:
            continue
        if section_id not in by_section:
            section_order.append(section_id)
            by_section[section_id] = []
        by_section[section_id].append(candidate)

    # 장 우선순위는 호출자가 정한다. 같은 장에서는 숫자 없는 문장을 먼저 고르되,
    # 전부 숫자를 포함해도 검증된 본문을 그대로 쓸 수 있으므로 장 전체를 버리지 않는다.
    for section_id in section_order:
        section_candidates = sorted(
            enumerate(by_section[section_id]),
            key=lambda item: (
                _NUMBER_RE.search(" ".join(str(item[1].text or "").split()))
                is not None,
                item[0],
            ),
        )
        for _original_index, candidate in section_candidates:
            text = " ".join(str(candidate.text or "").split())
            fact_id = str(candidate.fact_id or "").strip()
            if (
                not text
                or text in used_text
                or not fact_id
                or _CITATION_RE.search(text)
                or any(marker in text for marker in _DETERMINISTIC_META_MARKERS)
            ):
                continue
            support_terms: list[str] = []
            seen_terms: set[str] = set()
            for raw_term in candidate.support_terms:
                term = " ".join(str(raw_term or "").split())
                normalized = term.casefold()
                if (
                    not term
                    or len(term.replace(" ", "")) < 2
                    or normalized in seen_terms
                    or normalized not in text.casefold()
                ):
                    continue
                seen_terms.add(normalized)
                support_terms.append(term)
            if len(support_terms) < 2:
                continue
            used_text.add(text)
            out.append(
                SummaryDraft(
                    section_id=section_id,
                    text=text,
                    fact_ids=(fact_id,),
                    support_terms=tuple(support_terms),
                )
            )
            break
        if len(out) >= SUMMARY_MAX:
            break

    steps = [
        {
            "step": SUMMARY_STEP,
            "방식": "검증 완료 본문 재사용",
            "후보": len(candidates),
            "규칙통과": len(out),
            "AI호출": 0,
        }
    ]
    return (out if len(out) >= SUMMARY_MIN else []), steps


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
