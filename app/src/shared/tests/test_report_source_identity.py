"""DART 원문을 저장하지 않는 출처 지문의 정규화 계약."""

from __future__ import annotations

import pytest

from src.shared.report_source_identity import (
    ReportSourceIdentity,
    ReportSourceIdentityError,
    dart_receipt_numbers_from_filing,
    financial_payload_digest,
)


def _financial_payload(amount: str = "100") -> dict[str, object]:
    return {
        "status": "000",
        "message": "정상",
        "list": [
            {"account_nm": "매출액", "thstrm_amount": amount, "ord": "1"},
            {"account_nm": "영업이익", "thstrm_amount": "20", "ord": "2"},
        ],
    }


def test_재무지문은_설명과_행순서가_달라도_같고_금액정정은_달라진다():
    reordered = {
        "list": [
            {"ord": "2", "thstrm_amount": "20", "account_nm": "영업이익"},
            {"ord": " 1 ", "account_nm": "매출액", "thstrm_amount": "100"},
        ],
        "message": "공급자 설명 문구 변경",
        "status": "000",
    }

    assert financial_payload_digest(_financial_payload()) == financial_payload_digest(
        reordered
    )
    assert financial_payload_digest(_financial_payload()) != financial_payload_digest(
        _financial_payload("101")
    )


def test_본문과_정정접수번호는_정해진키에서만_중복없이_뽑는다():
    filing = {
        "rcept_no": "20260315000001",
        "source_identity_rcept_nos": [
            "20260315000002",
            "20260315000001",
        ],
        # 다른 14자리 숫자를 무작정 훑으면 회사번호까지 섞인다.
        "unrelated_number": "20260315999999",
    }

    assert dart_receipt_numbers_from_filing(filing) == (
        "20260315000001",
        "20260315000002",
    )


@pytest.mark.parametrize(
    "filing",
    (
        {"rcept_no": "20260315"},
        {"rcept_no": "2026031500000A"},
        {"source_identity_rcept_nos": "20260315000001"},
    ),
)
def test_잘못된_DART접수번호를_조용히_버리지않는다(filing):
    with pytest.raises(ReportSourceIdentityError):
        dart_receipt_numbers_from_filing(filing)


def test_실제_공시와_재무가_둘다_있을때만_캐시사용가능하다():
    complete = ReportSourceIdentity.capture(
        filing={"rcept_no": "20260315000001"},
        financial_payload=_financial_payload(),
    )
    no_finance = ReportSourceIdentity.capture(
        filing={"rcept_no": "20260315000001"},
        financial_payload=None,
    )

    assert complete.cache_usable is True
    assert no_finance.cache_usable is False
    assert len(complete.cache_digest) == 64
    assert no_finance.cache_digest == ""


def test_캐시지문은_공시나_재무_어느쪽이_바뀌어도_달라진다():
    original = ReportSourceIdentity.capture(
        filing={"rcept_no": "20260315000001"},
        financial_payload=_financial_payload("100"),
    )
    corrected_filing = ReportSourceIdentity.capture(
        filing={"rcept_no": "20260315000002"},
        financial_payload=_financial_payload("100"),
    )
    corrected_finance = ReportSourceIdentity.capture(
        filing={"rcept_no": "20260315000001"},
        financial_payload=_financial_payload("101"),
    )

    assert len({
        original.cache_digest,
        corrected_filing.cache_digest,
        corrected_finance.cache_digest,
    }) == 3


def test_FULL_캐시지문은_공식자료_snapshot이_바뀌면_달라진다():
    identity = ReportSourceIdentity(
        dart_receipt_numbers=("20260315000123",),
        financial_payload_digest="a" * 64,
    )

    first = identity.cache_digest_with_official_snapshot("b" * 64)
    second = identity.cache_digest_with_official_snapshot("c" * 64)

    assert len(first) == 64
    assert first != second
    assert first == identity.cache_digest_with_official_snapshot("b" * 64)


def test_FULL_캐시지문은_기본신원이나_공식자료를_확정못하면_비운다():
    complete = ReportSourceIdentity(
        dart_receipt_numbers=("20260315000123",),
        financial_payload_digest="a" * 64,
    )
    partial = ReportSourceIdentity(
        dart_receipt_numbers=("20260315000123",),
    )

    assert complete.cache_digest_with_official_snapshot("") == ""
    assert complete.cache_digest_with_official_snapshot("not-a-sha") == ""
    assert partial.cache_digest_with_official_snapshot("b" * 64) == ""


def test_재무자료없음_생성신원은_접수번호와_공식snapshot으로만_선다():
    """감사보고서만 내는 비상장사(2026-09-05 인이지)는 재무 도장이 비어도 생성은 된다."""

    absent = ReportSourceIdentity(dart_receipt_numbers=("20260406001240",))

    first = absent.generation_digest_without_financials("b" * 64)

    assert len(first) == 64
    assert first == absent.generation_digest_without_financials("b" * 64)
    assert first != absent.generation_digest_without_financials("c" * 64)
    # 캐시 재사용 열쇠는 그대로 비어 있어야 한다 — 재무 도장 없는 옛 보고서를 돌려주지 않는다.
    assert absent.cache_usable is False
    assert absent.cache_digest_with_official_snapshot("b" * 64) == ""


def test_재무자료없음_생성신원은_재무도장이_있거나_접수번호나_snapshot이_없으면_비운다():
    complete = ReportSourceIdentity(
        dart_receipt_numbers=("20260406001240",),
        financial_payload_digest="a" * 64,
    )
    absent = ReportSourceIdentity(dart_receipt_numbers=("20260406001240",))
    nothing = ReportSourceIdentity()

    assert complete.generation_digest_without_financials("b" * 64) == ""
    assert absent.generation_digest_without_financials("") == ""
    assert absent.generation_digest_without_financials("not-a-sha") == ""
    assert nothing.generation_digest_without_financials("b" * 64) == ""
    # 재무 도장이 있는 완전 신원의 FULL 지문과 절대 같지 않다.
    assert complete.cache_digest_with_official_snapshot(
        "b" * 64
    ) != absent.generation_digest_without_financials("b" * 64)
