"""산업 과제를 회사 피해로 승격하지 않는 공개 경계의 반례 시험."""

from dataclasses import replace
import hashlib

import pytest

from src.features.composer.constants import SECTION_IDS
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.render import render_report
from src.features.composer.public_manifest import build_public_structure_seal, assert_report_matches_public_structure
from src.features.composer.quality_projection import build_generation_quality_candidate
from src.features.provenance.sources import has_valid_provenance_seal
from src.features.report_standard.public_projection import build_public_projection
from src.features.storage.reports import report_from_dict, report_to_dict
from src.shared.business_challenge_context import BusinessActivityAnchor, IndustryProblemEvidence
from src.shared.report_evidence.constants import SOURCE_KIND_DART_BUSINESS_REPORT
from src.shared.report_generation.public_projection import (
    public_report_projection_to_dict as public_projection_to_dict,
    public_report_projection_from_dict as public_projection_from_dict,
)
from src.shared.report_quality.source_identity import document_identity_from_parts


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _materials():
    company = "가상센서기업"
    official = "가상센서기업은 산업용 센서의 개발과 공급을 주요 사업으로 영위한다."
    doc_id = "20260901000001"
    url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={doc_id}"
    fragment = CollectedFragment(
        "11", "공식 사업", official, source_url=url, document_title="가상 사업보고서",
        location="사업의 내용", document_date="2026-09-01",
        document_identity=document_identity_from_parts(document_id=doc_id, host="dart.fss.or.kr", url=url),
        document_content_sha256=_sha(official), formal_source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
        source_document_id=doc_id, source_publisher=company,
        identity_binding="회사코드 00000001의 공식 사업보고서", source_collected_on="2026-09-30",
        supported_claim_slots=("portfolio:product_role",),
    )
    anchor = BusinessActivityAnchor(
        "original-fragment-1", "00000001", "", doc_id, SOURCE_KIND_DART_BUSINESS_REPORT,
        url, fragment.location, official, _sha(official), "산업용 센서",
        publisher=company, title=fragment.document_title, published_on=fragment.document_date,
        document_content_sha256=fragment.document_content_sha256, identity_binding=fragment.identity_binding,
    )
    industry = "국내 산업용 센서 산업에서는 측정 신뢰성 확보가 과제로 남아 있다."
    problem = IndustryProblemEvidence(
        "problem-1", anchor.anchor_id, "article-1", "https://news.example/article-1",
        "가상산업신문", "센서 산업의 과제", "2026-09-20", "기사 본문 1문단",
        industry, _sha(industry), "산업용 센서", "측정 신뢰성 확보가 과제로 남아 있다",
        "domestic", "국내", "국내", document_content_sha256=_sha(industry),
        analysis_response_sha256=_sha("검수 응답"), applicability_quote="산업용 센서 산업",
    )
    composed = ComposedReport(tuple(ComposedSection(sid, ()) for sid in SECTION_IDS))
    return company, fragment, anchor, problem, composed


def _render(*, direct=False, **overrides):
    company, fragment, anchor, problem, composed = _materials()
    if direct:
        composed = replace(composed, sections=tuple(
            replace(section, sentences=(ComposedSentence(
                "센서의 측정 신뢰성이 현재 과제이다.", ("11",), "확인",
                planned_claim_slot="current_challenges:issue", verification_state="verified",
            ),)) if section.section_id == "current_challenges" else section
            for section in composed.sections
        ))
    kwargs = dict(company_id="00000001", as_of_date="2026-09-30", industry_anchors=(anchor,), industry_problems=(problem,))
    kwargs.update(overrides)
    return render_report(company, composed, (fragment,), None, **kwargs), composed


def test_산업보조는_회사사실과_장준비상태를_채우지_않는다():
    rendered, composed = _render()
    section = next(value for value in rendered.sections if value.cell == "current_challenges")
    assert len(section.industry_contexts) == 1
    assert not section.is_filled and not section.fact_ids and not section.prose_lines
    assert not rendered.fact_records
    assert all(has_valid_provenance_seal(source) for source in rendered.citations)
    candidate = build_generation_quality_candidate(rendered, composed)
    assert all(not source.counts_toward_document_floor for source in candidate.sources)
    assert all(not section.fact_ids and section.notice_only for section in candidate.sections)


def test_직접_검수된_회사과제가_있으면_산업보조를_추가하지_않는다():
    rendered, _ = _render(direct=True)
    assert all(not section.industry_contexts for section in rendered.sections)
    assert all(not source.source_id.startswith("industry-") for source in rendered.citations)


@pytest.mark.parametrize("field,value", [("company_id", "00000002"), ("document_content_sha256", "f" * 64), ("identity_binding", "다른 회사 proof"), ("publisher", "다른 발행처"), ("title", "다른 자료명"), ("published_on", "2026-09-02")])
def test_공식사업_법인과_전체원문_결속이_다르면_차단한다(field, value):
    _, _, anchor, _, _ = _materials()
    with pytest.raises(ValueError):
        _render(industry_anchors=(replace(anchor, **{field: value}),))


@pytest.mark.parametrize("field,value", [("geography_detail", "세계"), ("problem", "매출이 급감했다"), ("analysis_response_sha256", ""), ("applicability_quote", "의료 서비스")])
def test_범위와_문제와_적용관계와_검수기록을_만들면_차단한다(field, value):
    _, _, _, problem, _ = _materials()
    with pytest.raises(ValueError):
        replace(problem, **{field: value})


def test_산업보조의_원문과_표시문구는_저장과_공개봉인을_왕복한다():
    rendered, _ = _render()
    stored = report_to_dict(rendered)
    restored = report_from_dict(stored)
    assert restored.sections[4].industry_contexts == rendered.sections[4].industry_contexts
    projection = build_public_projection(rendered)
    restored_projection = public_projection_from_dict(public_projection_to_dict(projection))
    assert restored_projection == projection
    display = projection.sections[4].display.industry_contexts[0]
    assert " — 해석" in display.lines[2]
    assert "직접 피해" in display.lines[3]
    assert "[11]" in display.lines[1]
    assert display.context.problem.source_id.startswith("industry-")


def test_빈_산업보조_키는_옛_보고서_봉인바이트에_추가되지_않는다():
    rendered, _ = _render(industry_problems=())
    assert all("industry_contexts" not in section for section in report_to_dict(rendered)["sections"])
    projection = public_projection_to_dict(build_public_projection(rendered))
    assert all("industry_contexts" not in section["display"] for section in projection["sections"])


def test_공개봉인에_회사피해문장이나_다른_출처번호를_주입하면_차단한다():
    rendered, _ = _render()
    original = public_projection_to_dict(build_public_projection(rendered))
    import copy
    changed = copy.deepcopy(original)
    changed["sections"][4]["display"]["industry_contexts"][0]["lines"][2] = "회사가 피해를 입었다."
    with pytest.raises(ValueError):
        public_projection_from_dict(changed)
    changed = copy.deepcopy(original)
    changed["sections"][4]["display"]["industry_contexts"][0]["industry_source_number"] = 999
    changed["sections"][4]["display"]["industry_contexts"][0]["lines"] = []
    with pytest.raises(ValueError):
        public_projection_from_dict(changed)


def test_사전_manifest와_최종renderer는_동일한_두근거_출처를_봉인한다():
    company, fragment, anchor, problem, composed = _materials()
    options = dict(
        filing_meta=None, composition_tables=(), table_presentation="table", company_id="00000001",
        evidence_generation_sha256="a" * 64, evidence_packet_sha256s=tuple((sid, "b" * 64) for sid in SECTION_IDS),
        company_name=company, corp_type="", generated_at="", as_of_date="2026-09-30",
        analysis_period="", latest_performance_period="", citation_style="inline",
        industry_anchors=(anchor,), industry_problems=(problem,),
    )
    seal = build_public_structure_seal(composed, (fragment,), None, **options)
    rendered, _ = _render(public_structure_seal=seal, citation_style="inline")
    assert_report_matches_public_structure(rendered, seal)


@pytest.mark.parametrize("with_industry", [False, True])
def test_대응만_있는_장의_범위안내도_공개봉인과_저장에_같이_남는다(with_industry):
    from src.features.composer.constants import NOTICE_CHALLENGE_RESPONSE_ONLY

    company, fragment, anchor, problem, composed = _materials()
    response_text = "가상센서기업은 제품 공급을 위한 생산 설비를 구축하고 있다."
    document_hash = _sha(fragment.text + response_text)
    fragment = replace(fragment, document_content_sha256=document_hash)
    anchor = replace(anchor, document_content_sha256=document_hash)
    response_fragment = replace(
        fragment, fragment_id="12", text=response_text, location="생산 설비",
        supported_claim_slots=("current_challenges:response",),
    )
    sentence = ComposedSentence(response_text, ("12",), "확인",
        planned_claim_slot="current_challenges:response", verification_state="verified")
    composed = replace(composed, sections=tuple(
        replace(section, sentences=(sentence,)) if section.section_id == "current_challenges"
        else section for section in composed.sections
    ))
    fragments = (fragment, response_fragment)
    problems = (problem,) if with_industry else ()
    options = dict(
        filing_meta=None, composition_tables=(), table_presentation="table", company_id="00000001",
        evidence_generation_sha256="a" * 64, evidence_packet_sha256s=tuple((sid, "b" * 64) for sid in SECTION_IDS),
        company_name=company, corp_type="", generated_at="", as_of_date="2026-09-30",
        analysis_period="", latest_performance_period="", citation_style="inline",
        industry_anchors=(anchor,), industry_problems=problems,
    )
    seal = build_public_structure_seal(composed, fragments, None, **options)
    rendered = render_report(company, composed, fragments, None,
        company_id="00000001", as_of_date="2026-09-30", citation_style="inline",
        industry_anchors=(anchor,), industry_problems=problems, public_structure_seal=seal)
    assert_report_matches_public_structure(rendered, seal)
    section = rendered.sections[4]
    assert (NOTICE_CHALLENGE_RESPONSE_ONLY in section.guidance_lines) is not with_industry
    assert response_text in section.prose_lines[0][0]
    assert section.fact_ids and len(section.fact_ids) == 1
    # 부분 보고서의 저장 검사는 실제 부분 렌더 경로로 한다. FULL 사전 봉인만
    # 붙인 중간 산출물은 출고 계약이 없어 저장 재열기에서 정당하게 차단된다.
    partial = render_report(company, composed, fragments, None,
        company_id="00000001", as_of_date="2026-09-30", citation_style="inline",
        industry_anchors=(anchor,), industry_problems=problems)
    assert partial.sections[4].guidance_lines == section.guidance_lines
    restored = report_from_dict(report_to_dict(partial))
    assert restored.sections[4].guidance_lines == section.guidance_lines
    assert restored.fact_records == rendered.fact_records
