"""자기 영업표의 검색 항목과 원 fragment 최종 산업결속을 확인한다."""

from dataclasses import replace
import hashlib
from types import SimpleNamespace

import pytest

from src.features.pipeline.business_activity_anchors import build_business_activity_anchors
from src.features.pipeline.tests.test_business_activity_anchors import _evidence, PROFILE


TABLE = ("당기와 전기 중 당사가 시공한 주요 공사입니다.\n\n"
         "당기말 현재 공사종류별 내역입니다. ① 당기\n\n"
         "구분 | 도급금액 | 누적공사수익 ; 정밀설비공사 현장 | 100 | 60")


def test_표는_기존생산과_현재선언_이후_한원ID로_보충한다():
    production = "당사는 정밀부품을 생산합니다."
    declaration = "당사의 사업은 장비 임대 사업과 교육 사업으로 구성되어 있습니다."
    evidence = _evidence((TABLE, declaration, production), section="identity")
    anchors = build_business_activity_anchors(evidence, profile=PROFILE)
    assert [a.business_item for a in anchors] == ["정밀부품", "장비 임대 사업", "정밀설비공사"]
    fragment = evidence.candidates[0].fragments[0]
    assert (anchors[2].anchor_id, anchors[2].exact_text, anchors[2].location, anchors[2].text_sha256) == (
        fragment.fragment_id, fragment.text, fragment.location, fragment.text_sha256)


def test_표원문_위치_회사_해시_변조와_과거목적_대체는_거절():
    evidence = _evidence((TABLE,), section="identity")
    assert len(build_business_activity_anchors(evidence, profile=PROFILE)) == 1
    assert not build_business_activity_anchors(evidence, profile={**PROFILE, "corp_code": "00999999"})
    assert not build_business_activity_anchors(_evidence((TABLE.replace("당기", "전기"),), section="identity"), profile=PROFILE)
    assert not build_business_activity_anchors(_evidence(("당사는 정관 사업목적으로 정밀부품 제조를 정했습니다.",), section="identity"), profile=PROFILE)
    fragment = evidence.candidates[0].fragments[0]
    for altered in (replace(fragment, location="1-2"),):
        candidates = list(evidence.candidates)
        candidates[0] = replace(candidates[0], fragments=(altered,))
        assert not build_business_activity_anchors(replace(evidence, candidates=tuple(candidates)), profile=PROFILE)


def test_기존3앵커를_표로_대체하거나_총상한을_늘리지_않는다():
    production = tuple(f"당사는 {item}을 생산합니다." for item in ("정밀부품", "산업장비", "측정기기"))
    evidence = _evidence((TABLE, *production), section="identity")
    anchors = build_business_activity_anchors(evidence, profile=PROFILE)
    assert [a.business_item for a in anchors] == ["정밀부품", "산업장비", "측정기기"]


def test_다른법인_표제를_발주처_명칭으로_오인해_허용하지_않는다():
    assert not build_business_activity_anchors(_evidence(("다른기업의 사업 개요: " + TABLE,), section="identity"), profile=PROFILE)
    assert len(build_business_activity_anchors(_evidence(("가온기업의 사업 개요: " + TABLE,), section="identity"), profile=PROFILE)) == 1


def test_추가표앵커가_composer_원조각과_해석에만_결속한다():
    from src.features.composer.industry_context import select_industry_context_for_fragments
    from src.features.composer.tests.test_industry_context import _materials

    evidence = _evidence((TABLE,), section="identity")
    anchor, = build_business_activity_anchors(evidence, profile=PROFILE)
    _, _, _, original_problem, _ = _materials()
    text = "국내 정밀설비공사 업계에서는 원가 상승에 따른 수익성 악화가 문제다."
    digest = hashlib.sha256(text.encode()).hexdigest()
    problem = replace(original_problem, business_anchor_id=anchor.anchor_id, exact_text=text,
                      text_sha256=digest, document_content_sha256=digest, industry="정밀설비공사",
                      problem="원가 상승", applicability_quote="정밀설비공사")
    fragment = SimpleNamespace(fragment_id=1, text=anchor.exact_text, source_document_id=anchor.document_id,
                               source_url=anchor.source_url, formal_source_kind=anchor.source_kind,
                               document_content_sha256=anchor.document_content_sha256,
                               identity_binding=anchor.identity_binding, location=anchor.location,
                               source_publisher=anchor.publisher, document_title=anchor.title,
                               document_date=anchor.published_on)
    arguments = dict(anchors=(anchor,), problems=(problem,), original_fragments=(fragment,),
                     selected_fragments=(fragment,), company_id=PROFILE["corp_code"])
    assert select_industry_context_for_fragments(**arguments) == ((anchor,), (problem,), 0)
    with pytest.raises(ValueError):
        select_industry_context_for_fragments(**{**arguments, "selected_fragments": (SimpleNamespace(
            **{**vars(fragment), "text": "다른 회사 표"}),)})
