"""공식 산업 해석의 출처·기간·선택·옛 저장본 호환 계약."""

from dataclasses import asdict, replace
import hashlib
import json

import pytest

from src.core.source_verification_adapter import register_industry_problem_source
from src.features.composer.industry_context import select_industry_context_for_fragments
from src.features.composer.constants import SECTION_IDS
from src.features.composer.port import ComposedSentence
from src.features.composer.public_manifest import build_public_structure_seal, assert_report_matches_public_structure
from src.features.composer.quality_projection import build_generation_quality_candidate
from src.features.composer.render import render_report
from src.features.composer.tests.test_industry_context import _materials
from src.features.provenance.sources import SourceKind, seal_collected_source
from src.features.report_standard.public_projection import build_public_projection
from src.features.storage.reports import report_from_dict, report_to_dict
from src.core.source_verification_adapter import supplementary_research_source_verifier
from src.shared.business_challenge_context import (
    IndustryChallengeContext, IndustryProblemEvidence, IndustryContextDisplay,
    OFFICIAL_INDUSTRY_FIELDS, industry_context_from_dict, industry_context_to_dict,
    industry_context_problems,
)
from src.shared.report_generation.models import canonical_value
from src.shared.report_evidence.constants import SOURCE_KIND_OFFICIAL_WEB_PAGE, SOURCE_KIND_OFFICIAL_IR_PDF
from src.shared.official_ir import IR_METADATA_VERIFICATION_VALUE
from src.shared.report_quality.source_identity import collected_document_identity
from src.shared.report_generation.public_projection import (
    public_report_projection_to_dict, public_report_projection_from_dict,
)


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _official_materials():
    company, anchor_fragment, anchor, _, composed = _materials()
    quote = "2025년 국내 산업용 센서 산업에서는 측정 신뢰성 확보가 과제로 남아 있다."
    text = quote + " 향후 공급 확대를 추진할 예정이다."
    document_hash = _sha(anchor_fragment.text + text)
    anchor_fragment = replace(anchor_fragment, document_content_sha256=document_hash)
    anchor = replace(anchor, document_content_sha256=document_hash)
    fragment = replace(
        anchor_fragment, fragment_id="12", text=text, location="산업 현황",
        document_title="사업보고서 (2025.12)", source_publisher="DART",
    )
    problem = IndustryProblemEvidence(
        evidence_id="official-problem-1", business_anchor_id=anchor.anchor_id,
        document_id=fragment.source_document_id, source_url=fragment.source_url,
        publisher=fragment.source_publisher, title=fragment.document_title,
        published_on=fragment.document_date, location=fragment.location,
        exact_text=text, text_sha256=_sha(text), industry="산업용 센서",
        problem="측정 신뢰성 확보가 과제로 남아 있다", geography="domestic",
        geography_detail="국내", geography_evidence="국내", applicability_quote="산업용 센서 산업",
        document_content_sha256=document_hash, analysis_response_sha256=_sha("별도 산업 검수 응답"),
        source_kind=fragment.formal_source_kind, identity_binding=fragment.identity_binding,
        assessment_quote=quote, observation_period="2025",
    )
    return company, (anchor_fragment, fragment), anchor, problem, composed


def _render_official():
    company, fragments, anchor, problem, composed = _official_materials()
    report = render_report(
        company, composed, fragments, None, company_id=anchor.company_id,
        as_of_date="2026-09-30", industry_anchors=(anchor,), industry_problems=(problem,),
    )
    return report, composed


def test_공식_산업은_공시와_자료기간을_유지하고_회사사실을_채우지_않는다():
    report, composed = _render_official()
    section = next(section for section in report.sections if section.cell == "current_challenges")
    context, = section.industry_contexts
    source = next(source for source in report.citations if source.source_id == context.problem.source_id)
    assert source.kind is SourceKind.FILING
    assert source.publisher == _official_materials()[0]
    assert context.problem.publisher == source.publisher
    assert context.problem.published_on == source.disclosed_at
    assert not source.published_at
    assert not any(source.kind is SourceKind.NEWS for source in report.citations)
    assert not report.fact_records and not section.fact_ids and not section.is_filled
    display = IndustryContextDisplay(context, 11, 12)
    assert len(display.lines) == 5
    assert display.lines[0] == "회사 공식 자료의 기준: 2025 · 2026-09-01 공표"
    assert "해석" in display.lines[-2]
    assert "직접 피해" in display.lines[-1]
    candidate = build_generation_quality_candidate(report, composed)
    assert all(not source.counts_toward_document_floor for source in candidate.sources)
    assert industry_context_from_dict(industry_context_to_dict(context)) == context


def test_옛_뉴스_저장형식과_공개지문_입력을_바꾸지_않는다():
    _, _, anchor, problem, _ = _materials()
    context = IndustryChallengeContext(replace(anchor, source_id="business"), replace(problem, source_id="news"))
    legacy = asdict(context)
    for field in OFFICIAL_INDUSTRY_FIELDS:
        legacy["problem"].pop(field)
    assert industry_context_to_dict(context) == legacy
    assert canonical_value(context) == legacy
    restored = industry_context_from_dict(legacy)
    assert industry_context_to_dict(restored) == legacy
    display = IndustryContextDisplay(restored, 11, 12)
    assert len(display.lines) == 4
    assert canonical_value(display)["context"] == legacy


@pytest.mark.parametrize("changes", [
    {"assessment_quote": "향후 공급 확대를 추진할 예정이다."},
    {"observation_period": "2026년 현재"},
    {"identity_binding": ""},
    {"source_kind": ""},
    {"source_kind": "news"},
])
def test_공식_검수범위나_신원이_깨지면_거절한다(changes):
    _, _, _, problem, _ = _official_materials()
    with pytest.raises(ValueError):
        replace(problem, **changes)


def test_공시를_뉴스_등록기로_봉인하지_않는다():
    _, _, _, problem, _ = _official_materials()
    with pytest.raises(ValueError, match="공식 출처 생성기"):
        register_industry_problem_source(problem, number=12, section_id="current_challenges")


def test_선택에서_빠진_공식_산업원문을_표시로_되살리지_않는다():
    _, fragments, anchor, problem, _ = _official_materials()
    anchors, problems, excluded = select_industry_context_for_fragments(
        anchors=(anchor,), problems=(problem,), original_fragments=fragments,
        selected_fragments=fragments[:1], company_id=anchor.company_id,
    )
    assert problems == () and excluded == 1


@pytest.mark.parametrize("changes", [
    {"source_publisher": "다른 발행자"}, {"document_date": "2026-09-02"},
    {"identity_binding": "다른 회사"}, {"document_content_sha256": "0" * 64},
])
def test_같은_번호의_다른_공식원문을_빌리지_않는다(changes):
    _, fragments, anchor, problem, _ = _official_materials()
    with pytest.raises(ValueError, match="선택된 공식 산업"):
        select_industry_context_for_fragments(
            anchors=(anchor,), problems=(problem,), original_fragments=fragments,
            selected_fragments=(fragments[0], replace(fragments[1], **changes)), company_id=anchor.company_id,
        )


def test_최종_공식출처_발행자_변조도_거절한다():
    report, _ = _render_official()
    context = next(section for section in report.sections if section.industry_contexts).industry_contexts[0]
    registry = tuple(
        seal_collected_source(replace(source, publisher="다른 발행자"))
        if source.source_id == context.problem.source_id else source for source in report.citations
    )
    problems = industry_context_problems(
        (context,), company_id=context.anchor.company_id, registry=registry,
        reference_date="2026-09-30", verifier=supplementary_research_source_verifier(),
    )
    assert problems


def test_공식_산업의_기간과_해석을_저장과_공개봉인에_같이_싣는다():
    company, fragments, anchor, problem, composed = _official_materials()
    options = dict(
        filing_meta=None, composition_tables=(), table_presentation="table", company_id=anchor.company_id,
        evidence_generation_sha256="a" * 64, evidence_packet_sha256s=tuple((sid, "b" * 64) for sid in SECTION_IDS),
        company_name=company, corp_type="", generated_at="", as_of_date="2026-09-30",
        analysis_period="", latest_performance_period="", citation_style="inline",
        industry_anchors=(anchor,), industry_problems=(problem,),
    )
    seal = build_public_structure_seal(composed, fragments, None, **options)
    report = render_report(
        company, composed, fragments, None, company_id=anchor.company_id,
        as_of_date="2026-09-30", citation_style="inline", public_structure_seal=seal,
        industry_anchors=(anchor,), industry_problems=(problem,),
    )
    assert_report_matches_public_structure(report, seal)
    # 구조 봉인 시험의 임시 객체는 FULL 생산 영수증을 갖지 않는다.
    # 저장 왕복은 기존 부분 보고서 계약으로 생성한 별도 객체로 확인한다.
    report, _ = _render_official()
    restored = report_from_dict(report_to_dict(report))
    assert restored.sections[4].industry_contexts == report.sections[4].industry_contexts
    projection = build_public_projection(report)
    assert public_report_projection_from_dict(public_report_projection_to_dict(projection)) == projection


def test_산업과_같은_공식출처의_직접_사업사실은_문서하한에_남긴다():
    company, fragments, anchor, problem, composed = _official_materials()
    text = fragments[1].text + " 당사는 산업용 센서를 판매하고 있다."
    fragments = (fragments[0], replace(fragments[1], text=text))
    problem = replace(problem, exact_text=text, text_sha256=_sha(text))
    sentence = ComposedSentence(
        "당사는 산업용 센서를 판매하고 있다.", ("12",), "확인",
        planned_claim_slot="portfolio:product_role", verification_state="verified",
    )
    composed = replace(composed, sections=tuple(
        replace(section, sentences=(sentence,)) if section.section_id == "portfolio" else section
        for section in composed.sections
    ))
    report = render_report(
        company, composed, fragments, None, company_id=anchor.company_id,
        as_of_date="2026-09-30", industry_anchors=(anchor,), industry_problems=(problem,),
    )
    candidate = build_generation_quality_candidate(report, composed)
    assert next(source for source in candidate.sources if source.source_id == "v2-frag-12").counts_toward_document_floor


@pytest.mark.parametrize("is_ir", [False, True])
def test_공식웹_산업자료의_시점과_회사증명을_함께_검증한다(is_ir):
    company, fragments, anchor, problem, composed = _official_materials()
    url = "https://sensor.example/industry.pdf" if is_ir else "https://sensor.example/industry"
    source_kind = SOURCE_KIND_OFFICIAL_IR_PDF if is_ir else SOURCE_KIND_OFFICIAL_WEB_PAGE
    text = fragments[1].text.replace("2025년", "2026년 상반기")
    evidence = json.dumps(
        {"corp_code": anchor.company_id, "corp_name": company, "hm_url": "https://sensor.example/"},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    fragment = replace(
        fragments[1], text=text, document_content_sha256=_sha(text),
        source_url=url, source_document_id="official-web-industry",
        document_title="2026년 상반기 센서 산업 현황", formal_source_kind=source_kind,
        document_identity=collected_document_identity(
            source_kind=source_kind, document_id="official-web-industry", url=url,
        ),
        identity_binding="공식 기업개황으로 확인된 회사 웹",
        source_publisher="sensor.example", location=url + "#0",
        domain_attestation_source_id="dart-company-profile-" + anchor.company_id,
        domain_attestation_evidence=evidence,
        reporting_period="2026-H1" if is_ir else "",
        attachment_url=url if is_ir else "",
        ir_metadata_verification=IR_METADATA_VERIFICATION_VALUE if is_ir else "",
    )
    problem = replace(
        problem, source_kind=fragment.formal_source_kind, source_url=url,
        document_id=fragment.source_document_id, identity_binding=fragment.identity_binding,
        publisher=fragment.source_publisher, title=fragment.document_title, location=fragment.location,
        exact_text=text, text_sha256=_sha(text), document_content_sha256=_sha(text),
        assessment_quote=problem.assessment_quote.replace("2025년", "2026년 상반기"),
        observation_period="2026년 상반기",
    )
    if not is_ir:
        # 목록 순번은 실제 공표일을 입증하지 못하므로 Source가 날짜를 지운다.
        # 산업 표시가 수집 메타데이터의 날짜를 대신 복구해서는 안 된다.
        with pytest.raises(ValueError):
            render_report(
                company, composed, (fragments[0], fragment), None, company_id=anchor.company_id,
                as_of_date="2026-09-30", industry_anchors=(anchor,), industry_problems=(problem,),
            )
        return
    report = render_report(
        company, composed, (fragments[0], fragment), None, company_id=anchor.company_id,
        as_of_date="2026-09-30", industry_anchors=(anchor,), industry_problems=(problem,),
    )
    context = report.sections[4].industry_contexts[0]
    source = next(source for source in report.citations if source.source_id == context.problem.source_id)
    assert source.kind is SourceKind.OTHER and source.source_type == "회사 공식 IR"
    assert context.problem.title == source.title
    assert source.published_at == fragment.document_date
    assert any(source.provenance_role == "attestation_only" for source in report.citations)
    registry = tuple(source for source in report.citations if source.provenance_role != "attestation_only")
    assert industry_context_problems(
        (context,), company_id=anchor.company_id, registry=registry,
        reference_date="2026-09-30", verifier=supplementary_research_source_verifier(),
    )
