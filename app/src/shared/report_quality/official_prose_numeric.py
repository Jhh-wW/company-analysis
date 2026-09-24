"""FULL 공개 안전 판정 — 공식 원문 산문의 숫자 토큰화와 «원문 그대로» 대조(ADR 0005).

확인 등급 공식 원문 산문은 문장의 숫자 표현이 **모두** 그 사실이 인용한 조각 원문에 같은
표기로 있을 때만 NumericBinding 없이 FULL 공개 안전을 통과한다. 이 모듈은 그 비교에 쓰는
토큰화(``official_number_tokens``)와 대조(``prose_numbers_verbatim_in_fragments``)만 가진다.
문장과 조각 원문은 **같은 함수**로 토큰화하고, 토큰은 통째로 비교한다(부분 문자열 검색 없음).

★ 뉴스 산문 토큰화(``supplementary_prose.news_number_tokens``)를 쓰지 않는다. 그것은 띄어 쓴
  크기어를 갈라(«3억 원» → «3»·«억원») «제3공장 … 5억 원» 같은 조각에서 값이 다른데도 통과한다.
  뉴스 규칙은 그대로 둔다.

정규화 규칙(문장·조각 공통, 이것 말고는 글자를 바꾸지 않는다):

| 입력 | 토큰 | 규칙 |
|---|---|---|
| «3억 원», «3억원» | ``3억원`` | 숫자 + 붙은 크기어(십·백·천·만·억·조) + 단위를 공백과 무관하게 한 토큰으로 묶는다 |
| «1조 2천억 원» | ``1조2천억원`` | 크기어가 붙은 숫자 무리가 이어지면(공백 허용) 한 토큰이다 |
| «5만 명», «100만 명의» | ``5만명``, ``100만명`` | 띄어 쓴 단위는 닫힌 단위 목록만 붙이고, 그 뒤에는 조사만 올 수 있다 |
| «2025년 3월» | ``2025년``, ``3월`` | 숫자마다 따로 토큰이다 |
| «12.5%», «12.5 %» | ``12.5%`` | 소수점은 그대로, 백분율 기호는 단위다 |
| «-3.5%», «△3.5%», «▲3%» | ``-3.5%``, ``-3.5%``, ``+3%`` | 숫자 앞 부호는 토큰 머리다 |
| «3%p», «3%포인트» | ``3%p``, ``3%포인트`` | 백분율 뒤에 붙은 글자까지 단위다(«3%»와 다른 토큰) |
| «1,234명» | ``1234명`` | 천 단위 쉼표만 지운다(쉼표 뒤가 세 자리일 때만 숫자의 일부) |
| «2019, 2020, 2021년» | ``2019``, ``2020``, ``2021년`` | 목록 쉼표는 숫자가 아니다. 단위는 붙은 숫자만 |
| «12개의», «2019년부터», «12.5%다» | ``12개``, ``2019년``, ``12.5%`` | 끝의 조사·서술 꼬리만 지운다 |
| «5도», «종로3가», «제3과» | ``5도``, ``3가``, ``3과`` | 한 글자 조사가 낱말 전체면 단위다 |
| «15일자», «2023년도», «3년간», «3월말» | ``15일``, ``2023년``, ``3년``, ``3월`` | 값이 같은 시간 꼬리 |
| «3개국», «3개월», «3세대» | ``3개국``, ``3개월``, ``3세대`` | 붙은 낱말은 조사를 뺀 전체가 단위다(«3개»와 다른 토큰) |
| «제3공장» | ``3공장`` | 숫자 앞 한글은 토큰에 넣지 않는다 |
| «H100», «FY2023», «5G» | ``H100``, ``FY2023``, ``5G`` | 숫자 바로 앞·뒤에 붙은 로마자는 토큰의 일부다 |
| «2019.03.15» | ``2019.03.15`` | 연·월·일을 점·빗금·붙임표로 이은 날짜는 한 토큰이다 |
| «２０２１년», «５％» | ``2021년``, ``5%`` | 전각 문자는 NFKC로 반각이 된다 |
| «두 배», «절반», «세 곳» | ``두배``, ``절반``, ``세곳`` | 공개 숫자 감지와 같은 한글 수량 문법 |

부호는 −·△·▼를 «-», ▲를 «+»로 적는다. 앞 글자가 숫자·로마자면 부호가 아니라 붙임표다
(«2019-2021», «COVID-19»). 부호가 다르면 다른 값이다(«-3.5%»와 «3.5%»).

받아들인 약점 — 토큰은 표기만 본다. 같은 조각 안에 같은 토큰이 다른 뜻으로 있으면 통과한다
(예: 다른 문맥의 같은 연도). 문장의 뜻(주어·지표·증감 방향)은 독립 검수의 «참» 판정이 맡는다.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Final, Sequence

from src.shared.report_quality.numeric_detection import korean_numeric_tokens

#: 숫자 바로 앞 부호 → 토큰 머리. 공시의 «△»·«▼»는 음수, «▲»는 양수 표기다.
_SIGN_PREFIXES: Final[dict[str, str]] = {
    "-": "-", "−": "-", "△": "-", "▼": "-", "+": "+", "▲": "+",
}
#: 숫자에 붙어 값을 키우는 한 글자 크기어.
_MAGNITUDE_CHARS: Final[str] = "십백천만억조"
#: 연·월·일을 점·빗금·붙임표로 이은 날짜 한 덩어리.
_DATE_PATTERN: Final[str] = r"\d{4}[./-]\d{1,2}[./-]\d{1,2}(?!\d)"
#: 숫자 — 천 단위 쉼표는 뒤가 정확히 세 자리일 때만 숫자의 일부다.
_NUMBER_PATTERN: Final[str] = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
#: 크기어가 붙은 숫자 무리 하나(«3억», «2천억»).
_GROUP_PATTERN: Final[str] = rf"{_NUMBER_PATTERN}[{_MAGNITUDE_CHARS}]+"
_EXPRESSION_RE: Final[re.Pattern[str]] = re.compile(
    # 부호는 앞 글자가 숫자·로마자가 아닐 때만 읽는다(«2019-2021»·«COVID-19»의 붙임표 제외).
    rf"(?:(?<![0-9A-Za-z])(?P<sign>[-+−△▲▼]))?"
    rf"(?P<prefix>[A-Za-z]*)"
    rf"(?P<body>{_DATE_PATTERN}"
    rf"|{_GROUP_PATTERN}(?:\s*{_GROUP_PATTERN})*"
    rf"|{_NUMBER_PATTERN})"
)
#: 숫자 바로 뒤에 붙은 낱말(백분율 기호는 맨 앞에만).
_ATTACHED_WORD_RE: Final[re.Pattern[str]] = re.compile(r"%?[가-힣A-Za-z]*")
#: 공백 뒤 낱말(띄어 쓴 단위 후보).
_SPACED_WORD_RE: Final[re.Pattern[str]] = re.compile(r"\s+(?P<word>%?[가-힣A-Za-z]*)")
#: 띄어 쓴 단위로 인정하는 닫힌 목록(«3억 원», «5만 명»). 공백 뒤 아무 낱말이나 단위로
#: 붙이면 «3 원래»처럼 없던 값이 생기므로, 이 목록 + 조사만 받는다.
_SPACED_UNITS: Final[frozenset[str]] = frozenset((
    "원", "달러", "엔", "위안", "유로", "명", "개", "곳", "건", "톤", "배",
    "%", "%p", "퍼센트", "퍼센트포인트", "포인트",
))
#: 붙은 낱말 끝에서 지우는 조사와 어림 꼬리. 긴 것부터 본다(«에서는» 먼저, «는» 나중).
_TRAILING_PARTICLES: Final[tuple[str, ...]] = tuple(sorted(
    (
        "으로부터", "에서부터", "에서는", "에서도", "으로는", "으로도", "에게서",
        "이라는", "이라고", "입니다", "이었다", "이며", "이고", "이다", "였다",
        "에서", "으로", "부터", "까지", "보다", "처럼", "마다", "에는", "에도",
        "에게", "이라", "은", "는", "이", "가", "을", "를", "에", "의", "로",
        "와", "과", "도", "만", "씩", "께", "경", "쯤", "다", "라고", "라는",
    ),
    key=len,
    reverse=True,
))


#: 값을 바꾸지 않는 시간 꼬리 — 조사를 뗀 뒤 붙은 낱말이 정확히 이것이면 앞 단위로 읽는다.
#: 「15일자」는 「15일」, 「3년간」은 「3년」과 같은 날·같은 햇수다. «1990년대»(연대)·
#: «30주년»처럼 뜻이 바뀌는 꼬리는 넣지 않는다. «3일간»(사흘 동안)은 «3일»(날짜)과 뜻이 달라 넣지 않는다.
_TIME_SUFFIX_UNITS: Final[dict[str, str]] = {
    "일자": "일",
    "년도": "년",
    "년간": "년",
    "년말": "년",
    "년초": "년",
    "개월간": "개월",
    "월말": "월",
    "월초": "월",
    "분기말": "분기",
}


def _strip_particle(word: str) -> str:
    """붙은 낱말 끝의 조사 하나를 지운다.

    낱말 전체가 조사면 — 한 글자면 단위로 남기고(«5도»·«종로3가»: 단위와 겹치는 조사를
    지우면 맨 숫자가 되어 아무 맨 숫자와 맞는다), 두 글자 이상이면 빈 값(«2019부터»).
    """

    for particle in _TRAILING_PARTICLES:
        if word == particle:
            return word if len(word) == 1 else ""
        if word.endswith(particle):
            return word[: -len(particle)]
    return word


def _unit_after(text: str, position: int) -> tuple[str, int]:
    """숫자 표현 바로 뒤의 단위와 그 끝 자리 — 붙은 낱말(조사 뺌), 없으면 띄어 쓴 닫힌 단위."""

    attached = _ATTACHED_WORD_RE.match(text, position)
    word = attached.group() if attached is not None else ""
    if word:
        unit = _strip_particle(word)
        return _TIME_SUFFIX_UNITS.get(unit, unit), position + len(word)
    spaced = _SPACED_WORD_RE.match(text, position)
    if spaced is None:
        return "", position
    candidate = _strip_particle(spaced.group("word"))
    if candidate in _SPACED_UNITS:
        return candidate, spaced.end()
    return "", position


def official_number_tokens(text: str) -> frozenset[str]:
    """공식 원문 산문 대조용 숫자 토큰 — 모듈 docstring의 정규화 표를 따른다.

    Args:
        text: 문장 또는 인용 조각 원문. 빈 값·None은 빈 집합.

    Returns:
        숫자 표현마다 «로마자 앞머리 + 숫자(쉼표·공백 뺌) + 크기어 + 단위»로 묶은
        토큰과 한글 수량 토큰의 집합.
    """

    normalized = unicodedata.normalize("NFKC", str(text or ""))
    tokens: set[str] = set()
    # ★ 한글 수량은 숫자 표현 자리를 비운 글에서만 뽑는다. 비우지 않으면 «4천억 원»
    #   안에서 «천억원»이 따로 나와, 한글로만 쓴 «천억 원» 문장이 값이 다른 조각에
    #   통과한다.
    remainder = list(normalized)
    for match in _EXPRESSION_RE.finditer(normalized):
        body = match.group("body")
        if re.fullmatch(_DATE_PATTERN, body) is None:
            body = re.sub(r"[\s,]", "", body)
        unit, end = _unit_after(normalized, match.end())
        sign = _SIGN_PREFIXES.get(match.group("sign") or "", "")
        tokens.add(f"{sign}{match.group('prefix')}{body}{unit}")
        remainder[match.start():end] = " " * (end - match.start())
    tokens.update(korean_numeric_tokens("".join(remainder)))
    return frozenset(tokens)


def prose_numbers_verbatim_in_fragments(
    sentence: str,
    fragment_texts: Sequence[str],
) -> bool:
    """문장의 숫자 토큰이 «모두» 인용 조각 원문에 그대로 있는가.

    우선순위:
      1. 문장에 숫자 토큰이 없으면 참 — 증명할 숫자가 없다(조각이 비어도 참).
      2. 토큰이 있는데 조각이 없거나 조각에서 토큰이 하나도 안 나오면 거짓.
      3. 문장 토큰 하나라도 조각별 토큰 합집합에 없으면 거짓.

    조각은 하나씩 따로 토큰화한 뒤 합친다. 이어 붙이면 경계에서 없던 토큰이 생긴다
    («합계 3» + «개 사업» → «3개»).

    Args:
        sentence: 공개할 산문 한 문장(사실의 claim).
        fragment_texts: 그 사실이 인용한 조각의 정확 원문들. 문자열 하나를 통째로
            넘기면 글자 단위로 쪼개지므로 거절한다.

    Returns:
        위 규칙의 참·거짓.

    Raises:
        TypeError: ``fragment_texts`` 가 문자열 하나일 때.
    """

    if isinstance(fragment_texts, str):
        raise TypeError("fragment_texts는 조각 원문들의 목록이어야 합니다")
    required = official_number_tokens(sentence)
    if not required:
        return True
    available: set[str] = set()
    for fragment_text in fragment_texts:
        available |= official_number_tokens(fragment_text)
    return bool(available) and required <= available


__all__ = ["official_number_tokens", "prose_numbers_verbatim_in_fragments"]
