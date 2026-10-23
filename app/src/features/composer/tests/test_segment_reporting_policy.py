"""실제 보고서의 부문 공시 방법이 고객·수익 구조를 대신하지 않도록 검증한다."""

import json
import re

import pytest

from src.features.composer.accounting_policy_guard import (
    accounting_policy_matched_rules,
    accounting_policy_problem,
)
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report


REPORTING_POLICY = (
    "회사의 최고영업의사결정자는 대표이사이며, 영업부문별 정보는 "
    "최고영업의사결정자에게 내부적으로 보고되는 방식에 기초하여 공시된다."
)


@pytest.mark.parametrize("text", (
    REPORTING_POLICY,
    "영업부문 정보는 최고영업의사결정자에게 보고되는 방식에 따라 공시됩니다.",
    "당사의 부문별 정보는 최고영업의사결정자에게 내부적으로 보고되는 방식에 따라 공시된다.",
))
def test_부문공시방법만으로_수익모델을_채우지_않는다(text):
    assert accounting_policy_problem(
        text, {"2": text}, section_id="business_model"
    ) == "accounting_policy_boilerplate"
    assert accounting_policy_matched_rules(
        text, {"2": text}, section_id="business_model"
    ) == ("부문보고방법",)


@pytest.mark.parametrize("text", (
    "회사의 영업부문은 결제대행과 자금관리 서비스로 구성된다.",
    "회사는 가맹점에 결제대행 서비스를 제공하고 거래 수수료를 받는다.",
    "최고영업의사결정자는 대표이사이며, 회사는 기업 고객에게 구독형 서비스를 판매한다.",
    "회사는 기업 고객에게 영업부문 정보 보고 시스템을 제공한다.",
    REPORTING_POLICY + " 회사는 가맹점에 결제대행 서비스를 제공한다.",
    REPORTING_POLICY.removesuffix(".") + ", 회사는 가맹점에 결제대행 서비스를 제공한다.",
))
def test_실제사업부와_고객서비스_혼합사실은_보존한다(text):
    assert not accounting_policy_problem(
        text, {"2": text}, section_id="business_model"
    )


def test_검수모델이_참으로_보아도_순수부문보고방법은_공개본문에서_제외한다():
    business = "회사는 가맹점에 결제대행 서비스를 제공한다."
    report = ComposedReport((ComposedSection("business_model", (
        ComposedSentence(REPORTING_POLICY, ("2",), "확인"),
        ComposedSentence(business, ("service",), "확인"),
    )),))
    fragments = (
        CollectedFragment("2", "수익인식", REPORTING_POLICY),
        CollectedFragment("service", "사업내용", business),
    )
    diagnostics = []

    def approve(prompt):
        items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
        return json.dumps({"판정": [
            {"번호": item.number, "장": item.section,
             "근거": list(item.citations), "결과": "참"}
            for item in items
        ]}, ensure_ascii=False)

    checked = verify_report(report, fragments, None, approve, diagnostics=diagnostics)
    assert [sentence.text for sentence in checked.sections[0].sentences] == [business]
    assert any(item.get("reason_code") == "accounting_policy_boilerplate"
               for item in diagnostics)
