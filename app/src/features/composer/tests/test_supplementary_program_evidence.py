from __future__ import annotations

from dataclasses import replace

import pytest

from src.core import news_intake_switch
from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer.port import (
    CollectedFragment,
    ComposedSentence,
    VerifiedProgramEvidence,
)
from src.features.pipeline.port import FactRecord
from src.features.provenance.sources import (
    Source,
    SourceKind,
    evidence_text_hash,
    exact_evidence_text_hash,
    seal_collected_source,
)
from src.shared.report_quality.fact_binding import fact_evidence_binding
from src.shared.report_quality.source_identity import document_identity


@pytest.fixture(autouse=True)
def _news_intake_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001


def _program_evidence(claim: str) -> VerifiedProgramEvidence:
    slot = "business_model:revenue_model"
    source = seal_collected_source(
        Source(
            number=7,
            kind=SourceKind.NEWS,
            label="신규 사업 기사",
            published_at="2026-08-30",
            domain="news.example.com",
            source_id="source-news-7",
            title="신규 사업 기사",
            publisher="예시경제",
            host="news.example.com",
            url="https://news.example.com/articles/n-7",
            document_id="n-7",
            location="본문 2문단",
            source_type="언론 보도",
            fact_status="외부 보도",
            used_in=["business_model"],
            evidence_hashes=[evidence_text_hash(claim)],
            exact_evidence_hashes=[exact_evidence_text_hash(claim)],
        )
    )
    identity = document_identity(source)
    fragment = CollectedFragment(
        fragment_id="7",
        kind="뉴스",
        text=claim,
        source_url=source.url,
        document_title=source.title,
        location=source.location,
        document_date=source.published_at,
        document_identity=identity,
        supported_claim_slots=(slot,),
        bound_source=source,
    )
    fact = FactRecord(
        fact_id="fact-news-7",
        legal_entity="예시회사",
        subject_scope="신규 사업",
        relationship_or_action="사업 운영",
        claim=claim,
        claim_type="identity",
        section_owner="business_model",
        time_state="current",
        as_of="2026-08-30",
        source_id=source.source_id,
        source_type=source.source_type,
        source_title=source.title,
        source_publisher=source.publisher,
        source_host=source.host,
        source_url=source.url,
        source_document_id=source.document_id,
        location=source.location,
        status="verified",
        fact_status="actual",
        verification_status="verified",
        state_evidence=claim,
        source_date=source.published_at,
        claim_slot=slot,
        supporting_source_ids=[source.source_id],
        supporting_source_identities=[identity],
        supporting_evidence_hashes=[exact_evidence_text_hash(claim)],
    )
    fact = replace(fact, evidence_binding=fact_evidence_binding(fact))
    sentence = ComposedSentence(
        text=claim,
        citations=("7",),
        grade=GRADE_CONFIRMED,
        planned_claim_slot=slot,
        verification_state="verified",
        verified_fact_id=fact.fact_id,
    )
    return VerifiedProgramEvidence(
        section_id="business_model",
        source_fragments=(fragment,),
        registry_sources=(source,),
        facts=(fact,),
        sentences=(sentence,),
    )


def test_봉인된_언론_Source는_산문_인용_게이트를_통과한다() -> None:
    evidence = _program_evidence("회사는 신규 사업을 시작했다고 밝혔다.")

    assert evidence.registry_sources[0].kind is SourceKind.NEWS


def test_언론_Source의_정확원문_수치는_허용한다() -> None:
    evidence = _program_evidence("회사는 신규 사업 계약을 3건 체결했다고 밝혔다.")
    assert evidence.sentences[0].verification_state == "verified"


def test_언론_Source의_수치를_변조하면_거절한다() -> None:
    evidence = _program_evidence("회사는 신규 사업 계약을 3건 체결했다고 밝혔다.")
    with pytest.raises(ValueError, match="수치"):
        replace(evidence, sentences=(replace(evidence.sentences[0], text="회사는 신규 사업 계약을 30건 체결했다고 밝혔다."),))
