"""공식 사업 → 뉴스 조사 → 별도 산업 근거 운반 경계를 무료 대역으로 검증한다."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace
from types import SimpleNamespace

from src.features.pipeline import real
from src.features.pipeline.tests.test_business_activity_anchors import PROFILE, _evidence
from src.features.pipeline.business_activity_anchors import build_business_activity_anchors
from src.shared.business_challenge_context import IndustryProblemEvidence
from src.shared.report_generation.models import exact_text_sha256


AS_OF = dt.date(2026, 9, 30)
ARTICLE_URL = "https://www.reuters.com/world/test-business-challenges"
BODY = (
    "한국의 정밀부품 제조 산업은 핵심 부품의 공급 지연으로 생산 일정이 늦어지는 문제를 겪고 있다. "
    "업체들은 납기를 맞추기 위해 공급처를 추가로 확인하고 있으며 기존 거래처와 일정을 조정하고 있다."
)


def _branch(evidence):
    queries, reservations = [], []

    def search(query, **_kwargs):
        queries.append(query)
        return SimpleNamespace(
            state="success", reason_code="news_search_ok", transport_attempts=1,
            retry_recovered=False, attempt_reason_codes=("news_search_ok",),
            items=[SimpleNamespace(
                title="국내 정밀부품 산업 공급 지연", description=BODY,
                originallink=ARTICLE_URL, link="", pubDate="2026-09-01",
            )] if "정밀부품" in query else [],
        )

    def available(*, reserved_calls):
        reservations.append(reserved_calls)
        return 3

    outcome = real._run_news_search_branch(
        engine=SimpleNamespace(available_provider_calls=available),
        profile=PROFILE, official_evidence=evidence,
        company_name=PROFILE["corp_name"], business_date=AS_OF,
        pipeline_news_search=search,
    )
    return outcome, queries, reservations


def test_industry_problems_cross_runtime_without_becoming_company_fragments():
    evidence = _evidence(("당사는 정밀부품을 제조합니다.",))
    outcome, queries, reservations = _branch(evidence)
    baseline, ordinary_queries, ordinary_reservations = _branch(None)
    assert outcome.error is baseline.error is None
    assert not outcome.value.preparation_failed
    session = outcome.value.session
    assert len(session.company.business_anchors) == 1
    assert len(queries) == len(ordinary_queries)
    assert reservations == ordinary_reservations
    assert session.policy.max_analysis_calls == baseline.value.session.policy.max_analysis_calls
    assert outcome.steps[-1]["공식사업조사앵커"] == 1
    anchor = session.company.business_anchors[0]
    calls, steps, problems = [], [], []

    def analyze(prompt, schema, max_tokens):
        calls.append((schema, max_tokens))
        articles = json.loads(prompt.split("자료 시작:\n", 1)[1])["articles"]
        return {"items": [{
            "id": article["id"], "same_company": False, "material": False,
            "entity_evidence": "", "source_type": "news_report", "excerpts": [],
            "industry_problems": [{
                "anchor_id": anchor.anchor_id, "text": BODY,
                "industry": "정밀부품 제조", "problem": "공급 지연",
                "geography": "domestic", "geography_detail": "한국",
                "geography_evidence": "한국의", "applicability_quote": "정밀부품 제조 산업",
                "problem_present": True, "same_business": True, "geography_supported": True,
            }],
        } for article in articles]}

    fragments = real._collect_grounded_news(
        session=session, analyze=analyze, fetch_text=lambda _url: BODY,
        corp_id=PROFILE["corp_code"], official_web_documents=0,
        collected_on=AS_OF.isoformat(), steps=steps, industry_problem_sink=problems,
    )
    assert len(calls) == 1
    assert fragments == []
    assert len(problems) == 1
    assert problems[0].business_anchor_id == anchor.anchor_id
    assert problems[0].exact_text == BODY
    assert steps[-1]["조각"] == steps[-1]["관련성통과"] == 0


def test_anchor_preparation_error_is_recorded_without_discarding_official_data(monkeypatch):
    from src.features.pipeline import business_activity_anchors

    def fail(*_args, **_kwargs):
        raise ValueError("모의 사업 근거 오류")

    monkeypatch.setattr(business_activity_anchors, "build_business_activity_anchors", fail)
    outcome, queries, _reservations = _branch(None)
    assert outcome.error is None
    assert outcome.value.preparation_failed
    assert outcome.value.session is None
    assert not queries


def test_failed_collection_does_not_leave_partially_transported_industry_evidence():
    def fail(**_kwargs):
        raise ValueError("모의 기사 근거 오류")

    problems, steps = [], []
    session = SimpleNamespace(
        collect=fail,
        snapshot=SimpleNamespace(query_attempts=(), transport_diagnostics={}),
    )
    assert real._collect_grounded_news(
        session=session, analyze=lambda *_args: {}, fetch_text=lambda _url: None,
        corp_id=PROFILE["corp_code"], official_web_documents=0,
        collected_on=AS_OF.isoformat(), steps=steps, industry_problem_sink=problems,
    ) == []
    assert not problems
    assert steps[-1]["캐시재사용가능"] is False


def test_available_report_keeps_industry_context_without_counting_it_as_company_facts():
    evidence = _evidence(("당사는 정밀부품을 제조합니다.",))
    # 실제 수집기의 공개 출처 계약처럼 collected_at에는 수집 기준일을 넣는다.
    evidence = replace(evidence, candidates=tuple(
        replace(candidate, documents=tuple(
            replace(document, collected_at=AS_OF.isoformat()) for document in candidate.documents
        )) for candidate in evidence.candidates
    ))
    anchors = build_business_activity_anchors(evidence, profile=PROFILE)
    frags, _added = real.merge_official_evidence_fragments({}, evidence)
    problem = IndustryProblemEvidence(
        evidence_id="synthetic-industry", business_anchor_id=anchors[0].anchor_id,
        document_id="synthetic-article", source_url=ARTICLE_URL,
        publisher="reuters.com", title="국내 정밀부품 산업 공급 지연", published_on="2026-09-01",
        location=f"chars:0-{len(BODY)}", exact_text=BODY, text_sha256=exact_text_sha256(BODY),
        industry="정밀부품 제조", problem="공급 지연", geography="domestic",
        geography_detail="한국", geography_evidence="한국의",
        document_content_sha256=exact_text_sha256(BODY),
        analysis_response_sha256=exact_text_sha256("무료 합성 검수 응답"),
        applicability_quote="정밀부품 제조 산업",
    )
    result = real._run_available_evidence_report(
        engine=SimpleNamespace(usages=[]), company_name=PROFILE["corp_name"],
        corp_id=PROFILE["corp_code"], corp_type="", frags=frags, filing=None,
        performance_table=None, revenue_tables=[], sources=[], business_date=AS_OF,
        model="", steps=[], reason_code="synthetic_evidence_available",
        official_evidence=evidence, industry_anchors=anchors, industry_problems=(problem,),
    )
    section = next(value for value in result.report.sections if value.cell == "current_challenges")
    assert len(section.industry_contexts) == 1
    assert not section.is_filled and not section.fact_ids
    assert not result.report.fact_records
    assert result.cost_krw == 0
    assert result.fragments_cited == result.generation_metrics.fragments_cited
    assert result.fragments_cited <= result.fragments_collected
    assert real._industry_context_diagnostics(result.report) == {
        "공개산업과제": 1, "공개산업자료": 1, "공개사업연결근거": 1,
    }
