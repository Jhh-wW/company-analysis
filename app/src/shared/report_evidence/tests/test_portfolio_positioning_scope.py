"""회사 포지셔닝 문구의 해당 의미칸과 혼합 절 범위를 검사한다."""
import hashlib

import pytest

from src.shared.report_evidence.business_slot_scope import business_slot_scope, business_slot_scope_problem, business_slot_quote_problem

SLOT = "portfolio:product_role"
POSITIONING = "우리는 하이테크 기술력으로 미래 산업에 대한 다양한 솔루션을 제시합니다. 시장을 이끌어가고 있습니다."


def test_슬로건은_제품역할에서만_제외한다():
    assert business_slot_scope_problem(POSITIONING, SLOT) == "business_slot_scope_unsupported"
    assert business_slot_scope(POSITIONING, SLOT).score_text == ""
    assert business_slot_scope(POSITIONING, "identity:business_definition").score_text == POSITIONING


@pytest.mark.parametrize("ending", ["제시하며", "제시하고", "제시하여"])
def test_솔루션제시와_시장선도만_이어져도_제품역할이_아니다(ending):
    text = f"우리는 하이테크 기술력으로 다양한 솔루션을 {ending} 시장을 이끌어갑니다."
    assert business_slot_scope_problem(text, SLOT) == "business_slot_scope_unsupported"


@pytest.mark.parametrize("text", [
    "하이테크 기술력으로 전기차용 타이어의 회전저항을 낮춘다.",
    "혁신적인 기술력으로 공기청정기는 실내 미세먼지를 제거한다.",
    "우리는 다양한 솔루션을 제시하며, 전기차용 타이어의 회전저항을 낮춘다.",
    "우리는 다양한 솔루션을 제시하며, 공기청정기는 실내 미세먼지를 제거한다.",
    "공기청정기는 미세먼지를 제거하며 시장을 선도한다.",
    "우리는 다양한 솔루션을 제시하며 전기차용 타이어의 회전저항을 낮춘다.",
    "우리는 다양한 솔루션을 제시하여 공기청정기의 미세먼지 제거 효율을 높인다.",
])
def test_기술력_표현만으로_구체제품의_기능을_제한하지_않는다(text):
    assert not business_slot_scope_problem(text, SLOT)
    scoped = business_slot_scope(text, SLOT)
    assert any(word in scoped.score_text for word in ("회전저항", "미세먼지"))


@pytest.mark.parametrize("separator", [" ", ", ", "\n", "; "])
def test_혼합문단_구체솔루션을_남기고_원문좌표를_유지한다(separator):
    fact = "설비진단솔루션은 진동을 측정하여 이상을 감지한다."
    text = POSITIONING + separator + fact
    original_hash = hashlib.sha256(text.encode()).hexdigest()
    scoped = business_slot_scope(text, SLOT)
    assert fact.rstrip(".") in scoped.score_text
    assert not business_slot_scope_problem(text, SLOT)
    assert scoped.excluded_clauses
    assert tuple(text[a:b] for a, b in scoped.excluded_spans) == scoped.excluded_clauses
    begin = text.index(fact)
    assert not business_slot_quote_problem(text, SLOT, begin, begin + len(fact))
    assert business_slot_quote_problem(text, SLOT, 0, len(POSITIONING))
    assert hashlib.sha256(text.encode()).hexdigest() == original_hash
