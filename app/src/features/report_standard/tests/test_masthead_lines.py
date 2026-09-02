"""표지 다음 첫 본문 페이지 마스트헤드 두 줄을 만드는 순수 함수 계약.

웹·PDF·Notion 세 채널이 각자 f-string으로 문장을 짓지 않고 이 함수 하나만
호출하게 하는 것이 목적이다(정본: D-S4a). 새 사실을 만들지 않는다 — 이미
검증된 ``report.company``·``report.generated_at``만 옮긴다.
"""

from __future__ import annotations

from dataclasses import replace

from src.features.export_pdf.logic import _display_generated_at
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.report_standard.section_content import masthead_lines


def test_masthead_lines는_회사명과_생성일을_글자_그대로_쓴다() -> None:
    report = replace(
        build_demo_report(),
        company="(주)진영",
        generated_at="2026-08-19",
    )

    company_line, meta_line = masthead_lines(report)

    assert company_line == "(주)진영"
    assert meta_line == "기업 분석 보고서 · 생성일 2026-08-19 · 공개 자료 기반"


def test_masthead_lines는_as_of_date가_아니라_generated_at을_읽는다() -> None:
    """표지 메타(``_cover_metadata``)의 「내용 생성」과 같은 필드를 써야 한다.

    ``as_of_date``와 ``generated_at``을 서로 다른 날로 벌려 두면, 잘못된
    필드를 읽는 회귀가 생겼을 때 이 시험이 실제로 빨간불이 된다(두 필드가
    우연히 같은 값이면 어느 쪽을 읽어도 통과해 버려서 못 잡는다).
    """

    report = replace(
        build_demo_report(),
        generated_at="2026-08-19",
        as_of_date="2026-08-20",
    )

    _company_line, meta_line = masthead_lines(report)

    assert "생성일 2026-08-19" in meta_line
    assert "2026-08-20" not in meta_line


def test_masthead_lines_둘째줄_생성일은_표지_메타_함수와_같은_날짜다() -> None:
    """PDF 표지(``_cover_metadata``)가 실제로 쓰는 판정 함수와 대조한다.

    형식(마침표 vs 대시)만 다르고 가리키는 날짜 자체는 항상 같아야 한다.
    """

    report = replace(build_demo_report(), generated_at="2026-08-19T09:30:00+09:00")

    _company_line, meta_line = masthead_lines(report)

    cover_generated = _display_generated_at(report)  # "2026.08.19" 형식
    assert cover_generated
    expected_dashes = cover_generated.replace(".", "-")
    assert f"생성일 {expected_dashes}" in meta_line


def test_masthead_lines는_생성일을_못_읽으면_둘째줄에서_그_부분만_뺀다() -> None:
    """옛 저장본처럼 ``generated_at``이 비었거나 ISO가 아니어도 회사명은 남는다."""

    report = replace(build_demo_report(), generated_at="")

    company_line, meta_line = masthead_lines(report)

    assert company_line == report.company
    assert meta_line == "기업 분석 보고서 · 공개 자료 기반"
    assert "생성일" not in meta_line


def test_masthead_lines는_한글_ISO_아닌_생성일_문자열도_안전하게_처리한다() -> None:
    report = replace(build_demo_report(), generated_at="확인 불가")

    company_line, meta_line = masthead_lines(report)

    assert company_line == report.company
    assert meta_line == "기업 분석 보고서 · 공개 자료 기반"
