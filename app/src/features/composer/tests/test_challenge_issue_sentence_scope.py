"""5장 문장 슬롯과 표 첫 문제 셀의 대여 경계를 검증한다."""
import pytest
import json
from src.features.composer import verify as v
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, FlowRow
from src.features.composer.challenge_business_scope import challenge_business_problem

ORDINARY = "회사는 고객 서비스를 제공하고 있다."
ACTUAL = "회사는 고객 결제 장애로 피해가 발생했다."

def test_슬롯을모르는이동경로와대응은새issue조건을빌리지않는다():
    sources = {"1": ORDINARY}
    assert not challenge_business_problem(ORDINARY, sources)
    assert not challenge_business_problem(ORDINARY, sources, claim_slot="current_challenges:response")
    assert challenge_business_problem(ORDINARY, sources, claim_slot="current_challenges:issue")

def test_다른원문절의문제를빌려일반운영issue를살리지못한다():
    assert challenge_business_problem(ORDINARY, {"1": ORDINARY + " " + ACTUAL},
                                      claim_slot="current_challenges:issue")

def test_대응셀의문제어를빌려일반운영문제셀을살리지못한다():
    assert challenge_business_problem(ORDINARY, {"1": ORDINARY + " " + ACTUAL},
                                      cells=(ORDINARY, ACTUAL))

def test_실제문제셀과일반운영대응셀은보존한다():
    assert not challenge_business_problem(ACTUAL, {"1": ACTUAL + " " + ORDINARY},
                                          cells=(ACTUAL, ORDINARY))

def test_숫자증명선택지는최종issue승인과구분한다():
    assert not challenge_business_problem(ORDINARY, {"1": ORDINARY},
                                          claim_slot="current_challenges:issue", require_current=False)

@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("slot,blocked", [("current_challenges:issue", True),
                                          ("current_challenges:response", False), ("", False)])
def test_평면및그룹검수의참판정도자기issue문장경계를통과한다(wrapped, slot, blocked):
    rows = [{"번호": 1, "결과": "참", "근거": ["a"]}]
    raw = json.dumps({"판정": rows} if wrapped else rows, ensure_ascii=False)
    problems = {}
    verdicts = v._apply_grounding(raw, {1: "참"}, {1: (ORDINARY, {"a": ORDINARY + " " + ACTUAL})},
        diagnostic_contexts={1: ("current_challenges", "본문", "검수")},
        claim_slots_by_number={1: slot}, grounding_problems=problems)
    assert (verdicts[1] != "참") == blocked
    assert bool(problems) == blocked

def test_최종표에서다른셀과같은원문의문제관계를빌리지못한다(monkeypatch):
    monkeypatch.setattr(v, "_semantic_review", lambda groups, *args, **kwargs: groups)
    row = FlowRow((ORDINARY, ACTUAL), ("a",))
    report = ComposedReport(sections=(ComposedSection("current_challenges", (), flow_rows=(row,)),))
    result = v.verify_report(report, (CollectedFragment("a", "DART", ORDINARY + " " + ACTUAL),),
                             None, lambda *args: "")
    assert not result.sections[0].flow_rows
    assert report.sections[0].flow_rows == (row,)
