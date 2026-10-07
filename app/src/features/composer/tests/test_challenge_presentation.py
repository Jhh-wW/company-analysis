"""대응 설명이 회사 문제로 보이거나 검증된 문제가 없다고 표시되지 않는다."""

from dataclasses import replace

import pytest

from src.features.composer.challenge_presentation import challenge_response_only_notice
from src.features.composer.constants import NOTICE_CHALLENGE_RESPONSE_ONLY
from src.features.composer.logic import summary_candidates
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence


def _sentence(slot="response", **changes):
    return replace(ComposedSentence(
        "회사는 고객 지원을 위한 서비스 센터를 운영한다.", ("1",), "확인",
        planned_claim_slot=f"current_challenges:{slot}", verification_state="verified",
    ), **changes)


def _report(*sentences):
    return ComposedReport((ComposedSection("current_challenges", tuple(sentences)),))


def test_대응은_본문에_남기고_현재과제_요약후보에서는_뺀다():
    response = _sentence()
    issue = _sentence("issue", text="부품 공급 지연으로 고객 납품이 지연되고 있다.")
    report = _report(response, issue)
    candidates = summary_candidates(report)
    assert len(candidates) == 1 and candidates[0].sentence is issue
    assert report.sections[0].sentences == (response, issue)


def test_다른장_대응_설명과_옛_의미칸없는_문장은_요약기준을_바꾸지_않는다():
    response = _sentence()
    legacy = _sentence(planned_claim_slot="", text="회사는 고객 납품 지연을 겪고 있다.")
    report = ComposedReport((
        ComposedSection("operations_partners", (response,)),
        ComposedSection("current_challenges", (legacy,)),
    ))
    assert tuple(value.sentence for value in summary_candidates(report)) == (response, legacy)


def test_검수된_대응만_있으면_자료범위를_설명한다():
    response = _sentence()
    report = _report(response)
    assert challenge_response_only_notice(report, has_industry_context=False) == NOTICE_CHALLENGE_RESPONSE_ONLY
    assert report.sections[0].sentences[0] is response
    assert report.sections[0].notice == ""


@pytest.mark.parametrize("sentences,industry", [
    ((), False),
    ((_sentence(), _sentence("issue")), False),
    ((_sentence(),), True),
    ((_sentence(planned_claim_slot=""),), False),
    ((_sentence(verification_state="unverified"),), False),
    ((_sentence(grade="해석"),), False),
])
def test_문제와_산업이_있거나_대응전용임이_불확실하면_안내를_만들지_않는다(sentences, industry):
    assert challenge_response_only_notice(_report(*sentences), has_industry_context=industry) == ""
