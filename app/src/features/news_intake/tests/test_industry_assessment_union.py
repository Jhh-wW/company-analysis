"""단일 산업 판정의 닫힌 계약·원문 복원·캐시·회사 격리를 검증한다."""
from copy import deepcopy
from dataclasses import replace

import pytest
from jsonschema import Draft202012Validator

from src.features.news_intake import analysis_result_cache as cache
from src.features.news_intake import industry_constants as ic
from src.features.news_intake.grounded import build_grounded_prompt, build_grounded_schema, validate_grounded_response
from src.features.news_intake.industry_assessment import normalize_assessments
from src.features.news_intake.industry_context import extend_prompt, extend_schema, split_response
from src.features.news_intake.models import NewsCollectionPolicy
from src.features.news_intake.quote_selection import quote_candidates, quote_response_sha256, quote_schema, restore_quote_response
from src.features.news_intake.tests.test_analysis_result_cache import NAMESPACE
from src.features.news_intake.tests.test_collection import accepted
from src.features.news_intake.tests.test_industry_context import ANCHOR, AS_OF, BODY, CANDIDATE, COMPANY, response
from src.features.news_intake.tests.test_industry_priority_assessment import assessed
from src.shared.report_generation.models import exact_text_sha256

DIRECT = "예제법인은 산업설비 제조 사업에서 자동화 설비를 고객에게 공급했다."
MIXED = DIRECT + "\n" + BODY


def selected():
    raw = assessed()
    item = raw["items"][0]
    item.update(accepted({"id": CANDIDATE.id, "body": MIXED}, text=DIRECT))
    table = {MIXED[row["start"]:row["end"]]: row["id"] for row in quote_candidates(CANDIDATE, MIXED, COMPANY)}
    item.pop("entity_evidence")
    item["entity_evidence_quote_id"] = table[DIRECT]
    for name in ("text", "time_evidence", "subject_evidence"):
        value = item["excerpts"][0].pop(name)
        item["excerpts"][0][name + "_quote_id"] = table[DIRECT] if value else ""
    item["excerpts"][0]["subject_is_target"] = True
    entry = item[ic.INDUSTRY_ASSESSMENT_FIELD][0]
    entry.pop("text")
    entry["text_quote_id"] = table[BODY]
    return raw


def check(raw, *, company=COMPANY, body=MIXED):
    traces = []
    direct, problems, rejected = split_response(
        raw, articles=[(CANDIDATE, body)], company=company, as_of=AS_OF,
        full_body_hashes={CANDIDATE.source_url: exact_text_sha256(body)},
        assessment_required=True, normalization_traces=traces,
    )
    return direct, problems, rejected, traces


def test_quote_restore_and_normalization_preserve_raw_response_and_three_hashes():
    raw = selected()
    before = deepcopy(raw)
    direct, problems, rejected, traces = check(raw)
    restored = restore_quote_response(raw, articles=[(CANDIDATE, MIXED)], company=COMPANY)
    normalized, errors = normalize_assessments(restored["items"], company=COMPANY)
    assert raw == before and not errors and not rejected and len(problems) == 1
    assert problems[0].analysis_response_sha256 == quote_response_sha256(raw)
    assert len({quote_response_sha256(raw), quote_response_sha256(restored),
                quote_response_sha256({"items": normalized})}) == 3
    assert traces == [{"원응답정규JSON_SHA256": quote_response_sha256(raw),
                       "정규화응답정규JSON_SHA256": quote_response_sha256({"items": normalized})}]
    assert len(validate_grounded_response(direct, articles=[(CANDIDATE, MIXED)], company=COMPANY, as_of=AS_OF)[0]) == 1


@pytest.mark.parametrize("entries", (
    [{"anchor_id": ANCHOR.anchor_id, "status": "proposed"}],
    [{"anchor_id": ANCHOR.anchor_id, "status": "uncertain", "text": BODY}],
    [{"anchor_id": ANCHOR.anchor_id, "status": "invalid"}],
))
def test_closed_union_rejects_incomplete_proposal_extra_evidence_and_unknown_status(entries):
    raw = assessed()
    raw["items"][0][ic.INDUSTRY_ASSESSMENT_FIELD] = entries
    schema = extend_schema(build_grounded_schema([(CANDIDATE, BODY)]), COMPANY, priority=True)
    assert not Draft202012Validator(schema).is_valid(raw)
    assert not check(raw, body=BODY)[1] and check(raw, body=BODY)[2]


def test_legacy_arrays_do_not_gain_union_approval_and_preserve_company_quote():
    raw = selected()
    raw["items"][0]["industry_problems"] = response()["items"][0]["industry_problems"]
    direct, problems, rejected, _ = check(raw)
    assert not problems and rejected["industry_assessment_legacy_conflict"] == 1
    assert len(validate_grounded_response(direct, articles=[(CANDIDATE, MIXED)], company=COMPANY, as_of=AS_OF)[0]) == 1


def test_multiple_anchor_proposals_exceed_article_limit_and_preserve_company_quote():
    company = replace(COMPANY, business_anchors=(ANCHOR, replace(ANCHOR, anchor_id="second"), replace(ANCHOR, anchor_id="third")))
    raw = selected()
    first = raw["items"][0][ic.INDUSTRY_ASSESSMENT_FIELD][0]
    raw["items"][0][ic.INDUSTRY_ASSESSMENT_FIELD] = [first, {**first, "anchor_id": "second"},
                                                               {"anchor_id": "third", "status": "uncertain"}]
    direct, problems, rejected, _ = check(raw, company=company)
    assert not problems and rejected["industry_assessment_proposal_limit"] == 1
    assert len(validate_grounded_response(direct, articles=[(CANDIDATE, MIXED)], company=company, as_of=AS_OF)[0]) == 1


def test_invalid_industry_quote_id_preserves_valid_company_selection():
    raw = selected()
    raw["items"][0][ic.INDUSTRY_ASSESSMENT_FIELD][0]["text_quote_id"] = "quote-" + "0" * 20
    direct, problems, rejected, _ = check(raw)
    assert not problems and rejected
    assert len(validate_grounded_response(direct, articles=[(CANDIDATE, MIXED)], company=COMPANY, as_of=AS_OF)[0]) == 1


def test_nonproposal_extra_quote_id_preserves_company_evidence_on_restore():
    raw = selected()
    raw["items"][0][ic.INDUSTRY_ASSESSMENT_FIELD][0]["status"] = "uncertain"
    direct, problems, rejected, _ = check(raw)
    assert not problems and rejected
    assert len(validate_grounded_response(direct, articles=[(CANDIDATE, MIXED)], company=COMPANY, as_of=AS_OF)[0]) == 1


def test_sdk_transform_preserves_closed_branches_without_claiming_server_validation():
    from anthropic import transform_schema
    schema = extend_schema(quote_schema(build_grounded_schema([(CANDIDATE, MIXED)]), [(CANDIDATE, MIXED)], COMPANY), COMPANY, priority=True)
    original = deepcopy(schema)
    wire = transform_schema(deepcopy(schema))
    branches = wire["properties"]["items"]["items"]["properties"][ic.INDUSTRY_ASSESSMENT_FIELD]["items"]["anyOf"]
    assert schema == original and len(branches) == 2
    assert all(branch["additionalProperties"] is False for branch in branches)
    assert branches[0]["properties"]["status"]["enum"] == ["proposed"]
    assert set(branches[0]["required"]) == (set(ic.INDUSTRY_REQUIRED_FIELDS) - {"text"}) | {"text_quote_id", "status"}
    assert "pattern" not in branches[0]["properties"]["text_quote_id"]
    assert "description" in branches[0]["properties"]["text_quote_id"]
    assert set(branches[1]["required"]) == {"anchor_id", "status"}


def test_union_cache_cold_and_warm_preserve_raw_hash_and_body_binding():
    policy = NewsCollectionPolicy()
    articles = [(CANDIDATE, MIXED)]
    schema = extend_schema(quote_schema(build_grounded_schema(articles), articles, COMPANY), COMPANY, priority=True)
    req = cache.AnalysisRequest(COMPANY, AS_OF, policy, articles, {CANDIDATE.source_url: exact_text_sha256(MIXED)},
                                extend_prompt(build_grounded_prompt(COMPANY, articles, AS_OF), COMPANY, priority=True), schema, policy.analysis_max_tokens)
    store, calls = cache.AnalysisResultCache(), []
    raw = selected()
    def provider():
        calls.append(1)
        return cache.ProviderAnalysis(deepcopy(raw), True)
    cold = store.run(req, NAMESPACE, provider, lambda: None)
    source_sha = req.source_response_sha256
    warm = store.run(req, NAMESPACE, provider, lambda: None)
    assert calls == [1] and req.cache_hits == 1 and source_sha == req.source_response_sha256 == quote_response_sha256(raw)
    assert warm == restore_quote_response(cold, articles=articles, company=COMPANY)
    assert check(warm)[1][0].exact_text == BODY
    legacy_schema = extend_schema(build_grounded_schema(articles), COMPANY)
    assert replace(req, schema=legacy_schema).key(NAMESPACE) != req.key(NAMESPACE)
