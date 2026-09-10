# -*- coding: utf-8 -*-
"""후보가 인물명에 붙인 등기임원 직함이 «인용한 원문 안에서» 이미 이탈로 적혔는가.

grounding.py의 constrain_verdicts가 direct_support_problem·role_binding_problem과
같은 자리에서 부른다. 대조 경계도 그 둘과 같다 — 그 후보가 «인용한» 조각만 본다.
다른 후보·다른 장의 근거는 빌리지 않는다.

무엇을 새로 만들지 않는가:
  · 유사도·임베딩·회사별 특칙 없음. executive_status_constants의 닫힌 목록과
    문자열 인접·포함·정규식만 쓴다.
  · 검수 응답에 새 JSON 필드를 요구하지 않는다 — 이 가드는 (문장, 인용 원문)만
    보고 스스로 판정하는 결정론 검사다. 그래서 verify.py의 응답 파서를 바꾸지
    않아도 오늘부터 동작한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from src.features.composer.executive_status_constants import (
    DEPARTURE_DATE_RE,
    EXECUTIVE_STATUS_OUTDATED,
    STATUS_DEPARTURE_WORDS,
    STATUS_REAPPOINTMENT_WORDS,
    STATUS_WINDOW_CHARS,
    TITLE_NAME_PATTERNS,
    TRAILING_PARTICLE_CHARS,
)


def _occurrences(haystack: str, needle: str) -> tuple[int, ...]:
    if not needle:
        return ()
    found: list[int] = []
    start = haystack.find(needle)
    while start >= 0:
        found.append(start)
        start = haystack.find(needle, start + 1)
    return tuple(found)


def _candidate_names(text: str) -> tuple[str, ...]:
    """직함 곁에 붙은 «이름처럼 보이는» 표면형을 뽑는다. 조사 삼킴은 되돌린다.

    ★ {2,4}는 탐욕적이라 「정석목이」처럼 이름 뒤 조사 한 글자까지 삼킬 수 있다
      (「목」이 받침으로 끝나 「이」가 붙는 꼴). 원문 조각에는 조사 없는 표 형태
      (「정석목」)로 실리는 일이 많으므로, 조사로 끝나면 그 글자를 뗀 짧은 형태도
      함께 후보로 둔다 — 둘 다 시도해 한쪽이라도 원문과 맞으면 된다.
    """

    names: list[str] = []
    for pattern in TITLE_NAME_PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(1)
            if not raw:
                continue
            if raw not in names:
                names.append(raw)
            if len(raw) >= 3 and raw[-1] in TRAILING_PARTICLE_CHARS:
                trimmed = raw[:-1]
                if len(trimmed) >= 2 and trimmed not in names:
                    names.append(trimmed)
    return tuple(names)


def _parse_departure_date(window: str) -> "date | None":
    match = DEPARTURE_DATE_RE.search(window)
    if not match:
        return None
    try:
        year, month, day = (int(part) for part in match.groups())
        return date(year, month, day)
    except ValueError:
        return None


def _departed_without_reappointment(
    name: str, source_text: str, baseline: "date | None",
) -> bool:
    """그 인용 원문 «안에서» 이름이 이탈 표지와 함께 있고, 재선임 표지가 없는가.

    ★ 재선임 표지는 «그 원문 조각 전체» 어디에 있어도 보류로 본다(자리를 좁혀
      찾지 않는다) — 같은 인물의 이탈·복귀가 한 표 안에서 서로 다른 행으로
      떨어져 있어도 안전한 쪽(오탐 대신 미탐)으로 넘어가기 위해서다.
    """

    if any(word in source_text for word in STATUS_REAPPOINTMENT_WORDS):
        return False
    for start in _occurrences(source_text, name):
        window_start = max(0, start - STATUS_WINDOW_CHARS)
        window_end = min(len(source_text), start + len(name) + STATUS_WINDOW_CHARS)
        window = source_text[window_start:window_end]
        if not any(word in window for word in STATUS_DEPARTURE_WORDS):
            continue
        if baseline is None:
            return True
        departure_date = _parse_departure_date(window)
        # ★ 날짜를 못 찾으면(표지만 있고 날짜가 창 밖에 있으면) 미확인이 아니라
        #   표지를 그대로 믿는다 — 날짜 표기가 닫힌 꼴 밖일 뿐 이탈 사실 자체는
        #   여전히 표지가 말하고 있기 때문이다.
        if departure_date is None or departure_date <= baseline:
            return True
    return False


def executive_status_problem(
    text: str,
    sources: Mapping[str, str],
    cells: Sequence[str] | None = None,
    *,
    baseline_date: str | None = None,
) -> str:
    """후보 문장(또는 도식 칸)의 인물+임원 직함이 «자기 인용» 안에서 이탈로 적혔는가.

    표지가 없으면 빈 문자열을 돌려준다 — 정상 재직 중 임원 소개 문장은 막지
    않는다. ``baseline_date``(ISO ``YYYY-MM-DD``)를 주면 «아직 발효되지 않은
    예정 이탈»(원문의 날짜가 기준일보다 뒤)은 걸지 않는다. 주지 않으면 표지
    존재만으로 판정한다 — 기준일을 못 받아도 검사 자체가 꺼지지는 않는다.
    """

    haystacks = [text]
    if cells:
        haystacks.append(" ".join(str(cell) for cell in cells))
    names: list[str] = []
    for haystack in haystacks:
        for name in _candidate_names(haystack):
            if name not in names:
                names.append(name)
    if not names:
        return ""

    parsed_baseline: "date | None" = None
    if baseline_date:
        try:
            parsed_baseline = date.fromisoformat(baseline_date)
        except ValueError:
            parsed_baseline = None

    for name in names:
        for source_text in sources.values():
            if not isinstance(source_text, str) or name not in source_text:
                continue
            if _departed_without_reappointment(name, source_text, parsed_baseline):
                return EXECUTIVE_STATUS_OUTDATED
    return ""
