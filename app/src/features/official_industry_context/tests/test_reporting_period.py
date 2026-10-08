"""보고기간의 관찰과 공표일·미래·해소 상태를 합성 원문으로 구분한다."""

from src.features.official_industry_context import constants as c
from src.features.official_industry_context.tests.test_logic import material, run


def test_observed_previous_reporting_year_keeps_explicit_period():
    text = "2025년 국내 산업용 센서 시장에서는 부품 공급 부족으로 생산 차질이 발생했습니다."
    result, diagnostics, calls = run(
        pairs=(material(text, title="사업보고서 (2025.12)"),),
        modify=lambda row, _: row.update(observation_period="2025년"),
    )
    assert len(result) == 1
    assert result[0].observation_period == "2025년"
    assert result[0].assessment_quote == result[0].exact_text == text
    assert diagnostics["model_calls"] == len(calls) == 1
    assert "보고기간의 관찰을 오늘 현재 회사 피해로 단정하지 말고" in calls[0][0]
    assert c.PROMPT_VERSION == "official-industry-context-v2"


def test_future_period_after_publication_is_not_current_observation():
    text = "2027년 국내 산업용 센서 시장에서는 부품 공급 부족으로 생산 차질이 발생할 전망입니다."
    result, diagnostics, calls = run(
        pairs=(material(text, title="사업보고서 (2026.09)"),),
        modify=lambda row, _: row.update(observation_period="2027년"),
    )
    assert result == ()
    assert diagnostics["model_calls"] == len(calls) == 1


def test_resolved_problem_is_not_revived_by_supported_reporting_year():
    text = "2025년 국내 산업용 센서 시장의 부품 공급 부족은 현재 해소됐습니다."
    result, diagnostics, calls = run(
        pairs=(material(text, title="사업보고서 (2025.12)"),),
        modify=lambda row, _: row.update(observation_period="2025년"),
    )
    assert result == ()
    assert diagnostics["model_calls"] == len(calls) == 1
