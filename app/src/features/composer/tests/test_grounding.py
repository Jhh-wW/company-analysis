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


# ══════════════════════════════════════════════════════════
# 배율 어휘 확장 · 원문 단위 머리말 (2026-09-11 인텍에프에이 실측)
# ══════════════════════════════════════════════════════════

#: 실제 감사보고서 주석 11 (2)의 지급보증 표. 태그를 걷어 한 줄로 편 모양이라
#: «표 전체가 한 구절 안에 있는» 가장 통과하기 쉬운 조건이다 — 그런데도
#: 합계를 특정 제공처의 금액으로 옮긴 문장은 막혀야 한다.
GUARANTEE_NOTE = (
    "당기말 현재 당사가 타인으로부터 제공받은 지급보증의 내역은 다음과 같습니다. "
    "(단위: 천원) 제공자 보증내용 제공처 보증금액 "
    "대표이사 연대보증 서울보증보험 5,784,949 기업은행 1,295,000 "
    "서울보증보험 이행계약 등 - 8,111,281 합 계 15,191,230"
)
#: 같은 표에서 단위 머리말만 뗀 대조군. 단위를 모르는 맨 숫자는 결속하지 않는다.
GUARANTEE_NOTE_WITHOUT_HEADER = GUARANTEE_NOTE.replace("(단위: 천원) ", "")
WRONG_GUARANTEE_SENTENCE = (
    "서울보증보험에 대해 총 15,191,230천원 규모의 지급보증을 설정하고 있다."
)
RIGHT_GUARANTEE_SENTENCE = (
    "당기말 현재 회사가 제공받은 지급보증에는 기업은행 1,295,000천원이 포함된다."
)


def test_천원_금액_문장은_수치_결속을_요구받는다() -> None:
    """전에는 «천원»이 배율로 안 읽혀 수치 결속이 아예 요구되지 않았다.

    요구되지 않으면 관문을 «통과»한 것이 아니라 관문 밖으로 나간 것이다 —
    방향·귀속이 뒤집힌 이 문장이 그렇게 검사 없이 공개됐다.
    """
    assert NUMERIC_KEY in grounding_requirements(
        WRONG_GUARANTEE_SENTENCE, (GUARANTEE_NOTE,)
    )
    # 검증근거를 대지 않으면 «빠뜨림»으로 닫힌다(fail-closed).
    assert grounding_problem(WRONG_GUARANTEE_SENTENCE, {"공시": GUARANTEE_NOTE}, {})


def test_합계_금액을_특정_상대의_금액으로_옮긴_문장을_막는다() -> None:
    """합계 15,191,230천원은 서울보증보험분(13,896,230천원)이 아니다."""
    for source_metric in ("지급보증", "합 계"):
        proof = {"검증근거": {NUMERIC_KEY: [{
            "표현": "총 15,191,230천원 규모의 지급보증",
            "항목": "지급보증",
            "근거": "공시",
            "원문": GUARANTEE_NOTE,
            "원문항목": source_metric,
            "원문값": "15,191,230",
        }]}}
        assert grounding_problem(
            WRONG_GUARANTEE_SENTENCE, {"공시": GUARANTEE_NOTE}, proof
        ) == GROUNDING_INVALID, source_metric


def test_단위_머리말이_붙은_표의_맨_숫자를_원문값으로_인정한다() -> None:
    """재현율 음성 대조 — 같은 표의 «맞는 문장»은 그대로 통과해야 한다.

    공시 표는 「(단위: 천원)」 머리말 한 줄을 두고 칸에는 맨 숫자만 적는다.
    머리말을 읽지 않으면 이 문장도 함께 조용히 사라진다.
    """
    proof = {"검증근거": {NUMERIC_KEY: [{
        "표현": "기업은행 1,295,000천원",
        "항목": "기업은행",
        "근거": "공시",
        "원문": GUARANTEE_NOTE,
        "원문항목": "기업은행",
        "원문값": "1,295,000",
    }]}}
    assert grounding_problem(
        RIGHT_GUARANTEE_SENTENCE, {"공시": GUARANTEE_NOTE}, proof
    ) == ""
    # 머리말이 없으면 단위를 모르는 수이므로 결속하지 않는다.
    without = dict(proof["검증근거"][NUMERIC_KEY][0])
    without["원문"] = GUARANTEE_NOTE_WITHOUT_HEADER
    assert grounding_problem(
        RIGHT_GUARANTEE_SENTENCE,
        {"공시": GUARANTEE_NOTE_WITHOUT_HEADER},
        {"검증근거": {NUMERIC_KEY: [without]}},
    ) == GROUNDING_INVALID


def test_표의_다른_행_금액을_빌려온_문장은_막힌다() -> None:
    """1,295,000은 기업은행 행, 5,784,949는 대표이사 행의 값이다."""
    proof = {"검증근거": {NUMERIC_KEY: [{
        "표현": "기업은행 5,784,949천원",
        "항목": "기업은행",
        "근거": "공시",
        "원문": GUARANTEE_NOTE,
        "원문항목": "기업은행",
        "원문값": "5,784,949",
    }]}}
    text = "당기말 현재 회사가 제공받은 지급보증에는 기업은행 5,784,949천원이 포함된다."
    assert grounding_problem(text, {"공시": GUARANTEE_NOTE}, proof) == GROUNDING_INVALID


def test_반올림으로_지워진_원값을_인용한_문장이_수치_결속에_성공한다() -> None:
    """실적표 결속 원문이 표시값만 실으면 원값 문장이 근거를 못 댄다.

    억원 표시값은 당기순이익을 「1억원 / 4억원」으로 눌러 실제 변동
    (82,552,618원 → 366,016,342원)을 말한 문장이 통째로 떨어졌다.
    """
    source = (
        "2025년 | 당기순이익 | 1억원\n"
        "2025년 | 당기순이익 | 82,552,618원 (원값)\n"
        "2024년 | 당기순이익 | 4억원\n"
        "2024년 | 당기순이익 | 366,016,342원 (원값)"
    )
    for value, quote in (
        ("82,552,618원", "2025년 | 당기순이익 | 82,552,618원"),
        ("1억원", "2025년 | 당기순이익 | 1억원"),
    ):
        text = f"2025년 당기순이익은 {value}으로 집계됐다."
        proof = {"검증근거": {NUMERIC_KEY: [_numeric(
            f"당기순이익은 {value}", "당기순이익", quote, value, source_id="실적표",
        )]}}
        assert grounding_problem(text, {"실적표": source}, proof) == "", value
    # 다른 해의 원값을 그 해 값으로 옮기면 막힌다.
    borrowed = "2025년 당기순이익은 366,016,342원으로 집계됐다."
    proof = {"검증근거": {NUMERIC_KEY: [_numeric(
        "당기순이익은 366,016,342원", "당기순이익",
        "2025년 | 당기순이익 | 82,552,618원", "366,016,342원", source_id="실적표",
    )]}}
    assert grounding_problem(borrowed, {"실적표": source}, proof) == GROUNDING_INVALID


# ══════════════════════════════════════════════════════════
# 차원 분리 · 머리말 어휘 가드 (2026-09-11 독립 검토 P2-1·P2-2·P3-1)
# ══════════════════════════════════════════════════════════

#: 실제 코퍼스 문장 그대로 — 생산 «수량»이 원 금액 주장의 근거가 되던 자리.
COUNT_SOURCE = "SDC의 디스플레이 패널 생산실적은 1,851천개(8세대 Glass 환산 기준)이며"
#: 실제 코퍼스 문장 그대로 — 외화가 원 금액 주장의 근거가 되던 자리.
CURRENCY_SOURCE = "2021.11월 자본금 100백만불 유상증자 완료"


def _judge(text: str, expression: str, metric: str, source_value: str, quote: str) -> str:
    proof = {"검증근거": {NUMERIC_KEY: [{
        "표현": expression,
        "항목": metric,
        "근거": "공시",
        "원문": quote,
        "원문항목": metric,
        "원문값": source_value,
    }]}}
    return grounding_problem(text, {"공시": quote}, proof)


def test_수량_근거는_원_금액_주장을_뒷받침하지_못한다() -> None:
    """「1,851천개」가 「1,851천원」의 근거가 됐다 — 차원 판정이 꼬리 단위만 봤다."""
    assert _judge(
        "생산실적은 1,851천원이다.", "생산실적은 1,851천원", "생산실적",
        "1,851천개", COUNT_SOURCE,
    ) == GROUNDING_INVALID
    # 표지를 떼어 적는 것만으로 빠져나가지 못한다 — 값의 범위가 표지까지다.
    assert _judge(
        "생산실적은 1,851천원이다.", "생산실적은 1,851천원", "생산실적",
        "1,851천", COUNT_SOURCE,
    ) == GROUNDING_INVALID
    # 조사가 바로 붙은 모양도 같다.
    assert _judge(
        "생산실적은 1,851천원이다.", "생산실적은 1,851천원", "생산실적",
        "1,851천", "패널 생산실적은 1,851천개이며 전년과 같다.",
    ) == GROUNDING_INVALID


def test_외화_근거는_원_금액_주장을_뒷받침하지_못한다() -> None:
    """「100백만불」이 「100백만원」의 근거가 됐다. 표지를 떼어 적어도 막힌다."""
    for source_value in ("100백만불", "100백만"):
        assert _judge(
            "자본금 100백만원 유상증자를 완료했다.", "자본금 100백만원", "자본금",
            source_value, CURRENCY_SOURCE,
        ) == GROUNDING_INVALID, source_value
    for source_value in ("9,937백만달러", "9,937백만"):
        assert _judge(
            "약정한도액 9,937백만원을 보유하고 있다.", "약정한도액 9,937백만원",
            "약정한도액", source_value,
            "당기말 현재 약정한도액 9,937백만달러를 보유하고 있습니다.",
        ) == GROUNDING_INVALID, source_value


def test_원_금액_근거는_그대로_결속된다() -> None:
    """차원을 갈랐다고 정상 금액 결속이 막히면 안 된다 (재현율 음성 대조)."""
    assert _judge(
        "매출액 15,191,230천원을 기록했다.", "매출액 15,191,230천원", "매출액",
        "15,191,230천원", "당기 매출액 15,191,230천원을 기록하였습니다.",
    ) == ""


def test_단위_머리말_배율은_수량_비율_주식_칸에_붙지_않는다() -> None:
    """머리말 배율은 그 표의 «금액» 칸에만 해당한다.

    같은 표에 섞인 직원수·발행주식총수·매출비중에 배율을 붙이면
    「직원수 1,200」이 「직원수 120만원」의 근거가 된다.
    """
    for text, expression, metric, source_value, quote in (
        ("직원수 120만원을 기록했다.", "직원수 120만원", "직원수", "1,200",
         "(단위: 천원) 직원수 1,200 평균급여 45,000"),
        ("발행주식총수 100만원을 기록했다.", "발행주식총수 100만원", "발행주식총수",
         "1,000,000", "(단위: 원) 발행주식총수 1,000,000 자본금 5,000,000"),
        ("매출비중 3,702만원을 기록했다.", "매출비중 3,702만원", "매출비중", "37.02",
         "(단위: 백만원) 매출비중 37.02 영업이익 1,234"),
    ):
        assert _judge(text, expression, metric, source_value, quote) == GROUNDING_INVALID, metric
    # 같은 줄의 «금액» 칸은 그대로 결속된다.
    assert _judge(
        "매출액 15,191,230천원을 기록했다.", "매출액 15,191,230천원", "매출액",
        "15,191,230", "(단위: 천원) 매출액 15,191,230",
    ) == ""
    # 앞 칸의 이름이 뒤 칸까지 번지지 않는다 — 직원수 뒤의 평균급여는 금액이다.
    assert _judge(
        "평균급여 4,500만원을 지급했다.", "평균급여 4,500만원", "평균급여", "45,000",
        "(단위: 천원) 직원수 1,200 평균급여 45,000",
    ) == ""


def test_단위_머리말_표기_변형을_읽는다() -> None:
    """실측 59회 중 절반을 못 읽던 표기 변형 — 꼬리·공백·대괄호·전각 괄호."""
    for quote in (
        "(단위: 백만원, %) 매출액 1,234",
        "(단위 : 천 원, 주) 매출액 1,234,000",
        "[단위: 천원] 매출액 1,234,000",
        "（단위: 천원） 매출액 1,234,000",
    ):
        value = quote.rsplit(" ", 1)[1]
        expected = "1,234백만원" if "백만원" in quote else "1,234,000천원"
        assert _judge(
            f"매출액 {expected}을 기록했다.", f"매출액 {expected}", "매출액", value, quote,
        ) == "", quote


def test_원_단위가_아닌_머리말은_배율로_읽지_않는다() -> None:
    """「(단위: 주)」·「(단위:USD)」는 원 금액 머리말이 아니다 (fail-closed)."""
    for quote in ("(단위: 주) 발행총수 1,234", "(단위:USD) 한도액 1,234"):
        value = quote.rsplit(" ", 1)[1]
        metric = quote.rsplit(" ", 2)[1]
        assert _judge(
            f"{metric} 1,234원을 기록했다.", f"{metric} 1,234원", metric, value, quote,
        ) == GROUNDING_INVALID, quote
