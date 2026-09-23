"""뉴스 인용 범위의 «주장 역할»을 좁게 가려 명백한 장 불일치만 바로잡는다.

★ 왜 필요한가 (4차 실측) — 분석 모델이 «매출»이라는 단어를 보고 회사 전체의
  지난해 매출·성장률 보도를 2장(수익 모델) 칸에 넣었다. 2장은 과금 단위·고객·
  구성 비중을 설명하는 장인데 같은 실적 보도 두 건이 그 자리를 채웠고, 4장
  공식 실적과 같은 수치가 두 장에 반복됐다.

★ 무엇을 하나 — 문장 역할을 셋으로만 본다: 기간 실적 / 2장 역할(과금·구성·
  고객·채널) / 그 밖. 2장에 배정된 범위가 «기간 실적»뿐이면 4장 보조 칸으로
  옮기고, 서로 다른 역할이 섞였으면 각 부분이 스스로 대상 회사를 가리킬 때만
  연속 원문 범위로 나눈다. 원문 글자·위치·출처는 그대로이며 새 문장을 만들지
  않는다. 옮긴 후보는 새 장의 정상 작성·검수를 다시 받으며 승인이 따라가지
  않는다(이 단계에는 승인이 아직 없다).

★ 무엇을 하지 않나 — 매출·숫자·% 단어만으로 옮기지 않는다(과금·구성 표지가
  있으면 2장에 둔다). 계획·전망은 과거 실적으로 옮기지 않는다. 3장 등 다른
  장의 배정은 건드리지 않는다. 판단이 애매하면 모델의 배정을 그대로 둔다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.features.news_intake import constants as c
from src.features.news_intake.identity_names import mentions_target
from src.features.news_intake.models import NewsCompanyContext


_PERIOD_RE = re.compile(c.ROLE_PERIOD_PATTERN)
_METRIC_RE = re.compile(c.ROLE_FINANCIAL_METRIC_PATTERN)
_AMOUNT_RE = re.compile(c.ROLE_AMOUNT_PATTERN)
_CHANGE_RE = re.compile(c.ROLE_CHANGE_PATTERN)
_REVENUE_MODEL_RE = re.compile(c.ROLE_REVENUE_MODEL_PATTERN)
_FORWARD_RE = re.compile(c.ROLE_FORWARD_PATTERN)
_PLAN_RE = re.compile(c.FUTURE_PLAN_PATTERN)
_BACK_REF_RE = re.compile(c.ROLE_RELATIVE_BACK_REFERENCE_PATTERN)
_SENTENCE_ENDINGS = frozenset(".!?。！？")
_CLOSING_AFTER_SENTENCE = frozenset("”」’'\"）)]}")


@dataclass(frozen=True)
class RolePart:
    """원래 인용 범위 안의 연속 부분 범위와 그 부분이 지원할 장·칸."""

    start: int
    end: int
    section_id: str
    claim_slot: str


def sentence_ranges(text: str) -> tuple[tuple[int, int], ...]:
    """원문 글자를 바꾸지 않고 문장별 (시작, 끝) 위치만 돌려준다.

    `mapping.split_sentences`와 같은 경계(문장부호 뒤 공백·줄바꿈, 따옴표 안은
    자르지 않음, 소수점 보존)를 쓰되, 잘라 낸 문자열 대신 위치를 준다 — 부분
    범위가 원문 연속 구간이라는 사실을 위치로 증명해야 하기 때문이다.
    """

    ranges: list[tuple[int, int]] = []
    quote_closers = dict(c.QUOTE_PAIRS)
    active_quote = ""
    start = 0
    index = 0

    def push(begin: int, end: int) -> None:
        while begin < end and text[begin].isspace():
            begin += 1
        while end > begin and text[end - 1].isspace():
            end -= 1
        if begin < end:
            ranges.append((begin, end))

    while index < len(text):
        char = text[index]
        if active_quote:
            if char == active_quote:
                active_quote = ""
        elif char in quote_closers:
            active_quote = quote_closers[char]
        if char in "\r\n" and not active_quote:
            push(start, index)
            start = index + 1
        elif char in _SENTENCE_ENDINGS and not active_quote:
            if (char == "." and 0 < index < len(text) - 1
                    and text[index - 1].isdigit() and text[index + 1].isdigit()):
                index += 1
                continue
            end = index + 1
            while end < len(text) and text[end] in _CLOSING_AFTER_SENTENCE:
                end += 1
            if end == len(text) or text[end].isspace():
                push(start, end)
                start = end
                index = end - 1
        index += 1
    push(start, len(text))
    return tuple(ranges)


def is_period_financial_result(sentence: str) -> bool:
    """한 문장이 «기간 실적»(기간+재무 지표+값 또는 증감)만 말하는가.

    과금·구성·고객·채널 표지가 있으면 2장 역할로 보고 거짓. 목표·전망·예정은
    과거 실적이 아니므로 거짓. 판단 근거가 모자라면 거짓(모델 배정 유지).
    """

    if _REVENUE_MODEL_RE.search(sentence) or _FORWARD_RE.search(sentence) or _PLAN_RE.search(sentence):
        return False
    return bool(
        _PERIOD_RE.search(sentence)
        and _METRIC_RE.search(sentence)
        and (_AMOUNT_RE.search(sentence) or _CHANGE_RE.search(sentence))
    )


def has_revenue_model_role(sentence: str) -> bool:
    """2장이 소유하는 과금·구성·고객·채널 표지가 있는가."""

    return bool(_REVENUE_MODEL_RE.search(sentence))


def _runs(flags: list[bool]) -> list[tuple[int, int, bool]]:
    """같은 역할이 이어진 문장 색인 구간 [시작, 끝)."""

    runs: list[tuple[int, int, bool]] = []
    for index, flag in enumerate(flags):
        if runs and runs[-1][2] == flag:
            runs[-1] = (runs[-1][0], index + 1, flag)
        else:
            runs.append((index, index + 1, flag))
    return runs


def plan_role_parts(
    text: str,
    *,
    section_id: str,
    claim_slot: str,
    temporal_status: str,
    company: NewsCompanyContext,
) -> tuple[RolePart, ...] | None:
    """역할 조정이 필요하면 부분 범위들을, 아니면 None(모델 배정 유지)을 준다."""

    if section_id not in c.ROLE_REROUTE_FROM_SECTIONS or temporal_status == "planned":
        return None
    ranges = sentence_ranges(text)
    if not ranges:
        return None
    flags = [is_period_financial_result(text[start:end]) for start, end in ranges]
    if not any(flags):
        return None
    target = (c.ROLE_PERFORMANCE_TARGET_SECTION, c.ROLE_PERFORMANCE_TARGET_SLOT)
    if all(flags):
        return (RolePart(0, len(text), *target),)
    # ★ 뒤 문장이 앞 문장의 시점·대상을 역참조(«그해», «해당 서비스»)하면 나누지
    #   않는다. 떼어 내면 기준 연도나 대상이 사라져 사실이 변질된다.
    for idx in range(1, len(ranges)):
        sentence_text = text[ranges[idx][0]:ranges[idx][1]]
        if _BACK_REF_RE.search(sentence_text):
            if all(flags):
                return (RolePart(0, len(text), *target),)
            others = [text[s:e] for (s, e), f in zip(ranges, flags) if not f]
            if any(has_revenue_model_role(s) for s in others):
                return None
            return (RolePart(0, len(text), *target),)
    parts: list[RolePart] = []
    for first, last, performance in _runs(flags):
        start, end = ranges[first][0], ranges[last - 1][1]
        parts.append(RolePart(start, end, *(target if performance else (section_id, claim_slot))))
    # 부분마다 스스로 대상 회사를 가리키고 최소 길이를 넘을 때만 나눈다. 주어가
    # 앞 문장에만 있는 부분을 떼면 다른 회사·제품의 말로 읽힐 수 있다.
    if all(part.end - part.start >= c.GROUNDED_MIN_EXCERPT_CHARS
           and mentions_target(text[part.start:part.end], company) for part in parts):
        return tuple(parts)
    others = [text[start:end] for (start, end), flag in zip(ranges, flags) if not flag]
    if any(has_revenue_model_role(sentence) for sentence in others):
        # 2장 역할 문장이 섞였는데 나눌 수 없으면 2장에 그대로 둔다(정보 보존 우선).
        return None
    # 나머지도 2장 역할이 아니면 범위 전체가 2장 설명이 아니다 — 통째로 4장 보조 칸.
    return (RolePart(0, len(text), *target),)
