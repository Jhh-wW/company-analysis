"""유효한 첫 소개는 무변경, 누락은 사유에 맞춰 해당 장에만 알린다."""

import pytest

from src.features.composer.identity_opening_policy import apply_identity_opening_policy
from src.features.composer.identity_opening_policy_constants import (
    IDENTITY_OPENING_MISSING_NOTICE, IDENTITY_OPENING_NOTICE_BY_REASON,
)
from src.features.composer.identity_opening_recovery_constants import (
    IDENTITY_OPENING_RECOVERY_ADDED, IDENTITY_OPENING_RECOVERY_ATTEMPTED,
    IDENTITY_OPENING_RECOVERY_NO_BUDGET, IDENTITY_OPENING_RECOVERY_NO_CANDIDATE,
    IDENTITY_OPENING_RECOVERY_NO_EVIDENCE, IDENTITY_OPENING_RECOVERY_PARSE_FAILED,
    IDENTITY_OPENING_RECOVERY_REJECTED,
)
from src.features.composer.port import ComposedReport, ComposedSection
from src.features.composer.tests.test_identity_opening import _pair
from src.features.composer.tests.test_identity_opening_recovery import _existing


def test_현재_결속된_소개가_있으면_객체와_기존안내까지_완전무동작():
    sentence, fact = _pair()
    section = ComposedSection("identity", (sentence,), notice="기존 안내")
    report = ComposedReport((section,), summary=(sentence,))
    result = apply_identity_opening_policy(report, (fact,), reason=IDENTITY_OPENING_RECOVERY_REJECTED)
    assert result.report is report
    assert not result.missing
    assert result.shortfall_reasons == ()


@pytest.mark.parametrize("reason", tuple(IDENTITY_OPENING_NOTICE_BY_REASON))
def test_누락사유별_고지는_정체성에만_추가하고_승인본문을_보존한다(reason):
    report = _existing()
    result = apply_identity_opening_policy(report, (), reason=reason)
    assert result.missing
    notice = IDENTITY_OPENING_NOTICE_BY_REASON[reason]
    assert result.shortfall_reasons == (notice,)
    before, after = report.sections[0], result.report.sections[0]
    assert after.notice == before.notice + "\n" + notice
    assert after.sentences is before.sentences
    assert after.flow_rows is before.flow_rows
    assert result.report.sections[1] is report.sections[1]
    assert report.sections[0].notice == "기존 확인 범위"
    again = apply_identity_opening_policy(result.report, (), reason=reason)
    assert again.report is result.report
    assert again.shortfall_reasons == (notice,)


@pytest.mark.parametrize("reason", (
    IDENTITY_OPENING_RECOVERY_NO_BUDGET, IDENTITY_OPENING_RECOVERY_PARSE_FAILED,
    IDENTITY_OPENING_RECOVERY_NO_CANDIDATE, IDENTITY_OPENING_RECOVERY_REJECTED,
    IDENTITY_OPENING_RECOVERY_ADDED, IDENTITY_OPENING_RECOVERY_ATTEMPTED, "",
))
def test_근거확보_실패가_아니면_원문이_없다고_표현하지않는다(reason):
    result = apply_identity_opening_policy(_existing(), (), reason=reason)
    assert "근거를 충분히 확보하지 못했습니다" not in result.shortfall_reasons[0]


def test_추가후_최종결속에서_탈락하면_승인추정없이_일반누락고지를_쓴다():
    sentence, _fact = _pair()
    report = ComposedReport((ComposedSection("identity", (sentence,)),))
    result = apply_identity_opening_policy(report, (), reason=IDENTITY_OPENING_RECOVERY_ADDED)
    assert result.missing
    assert result.shortfall_reasons == (IDENTITY_OPENING_MISSING_NOTICE,)


def test_정체성장_자체가_없어도_다른장을_생성하거나_바꾸지않는다():
    report = ComposedReport((ComposedSection("portfolio", ()),))
    result = apply_identity_opening_policy(report, (), reason=IDENTITY_OPENING_RECOVERY_NO_EVIDENCE)
    assert result.missing
    assert result.report is report
    assert result.shortfall_reasons


def test_빈정체성에_이미_고지가_있으면_중복고지없이_누락만_반환한다():
    report = ComposedReport((ComposedSection("identity", (), notice="공식 근거가 부족합니다."),))
    result = apply_identity_opening_policy(report, (), reason=IDENTITY_OPENING_RECOVERY_NO_EVIDENCE)
    assert result.report is report
    assert result.missing
    assert result.shortfall_reasons
