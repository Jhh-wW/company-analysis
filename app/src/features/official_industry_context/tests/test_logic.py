"""공식 원문 선택·단회 검수·독립 산업 판정의 닫힌 경계."""

import hashlib
import json
from dataclasses import replace

import pytest

from src.features.official_industry_context import constants as c
from src.features.official_industry_context.logic import collect_official_industry_context, sentence_ranges
from src.shared.business_challenge_context import BusinessActivityAnchor
from src.shared.report_evidence.constants import SourceRequirement, SourceTier, SOURCE_KIND_DART_BUSINESS_REPORT
from src.shared.report_evidence.models import EvidenceFragment, CollectedEvidenceDocument, DocumentTextRange


CURRENT = "2026년 현재 국내 산업용 센서 시장에서는 부품 공급 부족으로 생산 차질이 발생하고 있습니다."
REFERENCE = "2026-10-08"


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def material(text=CURRENT, *, fragment_id="synthetic-fragment", title="사업보고서 (2026.09)"):
    prefix = "공식 원문 앞부분 "
    fragment = EvidenceFragment(
        company_id="synthetic-company", fragment_id=fragment_id, document_id="synthetic-document",
        location=f"{len(prefix)}-{len(prefix) + len(text)}", text_sha256=digest(text), text=text,
        section_id="current_challenges", slot_id="current_challenges:issue", score_millis=800,
        reason_codes=("synthetic_current",),
    )
    document = CollectedEvidenceDocument(
        company_id=fragment.company_id, document_id=fragment.document_id, canonical_url="https://dart.fss.or.kr/synthetic",
        source_tier=SourceTier.TIER_1_OFFICIAL, source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
        publisher="합성 문서 발행자", title=title, published_on="2026-10-01", collected_at=REFERENCE,
        content_sha256=digest(prefix + text), exact_evidence_hashes=(fragment.text_sha256,),
        identity_binding="합성 회사 원문 신원 확인", usable_ranges=(DocumentTextRange(0, len(prefix + text)),),
        collector_version="synthetic-collector", parser_version="synthetic-parser", requirement=SourceRequirement.REQUIRED,
    )
    return fragment, document


def anchor(anchor_id="synthetic-anchor"):
    text = "당사는 산업용 센서를 생산합니다."
    return BusinessActivityAnchor(
        anchor_id=anchor_id, company_id="synthetic-company", source_id="", document_id="synthetic-business-document",
        source_kind=SOURCE_KIND_DART_BUSINESS_REPORT, source_url="https://dart.fss.or.kr/synthetic-business",
        location=f"0-{len(text)}", exact_text=text, text_sha256=digest(text), business_item="산업용 센서",
        publisher="합성 문서 발행자", title="사업보고서", published_on="2026-10-01",
        document_content_sha256=digest(text), identity_binding="합성 회사 원문 신원 확인",
    )


def proposal(payload):
    item = payload["materials"][0]
    return {
        "fragment_id": item["fragment_id"], "anchor_id": payload["anchors"][0]["anchor_id"], "status": "proposed",
        "sentence_ids": [item["sentences"][0]["id"]], "current_problem": True, "same_business": True,
        "geography_supported": True, "period_supported": True, "industry": "산업용 센서 시장",
        "problem": "부품 공급 부족", "geography": "domestic", "geography_detail": "국내",
        "geography_evidence": "국내", "applicability_quote": "산업용 센서 시장", "observation_period": "2026년 현재",
    }


def run(*, pairs=None, anchors=None, modify=lambda row, payload: None, raw_transform=lambda value: value):
    calls = []
    diagnostics = {}

    def analyze(prompt, schema, max_tokens):
        payload = json.loads(prompt.rsplit("\n", 1)[1])
        calls.append((prompt, schema, max_tokens))
        row = proposal(payload)
        modify(row, payload)
        return raw_transform({"assessments": [row]})

    result = collect_official_industry_context(
        candidates=tuple(pairs or (material(),)), anchors=tuple(anchors or (anchor(),)),
        company_id="synthetic-company", reference_date=REFERENCE, analyze=analyze, diagnostics=diagnostics,
    )
    return result, diagnostics, calls


def test_proposal_restores_exact_fragment_and_assessment_without_copying():
    fragment, document = material()
    result, diagnostics, calls = run()
    assert len(result) == 1
    evidence = result[0]
    assert evidence.exact_text == evidence.assessment_quote == fragment.text
    assert evidence.text_sha256 == fragment.text_sha256
    assert evidence.document_content_sha256 == document.content_sha256
    assert evidence.location == fragment.location
    assert evidence.source_kind == document.source_kind
    assert evidence.identity_binding == document.identity_binding
    assert evidence.publisher == document.publisher
    assert diagnostics["model_calls"] == len(calls) == 1
    assert calls[0][2] == c.MAX_OUTPUT_TOKENS


@pytest.mark.parametrize("field", c.BOOL_FIELDS)
@pytest.mark.parametrize("value", [False, 1, "true"])
def test_non_boolean_or_false_semantic_verdict_cannot_approve(field, value):
    result, _, _ = run(modify=lambda row, _: row.update({field: value}))
    assert result == ()


@pytest.mark.parametrize("field,value", [
    ("fragment_id", "foreign-fragment"), ("anchor_id", "foreign-anchor"),
    ("sentence_ids", ["foreign-sentence"]), ("industry", "다른 산업"),
    ("problem", "없는 문제"), ("observation_period", "2027년"),
    ("geography", "unknown"), ("unexpected", "extra"),
])
def test_request_ids_exact_fields_period_and_unknown_keys_are_closed(field, value):
    result, _, _ = run(modify=lambda row, _: row.update({field: value}))
    assert result == ()


def test_nonproposal_has_only_closed_base_fields():
    def change(row, _):
        base = {key: row[key] for key in c.BASE_FIELDS}
        row.clear()
        row.update(base, status="uncertain")

    result, diagnostics, _ = run(modify=change)
    assert result == ()
    assert diagnostics["statuses"] == {"uncertain": 1}
    result, diagnostics, _ = run(modify=lambda row, _: row.update(status="uncertain"))
    assert result == ()
    assert diagnostics["rejected"]["unbound_assessment"] == 1


def test_whole_fragment_is_preserved_while_assessment_is_a_continuous_slice():
    prefix = "회사 자체의 유지보수 업무를 설명합니다. "
    suffix = " 내년에는 수요가 성장할 것으로 예상됩니다."
    pair = material(prefix + CURRENT + suffix)

    def change(row, payload):
        row["sentence_ids"] = [payload["materials"][0]["sentences"][1]["id"]]

    result, _, _ = run(pairs=(pair,), modify=change)
    assert result[0].exact_text == prefix + CURRENT + suffix
    assert result[0].assessment_quote == CURRENT
    assert result[0].text_sha256 == digest(pair[0].text)


@pytest.mark.parametrize("indexes", [[0, 2], [1, 0], [0, 0]])
def test_noncontinuous_reversed_or_duplicate_sentence_ids_are_rejected(indexes):
    pair = material(CURRENT + " 다른 자료를 설명합니다. " + CURRENT.replace("국내", "해외"))

    def change(row, payload):
        row["sentence_ids"] = [payload["materials"][0]["sentences"][i]["id"] for i in indexes]

    assert run(pairs=(pair,), modify=change)[0] == ()


def test_region_or_problem_from_unselected_sentence_cannot_be_borrowed():
    pair = material("2026년 현재 국내 산업용 센서 시장을 설명합니다. 해외에서는 부품 공급 부족이 나타나고 있습니다.")
    assert run(pairs=(pair,))[0] == ()


@pytest.mark.parametrize("text,problem", [
    ("2026년 현재 국내 산업용 센서 시장에서는 부품 공급 부족이 예상됩니다.", "부품 공급 부족"),
    ("2026년 현재 국내 산업용 센서 시장에서는 과거 부품 공급 부족이 해소되었습니다.", "부품 공급 부족"),
    ("2026년 현재 국내 산업용 센서 시장은 공급 부족을 의미하는 정의입니다.", "공급 부족"),
    ("2026년 현재 국내 산업용 센서 시장에서는 선도업체가 강세를 보이고 있습니다.", "강세를 보이고 있습니다"),
    ("2026년 현재 국내 산업용 센서 시장의 회계정책은 공정가치로 평가합니다.", "공정가치"),
])
def test_future_resolved_definition_dominance_and_policy_are_not_current_problems(text, problem):
    assert run(pairs=(material(text),), modify=lambda row, _: row.update(problem=problem))[0] == ()


def test_domestic_global_company_modifier_cannot_be_global_problem():
    text = "2026년 현재 국내 산업용 센서 시장에서는 글로벌 기업의 강세와 부품 공급 부족이 나타나고 있습니다."
    result, _, _ = run(pairs=(material(text),), modify=lambda row, _: row.update(geography="global", geography_detail="글로벌", geography_evidence="글로벌"))
    assert result == ()


def test_customer_advertising_cost_is_not_supplier_business_impact():
    text = "2026년 현재 국내 산업용 센서 시장의 고객은 광고비 상승 부담을 겪고 있습니다."
    assert run(pairs=(material(text),), modify=lambda row, _: row.update(problem="광고비 상승 부담"))[0] == ()


@pytest.mark.parametrize("mutation", [
    lambda f, d: (replace(f, company_id="foreign-company"), d),
    lambda f, d: (f, replace(d, document_id="foreign-document")),
    lambda f, d: (f, replace(d, published_on="2027-01-01")),
    lambda f, d: (f, replace(d, exact_evidence_hashes=(digest("different"),))),
    lambda f, d: (replace(f, location="0-1"), d),
    lambda f, d: (replace(f, item_title="목록의 다른 항목"), d),
])
def test_unbound_input_is_excluded_before_model(mutation):
    result, diagnostics, calls = run(pairs=(mutation(*material()),))
    assert result == ()
    assert diagnostics["model_calls"] == len(calls) == 0


def test_duplicate_fragment_or_anchor_is_never_ambiguously_selected():
    assert run(pairs=(material(), material()))[2] == []
    assert run(anchors=(anchor(), anchor()))[2] == []


def test_input_budget_preserves_whole_fragments_and_order():
    pairs = tuple(material(CURRENT + " " + "설명 " * 300, fragment_id=f"fragment-{i}") for i in range(c.MAX_CANDIDATES + 1))
    _, diagnostics, calls = run(pairs=pairs)
    inputs = json.loads(calls[0][0].rsplit("\n", 1)[1])["materials"]
    assert len(inputs) <= c.MAX_CANDIDATES
    assert diagnostics["input_chars"] <= c.MAX_INPUT_CHARS
    assert all(row["text"] == pairs[i][0].text for i, row in enumerate(inputs))
    assert diagnostics["rejected"]["input_budget"] >= 1


def test_multiple_proposals_for_same_fragment_are_rejected_without_clamping():
    def duplicate(payload):
        second = dict(payload["assessments"][0], anchor_id="second-anchor")
        return {"assessments": [*payload["assessments"], second]}

    assert run(anchors=(anchor(), anchor("second-anchor")), raw_transform=duplicate)[0] == ()


def test_raw_string_and_mapping_fingerprints_have_explicit_distinct_basis():
    raw_holder = []

    def stringify(value):
        raw = json.dumps(value, ensure_ascii=False, indent=2)
        raw_holder.append(raw)
        return raw

    result, diagnostics, _ = run(raw_transform=stringify)
    assert result[0].analysis_response_sha256 == digest(raw_holder[0])
    assert diagnostics["response_sha256_basis"] == "raw_text"
    assert diagnostics["response_sha256"] != diagnostics["normalized_response_sha256"]


def test_callback_failure_is_not_retried_or_swallowed():
    calls = []

    def analyze(*args):
        calls.append(args)
        raise RuntimeError("검수 공급자 호출 실패")

    with pytest.raises(RuntimeError, match="검수 공급자"):
        collect_official_industry_context(candidates=(material(),), anchors=(anchor(),), company_id="synthetic-company", reference_date=REFERENCE, analyze=analyze, diagnostics={})
    assert len(calls) == 1


def test_adapter_uses_current_feature_entrypoint():
    from src.core.official_industry_context_adapter import collect_official_industry_context as collect
    diagnostics = {}
    assert collect(candidates=(), anchors=(anchor(),), company_id="synthetic-company", reference_date=REFERENCE, analyze=lambda *_: pytest.fail("호출 금지"), diagnostics=diagnostics) == ()
    assert diagnostics["model_calls"] == 0


def test_sentence_ids_change_when_fragment_or_source_text_changes():
    fragment, _ = material()
    changed, _ = material(CURRENT.replace("부품", "원자재"))
    assert sentence_ranges(fragment)[0]["id"] != sentence_ranges(changed)[0]["id"]


def test_other_business_cannot_use_same_business_true_to_borrow_anchor():
    text = CURRENT.replace("산업용 센서", "위생 소모품")
    assert run(pairs=(material(text),), modify=lambda row, _: row.update(industry="위생 소모품 시장", applicability_quote="위생 소모품 시장"))[0] == ()


def test_current_problem_is_preserved_beside_separate_future_clause():
    text = CURRENT[:-1] + ", 내년에는 공급망 회복이 예상됩니다."
    result, _, _ = run(pairs=(material(text),))
    assert len(result) == 1
    assert result[0].assessment_quote == text


def test_period_from_title_cannot_override_different_quote_year():
    pair = material(title="사업보고서 (2025.12)")
    assert run(pairs=(pair,), modify=lambda row, _: row.update(observation_period="2025.12"))[0] == ()


def test_future_period_is_not_current_observation_even_if_literal():
    text = CURRENT.replace("2026년", "2027년")
    assert run(pairs=(material(text),), modify=lambda row, _: row.update(observation_period="2027년 현재"))[0] == ()


@pytest.mark.parametrize("transform", [
    lambda value: '{"assessments":[],"assessments":' + json.dumps(value["assessments"]) + '}',
    lambda value: {"assessments": [dict(value["assessments"][0], invalid=float("nan"))]},
    lambda value: {"assessments": [dict(value["assessments"][0], invalid=b"bytes")]},
])
def test_ambiguous_or_non_json_responses_fail_closed(transform):
    assert run(raw_transform=transform)[0] == ()


def test_future_anchor_is_rejected_before_model():
    result, _, calls = run(anchors=(replace(anchor(), published_on="2027-01-01"),))
    assert result == ()
    assert calls == []


@pytest.mark.parametrize("location", ["https://example.com/report#0", "https://example.com/report · 목록 1번째 항목"])
def test_general_web_list_location_does_not_supply_publication_date(location):
    from src.shared.report_evidence.constants import SOURCE_KIND_OFFICIAL_WEB_PAGE
    fragment, document = material()
    pair = (replace(fragment, location=location), replace(document, source_kind=SOURCE_KIND_OFFICIAL_WEB_PAGE, canonical_url="https://example.com/report"))
    result, diagnostics, calls = run(pairs=(pair,))
    assert result == () and calls == []
    assert diagnostics["rejected"]["web_list_date_unbound"] == 1


def ir_material():
    from src.shared.official_ir import IR_METADATA_VERIFICATION_VALUE
    from src.shared.report_evidence.constants import SOURCE_KIND_OFFICIAL_IR_PDF
    fragment, document = material()
    start, end = map(int, fragment.location.split("-"))
    document = replace(document, source_kind=SOURCE_KIND_OFFICIAL_IR_PDF, canonical_url="https://example.com/ir.pdf", usable_ranges=(DocumentTextRange(start, end),), reporting_period="2026-Q3", attachment_url="https://example.com/ir.pdf", ir_metadata_verification=IR_METADATA_VERIFICATION_VALUE, domain_attestation_source_id="synthetic-attester", domain_attestation_evidence="합성 공식 도메인 확인 원문")
    return replace(fragment, location=document.canonical_url + "#0"), document


def test_verified_ir_legacy_location_preserves_publication_and_period():
    result, _, _ = run(pairs=(ir_material(),))
    assert len(result) == 1
    assert result[0].location == "https://example.com/ir.pdf#0"
    assert result[0].published_on == "2026-10-01"


@pytest.mark.parametrize("field", ["reporting_period", "attachment_url", "ir_metadata_verification"])
def test_incomplete_ir_metadata_is_excluded_before_model(field):
    fragment, document = ir_material()
    assert run(pairs=((fragment, replace(document, **{field: ""})),))[2] == []


def test_valid_sentence_from_another_requested_fragment_cannot_be_borrowed():
    first = material()
    second = material(CURRENT.replace("부품", "원자재"), fragment_id="second-fragment")

    def change(row, payload):
        row["sentence_ids"] = [payload["materials"][1]["sentences"][0]["id"]]

    assert run(pairs=(first, second), modify=change)[0] == ()


def test_unknown_api_source_has_zero_model_calls():
    fragment, document = material()
    result, diagnostics, calls = run(pairs=((fragment, replace(document, source_kind="unknown_api")),))
    assert result == ()
    assert diagnostics["model_calls"] == len(calls) == 0


def test_unknown_envelope_cannot_replace_closed_assessments():
    result, diagnostics, calls = run(raw_transform=lambda value: {**value, "verified": True})
    assert result == ()
    assert diagnostics["rejected"]["invalid_envelope"] == 1
    assert len(calls) == 1


def test_industry_candidate_after_six_company_descriptions_receives_input():
    background = tuple(material("당사는 사업을 영위하고 있습니다.", fragment_id=f"identity-{index}") for index in range(c.MAX_CANDIDATES))
    target = material(fragment_id="industry-target")
    result, diagnostics, calls = run(pairs=(*background, target))
    assert len(result) == 1 and len(calls) == 1
    assert result[0].exact_text == target[0].text
    assert diagnostics["candidate_count"] == 1
    assert diagnostics["rejected"]["industry_discovery_unbound"] == c.MAX_CANDIDATES


def test_industry_scope_without_anchor_object_never_calls_provider():
    result, diagnostics, calls = run(pairs=(material("2026년 국내 의료용 소모품 시장에서는 공급 부족이 발생했습니다."),))
    assert result == () and calls == [] and diagnostics["model_calls"] == 0


def test_relative_period_cannot_hide_document_reporting_year():
    result, diagnostics, _ = run(modify=lambda row, _: row.update(observation_period="현재"))
    assert result == () and diagnostics["rejected"]["unbound_assessment"] == 1


@pytest.mark.parametrize("text", [
    "2026년 현재 국내 산업용 센서 시장은 공급이 안정적입니다. 해외 산업용 센서 시장에서는 부품 공급 부족으로 생산 차질이 발생하고 있습니다.",
    "2025년 국내 산업용 센서 시장에서는 부품 공급 부족으로 생산 차질이 발생했습니다. 2026년 현재 산업용 센서 시장의 품질 기준을 설명합니다.",
])
def test_problem_clause_cannot_borrow_other_sentence_region_or_year(text):
    def change(row, payload):
        row["sentence_ids"] = [sentence["id"] for sentence in payload["materials"][0]["sentences"]]
    assert run(pairs=(material(text),), modify=change)[0] == ()
