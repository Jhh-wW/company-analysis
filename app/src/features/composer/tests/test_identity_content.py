"""행정정보가 정체성·요약을 채우지 않으며 사업 설명은 보존하는 회귀."""

from dataclasses import replace
import json
import re

import pytest

from src.features.composer.extractive_summary import distinct_summary_candidates, select_extractive_summary
from src.features.composer.empty_section_recovery import recover_empty_sections, recovery_evidence
from src.features.composer.identity_content import administrative_identity_only, identity_content_problem
from src.features.composer.logic import SummaryCandidate
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.tests.test_extractive_summary import _fact, _report, _sentence
from src.features.composer.tests.test_empty_section_recovery import _fragment, _response
from src.features.composer.tests.test_pipeline import _FakeReviewer
from src.features.composer.verify import verify_report


ADMINISTRATIVE_TEXTS = (
    "회사는 서울특별시 강남구 예시로 10에 본점을 두고 있다.",
    "본점 소재지는 서울특별시 강남구 예시로 10이다.",
    "회사의 본사는 경기도 성남시에 위치한다.",
    "가나다 주식회사는 2020년 8월 3일에 설립되었다.",
    "주식회사 가나다는 2020년 설립되었다.",
    "회사의 설립일은 2020년 8월 3일이다.",
    "회사의 자본금은 30억원이다.",
    "회사는 2020년 8월 3일에 설립되었다. 자본금은 30억원이다.",
    # 실제 PDF 2쪽의 정체성 문장. 회사명·날짜는 검사의 조건으로 사용하지 않는다.
    "회사의 본점은 서울특별시 강남구 테헤란로 131, 14층에 위치하며, 대표 전화는 02) 1544-7772이다.",
    "공식 감사보고서에 기재된 회사의 법적 명칭은 '토스페이먼츠 주식회사'이며, 문서 B 기준 대표이사는 강병훈, 문서 C 기준 대표이사는 임한욱으로 각각 확인된다.",
    "문서 B는 제5기말(2024년 12월 31일 현재)을, 문서 C는 제6기말(2025년 12월 31일 현재)을 보고기간종료일로 삼고 있어, 회사는 현재 제6기까지의 재무제표를 공시한 법인임을 확인할 수 있다.",
    "회사의 경영진은 2025년 12월 31일 현재 내부회계관리제도가 '내부회계관리제도 설계 및 운영 개념체계'에 근거하여 중요성의 관점에서 효과적으로 설계되어 운영되고 있다고 판단한다고 공식 보고서에 기술하고 있다.",
    "감사보고서에 기재된 법인명은 '가온 주식회사'로 확인된다.",
    "문서는 제3기말을 보고기간종료일로 삼고 있으며 회사는 제3기 재무제표를 공시했다.",
)
BUSINESS_TEXTS = (
    "회사는 서울 물류센터를 운영하여 판매자에게 상품 보관과 배송 서비스를 제공한다.",
    "본사는 전국 지점의 재고를 연결하고 고객에게 물류 서비스를 제공한다.",
    "회사는 고객 주소를 검증하는 소프트웨어를 개발한다.",
    "회사는 2020년 설립되어 제조기업에 센서 검사 장비를 공급한다.",
    "회사는 서울특별시에 본점을 두고 있으며 고객에게 결제 서비스를 제공한다.",
    "회사는 서울특별시에 본점을 두고 있다. 주요 사업은 산업용 센서 제조다.",
    "회사는 자본금을 확충해 반도체 생산시설에 투자했다.",
    "회사는 해외법인의 설립을 결정했으며 납입 예정 자본금은 1천 달러이다.",
    "회사는 연구개발을 목적으로 2020년에 설립되었다.",
    "회사는 지역 기업을 지원하기 위해 2020년에 설립되었다.",
    "회사는 서울특별시 지점을 통해 고객과 거래한다.",
    "회사는 서울특별시에 위치한 물류센터에서 상품을 보관한다.",
    "온라인 결제를 처리하는 회사는 서울특별시에 본점을 두고 있다.",
    "반도체 검사장비 전문기업인 회사는 2020년 설립되었다.",
    "산업용 소프트웨어 기업인 당사는 서울특별시 강남구에 본점을 두고 있다.",
    "반도체 검사장비 전문기업은 2020년 설립되었다.",
    "해외 매출 비중이 80%인 회사는 2020년 설립되었다.",
    "구독 수익 비중이 80%인 회사는 2020년 설립되었다.",
    "반도체 장비 업체 가온전자는 2020년 설립되었다.",
    "회사의 법적 명칭은 '가온 주식회사'이며 기업 고객에게 결제 서비스를 제공한다.",
    "회사의 법적 명칭은 '가온 주식회사'로 확인되며 반도체 장비 업체이다.",
    "회사의 법적 명칭은 '가온 주식회사'로 확인되며 국내 시장 점유율은 30%이다.",
    "회사의 법적 명칭은 '가온 주식회사'로 확인되며 새로운 분야의 선두이다.",
    "회사는 제3기 재무제표를 공시했고 매출액은 100억원이다.",
    "회사는 내부회계관리제도를 효과적으로 설계하고 운영하는 소프트웨어를 고객에게 제공한다.",
    "내부회계관리제도가 효과적으로 설계되어 운영되고 있다고 판단하며 회사는 물류센터를 운영한다.",
    "회사는 내부회계관리제도 설계 및 운영 개념체계에 맞춰 고객의 회계 시스템을 구축한다.",
)


@pytest.mark.parametrize("text", ADMINISTRATIVE_TEXTS)
def test_행정정보만_있는_완결문장은_정체성에_쓰지_않는다(text):
    assert administrative_identity_only(text)
    assert identity_content_problem(text, section_id="identity") == "identity_administrative_only"
    assert identity_content_problem(text, section_id="past_changes") == ""


@pytest.mark.parametrize("text", BUSINESS_TEXTS + ("", "회사의 본사는 혁신의 중심이다."))
def test_사업활동과_혼합문장과_모르는문형은_기존검수에_맡긴다(text):
    assert not administrative_identity_only(text)


def test_검수자가_참이라도_주소는_빠지고_사업설명은_원문과_인용대로_남는다():
    address = ADMINISTRATIVE_TEXTS[0]
    business = BUSINESS_TEXTS[0]
    report = ComposedReport((ComposedSection("identity", (
        ComposedSentence(address, ("1",), "확인", planned_claim_slot="identity:official_location"),
        ComposedSentence(business, ("2",), "확인", planned_claim_slot="identity:business_definition"),
    )),))
    fragments = (
        CollectedFragment("1", "사업내용", address),
        CollectedFragment("2", "사업내용", business),
    )
    diagnostics = []

    def reviewer(prompt):
        items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
        return json.dumps({"판정": [
            {"번호": item.number, "장": item.section, "근거": list(item.citations), "결과": "참"}
            for item in items
        ]}, ensure_ascii=False)

    def rewrite(_prompt):
        pytest.fail("순수 행정정보를 바꿔 쓰는 유료 단계는 호출하지 않습니다")

    checked = verify_report(
        report, fragments, None, reviewer, diagnostics=diagnostics,
        grounding_rewrite_enabled=True, rewrite_ask=rewrite,
    )
    assert tuple(sentence.text for sentence in checked.sections[0].sentences) == (business,)
    assert checked.sections[0].sentences[0].citations == ("2",)
    assert "identity_administrative_only" in str(diagnostics)


@pytest.mark.parametrize("text", ADMINISTRATIVE_TEXTS)
def test_부분본_요약도_주소와_설립일과_자본금으로_장수를_채우지_않는다(text):
    admin = replace(_sentence("identity"), text=text)
    business = replace(_sentence("identity"), text=BUSINESS_TEXTS[0])
    candidates = (SummaryCandidate("identity", admin), SummaryCandidate("identity", business))
    assert distinct_summary_candidates(candidates, company_name="가나다 주식회사") == (candidates[1],)
    assert admin.text == text


@pytest.mark.parametrize("text", ADMINISTRATIVE_TEXTS[-6:])
def test_실제출력의_행정회계메타는_검수자가_참이라도_정체성에_남지_않는다(text):
    report = ComposedReport((ComposedSection("identity", (
        ComposedSentence(text, ("1",), "확인", planned_claim_slot="identity:business_definition"),
    )),))
    diagnostics = []
    checked = verify_report(
        report, (CollectedFragment("1", "공식자료", text),), None, _FakeReviewer(),
        diagnostics=diagnostics,
    )
    assert not checked.sections[0].sentences
    assert "identity_administrative_only" in str(diagnostics)


@pytest.mark.parametrize("text", (ADMINISTRATIVE_TEXTS[0], *ADMINISTRATIVE_TEXTS[-6:]))
def test_FULL_요약도_행정정보를_빼고_검증된_사업문장의_사실결속을_보존한다(text):
    report, facts = _report(("business_model", "portfolio"))
    admin = replace(_sentence("identity"), text=text)
    business = replace(_sentence("identity"), text=BUSINESS_TEXTS[0])
    report = replace(report, sections=(*report.sections, ComposedSection("identity", (admin, business))))
    selected = select_extractive_summary(report, (*facts, _fact("identity", admin), _fact("identity", business)))
    assert selected.release_ready
    assert any(item.sentence is business for item in selected.items)
    assert all(item.sentence is not admin for item in selected.items)
    assert report.sections[-1].sentences == (admin, business)

    thin_report = replace(report, sections=(*report.sections[:-1], ComposedSection("identity", (admin,))))
    thin = select_extractive_summary(thin_report, (*facts, _fact("identity", admin)))
    assert not thin.release_ready
    assert "identity" not in thin.section_ids


def test_행정정보_제외후_공식사업근거를_쓰는_빈장복구는_유지한다():
    address = ADMINISTRATIVE_TEXTS[0]
    business = BUSINESS_TEXTS[0]
    fragment = replace(
        _fragment("identity", address + " " + business),
        supported_claim_slots=("identity:official_location", "identity:business_definition"),
    )
    draft = ComposedReport((ComposedSection("identity", (
        ComposedSentence(address, ("1",), "확인", planned_claim_slot="identity:official_location"),
    )),))
    checked = verify_report(draft, (fragment,), None, _FakeReviewer())
    assert not checked.sections[0].sentences
    result = recover_empty_sections(
        "가나다전자", checked, targets=("identity",),
        evidence=recovery_evidence((fragment,)), reviewer=_FakeReviewer(),
        writer=lambda _: _response(
            {"identity": [(business, "1", "확인")]},
            claim_slot="identity:business_definition",
        ),
        require_claim_slot=True,
    )
    assert tuple(sentence.text for sentence in result.sections[0].sentences) == (business,)
    assert result.sections[0].sentences[0].citations == ("1",)
    assert result.sections[0].sentences[0].verification_state == "verified"
