"""formal 수집 문서 신원을 공개 URL에서 다시 검산하는 계약."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.shared.report_evidence.constants import (
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_OFFICIAL_WEB_PAGE,
)
from src.shared.report_quality.source_identity import (
    bind_declared_document_identity_to_url,
    collected_document_identity,
    document_identity,
    document_identity_from_parts,
)


@pytest.mark.parametrize(
    ("source_kind", "document_id", "host", "url", "expected"),
    (
        (SOURCE_KIND_OFFICIAL_WEB_PAGE, "collector:page-1", "example.com",
         "https://example.com/company", "url:https://example.com/company"),
        (SOURCE_KIND_DART_BUSINESS_REPORT, "dart_business_report:20260330000001",
         "dart.fss.or.kr", "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260330000001",
         "document:dart.fss.or.kr:20260330000001"),
        ("", "old-document", "example.com", "https://example.com/company",
         "document:example.com:old-document"),
        ("", "", "example.com", "https://example.com/company",
         "url:https://example.com/company"),
    ),
)
def test_객체와_JSON부분필드는_같은_문서신원을_만든다(
    source_kind: str, document_id: str, host: str, url: str, expected: str
) -> None:
    fields = {
        "formal_source_kind": source_kind,
        "document_id": document_id,
        "host": host,
        "url": url,
    }
    assert document_identity(SimpleNamespace(**fields)) == expected
    assert document_identity_from_parts(**fields) == expected


def test_공식웹은_수집기내부ID가_아니라_canonical_URL이_정본이다() -> None:
    first = collected_document_identity(
        source_kind=SOURCE_KIND_OFFICIAL_WEB_PAGE,
        document_id="collector-run-a",
        url="HTTPS://Example.COM/company/?utm_source=mail&b=2&a=1#history",
    )
    second = collected_document_identity(
        source_kind=SOURCE_KIND_OFFICIAL_WEB_PAGE,
        document_id="collector-run-b",
        url="https://example.com/company?a=1&b=2",
    )

    assert first == second == "url:https://example.com/company?a=1&b=2"


def test_같은_공식웹_내부ID라도_URL이_다르면_다른문서다() -> None:
    first = collected_document_identity(
        source_kind=SOURCE_KIND_OFFICIAL_WEB_PAGE,
        document_id="same-internal-id",
        url="https://example.com/company/business",
    )
    second = collected_document_identity(
        source_kind=SOURCE_KIND_OFFICIAL_WEB_PAGE,
        document_id="same-internal-id",
        url="https://example.com/company/recruit",
    )

    assert first and second and first != second


def test_DART는_접수번호와_원문URL이_같을때만_문서신원을_만든다() -> None:
    receipt_number = "20260330000001"
    source_url = (
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + receipt_number
    )

    identity = collected_document_identity(
        source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
        document_id=f"{SOURCE_KIND_DART_BUSINESS_REPORT}:{receipt_number}",
        url=source_url,
    )

    assert identity == f"document:dart.fss.or.kr:{receipt_number}"
    assert bind_declared_document_identity_to_url(identity, source_url) == identity
    assert (
        bind_declared_document_identity_to_url(
            identity,
            "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260330000002",
        )
        == ""
    )


def test_DART종류를_붙여도_다른host나_경로는_DART문서가_아니다() -> None:
    receipt_number = "20260330000001"

    for source_url in (
        f"https://attacker.example/dsaf001/main.do?rcpNo={receipt_number}",
        f"https://dart.fss.or.kr/other/path?rcpNo={receipt_number}",
        f"https://dart.fss.or.kr/dsaf001/main.do?other={receipt_number}",
    ):
        assert (
            collected_document_identity(
                source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
                document_id=f"{SOURCE_KIND_DART_BUSINESS_REPORT}:{receipt_number}",
                url=source_url,
            )
            == ""
        )


def test_등록되지않은_종류는_공식웹처럼_추측하지_않는다() -> None:
    assert (
        collected_document_identity(
            source_kind="공식 자료 비슷한 임의 문자열",
            document_id="document-1",
            url="https://example.com/company",
        )
        == ""
    )
