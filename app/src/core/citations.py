"""보고서 본문에 붙는 출처 번호를 세 출력 형태가 같이 읽는 도구.

수집·작가 단계의 내부 값은 ``조각 9·뉴스``처럼 출처 종류까지 담는다. 하지만
사용자에게 필요한 것은 아래 출처 목록을 가리키는 실제 번호 ``9``뿐이다. 화면,
워드, 노션이 각자 문자열을 자르면 한쪽만 내부 이름을 노출하던 사고가 되풀이되므로
번호 해석을 ``core`` 한 곳에 둔다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final


# 내부 저장값 외에도 옛 저장 보고서가 가진 숫자·괄호 표기를 읽는다. 문장 안의
# 아무 숫자나 주워 오면 근거가 아닌 연도 등을 출처로 오인하므로 문자열 전체가
# 출처 표기 모양일 때만 받는다.
_CITATION_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:조각\s*)?(?:\[(?P<square>\d+)\]|〔(?P<corner>\d+)〕|(?P<plain>\d+))"
    r"(?:\s*·\s*.+)?\s*$"
)


def citation_number(cite: str | None) -> str:
    """내부 출처 표기에서 실제 출처 번호만 돌려준다.

    모양이 분명하지 않거나 0번이면 빈 문자열을 돌려준다. 근거 연결을 추측해서
    틀린 번호를 내보내는 것보다 번호를 표시하지 않는 편이 안전하기 때문이다.
    """

    if not cite:
        return ""
    matched = _CITATION_RE.fullmatch(cite)
    if matched is None:
        return ""
    raw = next(value for value in matched.groupdict().values() if value is not None)
    number = int(raw)
    return str(number) if number > 0 else ""


def citation_marker(cite: str | None) -> str:
    """워드·노션 본문에 붙일 ``〔실제 번호〕``를 만든다."""

    number = citation_number(cite)
    return f"〔{number}〕" if number else ""


#: 본문 문자열에 박힌 인용 표기 `[12]`. render.py가 넣은 것과 같은 모양이다.
_INLINE_MARKER_RE: Final[re.Pattern[str]] = re.compile(r"\[(\d+)\]")


@dataclass(frozen=True)
class CitationPart:
    """본문 한 조각 — 보통 글이거나, 인용 번호 하나."""

    text: str = ""
    number: int = 0


def split_citation_markers(text: str) -> tuple[CitationPart, ...]:
    """본문 문자열을 «글»과 «인용 번호»로 쪼갠다.

    ★ 왜 필요한가 (사용자 신고) — v2 본문은 `[1]`이 문자열 안에 박힌 채
      템플릿에서 그대로 인쇄돼, 본문과 «같은 크기»의 대괄호 숫자가 문장마다
      나온다. v1은 같은 번호를 `.ref` 작은 위첨자 링크로 낸다. 화면이 갈려
      있던 것이라 v2도 같은 모양으로 맞춘다.
    ★ 번호를 새로 매기거나 없애지 않는다 — 이미 부록과 1:1로 맞춰진 값이므로
      «모양만» 바꾼다. 숫자가 아닌 대괄호는 건드리지 않는다.
    """
    parts: list[CitationPart] = []
    last = 0
    for match in _INLINE_MARKER_RE.finditer(text or ""):
        if match.start() > last:
            parts.append(CitationPart(text=text[last : match.start()]))
        parts.append(CitationPart(number=int(match.group(1))))
        last = match.end()
    remainder = (text or "")[last:]
    if remainder:
        parts.append(CitationPart(text=remainder))
    return tuple(parts)


#: 문장 끝 «해석» 표지. render.py가 붙이는 것과 같은 값이다.
#: ★ 저장 문자열에는 이 모양을 그대로 둔다 — PDF·노션이 같은 문자열을 쓰고,
#:   이미 저장된 보고서도 다시 읽어야 한다. «화면 모양»만 아래에서 바꾼다.
INTERPRETATION_SUFFIX: Final[str] = " — 해석"

#: 배지에 찍을 글자 (하이픈 없이).
INTERPRETATION_LABEL: Final[str] = "해석"


def split_interpretation_marker(text: str) -> tuple[str, bool]:
    """문장 끝의 «— 해석» 표지를 떼어 낸다.

    ★ 왜 떼는가 (제품 결정) — 하이픈과 함께 본문에 붙은 「— 해석」이 글처럼
      읽혀 문장 끝이 지저분하다. 표지는 «글»이 아니라 «이 문장이 어떤
      성격인지 알리는 딱지»이므로 둥근 배지로 따로 보여 준다.
    ★ 표지 자체를 없애지 않는다 — 확인과 해석을 구분하는 것은 이 제품의
      핵심 약속이다. 모양만 바꾼다.

    Returns:
        (표지를 뗀 본문, 해석 문장인가).
    """
    body = text or ""
    if body.endswith(INTERPRETATION_SUFFIX):
        return body[: -len(INTERPRETATION_SUFFIX)].rstrip(), True
    return body, False
