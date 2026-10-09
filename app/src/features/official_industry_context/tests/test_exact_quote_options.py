"""유한 원문 선택값은 작성 오류를 줄이며 기존 의미 검수를 대체하지 않는다."""

import json

import pytest

from src.features.official_industry_context import constants as c
from src.features.official_industry_context.logic import (
    build_prompt, collect_official_industry_context, exact_field_options,
    response_schema, sentence_ranges,
)
from src.features.official_industry_context.tests.test_logic import (
    CURRENT, REFERENCE, anchor, material, proposal, run,
)


def selected(*pairs):
    return tuple((f, d, sentence_ranges(f)) for f, d in pairs)


def enums(node):
    if isinstance(node, dict):
        if "enum" in node:
            yield node["enum"]
        for value in node.values():
            yield from enums(value)
    elif isinstance(node, list):
        for value in node:
            yield from enums(value)


def test_options_restore_original_title_text_and_sentence_coordinates():
    pair = material(CURRENT + " 2027년에는 수요 회복을 예상합니다.")
    options = exact_field_options(selected(pair), (anchor(),))[pair[0].fragment_id]
    for row in options["exact_period_options"]:
        assert row in pair[0].text or row in pair[1].title
        assert c.YEAR_RE.search(row)
    assert set(options["exact_period_options"]) == {
        "2026년 현재", "2027년", "2026.09",
    }
    application = options["applicability_quote_options"][0]
    assert application["text"] == pair[0].text[application["start"]:application["end"]] == "산업용 센서"
    assert application["anchor_ids"] == [anchor().anchor_id]
    assert application["sentence_ids"] == [sentence_ranges(pair[0])[0]["id"]]


def test_prompt_and_schema_share_exact_options_without_anchor_phrase_substitution():
    pairs = selected(material())
    payload = json.loads(build_prompt(pairs, (anchor(),), "synthetic-company", REFERENCE).rsplit("\n", 1)[1])
    branch = response_schema(pairs, (anchor(),))["properties"]["assessments"]["items"]["anyOf"][1]["properties"]
    assert branch["applicability_quote"]["enum"] == ["산업용 센서"]
    assert anchor().exact_text not in branch["applicability_quote"]["enum"]
    assert branch["observation_period"]["enum"] == ["2026.09", "2026년 현재"]
    assert payload["materials"][0]["text"] == material()[0].text
    assert payload["materials"][0]["applicability_quote_options"][0]["text"] == "산업용 센서"
    assert c.PROMPT_VERSION == "official-industry-context-v4"


def test_exact_option_choice_preserves_valid_observed_problem():
    def change(row, payload):
        row["applicability_quote"] = payload["materials"][0]["applicability_quote_options"][0]["text"]
        row["observation_period"] = "2026년 현재"
    result, diagnostics, calls = run(modify=change)
    assert len(result) == 1
    assert result[0].assessment_quote == CURRENT
    assert result[0].applicability_quote == "산업용 센서"
    assert len(calls) == diagnostics["model_calls"] == 1


def test_all_years_and_title_period_survive_without_repeating_application_per_anchor():
    text = " ".join(f"{year}년 현재 국내 산업용 센서 시장을 설명합니다." for year in range(2000, 2020))
    options = exact_field_options(selected(material(text)), (anchor(), anchor("second-anchor")))["synthetic-fragment"]
    assert len(options["exact_period_options"]) == 21
    assert options["exact_period_options"][0] == "2026.09"
    assert len(options["applicability_quote_options"]) == 1
    assert len(options["applicability_quote_options"][0]["sentence_ids"]) == 20
    assert all(row["anchor_ids"] == ["synthetic-anchor", "second-anchor"] for row in options["applicability_quote_options"])


@pytest.mark.parametrize("pairs,anchors", [
    ((), (anchor(),)), (selected(material()), ()),
    (selected(material("현재 국내 산업용 센서 시장에 공급 부족이 있습니다.", title="합성 보고서")), (anchor(),)),
    (selected(material("2026년 현재 국내 부품 시장에 공급 부족이 있습니다.")), (anchor(),)),
])
def test_missing_options_have_no_proposal_or_empty_enum(pairs, anchors):
    schema = response_schema(pairs, anchors)
    assert all(values and "" not in values for values in enums(schema))
    items = schema["properties"]["assessments"]["items"]
    if pairs and anchors:
        assert len(items["anyOf"]) == 1
        assert "proposed" not in items["anyOf"][0]["properties"]["status"]["enum"]
    else:
        assert schema["properties"]["assessments"]["maxItems"] == 0


@pytest.mark.parametrize("field", ["applicability_quote", "observation_period"])
def test_global_enum_cannot_borrow_other_fragment_application_or_period(field):
    second = material(CURRENT.replace("2026년 현재", "2025년").replace("산업용 센서", "산업용   센서"), fragment_id="other-fragment")
    def change(row, payload):
        row[field] = payload["materials"][1]["applicability_quote_options"][0]["text"] if field == "applicability_quote" else "2025년"
    assert run(pairs=(material(), second), modify=change)[0] == ()


def test_distant_same_business_problem_still_has_an_exact_option_in_its_own_assessment():
    prefix = " ".join("산업용 센서 사업을 소개합니다." for _ in range(6))
    middle = " ".join("문서의 다른 내용을 설명합니다." for _ in range(4))
    pair = material(prefix + " " + middle + " " + CURRENT)
    def change(row, payload):
        item = payload["materials"][0]
        row["sentence_ids"] = [item["sentences"][-1]["id"]]
        row["applicability_quote"] = item["applicability_quote_options"][0]["text"]
        assert item["sentences"][-1]["id"] in item["applicability_quote_options"][0]["sentence_ids"]
    result, _, calls = run(pairs=(pair,), modify=change)
    assert len(result) == 1
    assert result[0].assessment_quote == CURRENT
    assert len(calls) == 1


def test_line_wrapped_business_name_retains_exact_continuous_option():
    pair = material(CURRENT.replace("산업용 센서", "산업용\n센서"))
    def change(row, payload):
        item = payload["materials"][0]
        row["sentence_ids"] = [s["id"] for s in item["sentences"]]
        row["industry"] = "센서 시장"
        row["applicability_quote"] = item["applicability_quote_options"][0]["text"]
        assert row["applicability_quote"] == "산업용\n센서"
    result, _, _ = run(pairs=(pair,), modify=change)
    assert len(result) == 1
    assert result[0].exact_text == pair[0].text


def test_application_in_another_sentence_is_not_in_selected_assessment():
    pair = material("2026년 현재 국내 산업용 센서 시장을 설명합니다. 국내 부품 공급 부족이 발생했습니다.")
    def change(row, payload):
        row["sentence_ids"] = [payload["materials"][0]["sentences"][1]["id"]]
        row["industry"] = "국내"
        row["applicability_quote"] = payload["materials"][0]["applicability_quote_options"][0]["text"]
    assert run(pairs=(pair,), modify=change)[0] == ()


@pytest.mark.parametrize("text,problem,period", [
    ("2025년 국내 산업용 센서 시장에 부품 공급 부족이 발생했습니다. 2026년 현재 품질 기준을 설명합니다.", "부품 공급 부족", "2026년 현재"),
    ("2027년 국내 산업용 센서 시장에 부품 공급 부족이 예상됩니다.", "부품 공급 부족", "2027년"),
    ("2026년 현재 국내 산업용 센서 시장의 부품 공급 부족은 해소되었습니다.", "부품 공급 부족", "2026년 현재"),
    ("2026년 현재 국내 산업용 센서 시장의 고객은 광고를 구매하는 기업의 집행 비용 상승 부담을 겪고 있습니다.", "집행 비용 상승 부담", "2026년 현재"),
])
def test_finite_exact_options_do_not_approve_period_role_or_problem_conflicts(text, problem, period):
    def change(row, payload):
        row["sentence_ids"] = [item["id"] for item in payload["materials"][0]["sentences"]]
        row["applicability_quote"] = payload["materials"][0]["applicability_quote_options"][0]["text"]
        row["problem"] = problem
        row["observation_period"] = period
    assert run(pairs=(material(text),), modify=change)[0] == ()


def test_prompt_trim_recomputes_schema_options_for_final_selected_inputs(monkeypatch):
    first = material()
    second = material(CURRENT.replace("2026년 현재", "2025년"), fragment_id="other-fragment")
    first_length = len(build_prompt(selected(first), (anchor(),), "synthetic-company", REFERENCE))
    monkeypatch.setattr(c, "MAX_PROMPT_CHARS", first_length)
    calls = []
    def analyze(prompt, schema, limit):
        payload = json.loads(prompt.rsplit("\n", 1)[1])
        calls.append((payload, schema, limit, len(prompt)))
        return {"assessments": [{"fragment_id": first[0].fragment_id, "anchor_id": anchor().anchor_id, "status": "uncertain"}]}
    diagnostics = {}
    assert collect_official_industry_context(candidates=(first, second), anchors=(anchor(),), company_id="synthetic-company", reference_date=REFERENCE, analyze=analyze, diagnostics=diagnostics) == ()
    assert diagnostics["candidate_count"] == len(calls[0][0]["materials"]) == 1
    assert "2025년" not in json.dumps(calls[0][1], ensure_ascii=False)
    assert calls[0][2] == c.MAX_OUTPUT_TOKENS
    assert calls[0][3] <= c.MAX_PROMPT_CHARS
    assert len(calls) == diagnostics["model_calls"] == 1


@pytest.mark.parametrize("text,geography,detail,expected", [
    ("2026년 현재 국내 산업용 센서 시장에서는 글로벌 고객의 부품 공급 부족으로 생산 차질이 발생하고 있습니다.", "global", "글로벌", False),
    ("2026년 현재 국내 산업용 센서 시장에서는 글로벌 고객의 부품 공급 부족으로 생산 차질이 발생하고 있습니다.", "domestic", "국내", True),
    ("2026년 현재 전 세계 산업용 센서 시장에서는 글로벌 고객의 부품 공급 부족으로 생산 차질이 발생하고 있습니다.", "global", "전 세계", True),
])
def test_customer_modifier_does_not_replace_actual_market_geography(text, geography, detail, expected):
    def change(row, payload):
        row.update(geography=geography, geography_detail=detail, geography_evidence=detail)
        row["applicability_quote"] = payload["materials"][0]["applicability_quote_options"][0]["text"]
    assert bool(run(pairs=(material(text),), modify=change)[0]) is expected
