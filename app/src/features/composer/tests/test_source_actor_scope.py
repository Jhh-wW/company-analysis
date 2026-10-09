"""공식 원문 법인·단계를 바꾼 회사 사실과 유효한 그룹 사실을 구별한다."""

import hashlib
import json

import pytest

from src.features.composer.source_actor_scope import (
    source_actor_problem, source_actor_subject_scope,
)
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND


_OWNER = "가람제조주식회사"
_OTHER = "다온설비주식회사"


def _context(actor=_OTHER, *, status="", item="산업장비"):
    text = " | ".join((actor, item, "장비 개발을 완료했다.", status))
    payload = {
        "version": "source-context-v1", "text": text,
        "location": f"100-{100+len(text)}",
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "actor": actor, "document_actor": _OWNER,
        "document_actor_location": f"0-{len(_OWNER)}",
        "document_actor_sha256": hashlib.sha256(_OWNER.encode()).hexdigest(),
        "origin": "table_row", "status": status, "item": item,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize("candidate", [
    "회사는 산업장비를 제조한다.",
    "산업장비를 제조한다.",
    "장비 제조 | 고객 납품",
    "회사는 장비를 제조하고 종속회사와 협력한다.",
    f"회사는 장비를 제조한다. {_OTHER}는 부품을 판매한다.",
    f"회사는 장비를 제조하고 {_OTHER}는 부품을 판매한다.",
    "연결회사는 장비를 제조한다.",
])
def test_other_actor_cannot_become_target_fact_or_use_later_actor_ornament(candidate):
    assert source_actor_problem(candidate, _context(), {"1": "장비를 제조한다."}) == SCOPE_CONDITION_UNBOUND


def test_named_actual_actor_is_preserved_without_inventing_group_relation():
    text = f"{_OTHER}는 산업장비를 제조한다."
    assert source_actor_problem(text, _context(), {"1": text}) == ""
    assert source_actor_subject_scope(text, (_context(),), company_name=_OWNER, sources={"1": text}) == _OTHER
    assert source_actor_problem("종속회사 " + text, _context(), {"1": text}) == SCOPE_CONDITION_UNBOUND


def test_real_group_relation_is_required_and_preserved():
    sources = {"1": f"당사의 종속회사 {_OTHER}는 산업장비를 제조한다."}
    text = "연결회사는 산업장비를 제조한다."
    assert source_actor_problem(text, _context(), sources) == ""
    assert source_actor_subject_scope(text, (_context(),), company_name=_OWNER, sources=sources) == _OWNER + " 연결 범위"
    # 뒤에 관계 어휘를 붙여도 앞 직접 회사 행위의 주어는 바꿀 수 없다.
    assert source_actor_problem("회사는 장비를 제조하고 종속회사와 협력한다.", _context(), sources) == SCOPE_CONDITION_UNBOUND


def test_own_document_group_scope_needs_actual_group_statement():
    text = "연결회사는 산업장비를 제조한다."
    assert source_actor_subject_scope(text, (_context(_OWNER),), company_name=_OWNER, sources={"1": text}) == _OWNER + " 연결 범위"
    assert source_actor_subject_scope(text, (_context(_OWNER),), company_name=_OWNER, sources={"1": "당사는 장비를 제조한다."}) == ""


def test_through_actor_requires_target_subject_in_original_clause():
    text = f"회사는 {_OTHER}를 통해 산업장비를 제조한다."
    assert source_actor_problem(text, _context(), {"1": text}) == ""
    assert source_actor_problem(text, _context(), {"1": f"타사는 {_OTHER}를 통해 장비를 제조한다."}) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize("candidate", [
    "회사는 산업장비를 양산 적용 중이다.",
    "회사는 산업장비의 양산을 완료했다.",
    "회사는 산업장비를 개발했고 양산 적용을 완료했다.",
])
def test_pending_product_stage_cannot_become_current_completion(candidate):
    assert source_actor_problem(candidate, _context(_OWNER, status="양산 적용 예정")) == SCOPE_CONDITION_UNBOUND


@pytest.mark.parametrize("candidate", [
    "회사는 산업장비 개발을 완료했고 양산 적용 예정이다.",
    "회사는 산업장비의 양산 적용을 계획했고 다른센서는 양산 적용을 완료했다.",
    "회사는 산업장비의 양산 적용을 계획했다. 다른센서는 양산 적용 중이다.",
])
def test_other_product_or_other_stage_completion_is_not_globally_blocked(candidate):
    assert source_actor_problem(candidate, _context(_OWNER, status="양산 적용 예정")) == ""


def test_empty_context_keeps_legacy_subject_scope():
    assert source_actor_problem("회사는 장비를 제조한다.", "") == ""
    assert source_actor_subject_scope("회사는 장비를 제조한다.", ("",), company_name=_OWNER, sources={}) == _OWNER


def test_mixed_status_uses_pending_stage_instead_of_first_completed_stage():
    context = _context(_OWNER, status="개발 완료, 양산 적용 예정")
    assert source_actor_problem("회사는 산업장비의 양산을 완료했다.", context) == SCOPE_CONDITION_UNBOUND
    assert source_actor_problem("회사는 산업장비 개발을 완료했고 양산 적용 예정이다.", context) == ""


def test_development_pending_does_not_become_development_completed():
    assert source_actor_problem("회사는 산업장비 개발을 완료했다.", _context(_OWNER, status="개발 예정")) == SCOPE_CONDITION_UNBOUND
