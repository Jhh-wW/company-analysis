"""후단 제외 진단은 공개 결과를 바꾸지 않고 실제 실패 경계만 남긴다."""

from dataclasses import replace
from hashlib import sha256
import json

import pytest

from src.core.source_verification_adapter import supplementary_research_source_verifier
from src.core import news_intake_switch
from src.features.pipeline.supplementary_fact_binding import bound_supplementary_fact_sources
from src.features.pipeline.supplementary_research_filter import filter_supplementary_research_report
from src.features.pipeline.supplementary_research_runtime import enforce_supplementary_research_release
from src.features.pipeline.tests.test_supplementary_fact_binding import _produced_fact
from src.features.pipeline.tests.test_supplementary_research_filter import _run, _hash_tampered_evidence
from src.features.pipeline.tests.test_supplementary_research_producer_independent import _rendered_report
from src.shared.report_quality.fact_binding import fact_evidence_binding


@pytest.fixture(autouse=True)
def _news_enabled(monkeypatch):
    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
    news_intake_switch._reset_process_news_intake_switch_for_tests()
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()


def _binding(fact, registry, detail=None):
    return bound_supplementary_fact_sources(
        fact, registry=registry, reference_date="2026-09-04",
        source_verifier=supplementary_research_source_verifier(), failure_detail=detail,
    )


def _filter(report, evidence, diagnostics=None):
    return filter_supplementary_research_report(
        report, official_evidence=evidence,
        source_verifier=supplementary_research_source_verifier(), exclusion_diagnostics=diagnostics,
    )


def test_valid_fact_and_report_do_not_emit_exclusions():
    fact, registry = _produced_fact()
    detail = {}
    assert _binding(fact, registry, detail) == _binding(fact, registry)
    assert detail == {}
    report, evidence = _rendered_report()
    diagnostics = []
    assert _filter(report, evidence, diagnostics) == _filter(report, evidence)
    assert diagnostics == []


@pytest.mark.parametrize("field,value,check", [
    ("source_id", "임의 원문 주소", "manifest_source_id"),
    ("document_identity", "임의 법인 원문", "manifest_document_identity"),
    ("exact_sha256", "f" * 64, "manifest_exact_sha256"),
    ("fragment_id", "", "manifest_fragment_id"),
])
def test_manifest_failure_records_exact_check_without_original(field, value, check):
    fact, registry = _produced_fact()
    manifest = json.loads(fact.state_evidence)
    manifest[0][field] = value
    changed = replace(fact, state_evidence=json.dumps(manifest))
    changed = replace(changed, evidence_binding=fact_evidence_binding(changed))
    detail = {}
    assert _binding(changed, registry, detail) == _binding(changed, registry) == ()
    assert detail["stage"] == "fact_binding"
    assert detail["reason_code"] == "supplementary_fact_binding_invalid"
    assert detail["check_items"] == [check]
    assert detail["metadata_fields"] == []
    if value:
        assert value not in json.dumps(detail, ensure_ascii=False)


def test_primary_metadata_failure_identifies_only_field_name():
    fact, registry = _produced_fact()
    changed = replace(fact, source_title="원문에 없는 비공개 제목")
    changed = replace(changed, evidence_binding=fact_evidence_binding(changed))
    detail = {}
    assert _binding(changed, registry, detail) == ()
    assert detail["check_items"] == ["primary_source_metadata"]
    assert detail["metadata_fields"] == ["source_title"]
    assert changed.source_title not in json.dumps(detail, ensure_ascii=False)


def test_document_hash_failure_has_claim_hash_and_same_public_result():
    report, evidence = _rendered_report()
    changed = _hash_tampered_evidence(evidence)
    diagnostics = []
    observed = _filter(report, changed, diagnostics)
    assert observed == _filter(report, changed)
    removed = {fact.fact_id: fact for fact in report.fact_records
               if fact.fact_id not in {item.fact_id for item in observed.fact_records}}
    assert len(diagnostics) == len(removed)
    assert {item["claim_sha256"] for item in diagnostics} == {
        sha256(fact.claim.encode()).hexdigest() for fact in removed.values()
    }
    assert any(failure["check_items"] == ["document_content_sha256"]
               for item in diagnostics for failure in item["failures"])
    rendered = json.dumps(diagnostics, ensure_ascii=False)
    assert all(fact.claim not in rendered for fact in removed.values())
    assert "https://" not in rendered


def test_runtime_records_individual_exclusions_with_existing_count():
    report, evidence = _rendered_report()
    steps = []
    result = enforce_supplementary_research_release(
        _run(report), official_evidence=_hash_tampered_evidence(evidence), steps=steps,
    )
    event = next(item for item in steps if item["step"] == "8_보완조사_불일치근거제외")
    assert event["제외사실수"] == len(report.fact_records) - len(result.report.fact_records)
    assert len(event["제외사실진단"]) == event["제외사실수"]


def test_document_exact_hash_failure_is_distinct_from_document_hash():
    report, evidence = _rendered_report()
    first = evidence.candidates[0]
    document = replace(first.documents[0], exact_evidence_hashes=("f" * 64,))
    changed = replace(evidence, candidates=(replace(first, documents=(document,), fragments=()), *evidence.candidates[1:]))
    diagnostics = []
    assert _filter(report, changed, diagnostics) == _filter(report, changed)
    assert any(failure["check_items"] == ["exact_evidence_hashes"]
               for item in diagnostics for failure in item["failures"])


def test_dependent_fact_records_dependency_without_changing_selection():
    report, evidence = _rendered_report()
    identity = next(fact for fact in report.fact_records if fact.section_owner == "identity")
    business = next(fact for fact in report.fact_records if fact.section_owner == "business_model")
    dependent = replace(business, basis_fact_ids=[identity.fact_id])
    dependent = replace(dependent, evidence_binding=fact_evidence_binding(dependent))
    changed_report = replace(report, fact_records=[
        dependent if fact.fact_id == business.fact_id else fact for fact in report.fact_records
    ])
    diagnostics = []
    changed_evidence = _hash_tampered_evidence(evidence)
    assert _filter(changed_report, changed_evidence, diagnostics) == _filter(changed_report, changed_evidence)
    entry = next(item for item in diagnostics if item["claim_sha256"] == sha256(business.claim.encode()).hexdigest())
    assert entry["failures"] == [{"stage": "fact_dependency",
                                  "reason_code": "supplementary_fact_dependency_invalid",
                                  "check_items": ["basis_fact_ids"]}]
