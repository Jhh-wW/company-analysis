"""실제 산문 FactRecord 생산자와 보완조사 근거 검산의 양성·변조 대조."""

import json
from dataclasses import replace

import pytest

from src.core.source_verification_adapter import supplementary_research_source_verifier
from src.core import news_intake_switch
from src.features.composer.port import ComposedSentence
from src.features.composer.prose_facts import ProseEvidence, build_verified_prose_fact
from src.features.composer.render import _build_source, _fragment_metas
from src.features.composer.tests.test_supplementary_public_source import _news_fragment
from src.features.pipeline.supplementary_fact_binding import bound_supplementary_fact_sources
from src.features.provenance.sources import seal_collected_source
from src.features.provenance.tests.test_sources import _formal_source_registry
from src.shared.report_evidence.constants import SOURCE_KIND_DART_BUSINESS_REPORT
from src.shared.report_quality.fact_binding import fact_evidence_binding


def _produced_fact(*, multiple: bool = False):
    registry = _formal_source_registry(SOURCE_KIND_DART_BUSINESS_REPORT)
    if multiple:
        registry = (*registry, seal_collected_source(replace(
            registry[0], number=2, source_id=registry[0].source_id + "-second",
        )))
    sentence = ComposedSentence(
        text="공시 원문", citations=tuple(str(source.number) for source in registry),
        grade="확인", planned_claim_slot="business_model:revenue_model",
        verification_state="verified",
    )
    fact = build_verified_prose_fact(
        sentence, section_id="business_model", company_name="가나다전자",
        as_of_date="2026-09-04",
        evidence=tuple(ProseEvidence(str(source.number), source, "공시 원문") for source in registry),
    )
    assert fact is not None
    return fact, registry


def _bound(fact, registry):
    return bound_supplementary_fact_sources(
        fact, registry=registry, reference_date="2026-09-04",
        source_verifier=supplementary_research_source_verifier(),
    )


@pytest.mark.parametrize("multiple", [False, True])
def test_actual_prose_producer_manifest_is_not_mistaken_for_original_text(multiple: bool) -> None:
    fact, registry = _produced_fact(multiple=multiple)
    assert fact.state_evidence.startswith("[")
    bound = _bound(fact, registry)
    assert tuple(item.source_id for item in bound) == tuple(fact.supporting_source_ids)
    assert len(bound) == len(registry)


@pytest.mark.parametrize("field,value", [
    ("claim", "공시가 말하지 않은 주장"), ("source_title", "다른 제목"),
    ("supporting_evidence_hashes", ["f" * 64]),
    ("supporting_source_identities", ["다른 문서"]),
    ("supporting_source_ids", []), ("claim_slot", "identity:unknown"),
])
def test_fact_tampering_is_rejected_by_existing_binding_fingerprint(field: str, value: object) -> None:
    fact, registry = _produced_fact()
    assert _bound(replace(fact, **{field: value}), registry) == ()


def test_changing_manifest_hash_and_recomputing_public_fingerprint_does_not_bind_to_original() -> None:
    fact, registry = _produced_fact()
    manifest = json.loads(fact.state_evidence)
    manifest[0]["exact_sha256"] = "f" * 64
    changed = replace(fact, state_evidence=json.dumps(manifest))
    changed = replace(changed, evidence_binding=fact_evidence_binding(changed))
    assert _bound(changed, registry) == ()


def test_missing_supplementary_source_does_not_pass_with_primary_source_only() -> None:
    fact, registry = _produced_fact(multiple=True)
    assert _bound(fact, registry[:1]) == ()


def test_body_original_text_fact_is_checked_distinctly_from_manifest() -> None:
    fact, registry = _produced_fact()
    fact = replace(fact, claim_type="stated_differentiator", state_evidence="공시 원문")
    fact = replace(fact, evidence_binding=fact_evidence_binding(fact))
    assert len(_bound(fact, registry)) == 1


@pytest.fixture
def _news_enabled(monkeypatch):
    news_intake_switch._reset_process_news_intake_switch_for_tests()
    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()


def test_actual_news_source_rebinds_to_prose_producer_exact_original_text(_news_enabled) -> None:
    fragment = _news_fragment()
    [meta] = _fragment_metas((fragment,))
    source = _build_source(meta, 7, "예시회사", ("business_model",))
    sentence = ComposedSentence(
        text="2026-08-30 예시경제 보도에 따르면, " + fragment.text,
        citations=("7",), grade="확인", verification_state="verified",
        planned_claim_slot="business_model:revenue_model",
    )
    fact = build_verified_prose_fact(
        sentence, section_id="business_model", company_name="예시회사",
        as_of_date="2026-09-04", evidence=(ProseEvidence("7", source, fragment.text),),
    )
    assert fact is not None
    bound = _bound(fact, (source,))
    assert len(bound) == 1 and bound[0].news and not bound[0].official

    manifest = json.loads(fact.state_evidence)
    manifest[0]["news_exact_text"] += " 원문에 없는 내용"
    changed = replace(fact, state_evidence=json.dumps(manifest))
    changed = replace(changed, evidence_binding=fact_evidence_binding(changed))
    assert _bound(changed, (source,)) == ()


@pytest.mark.parametrize("value", [None, [], {}, "잘못된 자료형"])
def test_non_fact_dto_fails_closed(value: object) -> None:
    _fact, registry = _produced_fact()
    assert _bound(value, registry) == ()
