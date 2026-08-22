from __future__ import annotations

import pytest

from src.shared.company_identity import (
    exact_company_names_equivalent,
    latin_acronym_korean,
    normalized_latin_acronym,
    verified_official_company_names_equivalent,
)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("삼성전자", "삼성전자(주)"),
        ("삼성전자", "(주) 삼성전자"),
        ("삼성전자", "주식회사 삼성전자"),
        ("삼성전자", "삼성전자 주식회사"),
        ("삼성전자", "삼성전자㈜"),
        ("테스트", "테스트 유한회사"),
        ("JYP Ent.", "JYP Ent. (주)"),
    ],
)
def test_법인표지만_다른_공식명은_exact_등가다(left, right):
    assert exact_company_names_equivalent(left, right)
    assert exact_company_names_equivalent(right, left)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("삼성전자", "삼성전기(주)"),
        ("삼성전자", "(주)삼성전자서비스"),
        ("JYP Ent.", "JYP Entertainment (주)"),
        ("SM", "ЅМ"),
        ("JYP", "JҮP"),
        ("AG", "ΑG"),
        ("삼성전자", "삼성전자Α"),
        ("삼성전자", "삼성전자ᄀ"),
        ("삼성전자", "삼성전자ㄱ"),
        ("삼성전자", "삼성전자ㅏ"),
        ("", "(주)"),
    ],
)
def test_exact_등가는_별칭과_유사문자를_거부한다(left, right):
    assert not exact_company_names_equivalent(left, right)


@pytest.mark.parametrize("value", ["JYP", "jyp", "J.Y.P.", "ｊｙｐ"])
def test_짧은_라틴약어의_정규화와_한글독음은_결정적이다(value):
    assert normalized_latin_acronym(value) == "JYP"
    assert latin_acronym_korean(value) == "제이와이피"


@pytest.mark.parametrize("value", ["JYP엔터", "JY P", "JҮP", "TOO-LONG"])
def test_약어계약은_혼합이름_공백_유사문자_긴표현을_거부한다(value):
    assert normalized_latin_acronym(value) == ""
    assert latin_acronym_korean(value) == ""


@pytest.mark.parametrize(
    ("left", "right", "identity"),
    [
        (
            "SK하이닉스",
            "에스케이하이닉스(주)",
            {
                "observed_corp_code": "00164779",
                "expected_corp_code": "00164779",
            },
        ),
        (
            "LG전자",
            "엘지전자 주식회사",
            {"observed_stock_code": "066570", "expected_stock_code": "066570"},
        ),
    ],
)
def test_식별번호가_같을때만_공식_영문약어의_한글독음을_허용한다(
    left, right, identity
):
    assert verified_official_company_names_equivalent(left, right, **identity)
    assert verified_official_company_names_equivalent(right, left, **identity)


@pytest.mark.parametrize(
    ("left", "right", "identity"),
    [
        ("SK하이닉스", "에스케이하이닉스(주)", {}),
        (
            "SK하이닉스",
            "에스케이하이닉스(주)",
            {"observed_corp_code": "00164779", "expected_corp_code": "00126380"},
        ),
        (
            "SK하이닉스",
            "에스케이하이닉스(주)",
            {
                "observed_corp_code": "00164779",
                "expected_corp_code": "00164779",
                "observed_stock_code": "000660",
                "expected_stock_code": "005930",
            },
        ),
        (
            "sk하이닉스",
            "에스케이하이닉스(주)",
            {
                "observed_corp_code": "00164779",
                "expected_corp_code": "00164779",
            },
        ),
        (
            "SK하이닉스",
            "에스케이텔레콤(주)",
            {
                "observed_corp_code": "00164779",
                "expected_corp_code": "00164779",
            },
        ),
        (
            "SΚ하이닉스",
            "에스케이하이닉스(주)",
            {
                "observed_corp_code": "00164779",
                "expected_corp_code": "00164779",
            },
        ),
    ],
)
def test_공식명_약어등가는_식별충돌_소문자_타사명_유사문자를_거부한다(
    left, right, identity
):
    assert not verified_official_company_names_equivalent(left, right, **identity)
