"""취재 상대와 회사 사업 주체를 원문에서 구분한다."""

from copy import deepcopy

import pytest

from src.features.news_intake.reporting_subject_scope import target_is_only_interview_recipient
from src.features.news_intake.tests.test_collection import COMPANY, accepted, item, snapshot
from src.features.news_intake.tests.test_quote_selection import selected, validate


@pytest.mark.parametrize("body", [
    "다른기술의 대표는 가나다전자를 만나 해외 시장에 부품을 공급한다고 전했다.",
    "다른기술의 대표는 가나다전자와의 인터뷰에서 해외 시장에 부품을 공급한다고 말했다.",
    "다른기술의 대표는 가나다전자를 만나 ‘신제품을 해외 시장에 공급한다’고 설명했다.",
    "기자는 가나다전자를 만나 인터뷰했고, 다른기술과 푸른소재 양사 간 공급 계약을 체결했다고 전했다.",
])
@pytest.mark.parametrize("selection", [False, True])
def test_interview_recipient_does_not_support_target_business(body, selection):
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    raw = selected(candidate, body) if selection else {
        "items": [accepted({"id": candidate.id, "body": body})]
    }
    before = deepcopy(raw)
    assert target_is_only_interview_recipient(body, COMPANY)
    excerpts, exclusions = validate(raw, candidate, body)
    assert not excerpts and exclusions
    assert raw == before


@pytest.mark.parametrize("body", [
    "가나다전자는 다른매체를 만나 자사가 산업설비를 직접 공급했다고 전했다.",
    "가나다전자 대표는 기자와의 인터뷰에서 산업설비를 직접 공급했다고 말했다.",
    "기자는 가나다전자를 만나 인터뷰했다. 가나다전자는 산업설비를 직접 공급했다.",
    "다른기술은 가나다전자를 만나 협력 내용을 전했다. 가나다전자는 다른기술과 공동 공급 계약을 체결했다.",
    "가나다전자는 산업설비를 공급했다. 다른기술 대표는 가나다전자와의 인터뷰에서 자신의 사업을 말했다.",
    "다른기술은 가나다전자를 만나 공급계약을 체결했다고 밝혔다.",
    "다른기술은 가나다전자를 만나, 공급 계약을 체결했다고 밝혔다.",
    "다른기술은 가나다전자를 만나 공동 생산 계약을 체결했다고 전했다.",
    "다른기술은 가나다전자를 만나 협력 방안을 논의했다고 전했다.",
    "다른기술은 가나다전자를 만나 인터뷰하고 양사 간 공급 계약을 체결했다고 밝혔다.",
])
def test_target_action_and_explicit_cooperation_are_preserved(body):
    assert not target_is_only_interview_recipient(body, COMPANY)


def test_reporting_verb_in_another_sentence_does_not_define_interview_relation():
    body = "다른기술은 가나다전자를 만나 공급 계약을 체결했다. 다른기술 대표는 새 사업을 설명했다."
    assert not target_is_only_interview_recipient(body, COMPANY)


def test_company_action_is_still_accepted_without_interview_recipient():
    body = "가나다전자는 산업설비 제조 사업을 운영하며 산업설비 신제품을 공급했다."
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    excerpts, exclusions = validate(selected(candidate, body), candidate, body)
    assert excerpts and not exclusions
    assert excerpts[0].text == body
