"""원문 선택의 기사·본문·주어 경계와 기존 응답 호환을 무료 검증한다."""

from copy import deepcopy
from dataclasses import replace
import json
import hashlib
import re

import pytest

from src.features.news_intake import quote_selection_constants as qc
from src.features.news_intake.grounded import build_grounded_prompt, build_grounded_schema, validate_grounded_response
from src.features.news_intake.quote_selection import quote_candidates, quote_schema, restore_quote_response
from src.features.news_intake.quote_selection import quote_response_sha256
from src.features.news_intake.tests.test_collection import AS_OF, BODY, COMPANY, POLICY, accepted, collect, item, snapshot
from src.features.news_intake.tests import test_industry_context as industry


def selected(candidate, body, *, company=COMPANY):
    row = accepted({"id": candidate.id, "body": body})
    quote = next(q for q in quote_candidates(candidate, body, company)
                 if body[q["start"]:q["end"]] == body)
    row["entity_evidence_quote_id"] = quote["id"]
    row.pop("entity_evidence")
    for name in ("text", "time_evidence", "subject_evidence"):
        value = row["excerpts"][0].pop(name)
        row["excerpts"][0][name + "_quote_id"] = quote["id"] if value else ""
    row["excerpts"][0]["subject_is_target"] = True
    return {"items": [row]}


def validate(raw, candidate, body, company=COMPANY):
    return validate_grounded_response(raw, articles=[(candidate, body)], company=company, as_of=AS_OF)


def test_quote_restoration_preserves_unicode_and_newlines():
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    body = BODY + '\n가나다전자는 “Café·Cafe\u0301·⚙️” 제품을 출시했다.'
    raw = selected(candidate, body)
    before = deepcopy(raw)
    result, rejected = validate(raw, candidate, body)
    assert result and not rejected
    assert result[0].text == body and raw == before


def test_quote_id_rejects_other_article_and_changed_body():
    snap, _ = snapshot([item(), item(1)])
    first, second = snap.candidates
    raw = selected(first, BODY)
    raw["items"][0]["id"] = second.id
    assert not validate(raw, second, BODY)[0]
    assert not validate(selected(first, BODY), first, BODY.replace("120", "121"))[0]


@pytest.mark.parametrize("value", ["quote-unknown", "", 1, True])
def test_unknown_quote_ids_cannot_recover_identity(value):
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    raw = selected(candidate, BODY)
    raw["items"][0]["excerpts"][0]["text_quote_id"] = value
    assert not validate(raw, candidate, BODY)[0]


def test_mixed_free_text_and_quote_id_is_rejected():
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    raw = selected(candidate, BODY)
    raw["items"][0]["excerpts"][0]["text"] = BODY.replace("120", "900")
    assert not validate(raw, candidate, BODY)[0]
    old = {"items": [accepted({"id": candidate.id, "body": BODY})]}
    assert restore_quote_response(old, articles=[(candidate, BODY)], company=COMPANY) is old
    encoded = json.dumps(old)
    assert restore_quote_response(encoded, articles=[(candidate, BODY)], company=COMPANY) is encoded


@pytest.mark.parametrize("text", [
    "행사 참석 기업은 가나다전자와 다른기술이다. 다른기술은 산업설비 신제품을 개발해 공급했다.",
    "가나다전자의 자회사인 별도법인 푸른기술은 산업설비 신제품을 개발해 공급했다.",
    "공학자는 과거 가나다전자에서 근무했다. 현재 공학자의 독립 회사는 산업설비 신제품을 개발해 공급했다.",
    "다른기술은 가나다전자를 방문했다. 다른기술은 산업설비 신제품을 개발해 공급했다.",
    "기사 제공: 가나다전자. 다른기술은 산업설비 신제품을 개발해 공급했다.",
    "가나다전자는 산업설비 기업이다. 다른기술은 산업설비 신제품을 개발해 공급했다.",
])
def test_company_mention_cannot_promote_other_subjects(text):
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    assert not validate(selected(candidate, text), candidate, text)[0]


def test_company_action_and_pronoun_followup_can_survive():
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    body = BODY + " 회사는 산업설비 신제품을 개발해 공급했다."
    result, rejected = validate(selected(candidate, body), candidate, body)
    assert result and not rejected and result[0].text == body


def test_schema_ids_preserve_body_and_bound_candidate_count():
    snap, _ = snapshot([item()])
    articles = [(snap.candidates[0], BODY)]
    schema = build_grounded_schema(articles, selection=True, company=COMPANY)
    fields = schema["properties"]["items"]["items"]["properties"]
    assert "entity_evidence_quote_id" in fields and "entity_evidence" not in fields
    payload = json.loads(build_grounded_prompt(COMPANY, articles, AS_OF, selection=True).split("자료 시작:\n")[1])
    assert payload["articles"][0]["body"] == BODY
    assert len(quote_candidates(articles[0][0], BODY * 100, COMPANY)) <= qc.QUOTE_MAX_CANDIDATES_PER_ARTICLE


def test_selection_budget_fallback_preserves_existing_call_count():
    snap, _ = snapshot([item()])
    old = build_grounded_prompt(COMPANY, [(snap.candidates[0], BODY)], AS_OF)
    calls = []
    def analyze(prompt, schema, max_tokens):
        calls.append(schema)
        payload = json.loads(prompt.split("자료 시작:\n")[1])
        return {"items": [accepted(article) for article in payload["articles"]]}
    result = collect(policy=replace(POLICY, max_prompt_chars=len(old)), analyze=analyze)
    assert result.fragments and len(calls) == 1
    assert "entity_evidence" in calls[0]["properties"]["items"]["items"]["properties"]


def test_industry_quote_id_keeps_exact_fact_geography_and_anchor_checks():
    raw = industry.response()
    raw["items"][0].pop("entity_evidence")
    raw["items"][0]["entity_evidence_quote_id"] = ""
    quote = quote_candidates(industry.CANDIDATE, industry.BODY, industry.COMPANY)[0]
    problem = raw["items"][0]["industry_problems"][0]
    problem.pop("text")
    problem["text_quote_id"] = quote["id"]
    _, problems, rejected = industry.split(raw)
    assert len(problems) == 1 and not rejected
    problem["geography"] = "global"
    assert not industry.split(raw)[1]
    problem["geography"] = "domestic"
    problem["problem"] = "본문에 없는 공급 위기"
    assert not industry.split(raw)[1]


@pytest.mark.parametrize("text", [
    "Hanul Tire supplies industrial equipment to its clients.",
    "가나다전자, 산업설비 신제품을 개발해 고객사에 공급했다.",
    "(주)가나다전자는 산업설비 신제품을 개발해 고객사에 공급했다.",
    "한울타이어는 산업설비 신제품을 개발해 고객사에 공급했다.",
    "한울타이어앤테크놀로지는 산업설비 신제품을 개발해 고객사에 공급했다.",
    "새봄바이오테크는 산업설비 신제품을 개발해 고객사에 공급했다.",
    "한울타이어, 산업설비 신제품을 개발해 고객사에 공급했다.",
    "한울타이어앤테크놀로지, 산업설비 신제품을 개발해 고객사에 공급했다.",
    "새봄바이오테크, 산업설비 신제품을 개발해 고객사에 공급했다.",
])
def test_normal_company_subject_does_not_need_one_korean_particle_pattern(text):
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    company = replace(COMPANY, aliases=COMPANY.aliases + ("Hanul Tire", "한울타이어", "한울타이어앤테크놀로지", "새봄바이오테크"), identity_context="")
    result, rejected = validate(selected(candidate, text, company=company), candidate, text, company)
    assert result and not rejected


def test_subject_judgment_false_and_partial_new_contract_are_rejected():
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    raw = selected(candidate, BODY)
    raw["items"][0]["excerpts"][0]["subject_is_target"] = False
    assert not validate(raw, candidate, BODY)[0]
    raw = selected(candidate, BODY)
    raw["items"][0]["excerpts"][0].pop("text_quote_id")
    raw["items"][0]["excerpts"][0]["text"] = BODY
    assert not validate(raw, candidate, BODY)[0]


def test_collection_accepts_complete_selected_response_without_extra_calls():
    snap, _ = snapshot([item()])
    calls = []
    def analyze(prompt, schema, max_tokens):
        calls.append(schema)
        return selected(snap.candidates[0], BODY)
    result = collect(analyze=analyze)
    assert len(result.fragments) == len(calls) == 1
    assert "entity_evidence_quote_id" in calls[0]["properties"]["items"]["items"]["properties"]


def test_selection_cache_stores_restored_ranges_and_preserves_provider_payload():
    from src.features.news_intake.tests.test_analysis_result_cache import execute, request
    from src.features.news_intake.analysis_result_cache import AnalysisResultCache
    req = request()
    req.schema = build_grounded_schema(req.articles, selection=True, company=req.company)
    req.prompt = build_grounded_prompt(req.company, req.articles, req.as_of, selection=True)
    candidate, body = req.articles[0]
    raw = selected(candidate, body)
    original = deepcopy(raw)
    store, calls, hits = AnalysisResultCache(), [], []
    first = execute(store, req, output=raw, calls=calls, hits=hits)
    second = execute(store, req, output=raw, calls=calls, hits=hits)
    assert first is raw and raw == original
    assert restore_quote_response(first, articles=req.articles, company=req.company) == second
    assert len(calls) == len(hits) == 1
    assert req.source_response_sha256 == quote_response_sha256(raw)
    encoded = next(iter(store._entries.values())).encoded
    assert body.encode("utf-8") not in encoded
    assert "text_quote_id" not in second["items"][0]["excerpts"][0]


def test_quote_ids_are_rejected_when_request_did_not_supply_selection_table():
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    raw = selected(candidate, BODY)
    restored = restore_quote_response(raw, articles=[(candidate, BODY)], company=COMPANY, selection_enabled=False)
    assert not validate(restored, candidate, BODY)[0]


@pytest.mark.parametrize("invalid_field", ["text_quote_id", "subject_is_target"])
def test_bad_excerpt_preserves_valid_excerpt_and_restoration_is_idempotent(invalid_field):
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    raw = selected(candidate, BODY)
    bad = deepcopy(raw["items"][0]["excerpts"][0])
    bad[invalid_field] = False if invalid_field == "subject_is_target" else "quote-invalid"
    raw["items"][0]["excerpts"].append(bad)
    restored = restore_quote_response(raw, articles=[(candidate, BODY)], company=COMPANY)
    assert restore_quote_response(restored, articles=[(candidate, BODY)], company=COMPANY) is restored
    result, rejected = validate(restored, candidate, BODY)
    assert len(result) == 1 and rejected["grounded_invalid_excerpt"] == 1


@pytest.mark.parametrize("error_scope", ["entity", "industry", "excerpt"])
def test_direct_and_industry_selection_errors_are_isolated(error_scope):
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    company = replace(COMPANY, business_anchors=(industry.ANCHOR,))
    body = BODY + "\n" + industry.BODY
    raw = selected(candidate, body, company=company)
    problem = deepcopy(industry.response()["items"][0]["industry_problems"][0])
    problem.pop("text")
    problem["text_quote_id"] = next(q["id"] for q in quote_candidates(candidate, body, company)
                                          if body[q["start"]:q["end"]] == industry.BODY)
    raw["items"][0]["industry_problems"] = [problem]
    if error_scope == "entity":
        raw["items"][0]["entity_evidence_quote_id"] = "quote-invalid"
    elif error_scope == "industry":
        problem["text_quote_id"] = "quote-invalid"
    else:
        raw["items"][0]["excerpts"][0]["text_quote_id"] = "quote-invalid"
    original_sha = quote_response_sha256(raw)
    direct, problems, _ = industry.split_response(raw, articles=[(candidate, body)], company=company,
        as_of=AS_OF, full_body_hashes={candidate.source_url: hashlib.sha256(body.encode()).hexdigest()})
    excerpts, _ = validate(direct, candidate, body, company)
    assert len(excerpts) == int(error_scope == "industry")
    assert len(problems) == int(error_scope != "industry")
    if problems:
        assert problems[0].analysis_response_sha256 == original_sha


def test_body_without_any_bounded_quote_uses_legacy_schema():
    snap, _ = snapshot([item()])
    calls = []
    def analyze(prompt, schema, max_tokens):
        calls.append(schema)
        return {"items": [accepted({"id": snap.candidates[0].id, "body": BODY})]}
    collect(fetch=lambda url: BODY.replace(".", "") + "공급" * qc.QUOTE_MAX_CHARS, analyze=analyze)
    assert len(calls) == 1
    assert "entity_evidence" in calls[0]["properties"]["items"]["items"]["properties"]


def test_sdk_normalizes_selection_schema_without_mutating_closed_contract():
    import anthropic
    snap, _ = snapshot([item()])
    schema = build_grounded_schema([(snap.candidates[0], BODY)], selection=True, company=COMPANY)
    before = deepcopy(schema)
    normalized = anthropic.transform_schema(schema)
    assert schema == before
    item_schema = normalized["properties"]["items"]["items"]
    assert item_schema["additionalProperties"] is False
    entity_field = item_schema["properties"]["entity_evidence_quote_id"]
    assert entity_field["type"] == "string" and "enum" not in entity_field and "pattern" not in entity_field
    assert qc.QUOTE_EMPTY_ID_PATTERN in entity_field["description"]
    excerpt_schema = item_schema["properties"]["excerpts"]["items"]
    text_field = excerpt_schema["properties"]["text_quote_id"]
    assert text_field["type"] == "string" and "enum" not in text_field and "pattern" not in text_field
    assert qc.QUOTE_ID_PATTERN in text_field["description"]
    assert "subject_is_target" in excerpt_schema["required"]


def quote_fields(schema):
    item_fields = schema["properties"]["items"]["items"]["properties"]
    result = {"entity_evidence_quote_id": item_fields["entity_evidence_quote_id"]}
    result.update(item_fields["excerpts"]["items"]["properties"])
    if "industry_problems" in item_fields:
        result["industry_text_quote_id"] = item_fields["industry_problems"]["items"]["properties"]["text_quote_id"]
    return {name: field for name, field in result.items() if name.endswith("_quote_id")}


def test_quote_schema_is_stable_when_body_ids_change_and_preserves_original():
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    original = build_grounded_schema([(candidate, BODY)])
    before = deepcopy(original)
    selected_schema = quote_schema(original, [(candidate, BODY)], COMPANY)
    changed_body = BODY.replace("120", "121")
    assert quote_candidates(candidate, BODY, COMPANY) != quote_candidates(candidate, changed_body, COMPANY)
    assert selected_schema == quote_schema(original, [(candidate, changed_body)], COMPANY)
    assert original == before
    other_candidate = replace(candidate, id=candidate.id + "-other")
    other_schema = build_grounded_schema([(other_candidate, BODY)], selection=True, company=COMPANY)
    assert quote_fields(selected_schema) == quote_fields(other_schema)
    assert selected_schema["properties"]["items"]["items"]["properties"]["id"] != other_schema["properties"]["items"]["items"]["properties"]["id"]
    company = replace(COMPANY, business_anchors=(industry.ANCHOR,))
    extended = industry.extend_schema(selected_schema, company)
    fields = quote_fields(extended)
    assert len(fields) == 5
    for name, field in fields.items():
        expected = qc.QUOTE_ID_PATTERN if name in ("text_quote_id", "industry_text_quote_id") else qc.QUOTE_EMPTY_ID_PATTERN
        assert field == {"type": "string", "pattern": expected}


@pytest.mark.parametrize("pattern,empty_allowed", [(qc.QUOTE_ID_PATTERN, False), (qc.QUOTE_EMPTY_ID_PATTERN, True)])
def test_quote_pattern_only_describes_id_shape(pattern, empty_allowed):
    assert re.fullmatch(pattern, qc.QUOTE_ID_PREFIX + "0" * qc.QUOTE_ID_HASH_CHARS)
    assert bool(re.fullmatch(pattern, "")) is empty_allowed
    for invalid in ("quote-unknown", "quote-" + "A" * qc.QUOTE_ID_HASH_CHARS,
                    "quote-" + "0" * (qc.QUOTE_ID_HASH_CHARS + 1), "other-" + "0" * qc.QUOTE_ID_HASH_CHARS):
        assert re.fullmatch(pattern, invalid) is None


def unavailable_id(source, candidate, body, company=COMPANY):
    if source == "unoffered":
        result = qc.QUOTE_ID_PREFIX + "0" * qc.QUOTE_ID_HASH_CHARS
    elif source == "other_article":
        result = quote_candidates(replace(candidate, id=candidate.id + "-other"), body, company)[0]["id"]
    else:
        result = quote_candidates(candidate, body + " 원문이 변경됐다.", company)[0]["id"]
    assert re.fullmatch(qc.QUOTE_ID_PATTERN, result)
    assert result not in {entry["id"] for entry in quote_candidates(candidate, body, company)}
    return result


@pytest.mark.parametrize("source", ["unoffered", "other_article", "other_body"])
@pytest.mark.parametrize("field", ["entity_evidence_quote_id", "text_quote_id", "time_evidence_quote_id", "subject_evidence_quote_id"])
def test_pattern_valid_unoffered_ids_fail_every_direct_source_field(source, field):
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    raw = selected(candidate, BODY)
    target = raw["items"][0] if field == "entity_evidence_quote_id" else raw["items"][0]["excerpts"][0]
    target[field] = unavailable_id(source, candidate, BODY)
    articles = [(candidate, BODY), (replace(candidate, id=candidate.id + "-other"), BODY)]
    result, rejected = validate_grounded_response(raw, articles=articles, company=COMPANY, as_of=AS_OF)
    assert not result and rejected


@pytest.mark.parametrize("source", ["unoffered", "other_article", "other_body"])
def test_pattern_valid_unoffered_industry_id_is_rejected(source):
    raw = industry.response()
    row = raw["items"][0]
    row.pop("entity_evidence")
    row["entity_evidence_quote_id"] = ""
    problem = row["industry_problems"][0]
    problem.pop("text")
    problem["text_quote_id"] = unavailable_id(source, industry.CANDIDATE, industry.BODY, industry.COMPANY)
    assert not industry.split(raw)[1]


@pytest.mark.parametrize("field", ["text_quote_id", "time_evidence_quote_id", "subject_evidence_quote_id"])
def test_pattern_valid_bad_id_preserves_other_valid_excerpt(field):
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    raw = selected(candidate, BODY)
    bad = deepcopy(raw["items"][0]["excerpts"][0])
    bad[field] = unavailable_id("unoffered", candidate, BODY)
    raw["items"][0]["excerpts"].append(bad)
    result, rejected = validate(raw, candidate, BODY)
    assert len(result) == 1 and rejected["grounded_invalid_excerpt"] == 1


def test_sdk_wire_removes_repeated_quote_enums_but_server_still_owns_membership():
    from src.features.pipeline.real import _provider_output_config
    snap, _ = snapshot([item()])
    company = replace(COMPANY, business_anchors=(industry.ANCHOR,))
    body = " ".join(BODY for _ in range(40))
    articles = [(replace(snap.candidates[0], id=f"synthetic-{number}"), body) for number in range(4)]
    assert sum(len(quote_candidates(candidate, text, company)) for candidate, text in articles) == 192
    schema = industry.extend_schema(build_grounded_schema(articles, selection=True, company=company), company)
    before = deepcopy(schema)
    wire = _provider_output_config({"format": {"type": "json_schema", "schema": schema}})["format"]["schema"]
    assert schema == before
    fields = quote_fields(wire)
    assert len(fields) == 5
    for name, field in fields.items():
        assert field["type"] == "string" and "enum" not in field and "pattern" not in field
        expected = qc.QUOTE_ID_PATTERN if name in ("text_quote_id", "industry_text_quote_id") else qc.QUOTE_EMPTY_ID_PATTERN
        assert expected in field["description"]
    excerpt = wire["properties"]["items"]["items"]["properties"]["excerpts"]["items"]
    assert excerpt["properties"]["subject_is_target"] == {"type": "boolean"}
    assert "subject_is_target" in excerpt["required"]
