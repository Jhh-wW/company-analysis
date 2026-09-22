"""실제 메디라인 산문과 보도 표의 반복을 없애고 출처·봉인을 함께 지킨다."""

from dataclasses import replace

import pytest

from src.features.composer.constants import DEFAULT_CITATION_STYLE, SECTION_IDS
from src.features.composer.news_block import article_row
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence, NewsRow
from src.features.composer.render import _news_report_table, render_report


MEDILINE_NEWS = "2026-02-24 edaily.co.kr 보도에 따르면, 메디라인액티브코리아는 친환경 원료인 폴리우레탄(Polyurethane)을 소재로 한 수액세트를 국내 최초로 허가받아 의료현장에 공급하며, 환경호르몬 우려를 낮춘 수액세트 보급에 앞장서 왔다."
MEDILINE_NEWS_BODY = "메디라인액티브코리아는 친환경 원료인 폴리우레탄(Polyurethane)을 소재로 한 수액세트를 국내 최초로 허가받아 의료현장에 공급하며, 환경호르몬 우려를 낮춘 수액세트 보급에 앞장서 왔다."
OTHER_NEWS = "별도 보도는 해외 물류센터 신설을 다룬다."


def _row(text, citation="57", date="2026-02-24"):
    return NewsRow((date, "이데일리 · 수액세트 공급", text), (citation,))


def _section(body, rows):
    return ComposedSection(
        "portfolio", (ComposedSentence(body, ("57",), "확인"),), news_rows=tuple(rows),
    )


@pytest.mark.parametrize("body, news", (
    (MEDILINE_NEWS, MEDILINE_NEWS),
    (MEDILINE_NEWS, MEDILINE_NEWS_BODY),
    (MEDILINE_NEWS_BODY, MEDILINE_NEWS),
    (MEDILINE_NEWS, MEDILINE_NEWS.replace(",", "，").replace(" ", "\n")),
))
def test_산문과_같거나_어느_쪽에_포함되는_행만_빼고_캡션을_다시_센다(body, news):
    surviving = _row(OTHER_NEWS, "58", "2025-09-04")
    section = _section(body, (_row(news), surviving))

    table = _news_report_table(section, {"57": 57, "58": 58}, as_of_date="2026-09-22")

    assert table is not None
    assert table.rows == [list(surviving.cells)]
    assert table.caption == "관련 보도 1건 · 2025년 보도 포함"
    assert table.cite == "[58]"
    assert table.source_cites == ["[58]"]
    assert len(section.news_rows) == 2


def test_모든_행이_산문과_겹치면_표를_그리지_않는다():
    section = _section(MEDILINE_NEWS, (_row(MEDILINE_NEWS),))
    assert _news_report_table(section, {"57": 57}) is None


def test_부분문자열이_아니어도_3그램이_충분히_겹치면_빼준다():
    news = MEDILINE_NEWS.replace("2026-02-24", "2026년 2월 24일").replace("보도에 따르면,", "보도했다.")
    assert news not in MEDILINE_NEWS and MEDILINE_NEWS not in news
    assert _news_report_table(_section(MEDILINE_NEWS, (_row(news),)), {"57": 57}) is None


@pytest.mark.parametrize("news, removed", (("abcdefghijXY", True), ("abcdefghiXYZ", False)))
def test_3그램_교집합을_짧은_집합으로_나눈_겹침_경계는_0_8이다(news, removed):
    # 원문 지문 열 개 중 여덟 개면 제거하고 일곱 개면 보존한다.
    section = _section("abcdefghijkl", (_row(news),))
    table = _news_report_table(section, {"57": 57})
    assert (table is None) is removed


def test_다른_장_산문과_같은_행은_지우지_않는다():
    section = ComposedSection("identity", (), news_rows=(_row(MEDILINE_NEWS),))
    other = _section(MEDILINE_NEWS, ())
    report = render_report(
        "메디라인액티브코리아", ComposedReport((section, other)),
        {57: {"종류": "사업내용", "원문": MEDILINE_NEWS}}, None,
    )
    assert report.sections[0].tables[0].rows[0][-1] == MEDILINE_NEWS


def test_빠진_옛_기사의_발행연도를_캡션에_남기지_않는다():
    section = _section(MEDILINE_NEWS, (
        _row(MEDILINE_NEWS, date="2023-02-24"),
        _row(OTHER_NEWS, "58", "2026-09-04"),
    ))
    table = _news_report_table(section, {"57": 57, "58": 58}, as_of_date="2026-09-22")
    assert table.caption == "관련 보도 1건"


@pytest.mark.parametrize("all_removed", (False, True))
def test_FULL_봉인과_렌더가_같은_생존행을_쓰고_빠진_행의_출처는_부록에_남지_않는다(
    monkeypatch, all_removed,
):
    from src.core import news_intake_switch
    from src.features.composer.public_manifest import (
        assert_report_matches_public_structure, build_public_structure_seal,
    )
    from src.features.composer.tests.test_news_block_channels import _news_fragment

    duplicate = _news_fragment("57", "2026-02-24", MEDILINE_NEWS_BODY)
    body_source = replace(duplicate, fragment_id="59")
    different = _news_fragment("58", "2025-09-04", OTHER_NEWS)
    fragments = (duplicate, body_source, different)
    rows = (article_row((duplicate,), MEDILINE_NEWS),)
    if not all_removed:
        rows += (article_row((different,), MEDILINE_NEWS),)
    composed = ComposedReport(tuple(
        ComposedSection(
            section_id,
            (ComposedSentence(MEDILINE_NEWS, ("59",), "확인"),) if section_id == "portfolio" else (),
            news_rows=rows if section_id == "portfolio" else (),
        )
        for section_id in SECTION_IDS
    ))
    news_intake_switch._reset_process_news_intake_switch_for_tests()
    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
    try:
        seal = build_public_structure_seal(
            composed, fragments, None, filing_meta=None, composition_tables=(),
            table_presentation="table", company_id="00123456",
            evidence_generation_sha256="a" * 64,
            evidence_packet_sha256s=tuple(
                (section_id, f"{index:02d}" * 32)
                for index, section_id in enumerate(SECTION_IDS, start=1)
            ),
            company_name="메디라인액티브코리아", corp_type="", generated_at="",
            as_of_date="2026-09-22", analysis_period="", latest_performance_period="",
            citation_style=DEFAULT_CITATION_STYLE,
        )
        rendered = render_report(
            "메디라인액티브코리아", composed, fragments, None,
            as_of_date="2026-09-22", public_structure_seal=seal, company_id="00123456",
        )
        assert_report_matches_public_structure(rendered, seal)
        section = next(item for item in rendered.sections if item.cell == "portfolio")
        if all_removed:
            assert section.tables == []
            assert {source.number for source in rendered.citations} == {59}
        else:
            assert section.tables[0].rows == [list(rows[1].cells)]
            assert section.tables[0].caption == "관련 보도 1건 · 2025년 보도 포함"
            assert section.tables[0].source_cites == ["[58]"]
            assert {source.number for source in rendered.citations} == {58, 59}
    finally:
        news_intake_switch._reset_process_news_intake_switch_for_tests()
