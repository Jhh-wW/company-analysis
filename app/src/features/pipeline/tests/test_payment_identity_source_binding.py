"""저장된 실제 공시 문장을 정상 수집·운반·출처 검증 경로로 다시 생산한다.

모델의 신규 판정은 재현하지 않는다. 검증 완료 DTO 이후 출처 결속만 검사하며,
기존 실행의 서명이나 비밀키는 사용하지 않는다.
"""

import importlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.core.source_verification_adapter import supplementary_research_source_verifier
from src.features.chapter_evidence.produce import produce_from_collection_envelopes
from src.features.composer.port import ComposedSentence
from src.features.composer.logic import build_section_prompt
from src.features.composer.evidence_pair_selection import writer_visible_fragments
from src.features.composer.prose_facts import ProseEvidence, build_verified_prose_fact
from src.features.composer.render import _build_source, _fragment_metas
from src.features.pipeline.business_activity_anchors import build_business_activity_anchors
from src.features.pipeline.evidence_transport import typed_fragments_from_raw
from src.features.pipeline.official_evidence_transport_adapter import merge_official_evidence_fragments
from src.features.pipeline.supplementary_fact_binding import bound_supplementary_fact_sources
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult


ROOT = Path(__file__).resolve().parents[5]
FIXTURE = json.loads((ROOT / "analysis_engine/src/features/evidence_collection/tests/fixtures/payment_service_slots.json").read_text(encoding="utf-8"))
COMPANY_ID = "01492697"
COMPANY_NAME = "토스페이먼츠"
REFERENCE_DATE = "2026-10-21"
IDENTITY_SLOT = "identity:business_definition"
PORTFOLIO_REVENUE_SLOT = "portfolio:revenue_link"


def _collected_payment_evidence(monkeypatch, fragment_id):
    monkeypatch.syspath_prepend(str(ROOT / "analysis_engine/src"))
    collect = importlib.import_module("features.evidence_collection.collect")
    filings = importlib.import_module("features.evidence_collection.filing_select")
    serialize = importlib.import_module("features.evidence_collection.serialize")
    fake = importlib.import_module("features.evidence_collection.tests.fixtures.fake_fetcher")
    row = filings.RawFilingRow("20260331000296", "감사보고서 (2025.12)", "20260331", corp_code=COMPANY_ID, corp_name=COMPANY_NAME)
    fetcher = fake.FakeFetcher(
        list_responses_by_pblntf_ty={"F": filings.FilingListResult(state="OK", rows=(row,))},
        document_responses_by_rcept_no={row.rcept_no: filings.DocumentFetchResult(state="OK", text=FIXTURE["fragments"][fragment_id])},
    )
    mapping = serialize.harvest_to_mapping(collect.collect_dart_evidence(fetcher, COMPANY_ID, now=REFERENCE_DATE))
    return OfficialEvidenceCollectionResult(
        company_id=COMPANY_ID,
        candidates=produce_from_collection_envelopes(
            company_id=COMPANY_ID, company_type=mapping["company_type"],
            company_name=COMPANY_NAME, collection_envelopes=(mapping,),
        ),
    )


@pytest.mark.parametrize("fragment_id", ("37", "73"))
def test_actual_current_business_survives_collection_and_final_source_binding(monkeypatch, fragment_id):
    result = _collected_payment_evidence(monkeypatch, fragment_id)
    merged, _ = merge_official_evidence_fragments({}, result)
    conversion = typed_fragments_from_raw(corp_id=COMPANY_ID, frags=merged, filing_meta=None)
    assert conversion.rejected_count == 0
    fragment = next(value for value in conversion.fragments if IDENTITY_SLOT in value.supported_claim_slots)
    assert fragment.text == FIXTURE["fragments"][fragment_id]
    meta, = _fragment_metas((fragment,))
    source = _build_source(meta, 1, COMPANY_NAME, ("identity",))
    sentence = ComposedSentence(
        text="회사는 전자지불결제대행업을 통하여 대금을 받아 가맹점에 수수료를 제외하여 지급하고 있습니다.",
        citations=("1",), grade="확인", verification_state="verified", planned_claim_slot=IDENTITY_SLOT,
    )
    fact = build_verified_prose_fact(
        sentence, section_id="identity", company_name=COMPANY_NAME, as_of_date=REFERENCE_DATE,
        evidence=(ProseEvidence("1", source, fragment.text),),
    )
    assert fact is not None
    verifier = supplementary_research_source_verifier()
    bound = bound_supplementary_fact_sources(fact, registry=(source,), reference_date=REFERENCE_DATE, source_verifier=verifier)
    assert len(bound) == 1 and bound[0].official
    # 현재 생산 봉인도 원문 SHA를 바꾸면 통과하지 않는다.
    damaged = replace(source, document_content_sha256="f" * 64)
    assert bound_supplementary_fact_sources(fact, registry=(damaged,), reference_date=REFERENCE_DATE, source_verifier=verifier) == ()

    anchors = build_business_activity_anchors(result, profile={"corp_code": COMPANY_ID, "corp_name": COMPANY_NAME})
    assert len(anchors) == 1 and anchors[0].business_item == "전자지불결제대행업"
    assert anchors[0].exact_text == fragment.text


@pytest.mark.parametrize("fragment_id", ("9", "40"))
def test_actual_service_sources_reach_typed_portfolio_writer_as_revenue_links(monkeypatch, fragment_id):
    result = _collected_payment_evidence(monkeypatch, fragment_id)
    merged, _ = merge_official_evidence_fragments({}, result)
    conversion = typed_fragments_from_raw(corp_id=COMPANY_ID, frags=merged, filing_meta=None)
    assert conversion.rejected_count == 0
    fragment = next(value for value in conversion.fragments if PORTFOLIO_REVENUE_SLOT in value.supported_claim_slots)
    assert fragment.text == FIXTURE["fragments"][fragment_id]
    assert "portfolio:product_role" not in fragment.supported_claim_slots
    assert any("business_model:revenue_model" in value.supported_claim_slots for value in conversion.fragments)
    assert fragment in writer_visible_fragments("portfolio", conversion.fragments)
    prompt = build_section_prompt(COMPANY_NAME, "portfolio", (fragment,), None, show_supported_claim_slots=True)
    assert fragment.text in prompt and PORTFOLIO_REVENUE_SLOT in prompt
