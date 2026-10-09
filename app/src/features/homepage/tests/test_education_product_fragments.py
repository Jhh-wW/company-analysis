"""실습 교육상품의 본문 관계와 도구·내부 교육·외부 소개 반증."""

import hashlib

import pytest

from src.features.homepage.wide_fragments import build_fragments
from src.features.homepage.wide_types import WideDocumentIdentity


TITLE = "사진 보정 실습 | 학습센터"
DESCRIPTION = "이미지 AI를 활용해 일관성 있는 이미지를 만드는 실습형 무료 특강입니다."
ENROLLMENT = "수강 신청 단계에서 학습 관리 서비스를 선택할 수 있습니다."


def document(ranges=None, **overrides):
    values = dict(
        company_id="c1", document_id="d1", canonical_url="https://example.org/product/42",
        source_kind="official_web_page", publisher="example.org", title=TITLE,
        published_on="", collected_at="2026-10-10", content_sha256="a" * 64,
        identity_binding="root", usable_ranges=ranges or (TITLE, DESCRIPTION, ENROLLMENT),
        collector_version="v1", parser_version="v1", requirement="REQUIRED",
        source_tier="TIER_1_OFFICIAL",
    )
    values.update(overrides)
    return WideDocumentIdentity(**values)


@pytest.mark.parametrize("price", ["무료", "유료"])
def test_실습상품_설명은_제품역할만_원문그대로_보존한다(price):
    text = DESCRIPTION.replace("무료", price)
    fragments = build_fragments(document((TITLE, text, ENROLLMENT)), company_id="c1")
    assert len(fragments) == 1
    fragment, = fragments
    assert fragment.covered_slot_ids == ("portfolio:product_role",)
    assert fragment.text == text and fragment.range_index == 1
    assert fragment.text_sha256 == hashlib.sha256(text.encode()).hexdigest()
    assert fragment.document_id == "d1"
    assert "education_product_relation" in fragment.reason_codes
    assert "body_keyword_match" not in fragment.reason_codes


@pytest.mark.parametrize("prefix", [
    "직원 대상 ", "임직원 대상 ", "사내 ", "내부 교육으로 ", "타사 ",
    "다른 회사의 ", "외부 업체의 ", "향후 제공 예정인 ", "종료된 ",
])
def test_내부_타사_미래_종료_관계는_제품역할을_추가하지_않는다(prefix):
    assert build_fragments(document((TITLE, prefix + DESCRIPTION, ENROLLMENT)), company_id="c1") == ()


@pytest.mark.parametrize("ranges", [
    (TITLE, ENROLLMENT),
    (DESCRIPTION, ENROLLMENT),
    (TITLE, DESCRIPTION),
    (TITLE, "이미지 AI로 고품질 이미지를 만들 수 있습니다.", ENROLLMENT),
    (TITLE, "학습 내용을 소개하는 실습형 무료 특강입니다.", ENROLLMENT),
    (TITLE, DESCRIPTION, "특강 수강 신청이 종료되었습니다."),
    (TITLE, DESCRIPTION, "이 과정은 향후 개설 예정입니다.", ENROLLMENT),
])
def test_제목만_도구기능_소개_신청종료_상품관계부재는_회복하지_않는다(ranges):
    assert build_fragments(document(ranges), company_id="c1") == ()


def test_블로그_튜토리얼과_낮은신뢰_문서는_회복하지_않는다():
    assert build_fragments(document(canonical_url="https://example.org/blog/product/42"), company_id="c1") == ()
    assert build_fragments(document(source_kind="official_identity_verified_web_page",
                                    source_tier="TIER_3_TRUSTED", requirement="OPTIONAL"), company_id="c1") == ()


def test_일반_홈페이지의_상품소개묶음을_단일교육상품으로_취급하지_않는다():
    assert build_fragments(document(canonical_url="https://example.org/"), company_id="c1") == ()


def test_상세설명은_도구제품명이아닌_교육상품문장으로_남는다():
    text = DESCRIPTION.replace("이미지 AI", "외부 도구(Gemini)")
    fragment, = build_fragments(document((TITLE, text, ENROLLMENT)), company_id="c1")
    assert fragment.text == text
    assert fragment.covered_slot_ids == ("portfolio:product_role",)


@pytest.mark.parametrize("constraint", [
    "본 강좌는 당사 임직원만을 위한 내부 교육입니다.",
    "이 강좌는 다른 회사가 제공하며 당사는 신청 주소만 소개합니다.",
    "본 강좌는 폐강되었습니다.",
    "본 강좌의 수강 신청은 종료되었습니다.",
    "이 강좌는 종료된 내부 교육이며 폐강되는 경우 환불합니다.",
])
def test_별도구간의_현재상품_내부_타사_종료제한을_검사한다(constraint):
    assert build_fragments(document((TITLE, DESCRIPTION, ENROLLMENT, constraint)), company_id="c1") == ()


@pytest.mark.parametrize("context", [
    "이 강좌가 폐강되는 경우 수강료를 반환합니다.",
    "다른 강좌는 종료되었습니다.",
    "강사는 과거 다른 회사에서 임직원 내부 교육을 진행했습니다.",
])
def test_조건부환불_다른상품_강사경력은_현재상품을_막지않는다(context):
    fragment, = build_fragments(document((TITLE, DESCRIPTION, ENROLLMENT, context)), company_id="c1")
    assert fragment.covered_slot_ids == ("portfolio:product_role",)


def test_고객사_임직원은_외부교육_수강대상이다():
    text = "고객사 임직원이 스프레드시트를 활용해 통계 자료를 분석하는 실습형 유료 강좌입니다."
    fragments = build_fragments(document((TITLE, text, ENROLLMENT)), company_id="c1")
    fragment, = [fragment for fragment in fragments if fragment.section_id == "portfolio"]
    assert fragment.text == text and fragment.covered_slot_ids == ("portfolio:product_role",)
    assert not any("business_model:revenue_model" in fragment.covered_slot_ids for fragment in fragments)
