"""현재 사업목록의 검색 앵커가 원 조각·최종 산업 바인딩을 유지한다."""

from dataclasses import replace
from types import SimpleNamespace
import hashlib

import pytest

from src.features.pipeline.business_activity_anchors import build_business_activity_anchors
from src.features.pipeline.tests.test_business_activity_anchors import _evidence, PROFILE


DECLARATION = "당사의 사업은 온라인 광고 사업과 콘텐츠 판매 사업으로 구성되어 있습니다."


@pytest.mark.parametrize("text,item", (
    ("당사의 사업은 2020년 당시 데이터 분석 사업과 장비 임대 사업으로 구성되어 있습니다.", ""),
    ("당사의 사업은 종속기업의 데이터 분석 사업과 장비 임대 사업으로 구성되어 있습니다.", ""),
    ("당사의 사업은 인수 완료를 전제로 한 데이터 분석 사업과 장비 임대 사업으로 구성되어 있습니다.", ""),
    ("당사의 사업은 고객 대상 회계 처리 사업과 세무 자문 사업으로 구성되어 있습니다.", "고객 대상 회계 처리 사업"),
))
def test_현재성_소유_조건_외부서비스_경계가_앵커에도_유지된다(text, item):
    evidence = _evidence((text,), section="identity")
    anchors = build_business_activity_anchors(evidence, profile=PROFILE)
    assert [anchor.business_item for anchor in anchors] == ([item] if item else [])
    for anchor in anchors:
        fragment = evidence.candidates[0].fragments[0]
        assert (anchor.anchor_id, anchor.exact_text, anchor.text_sha256, anchor.location) == (
            fragment.fragment_id, fragment.text, fragment.text_sha256, fragment.location)


def test_하나의_현재선언에서_한항목_원ID와_원문좌표를_유지한다():
    evidence = _evidence((DECLARATION,), section="identity")
    anchor, = build_business_activity_anchors(evidence, profile=PROFILE)
    fragment = evidence.candidates[0].fragments[0]
    assert anchor.business_item == "온라인 광고 사업"
    assert anchor.anchor_id == fragment.fragment_id
    assert anchor.exact_text == fragment.text
    assert anchor.text_sha256 == fragment.text_sha256
    assert anchor.location == fragment.location


def test_기존_실제생산_앵커를_현재구성_선언으로_교체하지_않는다():
    production = "당사는 정밀부품을 생산합니다."
    evidence = _evidence((DECLARATION, production), section="identity")
    anchors = build_business_activity_anchors(evidence, profile=PROFILE)
    assert [anchor.business_item for anchor in anchors] == ["정밀부품", "온라인 광고 사업"]
    assert len({anchor.anchor_id for anchor in anchors}) == 2
    assert evidence.candidates[0].fragments[0].text == DECLARATION


def test_목록이_있어도_다른법인_표제는_앵커가_되지_않는다():
    evidence = _evidence(("다른기업의 사업 개요: " + DECLARATION,), section="identity")
    assert build_business_activity_anchors(evidence, profile=PROFILE) == ()


def test_목록만_같아도_다른회사는_원문앵커가_되지_않는다():
    evidence = _evidence((DECLARATION,), section="identity")
    assert build_business_activity_anchors(evidence, profile={**PROFILE, "corp_code": "00999999"}) == ()


def test_새선언_앵커의_composer_원조각_결속과_변조거절():
    from src.features.composer.industry_context import select_industry_context_for_fragments
    from src.features.composer.tests.test_industry_context import _materials

    evidence = _evidence((DECLARATION,), section="identity")
    anchor, = build_business_activity_anchors(evidence, profile=PROFILE)
    _, _, _, original_problem, _ = _materials()
    industry_text = "국내 온라인 광고 사업에서는 광고 수요 감소가 문제로 나타났다."
    digest = hashlib.sha256(industry_text.encode()).hexdigest()
    problem = replace(original_problem, business_anchor_id=anchor.anchor_id,
                      exact_text=industry_text, text_sha256=digest, industry="온라인 광고",
                      problem="광고 수요 감소", applicability_quote="온라인 광고 사업",
                      document_content_sha256=digest)
    fragment = SimpleNamespace(
        fragment_id=1, text=anchor.exact_text, source_document_id=anchor.document_id,
        source_url=anchor.source_url, formal_source_kind=anchor.source_kind,
        document_content_sha256=anchor.document_content_sha256, identity_binding=anchor.identity_binding,
        location=anchor.location, source_publisher=anchor.publisher,
        document_title=anchor.title, document_date=anchor.published_on)
    arguments = dict(anchors=(anchor,), problems=(problem,), original_fragments=(fragment,),
                     selected_fragments=(fragment,), company_id=PROFILE["corp_code"])
    assert select_industry_context_for_fragments(**arguments) == ((anchor,), (problem,), 0)
    for key, value in (("text", "다른 원문"), ("location", "1-2"),
                       ("document_content_sha256", "0" * 64)):
        altered = SimpleNamespace(**{**vars(fragment), key: value})
        with pytest.raises(ValueError):
            select_industry_context_for_fragments(**{**arguments, "selected_fragments": (altered,)})


def test_선언앵커도_최종공개_산업맥락에_결속되고_회사문제로_계수되지_않는다():
    from src.features.composer.tests.test_industry_context import _materials
    from src.features.composer.port import CollectedFragment
    from src.features.composer.render import render_report
    from src.shared.report_quality.source_identity import document_identity_from_parts

    evidence = _evidence((DECLARATION,), section="identity")
    anchor, = build_business_activity_anchors(evidence, profile=PROFILE)
    _, _, _, original_problem, composed = _materials()
    industry_text = "국내 온라인 광고 사업에서는 광고 수요 감소가 문제로 나타났다."
    digest = hashlib.sha256(industry_text.encode()).hexdigest()
    problem = replace(original_problem, business_anchor_id=anchor.anchor_id,
                      exact_text=industry_text, text_sha256=digest, industry="온라인 광고",
                      problem="광고 수요 감소", applicability_quote="온라인 광고 사업",
                      document_content_sha256=digest)
    fragment = CollectedFragment(
        "11", "공식 사업", anchor.exact_text, source_url=anchor.source_url,
        document_title=anchor.title, location=anchor.location, document_date=anchor.published_on,
        document_identity=document_identity_from_parts(
            document_id=anchor.document_id.rpartition(":")[2], host="dart.fss.or.kr", url=anchor.source_url),
        document_content_sha256=anchor.document_content_sha256, formal_source_kind=anchor.source_kind,
        source_document_id=anchor.document_id, source_publisher=anchor.publisher,
        identity_binding=anchor.identity_binding, source_collected_on="2026-09-30",
        supported_claim_slots=("identity:business_definition",))
    report = render_report(PROFILE["corp_name"], composed, (fragment,), None,
                           company_id=PROFILE["corp_code"], as_of_date="2026-09-30",
                           industry_anchors=(anchor,), industry_problems=(problem,))
    section = report.sections[4]
    assert len(section.industry_contexts) == 1
    assert section.industry_contexts[0].anchor.exact_text == DECLARATION
    assert not section.fact_ids and not section.is_filled
    assert not report.fact_records
