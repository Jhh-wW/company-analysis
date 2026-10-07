"""원가 배부 절의 수집·AI·작성 지원 제한이 같은 원문 좌표를 사용한다."""
from hashlib import sha256
from pathlib import Path

import pytest

from src.shared.report_evidence.business_slot_scope import (
    business_slot_scope, business_slot_scope_problem, business_slot_quote_problem,
)
from src.shared.report_evidence.overhead_allocation_scope import overhead_allocation_scope

ROLE = "operations_partners:operating_role"
POLICY = "실제조업도가 정상조업도에 미달하는 경우 생산단위당 고정제조간접원가는 정상조업도를 기초로 배부되며, 배부되지 않은 고정제조간접원가는 발생 기간의 비용으로 인식한다."


def test_pure_and_inventory_mixed_source_lose_the_operating_role_only():
    for source in (POLICY, "재고자산은 총평균법으로 결정한다. " + POLICY):
        digest = sha256(source.encode()).hexdigest()
        scope = business_slot_scope(source, ROLE)
        assert scope.excluded_clauses
        assert business_slot_scope_problem(source, ROLE) == "business_slot_scope_unsupported"
        assert business_slot_quote_problem(source, ROLE, 0, len(source))
        quote = "배부되지 않은 고정제조간접원가는 발생 기간의 비용으로 인식한다"
        start = source.index(quote)
        assert business_slot_quote_problem(source, ROLE, start, start + len(quote))
        assert business_slot_scope(source, "past_changes:performance").score_text == source
        assert sha256(source.encode()).hexdigest() == digest


@pytest.mark.parametrize("current", (
    "공장에서 제품을 제조하고 있다.",
    "실제조업도가 정상조업도에 미달했다.",
    "생산이 중단되어 복구를 진행하고 있다.",
    "생산원가가 급증했다.",
))
def test_real_activity_and_current_problem_quotes_survive_with_policy(current):
    source = POLICY + " " + current
    assert current in overhead_allocation_scope(source).score_text
    start = source.index(current)
    assert not business_slot_quote_problem(source, ROLE, start, start + len(current))
    policy_start = source.index("배부되지 않은")
    assert business_slot_quote_problem(source, ROLE, policy_start, source.index("인식한다") + len("인식한다"))


def test_allocation_scope_exact_spans_and_engine_mirror_remain_equal():
    source = "앞부분. " + POLICY + " 실제 제품을 생산하고 있다."
    scope = overhead_allocation_scope(source)
    assert len(scope.excluded_spans) == 1
    start, end = scope.excluded_spans[0]
    assert source[start:end].strip() == POLICY.rstrip(".")
    root = Path(__file__).resolve().parents[5]
    engine = root / "analysis_engine/src/features/evidence_collection"
    shared = root / "app/src/shared/report_evidence"
    assert (engine / "overhead_allocation_constants.py").read_text(encoding="utf-8") == (shared / "overhead_allocation_constants.py").read_text(encoding="utf-8")
    assert (engine / "overhead_allocation_scope.py").read_text(encoding="utf-8").replace("from features.evidence_collection", "from src.shared.report_evidence") == (shared / "overhead_allocation_scope.py").read_text(encoding="utf-8")


@pytest.mark.parametrize("actual", (
    "올해 실제조업도가 정상조업도에 미달했다",
    "올해 실제조업도는 정상조업도의 40%에 그쳤다",
    "실제조업도는 정상조업도의 40.5%에 그쳤습니다",
))
@pytest.mark.parametrize("separator", (", ", " "))
def test_같은절의_실제저조업도사건도_원문과인용을보존한다(actual, separator):
    source = POLICY.removesuffix("인식한다.") + "인식하며" + separator + actual + "."
    digest = sha256(source.encode()).hexdigest()
    assert actual in overhead_allocation_scope(source).score_text
    assert actual in business_slot_scope(source, ROLE).score_text
    start = source.index(actual)
    assert not business_slot_quote_problem(source, ROLE, start, start + len(actual))
    policy_quote = "배부되지 않은 고정제조간접원가는 발생 기간의 비용으로 인식하며"
    policy_start = source.index(policy_quote)
    assert business_slot_quote_problem(source, ROLE, policy_start, policy_start + len(policy_quote))
    assert sha256(source.encode()).hexdigest() == digest


@pytest.mark.parametrize("nonactual", (
    "실제조업도가 정상조업도에 미달할 경우",
    "실제조업도가 정상조업도에 미달할 것으로 예상한다",
    "실제조업도가 정상조업도에 미달했다면",
    "실제조업도가 정상조업도에 미달했다고 가정한다",
    "실제조업도는 정상조업도의 40%에 그칠 전망이다",
    "실제조업도는 정상조업도의 40%에 그쳤다는 가정이다",
))
def test_조건_미래_예상비율이_회계정책제외를_해제하지않는다(nonactual):
    source = POLICY.removesuffix("인식한다.") + "인식하며, " + nonactual + "."
    assert not overhead_allocation_scope(source).score_text.strip()


def test_앞선실제사건의주어가_뒤조건부주어범위에_흡수되지않는다():
    actual = "올해 실제조업도가 정상조업도에 미달했다고 공시했으며"
    source = actual + ", " + POLICY
    scope = overhead_allocation_scope(source)
    assert actual in scope.score_text
    assert actual in business_slot_scope(source, ROLE).score_text
    assert not business_slot_quote_problem(source, ROLE, 0, len(actual))
    quote = "배부되지 않은 고정제조간접원가는 발생 기간의 비용으로 인식한다"
    start = source.index(quote)
    assert business_slot_quote_problem(source, ROLE, start, start + len(quote))


def test_조건부배부의첫인식뒤_실제원가증가의인식을_정책으로묶지않는다():
    actual = "올해 생산원가가 급증해 증가분을 비용으로 인식했다"
    source = POLICY.removesuffix("인식한다.") + "인식하며, " + actual + "."
    scope = overhead_allocation_scope(source)
    assert actual in scope.score_text
    assert actual in business_slot_scope(source, ROLE).score_text
    start = source.index(actual)
    assert not business_slot_quote_problem(source, ROLE, start, start + len(actual))
