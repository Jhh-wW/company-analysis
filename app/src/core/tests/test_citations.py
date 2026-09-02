"""화면·워드·노션이 함께 쓰는 출처 번호 해석 회귀시험."""

from src.core.citations import citation_marker, citation_number


def test_내부_출처에서_실제_번호만_꺼낸다():
    assert citation_number("조각 9·뉴스") == "9"
    assert citation_number("9") == "9"
    assert citation_number("[9]") == "9"
    assert citation_number("〔9〕") == "9"


def test_출처가_아닌_숫자를_추측하지_않는다():
    assert citation_number("2026년 사업보고서") == ""
    assert citation_number("조각 0·뉴스") == ""
    assert citation_number("") == ""


def test_내보내는_표기에는_내부_이름이_없다():
    marker = citation_marker("조각 9·뉴스")
    assert marker == "〔9〕"
    assert "조각" not in marker
