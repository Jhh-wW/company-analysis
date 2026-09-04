"""도메인 이름이나 회사별 allowlist 없는 교차 도메인 신원 결속 시험."""

from __future__ import annotations

import pytest

from src.features.homepage.official_identity import (
    OfficialCompanyIdentity,
    normalize_registration_number,
    verify_official_company_identity,
    verify_official_company_identity_pages,
)


def _identity(**overrides: object) -> OfficialCompanyIdentity:
    values = {
        "legal_name": "주식회사 와이즐리컴퍼니",
        "aliases": ("Wisely Co., Ltd.",),
        "registration_numbers": ("123-45-67890", "110111-1234567"),
    }
    values.update(overrides)
    return OfficialCompanyIdentity(**values)


def test_DART_법인명과_사업자번호가_함께_있는_footer는_승인한다() -> None:
    html = """
    <html><main><h1>면도용품을 정직한 가격에 판매합니다</h1></main>
    <footer>(주) 와이즐리컴퍼니 · 사업자등록번호 123-45-67890</footer></html>
    """

    match = verify_official_company_identity(html, _identity())

    assert match is not None
    assert len(match.evidence_sha256) == 64
    assert "12345" not in match.evidence_sha256


def test_법인등록번호도_안정_식별자로_승인한다() -> None:
    html = """
    <html><footer>Wisely Co., Ltd. 법인등록번호 110111-1234567</footer></html>
    """

    assert verify_official_company_identity(html, _identity()) is not None


def test_같은_origin의_서로다른_페이지에_나뉜_이름과_번호를_승인한다() -> None:
    pages = (
        "<html><main>주식회사 와이즐리컴퍼니 회사 소개</main></html>",
        "<html><footer>사업자등록번호 123-45-67890</footer></html>",
    )

    assert verify_official_company_identity_pages(pages, _identity()) is not None


def test_페이지경계를_넘어_등록번호표지와_숫자를_이어붙이지_않는다() -> None:
    pages = (
        "<html><main>주식회사 와이즐리컴퍼니 사업자등록번호</main></html>",
        "<html><footer>123-45-67890</footer></html>",
    )

    assert verify_official_company_identity_pages(pages, _identity()) is None


@pytest.mark.parametrize(
    "html",
    (
        # 회사명만 있는 협력사·채용 솔루션 페이지
        "<html><p>주식회사 와이즐리컴퍼니 채용을 지원합니다.</p></html>",
        # 등록번호만 베낀 광고/디렉터리
        "<html><footer>사업자등록번호 123-45-67890</footer></html>",
        # 다른 회사의 번호
        "<html><footer>주식회사 와이즐리컴퍼니 사업자등록번호 999-99-99999</footer></html>",
        # 숫자가 우연히 있으나 registry 표지가 없음
        "<html><p>주식회사 와이즐리컴퍼니 주문번호 123-45-67890</p></html>",
        # 임의 글자를 숫자 사이 구분자로 받아 우연히 같은 10자리로 합치면 안 된다.
        "<html><p>주식회사 와이즐리컴퍼니 사업자등록번호 1a2b3c4d5e6f7g8h9i0</p></html>",
    ),
)
def test_이름과_안정번호_두가지_중_하나라도_없으면_거절한다(html: str) -> None:
    assert verify_official_company_identity(html, _identity()) is None


def test_등록번호를_못_받은_요청은_회사명이_정확해도_교차도메인을_승인하지_않는다() -> None:
    identity = _identity(registration_numbers=())
    html = "<html><footer>주식회사 와이즐리컴퍼니</footer></html>"

    assert identity.can_verify_cross_domain is False
    assert verify_official_company_identity(html, identity) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    (
        ("123-45-67890", "1234567890"),
        ("110111 1234567", "1101111234567"),
        ("123456789", ""),
        ("12345678901234", ""),
        ("not-a-number", ""),
        ("corp123-45-67890", ""),
        # ``isdigit``은 참이지만 DART 공개 등록번호/ASCII 영수증은 아니다.
        ("١٢٣٤٥٦٧٨٩٠", ""),
    ),
)
def test_등록번호는_DART_두_형식만_정규화한다(raw: str, expected: str) -> None:
    assert normalize_registration_number(raw) == expected


def test_일반_script에_숨긴_회사신원은_증거로_쓰지_않는다() -> None:
    html = """
    <html><script>const bait = '주식회사 와이즐리컴퍼니 사업자등록번호 123-45-67890';</script>
    <main>다른 회사의 공개 페이지</main></html>
    """

    assert verify_official_company_identity(html, _identity()) is None


def test_JSON_LD_조직정보는_신원증거로_쓸수있다() -> None:
    html = """
    <html><script type="application/ld+json">
    {"@type":"Organization","legalName":"주식회사 와이즐리컴퍼니",
     "identifier":"사업자등록번호 123-45-67890"}
    </script></html>
    """

    assert verify_official_company_identity(html, _identity()) is not None
