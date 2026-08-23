"""검수 AI가 지적한 문장만 한 번 고쳐 쓰고 새 검수 AI로 재검사한다.

이 모듈은 품질과 비용을 같이 지킨다.

* 첫 검수 AI를 통과한 문장은 다시 AI에게 보내지 않는다.
* 명시적으로 실패한 문장만 하나의 배치로 재작성한다.
* 재작성문이 원문과 완전일치하면 코드로 확정하고, 나머지만 별도·무문맥
  검수 AI 호출로 다시 검사한다.
* 두 번째 검사도 실패하면 재시도 없이 삭제한다.
* 재작성 대상이 없으면 추가 AI 호출은 0회다.

숫자·날짜·법인·상태의 최종 판정은 이 모듈이 아니라 보고서
조립 단계의 기존 결정론 검사가 계속 가진다. AI Reviewer 통과는 그 검사를
우회할 수 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from src.features.writer.logic import Evidence, Sentence
from src.features.writer.verify import (
    VERIFY_STEP,
    Pair,
    make_pairs,
    verify_pairs_with_ai,
)


REWRITE_MAX_TOKENS: int = 1200
REWRITE_STEP: str = "11_작성_실패문장_1회재작성"
REVERIFY_STEP: str = "11_작성_재작성_새근거대조"
EVIDENCE_CHARS: int = 500

PROMPT_HEADER: str = (
    "아래는 독립 검수 AI가 근거 불일치로 통과시키지 않은 보고서 문장이다.\n"
    "각 문장을 **해당 근거 원문 안의 내용만** 사용해 한 번 고쳐 써라.\n"
)
PROMPT_RULES: str = (
    "\n■ 반드시 지킬 것\n"
    "1. 근거에 없는 사실·해석·인과·전망을 추가하지 마라.\n"
    "2. 숫자·날짜·고유명사·확정/잠정/계획 상태를 원문 그대로 유지하라.\n"
    "3. 번호마다 문장 하나만 답하라. 두 근거를 합치지 마라.\n"
    "4. 근거 안에서 안전하게 고칠 수 없으면 그 번호를 답에서 빼라.\n"
    "5. 이전 작가나 검수 AI의 지식을 사용하지 말고 오직 아래 원문만 보라.\n"
)


@dataclass(frozen=True)
class Revision:
    """초기 실패 자리와 재작성 문장의 연결."""

    original: Pair
    sentence: Sentence


def build_rewrite_prompt(pairs: list[Pair]) -> str:
    """실패 문장과 원문만 새 작가 호출에 싣는다."""
    parts = [PROMPT_HEADER, PROMPT_RULES, "\n■ 고쳐 쓸 문장\n"]
    for pair in pairs:
        parts.append(
            f"\n[{pair.number}]\n"
            f"  근거 원문: {pair.evidence.text[:EVIDENCE_CHARS]}\n"
            f"  기존 문장: {pair.sentence.text}\n"
        )
    parts.append("\n고칠 수 있는 번호만 번호와 새 문장을 답하라.\n")
    return "".join(parts)


def rewrite_answer_schema(pairs: list[Pair]) -> dict[str, Any]:
    """재작성 답은 실패 번호와 새 문장만 받는다."""
    return {
        "type": "object",
        "properties": {
            "재작성": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "번호": {
                            "type": "integer",
                            "enum": [pair.number for pair in pairs],
                        },
                        "글": {"type": "string"},
                    },
                    "required": ["번호", "글"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["재작성"],
        "additionalProperties": False,
    }


def apply_rewrites(payload: object, pairs: list[Pair]) -> tuple[list[Revision], dict[str, int]]:
    """유효한 실패 번호만 원래 ``sid``와 같이 복원한다.

    AI가 근거 번호를 바꾸거나 한 문장에 여러 번 답해도 원래
    ``Pair``가 가리키던 fact/evidence 연결을 바꾸지 못한다.
    """
    pair_by_number = {pair.number: pair for pair in pairs}
    items = payload.get("재작성") if isinstance(payload, dict) else []
    if not isinstance(items, list):
        items = []
    seen: set[int] = set()
    duplicate: set[int] = set()
    candidates: dict[int, str] = {}
    discarded = {"모르는번호": 0, "빈글": 0, "중복": 0}
    for item in items:
        if not isinstance(item, dict):
            discarded["모르는번호"] += 1
            continue
        number = item.get("번호")
        text = (item.get("글") or "").strip() if isinstance(item.get("글"), str) else ""
        if not isinstance(number, int) or isinstance(number, bool) or number not in pair_by_number:
            discarded["모르는번호"] += 1
            continue
        if number in seen:
            duplicate.add(number)
            discarded["중복"] += 1
            continue
        seen.add(number)
        if not text:
            discarded["빈글"] += 1
            continue
        candidates[number] = text
    for number in duplicate:
        candidates.pop(number, None)

    revisions: list[Revision] = []
    for pair in pairs:
        text = candidates.get(pair.number)
        if text:
            revisions.append(
                Revision(
                    original=pair,
                    sentence=Sentence(text=text, sid=pair.sentence.sid),
                )
            )
    return revisions, discarded


def rewrite_failed_with_ai(
    ask: Callable[[str, dict[str, Any]], tuple[Optional[dict[str, Any]], dict[str, Any]]],
    *,
    pairs: list[Pair],
) -> tuple[list[Revision], dict[str, Any]]:
    """명시적으로 실패한 문장만 한 번, 한 배치로 고친다."""
    if not pairs:
        return [], {"대상": 0, "비고": "재작성 대상 없음 — AI 호출 안 함"}
    payload, usage = ask(build_rewrite_prompt(pairs), rewrite_answer_schema(pairs))
    revisions, discarded = apply_rewrites(payload, pairs)
    record: dict[str, Any] = {
        "대상": len(pairs),
        "재작성": len(revisions),
        "버림": {key: value for key, value in discarded.items() if value},
        "usage": usage,
    }
    if payload is None:
        record["오류"] = usage.get("error", "AI 답 없음")
    return revisions, record


def review_with_single_rewrite(
    initial_review_ask: Callable[
        [str, dict[str, Any]], tuple[Optional[dict[str, Any]], dict[str, Any]]
    ],
    rewrite_ask: Callable[
        [str, dict[str, Any]], tuple[Optional[dict[str, Any]], dict[str, Any]]
    ],
    retry_review_ask: Callable[
        [str, dict[str, Any]], tuple[Optional[dict[str, Any]], dict[str, Any]]
    ],
    *,
    written: dict[str, list[Sentence]],
    evidence: dict[str, list[Evidence]],
) -> tuple[dict[str, list[Sentence]], list[dict[str, Any]]]:
    """검수 AI → 실패만 1회 재작성 → 코드 확정 또는 새 검수 AI 흐름.

    세 호출 함수는 대화 상태를 공유하지 않는 각각의 단발 호출이어야
    한다. 통과 문장은 초기 목록에서 그대로 복원되며, 재작성과
    두 번째 검사의 프롬프트에 실리지 않는다. 재작성문도 원문과 완전일치하면
    코드가 확정하므로 두 번째 Reviewer에는 나머지만 전달된다.
    """
    initial_pairs = make_pairs(written, evidence)
    passed, initial_rejected, retryable, initial_record = verify_pairs_with_ai(
        initial_review_ask,
        pairs=initial_pairs,
    )
    steps: list[dict[str, Any]] = [{"step": VERIFY_STEP, **initial_record}]

    # ★ 모두 통과했거나 검수 AI 답이 깨졌다면 추가 비용을 쓰지 않는다.
    # 깨진 판정은 안전상 삭제되지만 «고쳐 쓸 콘텐츠»로 보지 않는다.
    if not retryable:
        return passed, steps

    revisions, rewrite_record = rewrite_failed_with_ai(rewrite_ask, pairs=retryable)
    steps.append({"step": REWRITE_STEP, **rewrite_record})
    if not revisions:
        return passed, steps

    rewritten_by_cell: dict[str, list[Sentence]] = {}
    for revision in revisions:
        rewritten_by_cell.setdefault(revision.original.cell, []).append(revision.sentence)
    revised_pairs = make_pairs(rewritten_by_cell, evidence)
    revised_passed, _, _, reverify_record = verify_pairs_with_ai(
        retry_review_ask,
        pairs=revised_pairs,
    )
    steps.append({"step": REVERIFY_STEP, **reverify_record})

    initially_rejected_numbers = {pair.number for pair in initial_rejected}
    retryable_numbers = {pair.number for pair in retryable}
    passed_initial_numbers = {
        pair.number for pair in initial_pairs if pair.number not in initially_rejected_numbers
    }
    passed_revision_ids = {
        id(sentence) for values in revised_passed.values() for sentence in values
    }
    revision_by_original_number = {
        revision.original.number: revision for revision in revisions
    }

    final: dict[str, list[Sentence]] = {}
    for pair in initial_pairs:
        chosen: Sentence | None = None
        if pair.number in passed_initial_numbers:
            chosen = pair.sentence
        elif pair.number in retryable_numbers:
            revision = revision_by_original_number.get(pair.number)
            if revision is not None and id(revision.sentence) in passed_revision_ids:
                chosen = revision.sentence
        if chosen is not None:
            final.setdefault(pair.cell, []).append(chosen)
    return final, steps
