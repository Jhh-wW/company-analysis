"""작가가 쓴 문장을 «근거와 대조»한다 (문제로그 P-110).

★★ **이것이 작가 AI를 붙일 수 있게 하는 유일한 장치다.**
  작가만 붙이고 이걸 안 붙이면 지어낸 문장이 그대로 화면에 나간다.

★ 왜 기존 검사로 안 되나 —
  | 기존 검사 | 보는 것 | 작가 글에 쓰면 |
  |---|---|---|
  | W3 원문 대조 | 글자 그대로 있는가 | **전부 지운다** (작가는 새로 쓰니까) |
  | ①-b 알맹이 검사 | 일반론인가 | **원본을 안 줘서** 거짓말을 못 잡는다 |

  → 여기서는 **문장과 근거를 «나란히» 준다.** 그것이 차이의 전부다.
    2026-08-16 사용자 선택: 「근거 대조 검증」.

★ **작가와 다른 호출·다른 지시문**이다 (정본 Generator/Evaluator 분리).
  같은 대화에서 이어 물으면 «자기가 쓴 것»을 감싸게 된다.

⚠️ **의심스러우면 버린다.** 어색한 보고서보다 «틀린 보고서»가 훨씬 나쁘다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from src.features.writer.logic import Evidence, Sentence

# ══════════════════════════════════════════════════════════
# 값
# ══════════════════════════════════════════════════════════

#: 답 길이 상한. 판정은 참/거짓이라 짧다.
VERIFY_MAX_TOKENS: int = 1200

#: 단계 기록 이름.
VERIFY_STEP: str = "11_작성_근거대조"

#: 근거 원문을 지시문에 실을 때 자르는 길이.
#: ⚠️ 자르면 «근거에 있는데 없다»고 잘못 판정한다. 작가에게 준 것보다 짧으면 안 된다.
EVIDENCE_CHARS: int = 500

#: ★ 답이 없거나 깨졌을 때의 기본 판정.
#: **False(버림)로 둔다** — 검사가 죽었는데 통과시키면 검사가 없는 것과 같다.
DEFAULT_VERDICT: bool = False

PROMPT_HEADER: str = (
    "아래는 어떤 보고서의 문장과, 그 문장이 기댔다는 **근거 원문**이다.\n"
    "문장마다 판정하라: **이 문장의 내용이 근거 원문 안에 있는가?**\n"
)

#: ★ 이 규칙이 헐거우면 검사가 있으나 마나가 된다.
PROMPT_RULES: str = (
    "\n■ 판정 규칙\n"
    "1. 근거에 **없는 정보가 한 조각이라도** 들어 있으면 **거짓**이다.\n"
    "2. **숫자·연도·고유명사가 근거와 다르면 거짓**이다. 반올림·환산도 거짓이다.\n"
    "3. 근거를 «요약»하거나 «쉬운 말로 바꾼» 것은 **참**이다. 뜻이 같으면 된다.\n"
    "4. 근거에 없는 **원인·결과·전망을 덧붙였으면 거짓**이다. "
    "(예: 근거는 「매출이 줄었다」인데 문장이 「경쟁 심화로 매출이 줄었다」면 거짓)\n"
    "5. ★ **애매하면 거짓으로 판정하라.** "
    "틀린 문장이 나가는 것이 문장이 빠지는 것보다 훨씬 나쁘다.\n"
    "6. 당신이 이 회사에 대해 «따로 아는 것»으로 판단하지 마라. "
    "**오직 아래 근거 원문만** 보고 판단하라.\n"
)

PROMPT_LIST_HEAD: str = "\n■ 대조할 것\n"

PROMPT_TAIL: str = "\n번호마다 참/거짓을 답하라.\n"


@dataclass(frozen=True)
class Pair:
    """대조 한 쌍 — 작가가 쓴 문장과 그 근거."""

    number: int
    cell: str
    sentence: Sentence
    evidence: Evidence


# ══════════════════════════════════════════════════════════
# ① 대조표 만들기
# ══════════════════════════════════════════════════════════


def make_pairs(
    written: dict[str, list[Sentence]], evidence: dict[str, list[Evidence]]
) -> list[Pair]:
    """작가 문장에 근거 원문을 붙여 대조표를 만든다.

    ★ 근거를 못 찾은 문장은 **표에 넣지 않고 버린다** (부르는 쪽이 버린다) —
      대조할 수 없는 문장은 지어낸 것과 구별되지 않는다.
    """
    out: list[Pair] = []
    for cell, sentences in written.items():
        # ★ 전역 번호표를 쓰면 다른 칸의 근거 번호도 대조표에 들어갈 수 있다.
        #   앞 단계가 한 번 막아도 검증 자체가 같은 안전선을 가져야 한다.
        번호표 = {e.sid: e for e in evidence.get(cell, [])}
        for s in sentences:
            근거 = 번호표.get(s.sid)
            if 근거 is None:
                continue
            out.append(Pair(len(out) + 1, cell, s, 근거))
    return out


def build_prompt(pairs: list[Pair]) -> str:
    """대조 지시문. **문장과 근거를 나란히** 놓는 것이 핵심이다."""
    부분 = [PROMPT_HEADER, PROMPT_RULES, PROMPT_LIST_HEAD]
    for p in pairs:
        부분.append(
            f"\n[{p.number}]\n"
            f"  근거 원문: {p.evidence.text[:EVIDENCE_CHARS]}\n"
            f"  문장    : {p.sentence.text}\n"
        )
    부분.append(PROMPT_TAIL)
    return "".join(부분)


def answer_schema(pairs: list[Pair]) -> dict[str, Any]:
    """대조 답의 모양 — 번호와 참/거짓뿐이다."""
    return {
        "type": "object",
        "properties": {
            "판정": {
                "type": "array",
                "minItems": len(pairs),
                "maxItems": len(pairs),
                "items": {
                    "type": "object",
                    "properties": {
                        "번호": {
                            "type": "integer",
                            "enum": [pair.number for pair in pairs],
                        },
                        "근거에있다": {"type": "boolean"},
                    },
                    "required": ["번호", "근거에있다"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["판정"],
        "additionalProperties": False,
    }


# ══════════════════════════════════════════════════════════
# ② 판정 적용
# ══════════════════════════════════════════════════════════


def apply_verdicts(
    pairs: list[Pair], payload: object
) -> tuple[dict[str, list[Sentence]], list[Pair]]:
    """판정을 적용해 «통과한 문장만» 남긴다.

    Args:
        pairs: 대조표.
        payload: 대조 답. `None`이나 딕셔너리가 아닌 값이면 검사가 죽거나 답이 깨진 것이다.

    Returns:
        ({칸: [남은 문장…]}, 버려진 쌍들).

    ★★ **답이 없으면 전부 버린다.** 검사가 죽었는데 통과시키면
      검사가 없는 것과 같고, 그때 나가는 것은 «검증됐다고 표시된 거짓말»이다.
    ★ 판정에 안 실린 번호도 버린다 — 같은 이유다.
    """
    남은, 버린, _ = apply_verdicts_detailed(pairs, payload)
    return 남은, 버린


def apply_verdicts_detailed(
    pairs: list[Pair], payload: object
) -> tuple[dict[str, list[Sentence]], list[Pair], list[Pair]]:
    """판정을 적용하고 «명시적으로 거짓 판정된 문장»을 따로 돌려준다.

    세 번째 값은 검수 AI가 정상적인 boolean ``False``로 답한
    문장만 담는다. 답이 없거나 깨진 문장은 안전을 위해 버리지만,
    장애를 «고쳐 쓸 문장»으로 오인해 비싼 추가 AI 호출을 하지 않는다.

    Returns:
        (검수 AI 통과, 전체 버림, 1회 재작성 가능 대상).
    """
    # ★ 깨진 전체 답도 예외를 내지 않고 «모두 거짓»으로 닫는다. 검증 예외를
    #   바깥에서 잡아 원문으로 복귀하더라도, 이 순수 함수 자체가 같은 약속을 지킨다.
    판정목록 = payload.get("판정") if isinstance(payload, dict) else []
    if not isinstance(판정목록, list):
        판정목록 = []
    판정표: dict[int, bool] = {}
    중복번호: set[int] = set()
    명시거짓: set[int] = set()
    for item in 판정목록:
        if not isinstance(item, dict):
            continue
        번호 = item.get("번호")
        판정 = item.get("근거에있다")
        # bool은 int의 하위 타입이다. 번호 `True`도 깨진 답이므로 받지 않는다.
        if not isinstance(번호, int) or isinstance(번호, bool):
            continue
        if 번호 in 판정표:
            중복번호.add(번호)
        # `"false"`·1처럼 그럴듯한 값은 불리언이 아니다. 실제 True만 통과한다.
        판정표[번호] = 판정 is True
        if 판정 is False:
            명시거짓.add(번호)
    for 번호 in 중복번호:
        판정표[번호] = DEFAULT_VERDICT
        명시거짓.discard(번호)
    남은: dict[str, list[Sentence]] = {}
    버린: list[Pair] = []
    재작성가능: list[Pair] = []
    for p in pairs:
        if 판정표.get(p.number, DEFAULT_VERDICT):
            남은.setdefault(p.cell, []).append(p.sentence)
        else:
            버린.append(p)
            # 정상적인 False와 판정 누락·중복·형식 파손을 구분한다.
            # 후자는 검수 AI 장애이므로 재작성해도 품질이 나아진다고 볼 수 없다.
            if p.number in 명시거짓:
                재작성가능.append(p)
    return 남은, 버린, 재작성가능


def verify_pairs_with_ai(
    ask: Callable[[str, dict[str, Any]], tuple[Optional[dict[str, Any]], dict[str, Any]]],
    *,
    pairs: list[Pair],
) -> tuple[dict[str, list[Sentence]], list[Pair], list[Pair], dict[str, Any]]:
    """이미 만든 대조 쌍을 별도·무문맥 검수 AI 호출로 판정한다.

    재작성 흐름은 초기 대조의 ``Pair.number``를 유지해야 하므로
    ``verify_with_ai``와 같은 안전 규칙을 쌍 단위로 재사용한다.
    """
    if not pairs:
        return {}, [], [], {
            "대조": 0,
            "통과": 0,
            "비고": "대조할 문장 없음 — AI 호출 안 함",
        }

    # ★ 작가 문장이 원문과 strip 후 글자까지 같으면 의미 판정이
    # 필요 없다. 코드가 확정적으로 증명할 수 있는 경우만 통과시켜
    # 비용을 줄인다. 공백 정규화·부분 일치·의미 유사성은 금지한다.
    완전일치 = [
        pair
        for pair in pairs
        if pair.sentence.text.strip()
        and pair.sentence.text.strip() == pair.evidence.text.strip()
    ]
    완전일치번호 = {pair.number for pair in 완전일치}
    AI대조쌍 = [pair for pair in pairs if pair.number not in 완전일치번호]
    AI대조번호 = {pair.number for pair in AI대조쌍}

    if not AI대조쌍:
        남은: dict[str, list[Sentence]] = {}
        for pair in pairs:
            남은.setdefault(pair.cell, []).append(pair.sentence)
        return 남은, [], [], {
            "대조": len(pairs),
            "AI대조": 0,
            "완전일치통과": len(완전일치),
            "통과": len(완전일치),
            "버림": 0,
            "재작성대상": 0,
            "버린문장": [],
            "비고": "모든 문장이 근거 원문과 완전일치 — AI 호출 안 함",
        }

    payload, usage = ask(build_prompt(AI대조쌍), answer_schema(AI대조쌍))
    _, 버린, 재작성가능 = apply_verdicts_detailed(AI대조쌍, payload)
    버린번호 = {pair.number for pair in 버린}
    남은 = {}
    for pair in pairs:
        if pair.number in 완전일치번호 or (
            pair.number not in 버린번호 and pair.number in AI대조번호
        ):
            남은.setdefault(pair.cell, []).append(pair.sentence)
    기록: dict[str, Any] = {
        "대조": len(pairs),
        "AI대조": len(AI대조쌍),
        "완전일치통과": len(완전일치),
        "통과": sum(len(v) for v in 남은.values()),
        "버림": len(버린),
        "재작성대상": len(재작성가능),
        # ★ 무엇이 왜 버려졌는지 남긴다 — 안 남기면 「조용한 누락」이 된다.
        "버린문장": [p.sentence.text[:60] for p in 버린][:10],
        "usage": usage,
    }
    if payload is None:
        기록["오류"] = usage.get("error", "AI 답 없음")
        기록["비고"] = (
            "AI 대조가 죽어 AI 대조분을 «전부» 버렸다 — "
            "완전일치분만 코드로 보존했다"
        )
    return 남은, 버린, 재작성가능, 기록


def verify_with_ai(
    ask: Callable[[str, dict[str, Any]], tuple[Optional[dict[str, Any]], dict[str, Any]]],
    *,
    written: dict[str, list[Sentence]],
    evidence: dict[str, list[Evidence]],
) -> tuple[dict[str, list[Sentence]], dict[str, Any]]:
    """작가 글을 근거와 대조해 통과분만 돌려준다.

    ★ 대조할 것이 없으면 **AI를 안 부른다.**
    """
    pairs = make_pairs(written, evidence)
    남은, _, _, 기록 = verify_pairs_with_ai(ask, pairs=pairs)
    return 남은, 기록
