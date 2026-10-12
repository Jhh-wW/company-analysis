"""수익 계정각주의 전체·부분인용이 혼합 사업 근거를 빌리지 못한다."""
import pytest

from src.shared.report_evidence.business_slot_scope import (
    business_slot_quote_problem, business_slot_scope, business_slot_scope_problem,
)


REVENUE = "business_model:revenue_model"
ACCOUNT = (
    "기타 수익 항목에는 수입기술료, 수입수수료, 대여금에 대한 이자수익 등이 포함되며, "
    "배당금수익은 이 항목에 포함되지 않는다."
)


@pytest.mark.parametrize("text", [
    "기타수익 항목에는 수입수수료 300억원과 수입기술료 100억원이 포함됩니다.",
    "수입수수료를 금융수익 항목으로 계상합니다.",
    "금융수익 항목에는 수입수수료가 포함됨.",
    "(*) 기타에는 수입기술료, 수입수수료, 대여금에 대한 이자수익 등이 포함되어 있습니다. 단, 배당금수익은 포함되어 있지 않습니다.",
    "(*) 기타에는 수입기술료와 수입수수료가 포함됩니다.",
])
def test_회계분류의_존대와_명사형_활용도_수익칸을_채우지_못한다(text):
    assert business_slot_scope_problem(text, REVENUE)
    assert business_slot_quote_problem(text, REVENUE, 0, len(text))


@pytest.mark.parametrize("quote", [ACCOUNT, "수입수수료", "대여금에 대한 이자수익"])
def test_계정범주의_전체와_부분인용은_수익방식을_지원하지_않는다(quote):
    actual = "회사는 고객에게 산업장비를 제공하고 서비스 수수료를 받는다."
    text = ACCOUNT + " " + actual
    start = text.index(quote)
    assert business_slot_scope_problem(ACCOUNT, REVENUE)
    assert not business_slot_scope_problem(text, REVENUE)
    assert business_slot_quote_problem(text, REVENUE, start, start + len(quote))
    assert not business_slot_quote_problem(text, REVENUE, text.index(actual), len(text))
    assert "수입수수료" not in business_slot_scope(text, REVENUE).score_text


@pytest.mark.parametrize("text", [
    "금융수익은 기업대출 고객 이자와 대출취급 수수료로 구성된다.",
    "금융수익은 고객대출 이자와 대출취급 수수료로 구성된다.",
    "금융수익에는 고객에게 대출 서비스를 제공하고 받은 수수료가 포함된다.",
    "기타수익에는 보험상품 판매 수수료가 포함된다.",
    "금융수익에는 신탁상품 운용 수수료가 포함된다.",
    "기타수익에는 장비 임대 수수료가 포함된다.",
    "기타수익에는 서비스 구독 수수료가 포함된다.",
    "기타수익에는 고객에게 기술사용권을 허여하고 받은 기술료가 포함된다.",
    "주된 영업수익의 형태는 산업장비 용역 매출, 유지보수 매출 등으로 구성됩니다.",
])
def test_실제_금융상품수익과_제품매출구성_정확인용은_보존한다(text):
    assert not business_slot_scope_problem(text, REVENUE)
    assert not business_slot_quote_problem(text, REVENUE, 0, len(text))


def test_같은문장에서도_분류절과_서비스대가절을_구분한다():
    account = "기타수익에는 수입기술료와 수입수수료가 포함되고"
    actual = "회사는 고객에게 산업장비를 제공하고 서비스 수수료를 받는다."
    text = account + " " + actual
    assert business_slot_quote_problem(text, REVENUE, 0, len(account))
    assert not business_slot_quote_problem(text, REVENUE, len(account) + 1, len(text))
