"""실제 PDF 문장과 인용 경계를 지키는 최종 문체·시점 회귀."""

import pytest

from src.features.composer.style_normalizer import (
    annotate_past_dated_future, normalize_sentence_style,
)


MEDILINE_SENTENCE = (
    "당사는 부채상환을 포함하여 합리적으로 예상되는 영업자금수요를 충당할 수 있는 "
    "유동성을 예측하고 관리하고 있습니다."
)
MEDILINE_NORMALIZED = (
    "당사는 부채상환을 포함하여 합리적으로 예상되는 영업자금수요를 충당할 수 있는 "
    "유동성을 예측하고 관리하고 있다."
)
WRTN_SENTENCE = (
    "회사의 재무제표는 일반기업회계기준에 따라 중요성의 관점에서 공정하게 표시되고 있으며, "
    "2026년 3월 31일자 주주총회에서 최종 승인될 예정이다."
)


def test_메디라인_PDF_실제_문장을_한다체로_바꾼다():
    assert normalize_sentence_style(MEDILINE_SENTENCE) == MEDILINE_NORMALIZED


@pytest.mark.parametrize(("original", "expected"), (
    ("관리를 하고 있습니다.", "관리를 하고 있다."),
    ("관리합니다.", "관리한다."),
    ("확정됩니다.", "확정된다."),
    ("기업입니다.", "기업이다."),
    ("자산이 있습니다.", "자산이 있다."),
    ("부채가 없습니다.", "부채가 없다."),
    ("확인했습니다.", "확인했다."),
    ("승인됐습니다.", "승인됐다."),
    ("목표였습니다.", "목표였다."),
    ("변하지 않습니다.", "변하지 않는다."),
))
def test_닫힌_어미_치환표(original, expected):
    assert normalize_sentence_style(original) == expected
    assert normalize_sentence_style(expected) == expected


@pytest.mark.parametrize("text", (
    "관리합니다", "관리합니다!", "관리합니다?", "관리합니다만 아직이다.",
    "승인되었습니다.", "기업이었습니다.", "확인했다.", "", "합니다, 됩니다.",
))
def test_마침표_종결_어미_이외에는_추정하지_않는다(text):
    expected = "합니다, 된다." if text == "합니다, 됩니다." else text
    assert normalize_sentence_style(text) == expected


@pytest.mark.parametrize(("opening", "closing"), (
    ("「", "」"), ("“", "”"), ("'", "'"), ('"', '"'), ("‘", "’"),
))
def test_따옴표_안과_인용_번호_해석_표지를_보존한다(opening, closing):
    original = f"{opening}관리합니다. 자산이 있습니다.{closing}라고 설명합니다. [12][3] — 해석"
    expected = f"{opening}관리합니다. 자산이 있습니다.{closing}라고 설명한다. [12][3] — 해석"
    assert normalize_sentence_style(original) == expected


def test_중첩_인용과_닫히지_않은_인용도_보존한다():
    text = "「‘합니다.’라고 말합니다.」라고 합니다. “미완성입니다."
    assert normalize_sentence_style(text) == (
        "「‘합니다.’라고 말합니다.」라고 한다. “미완성입니다."
    )


@pytest.mark.parametrize(("baseline", "count"), (
    ("2026-09-22", 1), ("2026-03-01", 0), ("2026-03-31", 0),
    ("2026-04-01", 1), ("", 0), ("잘못된 날짜", 0),
))
def test_뤼튼_PDF_실제_문장의_지난_일정은_원문_기준만_덧붙인다(baseline, count):
    text, actual_count = annotate_past_dated_future(WRTN_SENTENCE, baseline)
    assert actual_count == count
    assert text == WRTN_SENTENCE + (" (공시 원문 기준)" if count else "")


@pytest.mark.parametrize("ending", ("예정이다", "예정입니다", "할 계획이다"))
def test_월만_있는_지난_일정과_인용_마커를_보존한다(ending):
    text = f"2026년 3월 승인 {ending}. [12] — 해석"
    result, count = annotate_past_dated_future(text, "2026-09-22")
    assert result == f"2026년 3월 승인 {ending}. (공시 원문 기준) [12] — 해석"
    assert count == 1
    assert annotate_past_dated_future(result, "2026-09-22") == (result, 0)


@pytest.mark.parametrize("text", (
    "2026년 9월 출시 예정이다.",
    "2026년 13월 출시 예정이다.",
    "2026년 2월 30일자 승인 예정이다.",
    "2026년 3월 99일자 승인 예정이다.",
    "2026년 3월 31일 승인 예정이다.",
    "2026년 출시 예정이다.",
    "2026년 3월 승인 예정이었다.",
    "2026년 3월 승인 예정이라고 밝혔다.",
    "2026년 3월에 설립됐다. 출시 예정이다.",
    "“2026년 3월 승인 예정입니다.”라고 밝혔다.",
    "「2026년 3월」 자료에 따르면 승인 예정이다.",
))
def test_지난_일정이나_미래_종결이_확정되지_않으면_그대로_둔다(text):
    assert annotate_past_dated_future(text, "2026-09-22") == (text, 0)


def test_여러_문장의_일정을_각각_판정하고_안내를_중복하지_않는다():
    first = "2026년 3월 '제품' 출시 예정이다."
    second = "2026년 4월 승인할 계획이다."
    third = "2026년 12월 출시 예정이다."
    expected = f"{first} (공시 원문 기준) {second} (공시 원문 기준) {third}"
    assert annotate_past_dated_future(f"{first} {second} {third}", "2026-09-22") == (expected, 2)


def test_마침표가_없는_미래_종결도_날짜가_명확하면_표시한다():
    text = "2026년 3월 승인 예정이다 [12] — 해석"
    assert annotate_past_dated_future(text, "2026-09-22") == (
        "2026년 3월 승인 예정이다 (공시 원문 기준) [12] — 해석", 1,
    )
