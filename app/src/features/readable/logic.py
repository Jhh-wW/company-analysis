"""공시 문투를 «읽히는 말»로 바꾼다 (문제로그 P-107).

★ **AI를 안 쓴다.** 정해 둔 낱말·어미를 바꾸는 것뿐이라 환각이 생길 자리가 없다.
★ 못 알아보는 모양은 **그냥 둔다.** 억지로 바꾸느니 원문이 낫다 —
  어색한 문장보다 «틀린 문장»이 훨씬 나쁘다.

⚠️ **표시용이다.** 원문은 부르는 쪽이 반드시 따로 보관해야 한다 —
  검증(W3 원문 대조)은 원문으로 돌아야 하기 때문이다.
"""

from __future__ import annotations

import re

from src.features.readable.constants import (
    ADJECTIVE_STEMS,
    ENDINGS,
    MIN_COMPANY_NAME,
    SELF_RE,
    SENTENCE_TAILS,
    UNTOUCHED_CITES,
)

#: 법인격 표기 — 회사 이름을 문장에 넣을 때 뗀다.
#: 「(주)하이브는」보다 「하이브는」이 읽기 낫다.
_LEGAL_TOKENS = ("주식회사", "(주)", "㈜", "(유)", "유한회사")


def display_name(company: str) -> str:
    """문장 안에 넣을 회사 이름. 법인격 표기를 뗀다."""
    name = (company or "").strip()
    for token in _LEGAL_TOKENS:
        name = name.replace(token, "")
    return name.strip()


def replace_self(sentence: str, company: str) -> str:
    """「당사는」·「연결회사의」 같은 1인칭 주어를 회사 이름으로 바꾼다.

    Args:
        sentence: 원문 문장.
        company: 회사 이름.

    Returns:
        바꾼 문장. 이름이 너무 짧으면 원문 그대로.

    ★ 조사는 **그대로 둔다** — 「당사는」→「하이브는」. 조사를 다시 고르면
      「이/가」 같은 받침 규칙까지 따져야 하고, 틀리면 더 어색해진다.
    ⚠️ 「당사자」·「연결회사채」에 안 걸린다 — «낱말 + 조사» 형태로만 잡는다.
    """
    name = display_name(company)
    if len(name) < MIN_COMPANY_NAME:
        return sentence
    return SELF_RE.sub(lambda m: f"{name}{m.group(2)}", sentence)


def soften_ending(sentence: str) -> str:
    """문장 «끝»의 「…습니다」를 「…다」로 바꾼다.

    Args:
        sentence: 문장.

    Returns:
        바꾼 문장. 아는 모양이 아니면 원문 그대로.

    ★ **끝에서만** 바꾼다. 가운데를 건드리면 뜻이 흔들릴 수 있다.
    """
    body = sentence.rstrip()
    tail = sentence[len(body):]
    마침표 = ""
    while body and body[-1] in SENTENCE_TAILS:
        마침표 = body[-1] + 마침표
        body = body[:-1]
    # ★ 「합니다」는 동사냐 형용사냐에 따라 「한다」/「하다」로 갈린다.
    #   구별을 안 하면 「예측이 불가능합니다」가 「불가능한다」가 된다.
    if body.endswith("합니다"):
        어간 = body[: -len("합니다")]
        끝맺음 = "하다" if any(어간.endswith(a) for a in ADJECTIVE_STEMS) else "한다"
        return 어간 + 끝맺음 + 마침표 + tail
    for 전, 후 in ENDINGS:
        if body.endswith(전):
            return body[: -len(전)] + 후 + 마침표 + tail
    return sentence


def to_readable(sentence: str, company: str, cite: str = "") -> str:
    """공시 문장 하나를 읽히는 말로 바꾼다.

    Args:
        sentence: 원문 문장.
        company: 회사 이름.
        cite: 이 문장의 출처. 「공고」면 **손대지 않는다.**

    Returns:
        바꾼 문장.

    ★ 공고에서 온 문장은 안 건드린다 — 정본이 「원문 그대로 살린다」고 못 박았고,
      지원자가 자소서에 그대로 옮겨 쓸 글이기 때문이다.
    """
    if not sentence:
        return sentence
    if any(mark in (cite or "") for mark in UNTOUCHED_CITES):
        return sentence
    return soften_ending(replace_self(sentence, company))


def rewrite_lines(
    lines: list[tuple[str, str]], company: str
) -> list[tuple[str, str]]:
    """(문장, 출처) 목록을 통째로 다듬는다. 출처는 그대로 둔다."""
    return [(to_readable(text, company, cite), cite) for text, cite in lines]
