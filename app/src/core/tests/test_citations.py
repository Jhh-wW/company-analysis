"""화면·워드·노션이 함께 쓰는 출처 번호 해석 회귀시험."""

from src.core.citations import citation_marker, citation_number, location_display


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


# ══════════════════════════════════════════════════════════
# location_display — 뉴스 조각 내부 id가 부록 「원문 위치」 칸에 새는 문제(F-3)
# ══════════════════════════════════════════════════════════
#
# ★ 왜 필요한가 (2026-09-10 PDF 8쪽 실측) — 뉴스 245·247행의 「원문 위치」
#   칸이 `기사 본문 · news-fragment-a8c65ad4e1b333d6dbc5`처럼 내부 조각 id를
#   그대로 보여줬다. 같은 칸의 다른 행은 `수익인식`·`MD&A`처럼 사람이 읽는
#   값이다. 이 함수는 «화면에 인쇄할 문구»만 바꾸고 저장된 location 값
#   자체(해시·봉인이 참조)는 건드리지 않는다.


def test_news_fragment_identifier_is_hidden_from_display_location():
    raw = "기사 본문 · news-fragment-a8c65ad4e1b333d6dbc5"
    assert location_display(raw) == "기사 본문"


def test_human_readable_locations_are_preserved():
    """공시 위치(예: 「MD&A」·「33753-34204」)는 애초에 내부 id가 없다."""

    assert location_display("MD&A") == "MD&A"
    assert location_display("33753-34204") == "33753-34204"
    assert location_display("") == ""


def test_other_location_suffixes_are_preserved():
    """접미사가 16진수 조각 id 모양이 아니면 헛짐작하지 않고 그대로 둔다."""

    assert location_display("기사 본문 · 별도 참고") == "기사 본문 · 별도 참고"
