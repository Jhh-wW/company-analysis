"""같은 회사의 명시 비중을 표 분모 추정과 구별한다."""
import pytest
from src.shared.revenue_population_scope import revenue_population_claim_problem

TABLE = "품목 매출액 비율 설비 55 55% 부품 45 45%. "
DIRECT = "당사의 경우도 마찬가지로 설비 매출이 전체 매출의 약 55%를 차지하고 있습니다."

@pytest.mark.parametrize("candidate", [
    "설비 사업은 장비를 판매하여 수익을 창출하고 있으며, 전체 매출의 약 55%를 차지한다.",
    "개별재무제표 기준으로 설비 사업이 전체 매출의 약 55%를 차지하고 있다.",
    "설비 매출은 전체 매출의 약 55%를 차지한다.",
])
def test_same_item_and_explicit_company_share_survive(candidate):
    assert not revenue_population_claim_problem(candidate, {"1": TABLE + DIRECT})

@pytest.mark.parametrize("candidate, source", [
    ("부품 사업은 전체 매출의 약 55%를 차지한다.", TABLE + DIRECT),
    ("설비 사업은 전체 매출의 약 70%를 차지한다.", TABLE + DIRECT),
    ("설비 사업은 전체 매출의 약 55%를 차지한다.", TABLE),
    ("설비 사업은 전체 매출의 약 55%를 차지한다.", TABLE + DIRECT.replace("당사의", "경쟁사의")),
    ("설비 사업은 전체 매출의 약 55%를 차지한다.", "[제조 부문] " + TABLE + DIRECT),
    ("설비 사업은 전체 매출의 약 55%를 차지한다.", TABLE + "상품 | " + DIRECT + " | 55%"),
    ("설비 사업은 전체 매출의 약 55%를 차지하고 부품 사업은 전체 매출의 약 70%를 차지한다.", TABLE + DIRECT),
])
def test_other_item_amount_owner_and_table_label_do_not_borrow_share(candidate, source):
    assert revenue_population_claim_problem(candidate, {"1": source})


@pytest.mark.parametrize("verb", [
    "차지하지 않는다", "차지할 예정이다", "차지할 것으로 예상한다",
    "차지하고 있다고 가정한다", "차지한다는 주장은 사실이 아니다",
])
def test_negative_forecast_and_quoted_assertion_do_not_prove_actual_share(verb):
    source = TABLE + f"당사의 설비 매출은 전체 매출의 약 55%를 {verb}."
    candidate = "설비 사업은 전체 매출의 약 55%를 차지한다."
    assert revenue_population_claim_problem(candidate, {"1": source})


@pytest.mark.parametrize("candidate", [
    "설비 사업은 장비를 판매하며, 부품 사업은 전체 매출의 약 55%를 차지한다.",
    "자회사의 설비 사업은 전체 매출의 약 55%를 차지한다.",
    "경쟁사의 설비 사업은 전체 매출의 약 55%를 차지한다.",
    "2026년 연결 기준으로 설비 사업은 전체 매출의 약 55%를 차지한다.",
])
def test_later_subject_and_other_owner_do_not_borrow_the_first_subject(candidate):
    assert revenue_population_claim_problem(candidate, {"1": TABLE + DIRECT})
