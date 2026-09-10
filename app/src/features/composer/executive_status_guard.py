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
    STATUS_DATE_WINDOW_CHARS,
    STATUS_DEPARTURE_RE,
    STATUS_NAME_STOPWORDS,
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


def _is_name_like(surface: str) -> bool:
    """그 표면형을 사람 이름 자리로 인정할 것인가.

    ★ 이름 자리는 «직함 옆 한글 2~4음절»이라는 모양만 본다. 그래서 직함 뒤에
      이어지는 보통명사(「대표이사 선임이」의 「선임」, 「감사보고서의」의
      「보고서」, 「사외이사 비중을」의 「비중」)가 그대로 이름 후보가 된다.
      실측에서 그 오인 때문에 정상 문장 하나가 통째로 거절됐다.
      닫힌 불용어 목록으로만 걸러 낸다 — 이 모듈은 유사도·형태소 분석을 쓰지
      않기 때문이다(모듈 머리말의 «지키는 선»).
    """

    return bool(surface) and surface not in STATUS_NAME_STOPWORDS


def _candidate_names(text: str) -> tuple[str, ...]:
    """직함 곁에 붙은 «이름처럼 보이는» 표면형을 뽑는다. 조사 삼킴은 되돌린다.

    ★ {2,4}는 탐욕적이라 「정석목이」처럼 이름 뒤 조사 한 글자까지 삼킬 수 있다
      (「목」이 받침으로 끝나 「이」가 붙는 꼴). 원문 조각에는 조사 없는 표 형태
      (「정석목」)로 실리는 일이 많으므로, 조사로 끝나면 그 글자를 뗀 짧은 형태도
      함께 후보로 둔다 — 둘 다 시도해 한쪽이라도 원문과 맞으면 된다.

    ★ 조사를 뗀 꼴이 불용어면 붙은 꼴도 함께 버린다 — 「선임이」와 「선임」은
      같은 낱말이라 한쪽만 걸러 내면 다른 쪽으로 그대로 새기 때문이다.
    """

    names: list[str] = []
    for pattern in TITLE_NAME_PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(1)
            if not raw:
                continue
            trimmed = ""
            if len(raw) >= 3 and raw[-1] in TRAILING_PARTICLE_CHARS:
                trimmed = raw[:-1]
            if not _is_name_like(raw) or (trimmed and not _is_name_like(trimmed)):
                continue
            if raw not in names:
                names.append(raw)
            if len(trimmed) >= 2 and trimmed not in names:
                names.append(trimmed)
    return tuple(names)


def _parse_departure_date(window: str) -> "date | None":
    """창 안에서 이탈 날짜가 «하나로 확정될 때만» 돌려준다.

    ★ 서로 다른 날짜가 둘 이상이면 어느 것이 이 사람의 것인지 확정할 수 없으므로
      ``None``을 돌려준다 — 호출자는 그때 표지를 그대로 믿는다(fail-closed).
      날짜 창을 표지 창보다 넓게 잡아도 다른 사람의 날짜로 면제가 만들어지지
      않게 하는 유일한 안전선이다.
    """

    found: set[date] = set()
    for match in DEPARTURE_DATE_RE.finditer(window):
        try:
            year, month, day = (int(part) for part in match.groups())
            found.add(date(year, month, day))
        except ValueError:
            continue
    return found.pop() if len(found) == 1 else None


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
        if STATUS_DEPARTURE_RE.search(source_text[window_start:window_end]) is None:
            continue
        if baseline is None:
            return True
        # ★ 날짜는 표지보다 «넓은» 창에서 찾는다. 표지는 이름 바로 옆에 붙지만
        #   날짜는 그 표 행의 끝에 있어서, 같은 창을 쓰면 실제 표 모양에서
        #   날짜를 한 번도 못 찾는다(실측: 이름→날짜 129자).
        date_window = source_text[
            max(0, start - STATUS_DATE_WINDOW_CHARS) : min(
                len(source_text), start + len(name) + STATUS_DATE_WINDOW_CHARS
            )
        ]
        departure_date = _parse_departure_date(date_window)
        # ★ 날짜를 못 찾거나 여러 날짜가 섞여 확정할 수 없으면 미확인이 아니라
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
