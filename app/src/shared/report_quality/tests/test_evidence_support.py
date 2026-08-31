from __future__ import annotations

from src.shared.report_quality.evidence_support import (
    normalized_support_terms,
    prose_evidence_support_ready,
)


def test_일반산문은_서로다른_공통근거어가_두개_필요하다() -> None:
    assert not prose_evidence_support_ready("verified_prose", ())
    assert not prose_evidence_support_ready("verified_prose", ("고객", " 고객 "))
    assert prose_evidence_support_ready(
        "verified_prose", ("고객", "공식 채널")
    )


def test_Unicode와_대소문자만_바꾼_중복은_한개로_센다() -> None:
    assert normalized_support_terms(("ＡＢＣ", "abc", "가", "회사")) == (
        "abc",
        "회사",
    )


def test_구조화수치사실은_근거어대신_NumericBinding을_쓴다() -> None:
    assert prose_evidence_support_ready("historical_performance", ())
