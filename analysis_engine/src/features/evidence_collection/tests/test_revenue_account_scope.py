"""계정 정의의 수수료와 실제 상품 수익방식을 구분한다."""
import hashlib

import pytest

from features.evidence_collection.business_slot_scope import (
    business_slot_quote_problem, business_slot_scope, business_slot_scope_problem,
)
from features.evidence_collection.relevance import score_fragment_slots_with_signal
from features.evidence_collection.tests.test_streaming_collection import _collect


REVENUE = "business_model:revenue_model"
ACCOUNT = (
    "기타 수익 항목에는 수입기술료, 수입수수료, 대여금에 대한 이자수익 등이 포함되며, "
    "배당금수익은 이 항목에 포함되지 않는다."
)


@pytest.mark.parametrize("text", [
    ACCOUNT,
    "금융수익에는 수입수수료와 이자수익이 포함된다.",
    "영업외수익 계정은 이자수익과 수입수수료로 구성된다.",
    "수익 항목에는 수입기술료와 수입수수료를 계상한다.",
    "기타수익 항목에는 수입수수료 300억원과 수입기술료 100억원이 포함됩니다.",
    "수입수수료를 금융수익 항목으로 계상합니다.",
    "금융수익 항목에는 수입수수료가 포함됨.",
    "(*) 기타에는 수입기술료, 수입수수료, 대여금에 대한 이자수익 등이 포함되어 있습니다. 단, 배당금수익은 포함되어 있지 않습니다.",
    "(*) 기타에는 수입기술료와 수입수수료가 포함됩니다.",
])
def test_계정분류_각주만으로_수익칸을_채우거나_무신호_재분류하지_않는다(text):
    scores, observed = score_fragment_slots_with_signal(text)
    assert observed
    assert REVENUE not in {score.slot_id for score in scores}
    assert business_slot_scope_problem(text, REVENUE) == "business_slot_scope_unsupported"
    assert business_slot_scope(text, "business_model:regional_mix").score_text == text
    assert not _collect(text).unclassified_fragments


@pytest.mark.parametrize("text", [
    "주된 영업수익의 형태는 산업장비 용역 매출, 유지보수 매출 등으로 구성됩니다.",
    "회사는 고객에게 산업장비를 제공하고 서비스 수수료를 받는다.",
    "금융수익은 기업대출 고객 이자와 대출취급 수수료로 구성된다.",
    "금융수익은 고객대출 이자와 대출취급 수수료로 구성된다.",
    "금융수익에는 고객에게 대출 서비스를 제공하고 받은 수수료가 포함된다.",
    "기타수익에는 보험상품 판매 수수료가 포함된다.",
    "금융수익에는 신탁상품 운용 수수료가 포함된다.",
    "기타수익에는 장비 임대 수수료가 포함된다.",
    "기타수익에는 서비스 구독 수수료가 포함된다.",
])
def test_실제_제품매출구성과_금융상품_활동수익은_보존한다(text):
    assert not business_slot_scope_problem(text, REVENUE)
    scores, _ = score_fragment_slots_with_signal(text, allowed_slot_ids=frozenset({REVENUE}))
    assert REVENUE in {score.slot_id for score in scores}


def test_기술사용권_허여대가를_계정정의로_제외하지_않는다():
    text = "기타수익에는 고객에게 기술사용권을 허여하고 받은 기술료가 포함된다."
    assert not business_slot_scope_problem(text, REVENUE)
    assert not business_slot_quote_problem(text, REVENUE, 0, len(text))
    assert business_slot_scope(text, REVENUE).score_text.strip()


@pytest.mark.parametrize("separator", [" ", "\n", "; ", ", "])
def test_혼합원문은_수익정의절만_가리고_원문좌표와_지문을_보존한다(separator):
    actual = "회사는 고객에게 산업장비를 제공하고 서비스 수수료를 받는다."
    text = ACCOUNT + separator + actual
    scoped = business_slot_scope(text, REVENUE)
    assert "수입수수료" not in scoped.score_text
    assert actual.rstrip(".") in scoped.score_text
    assert not business_slot_scope_problem(text, REVENUE)
    assert business_slot_quote_problem(text, REVENUE, 0, len(ACCOUNT))
    start = text.index("수입수수료")
    assert business_slot_quote_problem(text, REVENUE, start, start + len("수입수수료"))
    assert not business_slot_quote_problem(text, REVENUE, text.index(actual), len(text))
    harvest = _collect(text)
    assert any(fragment.text == text for fragment in harvest.fragments)
    for fragment in harvest.fragments:
        start, end = map(int, fragment.location.split("-"))
        assert text[start:end] == fragment.text
        assert fragment.text_sha256 == hashlib.sha256(fragment.text.encode()).hexdigest()
    for document in harvest.documents:
        assert document.content_sha256 == hashlib.sha256(text.encode()).hexdigest()


def test_같은문장의_계정정의뒤_별도_서비스활동은_빌려쓰거나_소실되지_않는다():
    account = "기타수익에는 수입기술료와 수입수수료가 포함되고"
    actual = "회사는 고객에게 산업장비를 제공하고 서비스 수수료를 받는다."
    text = account + " " + actual
    assert business_slot_quote_problem(text, REVENUE, 0, len(account))
    assert not business_slot_quote_problem(text, REVENUE, len(account) + 1, len(text))
    assert actual.rstrip(".") in business_slot_scope(text, REVENUE).score_text


def test_계정정의뒤_정상매출구성_선언까지_제외하지_않는다():
    text = "기타수익에는 수입수수료가 포함되며 주된 영업수익의 형태는 용역 매출, 콘텐츠 매출 등으로 구성됩니다."
    scoped = business_slot_scope(text, REVENUE)
    assert "수입수수료" not in scoped.score_text
    assert "용역 매출" in scoped.score_text and "콘텐츠 매출" in scoped.score_text
    assert not business_slot_scope_problem(text, REVENUE)
    scores, _ = score_fragment_slots_with_signal(text, allowed_slot_ids=frozenset({REVENUE}))
    assert REVENUE in {score.slot_id for score in scores}
