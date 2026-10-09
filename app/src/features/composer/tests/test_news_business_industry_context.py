"""뉴스 사업의 정식 출처 운반·봉인과 기존 공식 표시를 검증한다."""

from hashlib import sha256
import pytest

from src.core import news_intake_switch

from src.core.source_verification_adapter import supplementary_research_source_verifier
from src.features.composer.constants import SECTION_IDS
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection
from src.features.composer.render import render_report
from src.features.composer.tests.test_official_industry_context import _render_official
from src.features.news_intake.tests.test_verified_business_anchors import anchored_company
from src.features.provenance.sources import has_valid_provenance_seal
from src.shared.business_challenge_context import IndustryContextDisplay, IndustryProblemEvidence, industry_context_problems
from src.shared.report_quality.source_identity import document_identity_from_parts


@pytest.fixture(autouse=True)
def enabled_news(monkeypatch):
    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, news_intake_switch.NEWS_INTAKE_ENV_ON)
    news_intake_switch._reset_process_news_intake_switch_for_tests()
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()


def news_materials():
    anchor = anchored_company().business_anchors[0]
    fragment = CollectedFragment(
        "71", "회사 뉴스", anchor.exact_text, source_url=anchor.source_url,
        document_title=anchor.title, location=anchor.location, document_date=anchor.published_on,
        source_collected_on="2026-10-09",
        document_content_sha256=anchor.document_content_sha256, formal_source_kind="news",
        source_document_id=anchor.document_id, source_publisher=anchor.publisher,
        document_identity=document_identity_from_parts(document_id=anchor.document_id,
                                                       host="media.example", url=anchor.source_url),
        news_claim_kind="reported_fact", news_temporal_status="completed", news_grounded=True,
        identity_binding=anchor.identity_binding, counts_toward_document_floor=False,
    )
    text = "국내 계측 사업 부문에서는 공급 부족이 현재 과제로 남아 있다."
    digest = sha256(text.encode()).hexdigest()
    problem = IndustryProblemEvidence(
        "problem-one", anchor.anchor_id, "industry-one", "https://news.example/industry-one",
        "가상산업신문", "계측 산업 관찰", "2026-10-08", "기사 본문 1문단", text, digest,
        "계측 사업 부문", "공급 부족이 현재 과제로 남아 있다", "domestic", "국내", "국내",
        document_content_sha256=digest, analysis_response_sha256=sha256(b"synthetic-response").hexdigest(),
        applicability_quote="계측 사업 부문",
    )
    composed = ComposedReport(tuple(ComposedSection(sid, ()) for sid in SECTION_IDS))
    return anchor, fragment, problem, composed


def rendered_news():
    anchor, fragment, problem, composed = news_materials()
    return render_report("가람회사", composed, (fragment,), None, company_id=anchor.company_id,
                         as_of_date="2026-10-09", industry_anchors=(anchor,), industry_problems=(problem,))


def test_news_anchor_transport_survives_real_render_and_source_verification():
    report = rendered_news()
    context, = report.sections[4].industry_contexts
    source = next(value for value in report.citations if value.source_id == context.anchor.source_id)
    assert source.identity_binding == context.anchor.identity_binding
    assert has_valid_provenance_seal(source)
    verified = supplementary_research_source_verifier()(
        source, tuple(report.citations), reference_date="2026-10-09", evidence_text=context.anchor.exact_text,
    )
    assert verified and verified.news and not verified.official
    assert not industry_context_problems(
        (context,), company_id=context.anchor.company_id, registry=tuple(report.citations),
        reference_date="2026-10-09", verifier=supplementary_research_source_verifier(),
    )
    assert not report.fact_records
    assert "인수한 사업의 관련성" in context.limitation


def test_news_and_formal_captions_keep_source_kind_and_formal_literal():
    report = rendered_news()
    context, = report.sections[4].industry_contexts
    assert IndustryContextDisplay(context, 1, 2).caption == "검증된 보도 사업과 관련된 산업 과제"
    official, _ = _render_official()
    context, = official.sections[4].industry_contexts
    assert IndustryContextDisplay(context, 1, 2).caption == "공식 자료에 나온 사업과 관련된 산업 과제"
