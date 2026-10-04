"""AI 재분류도 과거 완료·공시준수·긍정 성과의 5장 재유입을 막는다."""
import pytest

from src.features.evidence_reclassify.logic import parse_and_verify, to_typed_fragments
from src.features.evidence_reclassify.tests.test_logic import _assignment, _candidate, _response

HEADER = "제재조치일 | 조치대상자 | 처벌 또는 조치내용 | 이행 및 재발방지대책"
HISTORY = "2024.04.10 | 가온기업 | 과태료 100만원 | 납부 완료, 설비 점검 완료"
CURRENT = "은행의 차주 연체율이 급증하여 신용위험에 대응했다."


@pytest.mark.parametrize("slot", ["current_challenges:issue", "current_challenges:response"])
@pytest.mark.parametrize("source,quote,reason", [
    (HEADER + "; " + HISTORY, HISTORY, "challenge_current_problem_unbound"),
    (HEADER + "; " + HISTORY + "; " + CURRENT, "설비 점검 완료", "challenge_current_problem_unbound"),
    ("기업은 지연공시로 공시위반 제재금을 납부하고 공시교육을 실시했다.",
     "공시교육을 실시했다.", "challenge_business_relation_unbound"),
    ("우수기업으로 선정됐다.", "우수기업으로 선정됐다.", "challenge_response_problem_unbound"),
])
def test_ai_assignment_cannot_restore_excluded_current_support(slot, source, quote, reason):
    candidate = _candidate(text=source)
    original = dict(candidate)
    result = parse_and_verify(_response(_assignment(section_id="current_challenges", slot_id=slot, quote=quote)), [candidate])
    assert not result.assignments
    assert result.rejected[0].reason_code == reason
    assert candidate == original


@pytest.mark.parametrize("quote", [
    CURRENT,
    "2026.10.04 공장 화재로 생산이 중단됐다.",
    "원자재 가격 급등으로 생산원가 부담이 커졌다.",
])
def test_current_business_and_unlisted_problem_in_mixed_raw_keep_exact_binding(quote):
    source = HEADER + "; " + HISTORY + "; 우수기업으로 선정됐다; " + quote
    candidate = _candidate(text=source)
    result = parse_and_verify(_response(_assignment(section_id="current_challenges", slot_id="current_challenges:issue", quote=quote)), [candidate])
    assert len(result.assignments) == 1
    typed = to_typed_fragments(result, candidate)[0]
    assert typed["text"] == source
    assert typed["text_sha256"] == candidate["text_sha256"]
    assert typed["location"] == candidate["location"]
    assert result.assignments[0].exact_quote == quote
