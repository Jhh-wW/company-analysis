from __future__ import annotations

from dataclasses import dataclass

from src.features.report_quality.source_identity import (
    canonical_url,
    document_identity,
    document_identity_from_parts,
)


@dataclass(frozen=True)
class _IdentityInput:
    document_id: str
    host: str
    url: str


def test_같은_공시문서의_서로다른_url조각은_한_identity다() -> None:
    first = _IdentityInput(
        document_id="20250828000123",
        host="DART.FSS.OR.KR",
        url="https://dart.fss.or.kr/report/viewer.do?rcpNo=20250828000123&dcmNo=1#part-a",
    )
    second = _IdentityInput(
        document_id="20250828000123",
        host="dart.fss.or.kr",
        url="https://dart.fss.or.kr/report/viewer.do?dcmNo=2&rcpNo=20250828000123#part-b",
    )

    assert document_identity(first) == document_identity(second)


def test_문서id가_없으면_fragment를_버린_canonical_url을_쓴다() -> None:
    source = _IdentityInput(
        document_id="",
        host="example.com",
        url="HTTPS://Example.COM/ir/report/?b=2&a=1#page-3",
    )

    assert canonical_url(source.url) == "https://example.com/ir/report?a=1&b=2"
    assert document_identity(source) == "url:https://example.com/ir/report?a=1&b=2"


def test_자료형과_개별필드는_같은_문서_identity를_만든다() -> None:
    source = _IdentityInput(
        document_id="20250828000123",
        host="DART.FSS.OR.KR",
        url="https://dart.fss.or.kr/report/viewer.do?rcpNo=20250828000123",
    )

    assert document_identity_from_parts(
        document_id=source.document_id,
        host=source.host,
        url=source.url,
    ) == document_identity(source)


def test_추적문자열_기본포트_호스트끝점은_문서수를_부풀리지_않는다() -> None:
    first = canonical_url(
        "https://Example.com.:443/ir/report/?a=1&utm_source=mail&fbclid=tracking"
    )
    second = canonical_url("https://example.com/ir/report?a=1")

    assert first == second == "https://example.com/ir/report?a=1"


def test_선언한_host와_실제_url_host가_다르면_문서신원으로_인정하지_않는다() -> None:
    assert document_identity_from_parts(
        document_id="20250828000123",
        host="dart.fss.or.kr",
        url="https://evil.example/view?rcpNo=20250828000123",
    ) == ""


def test_DART문서id가_url에_없으면_같은_공시라고_꾸밀수_없다() -> None:
    assert document_identity_from_parts(
        document_id="20250828000123",
        host="dart.fss.or.kr",
        url="https://dart.fss.or.kr/report/viewer.do?rcpNo=DIFFERENT",
    ) == ""


def test_http외_scheme와_userinfo_url은_신원으로_인정하지_않는다() -> None:
    assert canonical_url("javascript:alert(1)") == ""
    assert canonical_url("https://user:password@example.com/report") == ""
