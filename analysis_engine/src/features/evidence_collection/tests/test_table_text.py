"""공시 표의 보수적 행·셀 평문 보존 회귀."""

from __future__ import annotations

import re
import hashlib

import pytest

from features.evidence_collection.dart_fetcher import _xml_to_plain_text
from features.evidence_collection.segment import segment_document
from features.evidence_collection import table_text_constants as table_limits
from features.evidence_collection.table_text import table_replacements


def _legacy(raw: bytes) -> str:
    text = re.sub(r"<[^>]+>", "\n", raw.decode("utf-8"))
    text = re.sub(r"[ \t\x0b\f\r]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def test_작은_표의_머리글과_행은_원문_숫자_그대로_한_후보에_남는다() -> None:
    raw = (
        b"<DOC><P>outside before</P><TABLE>"
        b"<TR><TH>revenue type</TH><TH>amount</TH></TR>"
        b"<TR><TD>product</TD><TD>100</TD></TR>"
        b"<TR><TD>goods</TD><TD>200</TD></TR>"
        b"</TABLE><P>outside after</P></DOC>"
    )
    text = _xml_to_plain_text(raw)
    assert text.count("100") == text.count("200") == 1
    assert "300" not in text
    assert "outside before" in text and "outside after" in text
    candidates = segment_document(text)
    assert any(
        all(word in candidate.text for word in ("revenue type", "product", "goods", "100", "200"))
        for candidate in candidates
    )


def test_검증된_span은_동일_원셀_텍스트만_소유_격자에_복제한다() -> None:
    raw = (
        b"<TABLE><TR><TH rowspan='2'>header</TH><TH>first</TH></TR>"
        b"<TR><TD>second</TD></TR><TR><TD colspan='2'>last</TD></TR></TABLE>"
    )
    text = _xml_to_plain_text(raw)
    assert "header | first ; header | second ; last | last" in text
    assert text.count("header") == 2
    assert text.count("last") == 2


def test_병합_숫자셀의_반복은_원문에_없던_숫자나_좌표를_만들지_않는다() -> None:
    raw = (
        b"<TABLE><TR><TH rowspan='2'>2024</TH><TH>sales</TH></TR>"
        b"<TR><TD>100</TD></TR></TABLE>"
    )
    original_sha = hashlib.sha256(raw).hexdigest()
    text = _xml_to_plain_text(raw)
    assert text.count("2024") == 2
    visible = re.sub(r"<[^>]+>", " ", raw.decode("utf-8"))
    numbers = lambda value: set(re.findall(r"\d+(?:[.,]\d+)*", value))
    assert numbers(text) <= numbers(visible)
    assert hashlib.sha256(raw).hexdigest() == original_sha
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() != hashlib.sha256(
        _legacy(raw).encode("utf-8")
    ).hexdigest()
    for candidate in segment_document(text):
        assert candidate.text == text[candidate.start:candidate.end]
        assert candidate.end - candidate.start <= 4_096


def test_인라인_숫자_경계는_새_숫자나_독립_숫자로_해석하지_않고_표를_되돌린다() -> None:
    raw = (
        b"<TABLE><TR><TD><B>20</B><B>24</B></TD>"
        b"<TD><B>1</B><B>,000</B></TD>"
        b"<TD><B>1</B><B>.5</B></TD></TR></TABLE>"
    )
    assert table_replacements(raw.decode("utf-8"), max_window_chars=4_096) == ()
    assert _xml_to_plain_text(raw) == _legacy(raw)


def test_장식_태그로_나뉜_한글_낱말은_같은_셀에서만_복원한다() -> None:
    raw = (
        "<TABLE><TR><TD><B>매출</B><I>유형</I></TD><TD>항목</TD></TR>"
        "<TR><TD><B>제</B><B>품</B></TD><TD>100</TD></TR>"
        "<TR><TD><B>상</B><B>품</B></TD><TD>200</TD></TR></TABLE>"
    ).encode("utf-8")
    text = _xml_to_plain_text(raw)
    assert "매출유형" in text and "제품" in text and "상품" in text
    assert text.count("100") == text.count("200") == 1


def test_표가_없으면_기존_정규화가_바이트_동일하다() -> None:
    raw = b"<DOC><TITLE>A</TITLE><P> one  two </P><P>three</P></DOC>"
    assert _xml_to_plain_text(raw) == _legacy(raw)


def test_중첩_미종결_잘못된_span_큰표는_기존_변환으로_되돌린다() -> None:
    cases = (
        b"<TABLE><TR><TD>outer<TABLE><TR><TD>inner</TD></TR></TABLE></TD></TR></TABLE>",
        b"<TABLE><TR><TD>unclosed</TD>",
        b"<TABLE><TR><TD rowspan='0'>bad</TD></TR></TABLE>",
        b"<TABLE><TR><TD>bad closure</TH></TR></TABLE>",
        b"<TABLE>important condition<TR><TD>A</TD></TR></TABLE>",
        b"<TABLE><TR><TD rowspan='2'>missing next row</TD></TR></TABLE>",
        b"<TABLE><TR><TD rowspan='2'>A</TD><TD>B</TD></TR>"
        b"<TR><TD colspan='2'>overlap</TD></TR></TABLE>",
        b"<TABLE><TR><TD colspan='33'>wide</TD></TR></TABLE>",
        b"<TABLE><TR><TD>" + b"x" * 4_100 + b"</TD></TR></TABLE>",
    )
    for raw in cases:
        assert table_replacements(raw.decode("utf-8"), max_window_chars=4_096) == ()
        assert _xml_to_plain_text(raw) == _legacy(raw)


def test_다른_비표_본문은_표_정규화_전후에_같은_글자열을_유지한다() -> None:
    before = "비표 첫 문단 123\n둘째 줄"
    after = "비표 마지막 문단 456"
    raw = (
        f"<DOC><P>{before}</P><TABLE><TR><TD>header</TD></TR>"
        f"<TR><TD>row</TD></TR></TABLE><P>{after}</P></DOC>"
    ).encode("utf-8")
    text = _xml_to_plain_text(raw)
    assert before in text
    assert after in text
    assert text.count("123") == text.count("456") == 1


@pytest.mark.parametrize(
    "limit_name, limit_value",
    (
        ("MAX_TABLES_PER_DOCUMENT", 1),
        ("MAX_REPLACEMENTS_PER_DOCUMENT", 1),
        ("MAX_REPLACEMENT_CHARS_PER_DOCUMENT", 3),
    ),
)
def test_문서전체_표_예산초과는_부분대체없이_기존_변환으로_되돌린다(
    monkeypatch: pytest.MonkeyPatch, limit_name: str, limit_value: int,
) -> None:
    raw = (
        b"<DOC><TABLE><TR><TD>first value</TD></TR></TABLE>"
        b"<P>outside</P>"
        b"<TABLE><TR><TD>second value</TD></TR></TABLE></DOC>"
    )
    monkeypatch.setattr(table_limits, limit_name, limit_value)
    assert table_replacements(raw.decode("utf-8"), max_window_chars=4_096) == ()
    assert _xml_to_plain_text(raw) == _legacy(raw)
