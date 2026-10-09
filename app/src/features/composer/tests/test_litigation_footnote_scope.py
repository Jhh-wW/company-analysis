"""사건별 각주를 다른 사건·소송 전체로 빌리지 않는지 확인한다."""
import pytest
from src.features.composer.challenge_event_scope import (
    _litigation_rows_and_notes, litigation_footnote_scope_problem,
    challenge_event_scope_problem,
)

SOURCE = (
    "계류중인 소송사건 (단위: 천원) 구분 계류법원 원고 피고 진행상황 소송금액 "
    "제품보증청구 수원지방법원 회사 고객 1심 진행중 100 "
    "납품대금청구 대법원 회사 고객 3심 진행중 200 "
    "채무부존재확인 (*1) 서울중앙지방법원 거래처 회사 1심 진행중 300 "
    "공사대금반환청구 (*1) 서울남부지방법원 고객 회사 1심 진행중 400 "
    "(*1) 해당 소송으로 인한 자원의 유출금액 및 시기는 불확실하며, "
    "당사의 재무상태에 중요한 영향을 미치지 않을 것으로 판단하고 있습니다. "
    "한편 회사의 충당부채로 인식한 금액은 없습니다."
)

def test_식별된_행은_원문연속좌표와_각주표지를_보존한다():
    rows, notes = _litigation_rows_and_notes(SOURCE)
    assert len(rows) == 4 and set(notes) == {"(*1)"}
    assert all(SOURCE[row.start:row.end] == row.raw_row for row in rows)
    assert [bool(row.markers) for row in rows] == [False, False, True, True]

@pytest.mark.parametrize("candidate", (
    "제품보증청구 소송으로 인한 자원의 유출금액은 불확실하다.",
    "계류 중인 소송으로 인한 자원의 유출금액 및 시기는 불확실하다.",
    "납품대금청구 소송은 재무상태에 중요한 영향을 미치지 않을 것으로 판단한다.",
    "제품보증청구와 공사대금반환청구 소송의 자원의 유출금액은 불확실하다.",
))
def test_다른사건이나_전체소송으로_각주평가를_확대하지않는다(candidate):
    assert litigation_footnote_scope_problem(candidate, {"a": SOURCE}) == "scope_condition_unbound"
    assert challenge_event_scope_problem(candidate, {"a": SOURCE}) == "scope_condition_unbound"

@pytest.mark.parametrize("candidate", (
    "채무부존재확인과 공사대금반환청구 소송의 자원의 유출금액은 불확실하다.",
    "각주1 해당 소송의 자원의 유출금액은 불확실하다.",
    "제품보증청구 소송은 1심 진행중이다.",
    "제품보증청구 소송으로 고객 납품이 중단되어 대체 공급을 진행 중이다.",
))
def test_동일적용행과_실제사건및_사업피해를_보존한다(candidate):
    assert not litigation_footnote_scope_problem(candidate, {"a": SOURCE})

def test_머리말없는_모호원문은_행을_만들어_승인하지않는다():
    rows, notes = _litigation_rows_and_notes("소송 (*1) 자원의 유출금액은 불확실하다.")
    assert rows == () and notes == {}

def test_긴사건명안의_접두사건은_별도언급으로_세지않는다():
    source = (
        "구분 계류법원 원고 피고 진행상황 소송금액 "
        "공사대금청구 수원지방법원 회사 고객 1심 진행중 100 "
        "공사대금청구취소 (*1) 대법원 회사 고객 3심 진행중 200 "
        "(*1) 해당 소송으로 인한 자원의 유출금액은 불확실하다."
    )
    assert not litigation_footnote_scope_problem(
        "공사대금청구취소 소송의 자원의 유출금액은 불확실하다.", {"a": source})
    for candidate in (
        "공사대금청구 소송의 자원의 유출금액은 불확실하다.",
        "공사대금청구취소와 공사대금청구 소송의 자원의 유출금액은 불확실하다.",
    ):
        assert litigation_footnote_scope_problem(candidate, {"a": source})

@pytest.mark.parametrize("separator", (" ", "\n"))
def test_종결점없는_여러각주도_다음각주의평가를_빌리지않는다(separator):
    source = (
        "구분 계류법원 원고 피고 진행상황 소송금액 "
        "제품보증청구 (*1) 수원지방법원 회사 고객 1심 진행중 100 "
        "납품대금청구 (*2) 대법원 회사 고객 3심 진행중 200 "
        "(*1) 해당 소송에 대하여 의견을 제출하였다" + separator +
        "(*2) 해당 소송으로 인한 자원의 유출금액은 불확실하다."
    )
    rows, notes = _litigation_rows_and_notes(source)
    assert len(rows) == 2 and "자원의유출금액" not in notes["(*1)"]
    assert litigation_footnote_scope_problem("제품보증청구 소송의 자원의 유출금액은 불확실하다.", {"a": source})
    assert not litigation_footnote_scope_problem("납품대금청구 소송의 자원의 유출금액은 불확실하다.", {"a": source})

def test_명시열과행구분자가있는_같은표도_각주적용행에_결속한다():
    source = (
        "구분 | 계류법원 | 원고 | 피고 | 진행상황 | 소송금액; "
        "제품보증청구 | 수원지방법원 | 회사 | 고객 | 1심 진행중 | 100; "
        "납품대금청구 (*1) | 대법원 | 회사 | 고객 | 3심 진행중 | 200; "
        "(*1) 해당 소송으로 인한 자원의 유출금액은 불확실하다."
    )
    rows, _ = _litigation_rows_and_notes(source)
    assert len(rows) == 2 and all(source[row.start:row.end] == row.raw_row for row in rows)
    assert litigation_footnote_scope_problem("계류 중인 소송의 자원의 유출금액은 불확실하다.", {"a": source})
    assert not litigation_footnote_scope_problem("납품대금청구 소송의 자원의 유출금액은 불확실하다.", {"a": source})
