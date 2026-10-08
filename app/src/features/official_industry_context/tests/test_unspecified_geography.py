"""지역 주장을 하지 않는 공식 산업 관찰과 기존 지역 판정의 경계."""

import pytest

from src.features.official_industry_context.tests.test_logic import material, run


OBSERVATION = "2026년 현재 산업용 센서 시장에서는 부품 공급 부족으로 생산 차질이 발생하고 있습니다."


def unspecified(row, _):
    row.update(geography="unspecified", geography_supported=False,
               geography_detail="", geography_evidence="")


def test_지역_미확인은_공식_관찰로_분리하고_국내세계_검증에_세지_않는다():
    result, diagnostics, calls = run(pairs=(material(OBSERVATION),), modify=unspecified)
    assert len(result) == len(calls) == 1
    assert result[0].assessment_quote == result[0].exact_text == OBSERVATION
    assert result[0].geography == "unspecified"
    assert result[0].geography_detail == result[0].geography_evidence == ""
    assert diagnostics["verified_region_unspecified_count"] == 1
    assert diagnostics["verified_regional_count"] == 0


@pytest.mark.parametrize("changes", [
    {"geography_supported": True}, {"geography_supported": 0},
    {"geography_detail": "국내"}, {"geography_evidence": "전체 시장"},
    {"geography_detail": None}, {"same_business": False},
    {"period_supported": False}, {"current_problem": False},
    {"industry": "산업용 센서 생산업"}, {"observation_period": "2027년"},
])
def test_미확인_표시가_다른_검수_실패를_우회하지_않는다(changes):
    def modify(row, payload):
        unspecified(row, payload)
        row.update(changes)
    result, _, _ = run(pairs=(material(OBSERVATION),), modify=modify)
    assert result == ()


@pytest.mark.parametrize("region", ["국내", "한국", "해외", "세계", "글로벌", "일본", "유럽", "인도네시아"])
def test_문제_소재절의_명시지역은_기존_지역검수를_거쳐야_한다(region):
    text = OBSERVATION.replace("산업용 센서 시장", f"{region} 산업용 센서 시장")
    result, _, _ = run(pairs=(material(text),), modify=unspecified)
    assert result == ()


@pytest.mark.parametrize("text", [
    "2026년 현재 산업용 센서 시장에서는 부품 공급 부족이 예상됩니다.",
    "2026년 현재 산업용 센서 시장에서는 부품 공급 부족이 해소됐습니다.",
])
def test_미래_전망이나_해소된_문제를_관찰로_승격하지_않는다(text):
    result, _, _ = run(pairs=(material(text),), modify=unspecified)
    assert result == ()


def test_명시지역_원래_판정과_계수는_유지한다():
    result, diagnostics, _ = run()
    assert result[0].geography == "domestic"
    assert diagnostics["verified_regional_count"] == 1
    assert diagnostics["verified_region_unspecified_count"] == 0


@pytest.mark.parametrize("actor", ["글로벌 기업", "일본 기업", "국내 업체"])
def test_기업의_수식어로_산업_문제의_지역을_만들지_않는다(actor):
    text = f"2026년 현재 산업용 센서 시장에서는 {actor}과의 경쟁 심화로 생산 차질이 발생했습니다."
    def modify(row, payload):
        unspecified(row, payload)
        row["problem"] = "생산 차질"
    result, _, _ = run(pairs=(material(text),), modify=modify)
    assert len(result) == 1
    assert result[0].geography == "unspecified"
