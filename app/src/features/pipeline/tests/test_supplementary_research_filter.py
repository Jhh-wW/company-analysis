"""보완조사 불일치 근거를 공개 본문에서 제거하는 독립 회귀 시험."""

from dataclasses import replace
from typing import Final

import pytest

from src.core import news_intake_switch
from src.features.composer.constants import CITATION_STYLE_INLINE
from src.features.pipeline.port import Outcome, Report, RunResult, SummaryItem
from src.features.pipeline.supplementary_research_runtime import (
    enforce_supplementary_research_release,
)
from src.features.pipeline.tests.test_supplementary_research_producer_independent import (
    _rendered_report,
)
from src.features.pipeline.tests import test_supplementary_research_producer_independent as producer
from src.features.storage.reports import report_from_json, report_to_json


_COST_KRW: Final[float] = 13.75
_CACHE_SNAPSHOT_ID: Final[str] = "c" * 32
_CACHE_ARTIFACT_ID: Final[str] = "d" * 32


@pytest.fixture(autouse=True)
def _news_intake_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
    news_intake_switch._reset_process_news_intake_switch_for_tests()
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()


def _section(report: Report, section_id: str):
    return next(section for section in report.sections if section.cell == section_id)


def _with_summaries(report: Report) -> Report:
    identity = next(fact for fact in report.fact_records if fact.section_owner == "identity")
    business = next(
        fact for fact in report.fact_records if fact.section_owner == "business_model"
    )
    return replace(
        report,
        summary_items=[
            SummaryItem(text=identity.claim, section_id="identity", fact_ids=[identity.fact_id]),
            SummaryItem(text=business.claim, section_id="business_model", fact_ids=[business.fact_id]),
        ],
    )


def _run(report: Report) -> RunResult:
    return RunResult(
        outcome=Outcome.REPORT,
        report=report,
        charged=True,
        cost_krw=_COST_KRW,
        cache_hit="1층",
        generation_cache_eligible=True,
        reused_content_snapshot_id=_CACHE_SNAPSHOT_ID,
        reused_artifact_id=_CACHE_ARTIFACT_ID,
    )


def _enforce(report: Report, evidence) -> RunResult:
    result = _run(report)
    return enforce_supplementary_research_release(
        result,
        official_evidence=evidence,
        steps=[],
    )


def _hash_tampered_evidence(evidence):
    first = evidence.candidates[0]
    changed_document = replace(first.documents[0], content_sha256="f" * 64)
    changed_candidate = replace(first, documents=(changed_document,))
    return replace(evidence, candidates=(changed_candidate, *evidence.candidates[1:]))


def _assert_storage_roundtrip(report: Report) -> Report:
    restored = report_from_json(report_to_json(report))
    assert restored == report
    return restored


def _assert_common_result_contract(result: RunResult) -> None:
    assert result.outcome is Outcome.REPORT
    assert result.report is not None
    assert result.charged is True
    assert result.cost_krw == _COST_KRW
    assert result.generation_cache_eligible is False
    assert result.cache_hit == ""
    assert result.reused_content_snapshot_id == ""
    assert result.reused_artifact_id == ""
    assert result.generation_evidence is None
    assert result.quality_observation is None


def test_hash_tampering_removes_identity_from_public_report_and_roundtrip() -> None:
    report, evidence = _rendered_report()
    original = _with_summaries(report)

    result = _enforce(original, _hash_tampered_evidence(evidence))
    _assert_common_result_contract(result)
    filtered = result.report
    assert filtered is not None

    identity = _section(filtered, "identity")
    assert identity.prose_lines == []
    assert identity.prose_paragraphs == []
    assert identity.fact_ids == []
    assert all(fact.section_owner != "identity" for fact in filtered.fact_records)
    assert all(item.section_id != "identity" for item in filtered.summary_items)
    assert any(item.section_id == "business_model" for item in filtered.summary_items)
    assert _section(filtered, "business_model").prose_lines == _section(
        original, "business_model"
    ).prose_lines
    assert _section(filtered, "portfolio").prose_lines == _section(
        original, "portfolio"
    ).prose_lines

    restored = _assert_storage_roundtrip(filtered)
    assert _section(restored, "identity").prose_lines == []
    assert _section(restored, "identity").prose_paragraphs == []
    assert all(fact.section_owner != "identity" for fact in restored.fact_records)
    assert _section(restored, "business_model").prose_lines == _section(
        original, "business_model"
    ).prose_lines
    assert _section(restored, "portfolio").prose_lines == _section(
        original, "portfolio"
    ).prose_lines


def test_inline_tampering_keeps_other_identity_sentence_and_removes_invalid_summary() -> None:
    identity_claims = (
        "가나다전자는 산업용 센서를 제조한다.",
        "가나다전자는 연구용 센서를 개발한다.",
    )
    combined_identity_source = " ".join(identity_claims)
    identity_fragment = producer._fragment(
        "1", "identity", combined_identity_source,
        kind=producer.SOURCE_KIND_DART_BUSINESS_REPORT,
    )
    identity_source = producer._sealed_source(identity_fragment, section_id="identity")
    fragments = [replace(identity_fragment, bound_source=identity_source)]
    sentences = [
        producer.ComposedSentence(
            text=claim,
            citations=("1",),
            grade="확인",
            planned_claim_slot=producer.CLAIM_SLOTS_BY_SECTION["identity"][index],
            verification_state="verified",
        )
        for index, claim in enumerate(identity_claims)
    ]
    sections = [producer.ComposedSection("identity", tuple(sentences))]
    source_map = {"identity": (identity_source, combined_identity_source)}
    for fragment_id, section_id, kind, claim in (
        ("2", "business_model", producer.SOURCE_KIND_DART_BUSINESS_REPORT,
         "가나다전자는 기업 고객에게 센서를 판매한다."),
        ("3", "portfolio", producer.SOURCE_KIND_NEWS,
         "가나다전자의 핵심 제품은 산업용 센서다."),
    ):
        fragment = producer._fragment(fragment_id, section_id, claim, kind=kind)
        source = producer._sealed_source(fragment, section_id=section_id)
        fragments.append(replace(fragment, bound_source=source))
        sections.append(producer.ComposedSection(section_id, (
            producer.ComposedSentence(
                text=claim,
                citations=(fragment_id,),
                grade="확인",
                planned_claim_slot=producer.CLAIM_SLOTS_BY_SECTION[section_id][0],
                verification_state="verified",
            ),
        )))
        if kind == producer.SOURCE_KIND_DART_BUSINESS_REPORT:
            source_map[section_id] = (source, claim)
    report = producer.render_report(
        producer._COMPANY,
        producer.ComposedReport(tuple(sections)),
        tuple(fragments),
        None,
        corp_type="주식회사",
        as_of_date=producer._AS_OF,
        company_id=producer._COMPANY_ID,
        citation_style=CITATION_STYLE_INLINE,
    )
    evidence = producer._official_evidence(source_map)
    identity = _section(report, "identity")
    identity_fact, normal_fact = [
        fact for fact in report.fact_records if fact.section_owner == "identity"
    ]
    normal_display = identity.prose_lines[1][0]
    tampered_display = identity.prose_lines[0][0].replace("[1]", "[2]")
    tampered_identity = replace(
        identity,
        prose_lines=[(tampered_display, ""), (normal_display, "")],
        prose_paragraphs=[tampered_display, normal_display],
        fact_ids=[identity_fact.fact_id, normal_fact.fact_id],
    )
    original = _with_summaries(
        replace(
            report,
            sections=[
                tampered_identity
                if section.cell == "identity"
                else section
                for section in report.sections
            ],
        )
    )

    result = _enforce(original, evidence)
    _assert_common_result_contract(result)
    filtered = result.report
    assert filtered is not None
    filtered_identity = _section(filtered, "identity")
    assert filtered_identity.prose_lines == [(normal_display, "")]
    assert filtered_identity.prose_paragraphs == [normal_display]
    assert filtered_identity.fact_ids == [normal_fact.fact_id]
    assert identity_fact.fact_id not in {fact.fact_id for fact in filtered.fact_records}
    assert normal_fact.fact_id in {fact.fact_id for fact in filtered.fact_records}
    assert all(item.fact_ids != [identity_fact.fact_id] for item in filtered.summary_items)
    assert any(item.section_id == "business_model" for item in filtered.summary_items)

    restored = _assert_storage_roundtrip(filtered)
    restored_identity = _section(restored, "identity")
    assert restored_identity.prose_lines == [(normal_display, "")]
    assert restored_identity.prose_paragraphs == [normal_display]
    assert normal_fact.fact_id in {fact.fact_id for fact in restored.fact_records}
    assert identity_fact.fact_id not in {fact.fact_id for fact in restored.fact_records}
