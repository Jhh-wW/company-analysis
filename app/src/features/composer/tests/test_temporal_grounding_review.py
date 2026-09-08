"""시점·추세 결속의 독립 반례 검수.

이 시험은 특정 회사명이나 지표 예외를 사용하지 않고, 입력
조각의 활동 기간·항목·값과 후보 문장의 주장이 같은 사실인지만 본다.
오류 주장을 거절하는 것과 동일하게, 근거가 있는 현재·계획·정성 설명을
과잉 삭제하지 않는 것도 공개 계약이다.
"""

from __future__ import annotations

from src.features.composer.grounding import (
    grounding_problem,
    grounding_requirements,
)
from src.features.composer.grounding_constants import (
    GROUNDING_INVALID,
    GROUNDING_MISSING,
    NUMERIC_KEY,
    TIME_KEY,
    TREND_KEY,
)


SOURCE_ID = "공시"
REVENUE_METRIC = "영업수익"


def _numeric_entry(
    expression: str,
    quote: str,
    value: str,
    *,
    metric: str = REVENUE_METRIC,
) -> dict[str, str]:
    return {
        "표현": expression,
        "항목": metric,
        "근거": SOURCE_ID,
        "원문": quote,
        "원문항목": metric,
        "원문값": value,
    }


def _trend_point(period: str, quote: str, value: str) -> dict[str, str]:
    return {
        "근거": SOURCE_ID,
        "원문": quote,
        "항목": REVENUE_METRIC,
        "기간": period,
        "원문항목": REVENUE_METRIC,
        "원문값": value,
    }


def _time_entry(expression: str, quote: str) -> dict[str, str]:
    return {
        "표현": expression,
        "근거": SOURCE_ID,
        "원문": quote,
        "기간": "현재",
    }


def test_과거_완료사업을_현재_진행으로_바꾸면_근거를_요구한다() -> None:
    text = "현재 스마트물류 물류허브 운영시스템 구축을 추진하고 있다."
    source = "2022년 스마트물류 물류허브 운영시스템 구축을 완료했다."

    assert TIME_KEY in grounding_requirements(text, (source,))
    assert grounding_problem(text, {SOURCE_ID: source}, {}) == GROUNDING_MISSING


def test_무관한_현재절은_과거_출시의_현재화_근거가_아니다() -> None:
    text = "현재 차세대플랫폼 분석서비스를 상용화한다."
    source = (
        "2023년 차세대플랫폼 분석서비스 출시를 완료했다. "
        "현재 고객지원시스템을 운영하고 있다."
    )
    unrelated_present = "현재 고객지원시스템을 운영하고 있다"
    evidence = {
        "검증근거": {
            TIME_KEY: [_time_entry("현재", unrelated_present)],
        }
    }

    # 단순히 '현재'라는 표지를 같은 조각의 다른 활동에서 빌려와
    # 과거 출시를 현재 상용화로 바꾸는 것은 거절해야 한다.
    assert grounding_problem(text, {SOURCE_ID: source}, evidence) == GROUNDING_INVALID


def test_현재_대상만_같아도_다른_현재활동은_출시_현재화_근거가_아니다() -> None:
    text = "현재 차세대플랫폼 분석서비스를 해외에 상용화한다."
    current_support = (
        "현재 차세대플랫폼 분석서비스의 국내 고객지원만 제공하고 있다"
    )
    source = (
        "2023년 차세대플랫폼 분석서비스 출시를 완료했다. "
        f"{current_support}."
    )
    evidence = {
        "검증근거": {
            TIME_KEY: [_time_entry("현재 차세대플랫폼 분석서비스", current_support)],
        }
    }

    # 현재 대상명의 일치만으로 해외 상용화 활동을 입증할 수 없다.
    assert grounding_problem(text, {SOURCE_ID: source}, evidence) == GROUNDING_INVALID


def test_무관한_과거절은_근거있는_현재설명을_과잉제거하지_않는다() -> None:
    text = "현재 고객지원 상담센터를 계속 운영 중이다."
    source = (
        "2024년 신형 제조설비 구축을 완료했다. "
        "현재 고객지원 상담센터 운영을 이어가고 있다."
    )

    # 과거 절 뒤의 전체 문자열을 비교하면 뒤에 나온 별개 현재 절이
    # 과거 사건에 잘못 묶인다. 시점 검증은 활동 절 경계를 넘지 않아야 한다.
    assert grounding_requirements(text, (source,)) == ()


def test_명시_현재_계획_정성설명은_별도_추세수치를_요구하지_않는다() -> None:
    current = "현재 연구개발센터를 운영하고 있다."
    planned = "신규 서비스 채널을 지속적으로 확대할 계획이다."
    qualitative = "고객 접점을 지속적으로 확대하고 있다."

    assert grounding_requirements(current, (current,)) == ()
    assert grounding_requirements(planned, (planned,)) == ()
    assert grounding_requirements(qualitative, (qualitative,)) == ()


def test_정상_혼합문은_과거연도와_별개_현재활동을_모두_보존한다() -> None:
    text = (
        "차세대플랫폼 분석서비스는 2023년에 출시됐고, "
        "현재 고객지원시스템을 운영하고 있다."
    )
    current_clause = "현재 고객지원시스템을 운영하고 있다"
    source = (
        "2023년 차세대플랫폼 분석서비스 출시를 완료했다. "
        f"{current_clause}."
    )
    evidence = {
        "검증근거": {
            TIME_KEY: [_time_entry(current_clause, current_clause)],
        }
    }

    assert grounding_problem(text, {SOURCE_ID: source}, evidence) == ""


def test_전년비교는_중간연도를_넘어_원하는_방향을_고를수_없다() -> None:
    text = "2025년 영업수익 90억원은 전년 대비 감소했다."
    quote_2023 = "영업수익 | 2023년 | 100억원"
    quote_2024 = "영업수익 | 2024년 | 80억원"
    quote_2025 = "영업수익 | 2025년 | 90억원"
    source = "\n".join((quote_2023, quote_2024, quote_2025))
    evidence = {
        "검증근거": {
            NUMERIC_KEY: [
                _numeric_entry("2025년 영업수익 90억원", quote_2025, "90억원")
            ],
            TREND_KEY: [{
                "표현": "전년 대비 감소",
                "항목": REVENUE_METRIC,
                "방향": "감소",
                "관측": [
                    _trend_point("2023", quote_2023, "100억원"),
                    _trend_point("2024", quote_2024, "80억원"),
                    _trend_point("2025", quote_2025, "90억원"),
                ],
            }],
        }
    }

    # 2023→2025는 감소지만 '전년'인 2024→2025는 증가다.
    assert grounding_problem(text, {SOURCE_ID: source}, evidence) == GROUNDING_INVALID


def test_소수점_명시기간_증가표현도_추세_검산_대상이다() -> None:
    text = "제품군 영업이익률은 2024년 3.4%에서 2025년 7.9%로 올랐다."
    source = "제품군 영업이익률은 2024년 3.4%에서 2025년 7.9%로 상승했다."

    required = grounding_requirements(text, (source,))

    assert NUMERIC_KEY in required
    assert TREND_KEY in required


def test_원문이_비교율과_방향을_직접보고하면_원시금액이_없어도_보존한다() -> None:
    metric = "공연 매출"
    text = "2026년 2분기 공연 매출은 전년 동기 대비 35.7% 감소했다."
    source = "2026년 2분기 공연 매출은 전년 동기보다 35.7% 줄었다."
    evidence = {
        "검증근거": {
            NUMERIC_KEY: [
                _numeric_entry(text, source, "35.7%", metric=metric)
            ],
        }
    }

    # 신뢰 원문이 같은 기간·지표·비교율·방향을 직접 보고했다.
    # 원시 매출액 두 개가 없다는 이유로 정상 재서술을 삭제하지 않는다.
    assert grounding_problem(text, {SOURCE_ID: source}, evidence) == ""


def test_원문_보고비교율이_같아도_반대방향은_승인하지_않는다() -> None:
    metric = "공연 매출"
    text = "2026년 2분기 공연 매출은 전년 동기 대비 35.7% 증가했다."
    source = "2026년 2분기 공연 매출은 전년 동기보다 35.7% 줄었다."
    evidence = {
        "검증근거": {
            NUMERIC_KEY: [
                _numeric_entry(text, source, "35.7%", metric=metric)
            ],
        }
    }

    # 비교율만 같고 방향이 반대면 원문 보고 예외가 아니다.
    # 추세 절대값 근거가 빠졌으므로 누락으로 정확히 거절한다.
    assert grounding_problem(text, {SOURCE_ID: source}, evidence) == GROUNDING_MISSING


def test_전분기_감소는_같은_지표의_인접분기_값으로_검산한다() -> None:
    text = "2025년 2분기 영업수익 90억원은 전분기 대비 감소했다."
    quote_q1 = "영업수익 | 2025년 1분기 | 100억원"
    quote_q2 = "영업수익 | 2025년 2분기 | 90억원"
    source = "\n".join((quote_q1, quote_q2))
    evidence = {
        "검증근거": {
            NUMERIC_KEY: [
                _numeric_entry("2025년 2분기 영업수익 90억원", quote_q2, "90억원")
            ],
            TREND_KEY: [{
                "표현": "전분기 대비 감소",
                "항목": REVENUE_METRIC,
                "방향": "감소",
                "관측": [
                    _trend_point("2025Q1", quote_q1, "100억원"),
                    _trend_point("2025Q2", quote_q2, "90억원"),
                ],
            }],
        }
    }

    assert grounding_problem(text, {SOURCE_ID: source}, evidence) == ""


def test_분기_연속증가는_모든_인접분기의_실제값이_올라야_한다() -> None:
    text = "2025년 3분기 영업수익 90억원은 3개 분기 연속 증가했다."
    quote_q1 = "영업수익 | 2025년 1분기 | 80억원"
    quote_q2 = "영업수익 | 2025년 2분기 | 100억원"
    quote_q3 = "영업수익 | 2025년 3분기 | 90억원"
    source = "\n".join((quote_q1, quote_q2, quote_q3))
    evidence = {
        "검증근거": {
            NUMERIC_KEY: [
                _numeric_entry("2025년 3분기 영업수익 90억원", quote_q3, "90억원")
            ],
            TREND_KEY: [{
                "표현": "연속 증가",
                "항목": REVENUE_METRIC,
                "방향": "지속증가",
                "관측": [
                    _trend_point("2025Q1", quote_q1, "80억원"),
                    _trend_point("2025Q2", quote_q2, "100억원"),
                    _trend_point("2025Q3", quote_q3, "90억원"),
                ],
            }],
        }
    }

    assert grounding_problem(text, {SOURCE_ID: source}, evidence) == GROUNDING_INVALID


def test_분기_연속증가의_모든_인접값이_올라가면_승인한다() -> None:
    text = "2025년 3분기 영업수익 120억원은 3개 분기 연속 증가했다."
    quote_q1 = "영업수익 | 2025년 1분기 | 80억원"
    quote_q2 = "영업수익 | 2025년 2분기 | 100억원"
    quote_q3 = "영업수익 | 2025년 3분기 | 120억원"
    source = "\n".join((quote_q1, quote_q2, quote_q3))
    evidence = {
        "검증근거": {
            NUMERIC_KEY: [
                _numeric_entry("2025년 3분기 영업수익 120억원", quote_q3, "120억원")
            ],
            TREND_KEY: [{
                "표현": "연속 증가",
                "항목": REVENUE_METRIC,
                "방향": "지속증가",
                "관측": [
                    _trend_point("2025Q1", quote_q1, "80억원"),
                    _trend_point("2025Q2", quote_q2, "100억원"),
                    _trend_point("2025Q3", quote_q3, "120억원"),
                ],
            }],
        }
    }

    assert grounding_problem(text, {SOURCE_ID: source}, evidence) == ""
