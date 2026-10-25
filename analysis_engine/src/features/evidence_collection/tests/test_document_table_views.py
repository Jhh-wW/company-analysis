"""한 번 파싱한 두 표 관점이 종전의 독립 파싱과 같은 근거를 보존한다."""

from __future__ import annotations

import pytest

from features.evidence_collection import constants as c
from features.evidence_collection import dart_fetcher, source_context
from features.evidence_collection import table_text_constants as table_c
from features.evidence_collection.dart_fetcher import _markup_to_plain_text, _xml_to_plain_text
from features.evidence_collection.source_context import (
    SourceContextBudgetExceeded, parse_document_tables, table_source_contexts,
)
from features.evidence_collection.table_text import table_replacements


ACTOR = "주식회사 가상회사"
SMALL_TABLE = (
    "<TABLE><TR><TH>회사명</TH><TH>제품명</TH><TH>진행현황</TH></TR>"
    "<TR><TD>주식회사 다른회사</TD><TD>가상제품</TD><TD>개발 예정</TD></TR></TABLE>"
)


def _markup(body: str) -> str:
    return f'<DOCUMENT><COMPANY-NAME AREGCIK="00126380">{ACTOR}</COMPANY-NAME><BODY>{body}</BODY></DOCUMENT>'


def _contexts(markup: str, text: str, **kwargs):
    try:
        return ("ok", table_source_contexts(markup, text, document_actor=ACTOR, **kwargs))
    except SourceContextBudgetExceeded as error:
        return ("failed", str(error))


@pytest.mark.parametrize("body", [
    "<P>표 없는 원문입니다.</P>",
    SMALL_TABLE,
    SMALL_TABLE * 2,
    SMALL_TABLE.replace("가상제품", "원문 &amp; 엔터티 &#38; 보존"),
    SMALL_TABLE.replace("가상제품", "<B>가상</B><I>제품</I>"),
    SMALL_TABLE.replace("가상제품", "<B>20</B><B>24</B>"),
    SMALL_TABLE.replace("가상제품", "<TABLE><TR><TD>중첩</TD></TR></TABLE>"),
    SMALL_TABLE.replace("</TABLE>", ""),
    SMALL_TABLE.replace("</TD>", "", 1),
    SMALL_TABLE.replace("가상제품", "<script>불완전</script>"),
    SMALL_TABLE.replace("가상제품", "<!--보이지 않는 주석-->가상제품"),
    SMALL_TABLE.replace("가상제품", "가" * c.MAX_CANDIDATE_WINDOW_CHARS),
    SMALL_TABLE.replace("가상제품", "가" * (c.MAX_CANDIDATE_WINDOW_CHARS - 24)),
    SMALL_TABLE.replace("가상제품", "가" * source_context.c.MAX_CONTEXT_TABLE_CHARS),
    "<TABLE><TR><TD rowspan='2'>매출</TD><TD>100</TD></TR><TR><TD>200</TD></TR></TABLE>",
], ids=[
    "no-table", "small", "repeated", "entities", "inline-text", "inline-number",
    "nested", "unclosed-table", "unclosed-cell", "script", "comment",
    "plain-limit", "near-plain-limit", "context-limit", "spans",
])
def test_두_표의_상한과_실패상태와_원문좌표를_독립파싱과_동일하게_유지한다(body):
    markup = _markup(body)
    prior_text = _xml_to_plain_text(markup.encode("utf-8"))
    prior_contexts = _contexts(markup, prior_text)
    views = parse_document_tables(markup, replacement_window_chars=c.MAX_CANDIDATE_WINDOW_CHARS)
    text = _markup_to_plain_text(markup, views.replacements)
    assert text == prior_text
    assert views.replacements == table_replacements(markup, max_window_chars=c.MAX_CANDIDATE_WINDOW_CHARS)
    assert _contexts(markup, text, table_views=views) == prior_contexts


@pytest.mark.parametrize("limit_name,limit", [
    ("MAX_REPLACEMENTS_PER_DOCUMENT", 1),
    ("MAX_REPLACEMENT_CHARS_PER_DOCUMENT", 20),
    ("MAX_TABLES_PER_DOCUMENT", 1),
])
def test_평문_표의_누적상한은_주어표_관측을_조용히_지우지_않는다(monkeypatch, limit_name, limit):
    monkeypatch.setattr(table_c, limit_name, limit)
    markup = _markup(SMALL_TABLE * 2)
    prior_text = _xml_to_plain_text(markup.encode("utf-8"))
    views = parse_document_tables(markup, replacement_window_chars=c.MAX_CANDIDATE_WINDOW_CHARS)
    assert views.replacements == ()
    assert _markup_to_plain_text(markup, views.replacements) == prior_text
    assert _contexts(markup, prior_text, table_views=views) == _contexts(markup, prior_text)


def test_큰_주어표와_뒤따르는_작은_평문표를_각각의_상한으로_보존한다():
    large = SMALL_TABLE.replace("가상제품", "가" * (c.MAX_CANDIDATE_WINDOW_CHARS + 1))
    markup = _markup(large + SMALL_TABLE.replace("가상제품", "작은제품"))
    views = parse_document_tables(markup, replacement_window_chars=c.MAX_CANDIDATE_WINDOW_CHARS)
    assert len(views.replacements) == 1
    assert len(views.context_grids) == 2
    assert views.replacements == table_replacements(markup, max_window_chars=c.MAX_CANDIDATE_WINDOW_CHARS)


def test_파싱결과를_다른_원문에_재사용하지_않는다():
    markup = _markup(SMALL_TABLE)
    views = parse_document_tables(markup, replacement_window_chars=c.MAX_CANDIDATE_WINDOW_CHARS)
    with pytest.raises(ValueError, match="원문이 다릅니다"):
        table_source_contexts(markup + " ", ACTOR, document_actor=ACTOR, table_views=views)


def test_실제_fetcher는_표를_한번만_읽고_원문과_회사주어_문맥을_함께_반환한다(tmp_path, monkeypatch):
    path = tmp_path / "20260315000001.xml"
    markup = _markup(SMALL_TABLE)
    path.write_text(markup, encoding="utf-8")
    calls = []
    original = source_context._ContextTableParser.feed

    def counted_feed(parser, text):
        calls.append(text)
        return original(parser, text)

    monkeypatch.setattr(source_context._ContextTableParser, "feed", counted_feed)
    result = dart_fetcher.DartRuntimeFetcher(
        document_cache_dir=tmp_path,
        download_document_fn=lambda *args: path,
    ).fetch_document_text("20260315000001")
    assert result.state == c.ATTEMPT_STATE_OK
    assert result.text == _xml_to_plain_text(markup.encode("utf-8"))
    assert calls == [markup]
    assert result.source_contexts
    assert result.corp_code == "00126380"
    assert result.document_actor == ACTOR


def test_통합파서의_예외에도_기존_평문과_주어검증_경계를_적용한다(tmp_path, monkeypatch):
    path = tmp_path / "20260315000001.xml"
    markup = _markup(SMALL_TABLE)
    path.write_text(markup, encoding="utf-8")

    def failed_parse(*args, **kwargs):
        raise ValueError("시험용 파서 실패")

    monkeypatch.setattr(dart_fetcher, "parse_document_tables", failed_parse)
    result = dart_fetcher.DartRuntimeFetcher(
        document_cache_dir=tmp_path,
        download_document_fn=lambda *args: path,
    ).fetch_document_text("20260315000001")
    assert result.state == c.ATTEMPT_STATE_OK
    assert result.text == _xml_to_plain_text(markup.encode("utf-8"))
    assert result.source_contexts == table_source_contexts(markup, result.text, document_actor=ACTOR)


def test_법인표제가_없는_문서에는_불필요한_큰_주어표_파싱을_추가하지_않는다(tmp_path, monkeypatch):
    path = tmp_path / "20260315000001.xml"
    markup = f"<DOCUMENT><BODY>{SMALL_TABLE}</BODY></DOCUMENT>"
    path.write_text(markup, encoding="utf-8")

    def unexpected_parse(*args, **kwargs):
        pytest.fail("법인표제 없는 문서의 주어 표 파싱은 열리면 안 됩니다")

    monkeypatch.setattr(dart_fetcher, "parse_document_tables", unexpected_parse)
    result = dart_fetcher.DartRuntimeFetcher(
        document_cache_dir=tmp_path,
        download_document_fn=lambda *args: path,
    ).fetch_document_text("20260315000001")
    assert result.state == c.ATTEMPT_STATE_OK
    assert result.text == _xml_to_plain_text(markup.encode("utf-8"))
    assert result.source_contexts == ()
