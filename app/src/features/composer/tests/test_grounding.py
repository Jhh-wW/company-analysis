"""수치 의미·추세·활동시점의 결정론적 결속 경계를 검증한다."""

from __future__ import annotations

from src.features.composer.grounding import (
    grounding_problem,
    grounding_requirements,
)
from src.features.composer.grounding_constants import (
    GROUNDING_INVALID,
    NUMERIC_KEY,
    TIME_KEY,
    TREND_KEY,
)


def _numeric(
    expression: str,
    metric: str,
    quote: str,
    value: str,
    source_id: str = "공시",
) -> dict[str, str]:
    return {
        "표현": expression,
        "항목": metric,
        "근거": source_id,
        "원문": quote,
        "원문항목": metric,
        "원문값": value,
    }


def _point(period: str, metric: str, value: str) -> dict[str, str]:
    quote = f"{metric} | {period}년 | {value}"
    return {
        "근거": "공시",
        "원문": quote,
        "항목": metric,
        "기간": period,
        "원문항목": metric,
        "원문값": value,
    }


def test_woori_fee_continuous_growth_is_rejected_by_all_observations() -> None:
    text = "2025년 수수료부문 수익 7,807억원은 지속적으로 확대되고 있다."
    source = (
        "수수료부문 | 2023년 | 7,272억원\n"
        "수수료부문 | 2024년 | 8,344억원\n"
        "수수료부문 | 2025년 | 7,807억원"
    )
    evidence = {
        "검증근거": {
            NUMERIC_KEY: [
                _numeric(
                    "2025년 수수료부문 수익 7,807억원",
                    "수수료부문",
                    "수수료부문 | 2025년 | 7,807억원",
                    "7,807억원",
                )
            ],
            TREND_KEY: [{
                "표현": "수수료부문 수익 7,807억원은 지속적으로 확대",
                "항목": "수수료부문",
                "방향": "지속증가",
                "관측": [
                    _point("2023", "수수료부문", "7,272억원"),
                    _point("2024", "수수료부문", "8,344억원"),
                    _point("2025", "수수료부문", "7,807억원"),
                ],
            }],
        }
    }

    assert grounding_problem(text, {"공시": source}, evidence) == GROUNDING_INVALID


def test_simple_year_over_year_direction_is_recalculated() -> None:
    text = "2025년 매출액 90억원으로 전년 대비 증가했다."
    source = "매출액 | 2024년 | 100억원\n매출액 | 2025년 | 90억원"
    evidence = {
        "검증근거": {
            NUMERIC_KEY: [
                _numeric(
                    "2025년 매출액 90억원",
                    "매출액",
                    "매출액 | 2025년 | 90억원",
                    "90억원",
                )
            ],
            TREND_KEY: [{
                "표현": "전년 대비 증가",
                "항목": "매출액",
                "방향": "증가",
                "관측": [
                    _point("2024", "매출액", "100억원"),
                    _point("2025", "매출액", "90억원"),
                ],
            }],
        }
    }

    assert TREND_KEY in grounding_requirements(text, (source,))
    assert grounding_problem(text, {"공시": source}, evidence) == GROUNDING_INVALID


def test_same_metric_increasing_series_passes() -> None:
    text = "2025년 매출액 120억원은 지속적으로 증가했다."
    source = (
        "매출액 | 2023년 | 80억원\n"
        "매출액 | 2024년 | 100억원\n"
        "매출액 | 2025년 | 120억원"
    )
    evidence = {
        "검증근거": {
            NUMERIC_KEY: [
                _numeric(
                    "2025년 매출액 120억원",
                    "매출액",
                    "매출액 | 2025년 | 120억원",
                    "120억원",
                )
            ],
            TREND_KEY: [{
                "표현": "매출액 120억원은 지속적으로 증가",
                "항목": "매출액",
                "방향": "지속증가",
                "관측": [
                    _point("2023", "매출액", "80억원"),
                    _point("2024", "매출액", "100억원"),
                    _point("2025", "매출액", "120억원"),
                ],
            }],
        }
    }

    assert grounding_problem(text, {"공시": source}, evidence) == ""


def test_woori_trust_subtotal_cannot_be_renamed_as_management_fee() -> None:
    text = "수탁 자산 규모에 따른 운용 수수료(신탁부문 2,123억원)"
    source = (
        "신탁업무운용수익 | 2025년 | 1,977억원\n"
        "중도해지수수료 | 2025년 | 192억원\n"
        "신탁업무운용손실 | 2025년 | 46억원\n"
        "신탁부문 | 2025년 | 2,123억원"
    )
    evidence = {
        "검증근거": {
            NUMERIC_KEY: [
                _numeric(
                    "운용 수수료(신탁부문 2,123억원)",
                    "신탁부문",
                    "신탁부문 | 2025년 | 2,123억원",
                    "2,123억원",
                )
            ]
        }
    }

    assert grounding_problem(text, {"공시": source}, evidence) == GROUNDING_INVALID


def test_unrelated_historical_clause_cannot_prove_present_claim() -> None:
    text = "2025년 성과를 평가했다. 자산 리밸런싱을 추진하고 있다."
    source = (
        "2025년 경영성과를 평가했다. 우량자산 중심 자산 리밸런싱을 추진하고 "
        "고객 기반 확대 목표를 달성함."
    )
    evidence = {
        "검증근거": {
            TIME_KEY: [{
                "표현": "2025년 성과를 평가했다",
                "근거": "공시",
                "원문": "2025년 경영성과를 평가했다",
                "기간": "2025",
            }]
        }
    }

    assert TIME_KEY in grounding_requirements(text, (source,))
    assert grounding_problem(text, {"공시": source}, evidence) == GROUNDING_INVALID


def test_explicit_current_source_preserves_current_claim() -> None:
    text = "사업성과 평가와 별도로 현재 자산 리밸런싱을 추진하고 있다."
    source = (
        "2025년 사업성과를 평가했다. "
        "현재 자산 리밸런싱을 추진하고 있다."
    )
    expression = "현재 자산 리밸런싱을 추진하고 있다"
    evidence = {
        "검증근거": {
            TIME_KEY: [{
                "표현": expression,
                "근거": "공시",
                "원문": expression,
                "기간": "현재",
            }]
        }
    }

    assert grounding_problem(text, {"공시": source}, evidence) == ""


def test_planned_or_qualitative_expansion_is_not_forced_into_trend_math() -> None:
    planned = "회사는 100억원 규모 투자를 지속적으로 확대할 계획이다."
    qualitative = "회사는 고객 기반을 지속적으로 확대하고 있다."

    assert TREND_KEY not in grounding_requirements(
        planned, ("회사는 향후 100억원을 투자할 계획이다.",)
    )
    assert grounding_requirements(
        qualitative, ("회사는 고객 접점을 넓힐 계획이다.",)
    ) == ()


def test_multi_year_same_metric_keeps_each_value_and_reported_growth() -> None:
    text = "2025년 연결 매출액은 8,219억 원으로 전년 5,665억 원보다 45.1% 늘었다."
    quote = "2025년 연결 매출액은 8,219억 원으로 전년(2024년) 5,665억 원 대비 45.1% 증가했다."
    metric = "연결 매출액"
    numeric = [dict(_numeric(text, metric, quote, value), 후보값=value)
               for value in ("8,219억 원", "5,665억 원", "45.1%")]
    points = [dict(_numeric(text, metric, quote, value), 기간=year)
              for year, value in (("2024", "5,665억 원"), ("2025", "8,219억 원"))]
    proof = {"검증근거": {NUMERIC_KEY: numeric, TREND_KEY: [{
        "표현": "전년 5,665억 원보다 45.1% 늘었다", "항목": metric,
        "방향": "증가", "관측": points,
    }]}}
    assert grounding_problem(text, {"공시": quote}, proof) == ""


def test_multi_year_profit_margin_can_share_one_honest_quote() -> None:
    text = "제품군 영업이익률은 2024년 3.4%에서 2025년 7.9%로 올랐다."
    quote = text.replace("올랐다", "상승했다")
    numeric = [dict(_numeric(text, "제품군 영업이익률", quote, value), 후보값=value)
               for value in ("3.4%", "7.9%")]
    points = [dict(_numeric(text, "제품군 영업이익률", quote, value), 기간=year)
              for year, value in (("2024", "3.4%"), ("2025", "7.9%"))]
    proof = {"검증근거": {NUMERIC_KEY: numeric, TREND_KEY: [{
        "표현": "2024년 3.4%에서 2025년 7.9%로 올랐다", "항목": "제품군 영업이익률",
        "방향": "증가", "관측": points,
    }]}}
    assert grounding_problem(text, {"공시": quote}, proof) == ""


def test_multi_year_number_cannot_be_attached_to_wrong_year() -> None:
    text = "매출액은 2024년 120억원, 2025년 100억원으로 집계됐다."
    quote = "매출액은 2024년 100억원, 2025년 120억원이다."
    numeric = [dict(_numeric(text, "매출액", quote, value), 후보값=value)
               for value in ("120억원", "100억원")]
    assert grounding_problem(text, {"공시": quote}, {"검증근거": {NUMERIC_KEY: numeric}}) == GROUNDING_INVALID


def test_shared_expression_does_not_cover_same_candidate_occurrence_twice() -> None:
    text = "매출액은 2024년 100억원에서 2025년 120억원이 됐다."
    quote = "매출액은 2024년 100억원, 2025년 120억원이다."
    entry = dict(_numeric(text, "매출액", quote, "100억원"), 후보값="100억원")
    assert grounding_problem(text, {"공시": quote}, {"검증근거": {NUMERIC_KEY: [entry, entry]}}) == GROUNDING_INVALID


def test_numeric_binding_does_not_cross_a_different_metric() -> None:
    text = "매출액은 120억원으로 집계됐다."
    quote = "매출액은 100억원, 영업이익은 120억원이다."
    proof = {"검증근거": {NUMERIC_KEY: [_numeric(text, "매출액", quote, "120억원")]}}
    assert grounding_problem(text, {"공시": quote}, proof) == GROUNDING_INVALID


def test_postfixed_metric_cannot_steal_previous_rows_amount() -> None:
    text = "영업이익은 100억원으로 집계됐다."
    quote = "매출액 100억원 영업이익 20억원"
    proof = {"검증근거": {NUMERIC_KEY: [_numeric(text, "영업이익", quote, "100억원")]}}
    assert grounding_problem(text, {"공시": quote}, proof) == GROUNDING_INVALID


def test_reported_rate_cannot_borrow_direction_from_another_metric() -> None:
    text = "2026년 공연 매출은 전년 대비 35.7% 감소했다."
    quote = "2026년 공연 매출은 전년 대비 35.7% 증가했다. MD 매출은 전년 대비 35.7% 감소했다."
    proof = {"검증근거": {NUMERIC_KEY: [_numeric(text, "공연 매출", quote, "35.7%")]}}
    assert grounding_problem(text, {"공시": quote}, proof)


def test_present_entity_only_cannot_prove_another_current_activity() -> None:
    text = "현재 차세대플랫폼 분석서비스를 해외에 상용화한다."
    source = ("2023년 차세대플랫폼 분석서비스 출시를 완료했다. "
              "현재 차세대플랫폼 분석서비스의 국내 고객지원만 제공하고 있다.")
    proof = {"검증근거": {TIME_KEY: [{
        "표현": "현재 차세대플랫폼 분석서비스", "근거": "공시",
        "원문": "현재 차세대플랫폼 분석서비스의 국내 고객지원만 제공하고 있다.", "기간": "현재",
    }]}}
    assert grounding_problem(text, {"공시": source}, proof)


def test_normalization_never_erases_decimal_point_or_negative_sign() -> None:
    for text, source in (("매출액은 12억원이다.", "매출액은 1.2억원이다."),
                         ("영업이익은 100억원이다.", "영업이익은 -100억원이다.")):
        assert NUMERIC_KEY in grounding_requirements(text, (source,))
        assert grounding_problem(text, {"공시": source}, {})


def test_loss_recovery_direction_uses_signed_values() -> None:
    text = "2025년 영업이익은 -20억원으로 전년 대비 증가했다."
    prior, current = "영업이익 | 2024년 | -100억원", "영업이익 | 2025년 | -20억원"
    proof = {"검증근거": {
        NUMERIC_KEY: [_numeric("2025년 영업이익은 -20억원", "영업이익", current, "-20억원")],
        TREND_KEY: [{"표현": "전년 대비 증가", "항목": "영업이익", "방향": "증가",
                    "관측": [dict(_point("2024", "영업이익", "-100억원")),
                             dict(_point("2025", "영업이익", "-20억원"))]}],
    }}
    assert grounding_problem(text, {"공시": prior + "\n" + current}, proof) == ""


def test_negative_amount_cannot_be_approved_as_positive_with_exact_quote() -> None:
    text, quote = "영업이익은 100억원이다.", "영업이익은 -100억원이다."
    proof = {"검증근거": {NUMERIC_KEY: [_numeric(text, "영업이익", quote, "-100억원")]}}
    assert grounding_problem(text, {"공시": quote}, proof) == GROUNDING_INVALID
