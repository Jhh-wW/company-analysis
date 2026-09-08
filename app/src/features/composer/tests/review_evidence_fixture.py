"""JYP 골든 입력에 결속된 검수 응답을 만드는 테스트 전용 도우미.

검수 응답의 ``근거``는 프롬프트가 배정한 인용 조각을 그대로 되돌린다. 따라서
번호만 맞춘 전부 참 응답으로 다른 문장이나 조각의 근거를 빌려 올 수 없다.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from src.features.composer.grounding_constants import (
    GROUNDING_KEY,
    NUMERIC_KEY,
    TREND_KEY,
)


_ITEM_HEAD_RE = re.compile(
    r"\n\[(?P<number>\d+)\] \(장: (?P<section>[^,]+), 종류: "
    r"(?P<kind>[^,]+), 인용: (?P<citations>[^)]*)\)\n"
)
_LEGACY_ITEM_HEAD_RE = re.compile(
    r"\n\[(?P<number>\d+)\] \(등급: [^,]+, 인용: "
    r"(?P<citations>[^)]*)\)\n"
)
_SENTENCE_PREFIX = "  문장(JSON 문자열): "
_FLOW_PREFIX = "  도식 칸(JSON 배열): "


@dataclass(frozen=True)
class ReviewItem:
    """검수 프롬프트에서 읽은 한 항목의 소유 장·인용·원문 후보."""

    number: int
    section: str
    kind: str
    citations: tuple[str, ...]
    text: str


def _read_json_value(prompt: str, start: int) -> tuple[object, int]:
    decoder = json.JSONDecoder()
    value, end = decoder.raw_decode(prompt[start:])
    return value, start + end


def review_items(prompt: str) -> tuple[ReviewItem, ...]:
    """그룹 검수 프롬프트의 항목을 손실 없이 읽는다."""

    items: list[ReviewItem] = []
    matches = tuple(_ITEM_HEAD_RE.finditer(prompt))
    for match in matches:
        value_start = match.end()
        if prompt.startswith(_SENTENCE_PREFIX, value_start):
            value, _end = _read_json_value(
                prompt, value_start + len(_SENTENCE_PREFIX)
            )
            text = str(value) if isinstance(value, str) else ""
        elif prompt.startswith(_FLOW_PREFIX, value_start):
            value, _end = _read_json_value(prompt, value_start + len(_FLOW_PREFIX))
            text = " ".join(str(cell) for cell in value) if isinstance(value, list) else ""
        else:
            continue
        citations = tuple(
            piece.removeprefix("조각 ").strip()
            for piece in match.group("citations").split(",")
            if piece.strip() and piece.strip() != "(없음)"
        )
        items.append(
            ReviewItem(
                number=int(match.group("number")),
                section=match.group("section"),
                kind=match.group("kind"),
                citations=citations,
                text=text,
            )
        )
    if items or matches:
        return tuple(items)

    # strict packet 검수 전환 중에도 기존 단일 프롬프트 계약은 유지된다.
    # 장 소유는 이 모양에 없으므로 빈 값으로 두되, 문장·인용·번호는 원문대로 읽는다.
    for match in _LEGACY_ITEM_HEAD_RE.finditer(prompt):
        value_start = match.end()
        if not prompt.startswith(_SENTENCE_PREFIX, value_start):
            continue
        value, _end = _read_json_value(prompt, value_start + len(_SENTENCE_PREFIX))
        citations = tuple(
            piece.removeprefix("조각 ").strip()
            for piece in match.group("citations").split(",")
            if piece.strip() and piece.strip() != "(없음)"
        )
        items.append(
            ReviewItem(
                number=int(match.group("number")),
                section="",
                kind="문장",
                citations=citations,
                text=str(value) if isinstance(value, str) else "",
            )
        )
    return tuple(items)


def _numeric(
    expression: str,
    metric: str,
    citation: str,
    quote: str,
    value: str,
    source_metric: str | None = None,
    candidate_value: str | None = None,
) -> dict[str, str]:
    return {
        "표현": expression,
        "항목": metric,
        "근거": citation,
        "원문": quote,
        "원문항목": source_metric or metric,
        "원문값": value,
        "후보값": candidate_value or value,
    }


def _point(
    citation: str, quote: str, metric: str, period: str, value: str
) -> dict[str, str]:
    return {
        "근거": citation,
        "원문": quote,
        "항목": metric,
        "기간": period,
        "원문항목": metric,
        "원문값": value,
    }


def _proof_for_jyp_sentence(item: ReviewItem) -> dict[str, object] | None:
    """변경하지 않은 JYP 작가 문장에만 실제 조각의 인용구를 붙인다.

    수치·기간을 바꾼 경계 시험의 문장은 이 표에 없으므로 참으로 승인되지 않는다.
    """

    text = item.text
    if text == "2025년 연결 매출에서 음악·공연·MD 핵심 3축의 합계 비중은 77.3%다.":
        return {GROUNDING_KEY: {NUMERIC_KEY: [_numeric(
            "음악·공연·MD 핵심 3축의 합계 비중은 77.3%",
            "음악·공연·MD 핵심 3축의 합계",
            "3",
            "2025년 연결 매출에서 음악·공연·MD 핵심 3축의 합계가 77.3%를 차지한다",
            "77.3%",
        )]}}
    if text == "2025년 수출 매출 비중은 57.2%다.":
        return {GROUNDING_KEY: {NUMERIC_KEY: [_numeric(
            "수출 매출 비중은 57.2%", "수출 매출 비중", "2",
            "2025년 수출 매출 비중은 57.2%다", "57.2%",
        )]}}
    if text == "2025년 연결 매출액은 8,219억 원으로 전년 5,665억 원보다 45.1% 늘었다.":
        quote = "2025년 연결 매출액은 8,219억 원으로 전년(2024년) 5,665억 원 대비 45.1% 증가했고, 연간 영업이익은 1,389억 원이다."
        expression = "2025년 연결 매출액은 8,219억 원으로 전년 5,665억 원보다 45.1% 늘었다"
        return {GROUNDING_KEY: {NUMERIC_KEY: [
            _numeric(expression, "연결 매출액", "2", quote, "8,219억 원", candidate_value="8,219억 원"),
            _numeric(expression, "연결 매출액", "2", quote, "5,665억 원", candidate_value="5,665억 원"),
            _numeric(expression, "연결 매출액", "2", quote, "45.1%", candidate_value="45.1%"),
        ]}}
    if text == "Blue Garage 영업이익률은 2024년 3.4%에서 2025년 7.9%로 올랐다.":
        quote = "Blue Garage 영업이익률은 2024년 3.4%에서 2025년 7.9%로 상승했다."
        expression = "Blue Garage 영업이익률은 2024년 3.4%에서 2025년 7.9%로 올랐다"
        return {GROUNDING_KEY: {NUMERIC_KEY: [
            _numeric(expression, "Blue Garage 영업이익률", "4", quote, "3.4%", candidate_value="3.4%"),
            _numeric(expression, "Blue Garage 영업이익률", "4", quote, "7.9%", candidate_value="7.9%"),
        ], TREND_KEY: [{
            "표현": expression,
            "항목": "Blue Garage 영업이익률",
            "방향": "증가",
            "관측": [
                _point("4", quote, "Blue Garage 영업이익률", "2024", "3.4%"),
                _point("4", quote, "Blue Garage 영업이익률", "2025", "7.9%"),
            ],
        }]}}
    if text == "연결 매출은 2024년 5,665억 원에서 2025년 8,219억 원으로 늘었다.":
        quote = "2025년 연결 매출액은 8,219억 원으로 전년(2024년) 5,665억 원 대비 45.1% 증가했고, 연간 영업이익은 1,389억 원이다."
        expression = "연결 매출은 2024년 5,665억 원에서 2025년 8,219억 원으로 늘었다"
        return {GROUNDING_KEY: {NUMERIC_KEY: [
            _numeric(expression, "연결 매출", "2", quote, "5,665억 원", "연결 매출액", "5,665억 원"),
            _numeric(expression, "연결 매출", "2", quote, "8,219억 원", "연결 매출액", "8,219억 원"),
        ], TREND_KEY: [{
            "표현": expression,
            "항목": "연결 매출",
            "방향": "증가",
            "관측": [
                _point("2", quote, "연결 매출액", "2024", "5,665억 원"),
                _point("2", quote, "연결 매출액", "2025", "8,219억 원"),
            ],
        }]}}
    if text == "2026년 2분기 잠정 실적에서 공연 매출은 35.7%, MD 매출은 34.5% 감소했고 영업이익은 41.4% 줄었다.":
        quote = "2026년 2분기 잠정 실적에서 공연 매출은 35.7%, MD 매출은 34.5% 감소해 영업이익이 41.4% 줄었고, 전사 영업이익률은 16.9%였다."
        return {GROUNDING_KEY: {NUMERIC_KEY: [
            _numeric("공연 매출은 35.7%", "공연 매출", "5", quote, "35.7%"),
            _numeric("MD 매출은 34.5%", "MD 매출", "5", quote, "34.5%"),
            _numeric("영업이익은 41.4%", "영업이익", "5", quote, "41.4%"),
        ]}}
    if text == "같은 분기 전사 영업이익률은 16.9%였다.":
        return {GROUNDING_KEY: {NUMERIC_KEY: [_numeric(
            "전사 영업이익률은 16.9%", "전사 영업이익률", "5",
            "2026년 2분기 잠정 실적에서 공연 매출은 35.7%, MD 매출은 34.5% 감소해 영업이익이 41.4% 줄었고, 전사 영업이익률은 16.9%였다.", "16.9%",
        )]}}
    if text == "MD 해외 판매는 Merch Traffic과 협력하며 Blue Garage는 100% 자회사다.":
        return {GROUNDING_KEY: {NUMERIC_KEY: [_numeric(
            "Blue Garage는 100%", "Blue Garage", "7",
            "Blue Garage는 100% 자회사다", "100%",
        )]}}
    if text == "2025년 연결 영업이익률 비교에서 SM은 15.6%, YG는 13.1%, HYBE는 1.9%로 집계됐다.":
        quote = "2025년 연결 영업이익률 비교에서 SM은 15.6%, YG는 13.1%, HYBE는 1.9%로 집계됐다. 2025 IFPI Global Album Sales Top 20에서는 Stray Kids 'KARMA'가 2위, 349만 장을 기록했다. 비교군은 국내 상장 종합 엔터테인먼트사 HYBE·SM·YG다."
        return {GROUNDING_KEY: {NUMERIC_KEY: [
            _numeric("SM은 15.6%", "SM", "9", quote, "15.6%"),
            _numeric("YG는 13.1%", "YG", "9", quote, "13.1%"),
            _numeric("HYBE는 1.9%", "HYBE", "9", quote, "1.9%"),
        ]}}
    if text == "2025 IFPI Global Album Sales Top 20에서 Stray Kids 'KARMA'가 2위, 349만 장을 기록했다.":
        return {GROUNDING_KEY: {NUMERIC_KEY: [_numeric(
            "Stray Kids 'KARMA'가 2위, 349만 장", "Stray Kids 'KARMA'", "9",
            "2025년 연결 영업이익률 비교에서 SM은 15.6%, YG는 13.1%, HYBE는 1.9%로 집계됐다. 2025 IFPI Global Album Sales Top 20에서는 Stray Kids 'KARMA'가 2위, 349만 장을 기록했다. 비교군은 국내 상장 종합 엔터테인먼트사 HYBE·SM·YG다.", "349만", candidate_value="349만",
        )]}}
    if text == "2025년 연결 매출은 8,219억 원으로 전년보다 45.1% 늘었다.":
        quote = "2025년 연결 매출액은 8,219억 원으로 전년(2024년) 5,665억 원 대비 45.1% 증가했고, 연간 영업이익은 1,389억 원이다."
        expression = "2025년 연결 매출은 8,219억 원으로 전년보다 45.1% 늘었다"
        return {GROUNDING_KEY: {NUMERIC_KEY: [
            _numeric(expression, "연결 매출", "2", quote, "8,219억 원", "연결 매출액", "8,219억 원"),
            # 비교율은 같은 원문 조각의 두 시점·두 금액에 결속한다. 생산 검증은
            # 이 표현처럼 항목명이 생략된 비교율도 거짓으로 만들지 않아야 한다.
            _numeric(expression, "연결 매출", "2", quote, "45.1%", "연결 매출액", "45.1%"),
        ], TREND_KEY: [{
            "표현": expression,
            "항목": "연결 매출",
            "방향": "증가",
            "관측": [
                _point("2", quote, "연결 매출액", "2024", "5,665억 원"),
                _point("2", quote, "연결 매출액", "2025", "8,219억 원"),
            ],
        }]}}
    if text == "2026년 2분기에는 공연·MD 감소로 영업이익이 41.4% 줄어 이익 전환 과제가 남아 있다.":
        return {GROUNDING_KEY: {NUMERIC_KEY: [_numeric(
            "영업이익이 41.4%", "영업이익", "5", "2026년 2분기 잠정 실적에서 공연 매출은 35.7%, MD 매출은 34.5% 감소해 영업이익이 41.4% 줄었고, 전사 영업이익률은 16.9%였다.", "41.4%",
        )]}}
    return None


def grounded_review_response(
    prompt: str,
    *,
    false_texts: Iterable[str] = (),
) -> str:
    """프롬프트의 장·인용을 반사하고, 정직한 JYP 수치 근거만 덧붙인다."""

    forced_false = frozenset(str(text).strip() for text in false_texts)
    verdicts: list[dict[str, object]] = []
    for item in review_items(prompt):
        proof = _proof_for_jyp_sentence(item)
        result = "거짓" if item.text.strip() in forced_false else "참"
        # fixture가 아직 결속 근거를 만들지 못한 정상 후보를 «거짓»으로 바꿔
        # 재작성 호출 수를 부풀리지 않는다. 필요한 근거가 없으면 필드를 명시적으로
        # 비워 production 경계가 별도 공개 제외로 처리한다.
        entry: dict[str, object] = {
            "번호": item.number,
            "장": item.section,
            "근거": list(item.citations),
            "결과": result,
        }
        if proof is not None and result == "참":
            entry.update(proof)
        verdicts.append(entry)
    return json.dumps({"판정": verdicts}, ensure_ascii=False)


def grounded_flow_response(numbers: Sequence[int]) -> str:
    """별도 도식 검수의 기존 계약을 유지한다."""

    return json.dumps(
        {"판정": [{"번호": number, "결과": "참"} for number in numbers]},
        ensure_ascii=False,
    )
