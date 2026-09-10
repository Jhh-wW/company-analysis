"""3장 제품·서비스명은 인용 조각에 있는 표면 문자열만 공개한다."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.features.composer.constants import (
    PORTFOLIO_TABLE_GUIDE_V2,
    PORTFOLIO_TABLE_SECTION_ID,
)
from src.features.composer.diagram_check import (
    PORTFOLIO_NAME_NOT_IN_SOURCE_CODE,
    check_diagram_numbers,
    portfolio_name_is_grounded,
)
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
)


def _fragment(
    fragment_id: str, text: str, *, document_identity: str = ""
) -> CollectedFragment:
    return CollectedFragment(
        fragment_id=fragment_id,
        kind="사업내용",
        text=text,
        document_identity=document_identity,
    )


def _report(
    portfolio_row: FlowRow,
    other_row: FlowRow | None = None,
) -> ComposedReport:
    sections = [
        ComposedSection(
            section_id=PORTFOLIO_TABLE_SECTION_ID,
            sentences=(
                ComposedSentence(
                    text="회사는 공식 제품을 운영한다.",
                    citations=portfolio_row.citations,
                    grade="확인",
                ),
            ),
            flow_rows=(portfolio_row,),
        )
    ]
    if other_row is not None:
        sections.append(
            ComposedSection(
                section_id="operations_partners",
                sentences=(),
                flow_rows=(other_row,),
            )
        )
    return ComposedReport(sections=tuple(sections))


def _portfolio_row(report: ComposedReport) -> FlowRow:
    section = next(
        item
        for item in report.sections
        if item.section_id == PORTFOLIO_TABLE_SECTION_ID
    )
    return section.flow_rows[0]


def test_인용_조각에_글자_그대로_있는_이름은_유지한다() -> None:
    row = FlowRow(
        cells=("카카오T", "모빌리티 서비스", "운영 확대", "주력"),
        citations=("1", "2"),
    )

    checked, problems = check_diagram_numbers(
        _report(row),
        (
            _fragment("1", "다른 제품 설명이다."),
            _fragment("2", "카카오T는 모빌리티 서비스를 운영한다."),
        ),
    )

    assert _portfolio_row(checked) == row
    assert problems == ()


def test_어느_인용_조각에도_없는_이름은_카드만_제외한다() -> None:
    cells = ("지어낸 이름", "공식 서비스 범위", "운영 확대", "주력")
    row = FlowRow(cells=cells, citations=("1",))

    checked, problems = check_diagram_numbers(
        _report(row), (_fragment("1", "공식 서비스 범위를 운영하고 있다."),)
    )

    assert checked.sections[0].flow_rows == ()
    assert checked.sections[0].sentences == _report(row).sections[0].sentences
    assert any(PORTFOLIO_NAME_NOT_IN_SOURCE_CODE in item for item in problems)


@pytest.mark.parametrize(
    ("source", "name"),
    (
        ("카카오 T를 운영한다.", "카카오T"),
        ("카카오-T를 운영한다.", "카카오 T"),
        ("ＡＢＣ 서비스를 운영한다.", "abc서비스"),
    ),
)
def test_호환문자_대소문자_공백_구두점_차이는_같은_이름이다(
    source: str, name: str
) -> None:
    row = FlowRow(
        cells=(name, "서비스 범위", "운영 확대", "주력"), citations=("1",)
    )

    checked, problems = check_diagram_numbers(
        _report(row), (_fragment("1", source),)
    )

    assert _portfolio_row(checked) == row
    assert problems == ()


def test_서로_다른_인용_조각의_경계에_걸친_이름은_접지가_아니다() -> None:
    row = FlowRow(
        cells=("카카오T", "서비스 범위", "운영 확대", "주력"),
        citations=("1", "2"),
    )

    checked, problems = check_diagram_numbers(
        _report(row),
        (_fragment("1", "카카오"), _fragment("2", "T를 운영한다.")),
    )

    assert checked.sections[0].flow_rows == ()
    assert any(PORTFOLIO_NAME_NOT_IN_SOURCE_CODE in item for item in problems)


def test_빈_이름_카드는_제외하지만_다른_장은_보존한다() -> None:
    empty_name = FlowRow(
        cells=("", "공식 범위", "운영 확대", "주력"), citations=("1",)
    )
    other_row = FlowRow(
        cells=("요약 이름", "공식 운영", "고객"), citations=("1",)
    )
    original = _report(empty_name, other_row)

    checked, problems = check_diagram_numbers(
        original, (_fragment("1", "공식 범위를 운영하며 고객에게 제공한다."),)
    )

    assert checked.sections[0].flow_rows == ()
    assert checked.sections[0].sentences == original.sections[0].sentences
    assert checked.sections[1].flow_rows == (other_row,)
    assert any(PORTFOLIO_NAME_NOT_IN_SOURCE_CODE in item for item in problems)


def test_missing_name_keeps_other_grounded_product() -> None:
    valid = FlowRow(cells=("공식 제품", "서비스", "운영", "주력"), citations=("1",))
    invalid = FlowRow(cells=("", "서비스", "운영", "주력"), citations=("1",))
    report = ComposedReport(sections=(ComposedSection(
        section_id=PORTFOLIO_TABLE_SECTION_ID, sentences=(), flow_rows=(invalid, valid),
    ),))
    checked, problems = check_diagram_numbers(
        report, (_fragment("1", "공식 제품 서비스를 운영한다."),),
    )
    assert checked.sections[0].flow_rows == (valid,)
    assert len(problems) == 1


# ══════════════════════════════════════════════════════════
# 괄호로 갈라진 이름 · 같은 문서 조각 (2026-09-11 소규모 회사 실측)
# ══════════════════════════════════════════════════════════

#: 실제 공시 원문 발췌(`원문_20260407002517.txt`). 손익계산서는 평문 7,057자
#: 부근, 회사 개요는 12,901자 부근이라 5,844자 떨어져 실행에서도 서로 다른
#: 조각이었다. 작가가 낸 이름 「제품(전기전자 제품 및 산업용 장비)」은 이 두
#: 조각에 부분이 «따로» 있어 통짜 부분문자열 잣대로는 접지에 실패했다.
_INCOME_STATEMENT_TEXT = (
    "Ⅰ. 매출액 27,351,053,389 25,811,194,484 "
    "1.상품매출 2,836,765,554 1,445,870,560 "
    "2.제품매출 24,514,287,835 24,365,323,924"
)
_COMPANY_OVERVIEW_TEXT = (
    "1. 당사의 개요 주식회사 인텍에프에이(이하 \"당사\")는 전기전자 제품 및 "
    "산업용 장비 등의 제조, 판매 및 전력전자제품 연구개발업을 주요 영업 "
    "목적으로 설립되었습니다."
)
_FILING_DOCUMENT = "dart.fss.or.kr:20260407002517"
_OTHER_FILING_DOCUMENT = "dart.fss.or.kr:20250331000594"

#: 회사를 가리는 힘을 재기 위한 «다른 회사» 원문. 이름 후보 파서가 이미 쓰고
#: 있는 저장소 안 공시 발췌를 그대로 쓴다(파이프라인 시험도 같은 자료를 쓴다).
_OTHER_COMPANY_DIR = (
    Path(__file__).resolve().parents[2] / "product_names" / "tests" / "fixtures"
)
#: (다른 회사 원문 파일, 그 회사가 «자기 원문에» 실제로 적은 이름)
_OTHER_COMPANY_CASES = (
    ("kakao_product_services.txt", "카카오톡"),
    ("samsung_product_services.txt", "메모리 반도체"),
    ("woori_named_services.txt", "WON 플러스 예금"),
    ("hybe_subsidiaries.txt", "㈜수퍼톤"),
)

#: 이 회사 공시에 실제로 근거가 있는 이름.
_GROUNDED_NAMES = (
    "제품(전기전자 제품 및 산업용 장비)",
    "전기전자 제품 및 산업용 장비",
    "상품",
    "제품매출",
)
#: 이 회사 공시에 없는 이름. 「의료용 장비」는 참 이름과 낱말 하나만 다르다.
_UNGROUNDED_NAMES = (
    "제품(전기전자 제품 및 의료용 장비)",
    "제품(반도체 장비)",
    "AI 플랫폼(구독형 SaaS)",
    "클라우드 서비스",
    "연구개발 용역",
)


def _same_document_fragments() -> tuple[CollectedFragment, ...]:
    """한 공시에서 잘린 두 조각. 카드는 손익계산서 조각만 인용한다."""

    return (
        _fragment("45", _INCOME_STATEMENT_TEXT, document_identity=_FILING_DOCUMENT),
        _fragment("46", _COMPANY_OVERVIEW_TEXT, document_identity=_FILING_DOCUMENT),
    )


def test_괄호로_갈라진_이름은_부분마다_같은_문서_조각에서_확인한다() -> None:
    row = FlowRow(
        cells=(
            "제품(전기전자 제품 및 산업용 장비)",
            "전력전자 제품 제조·판매",
            "매출 확대",
            "주력",
        ),
        citations=("45",),
    )

    checked, problems = check_diagram_numbers(
        _report(row), _same_document_fragments()
    )

    assert _portfolio_row(checked) == row
    assert problems == ()


def test_괄호_안이_다른_회사_표현이면_카드를_제외한다() -> None:
    row = FlowRow(
        cells=(
            "제품(전기전자 제품 및 의료용 장비)",
            "전력전자 제품 제조·판매",
            "매출 확대",
            "주력",
        ),
        citations=("45",),
    )

    checked, problems = check_diagram_numbers(
        _report(row), _same_document_fragments()
    )

    assert checked.sections[0].flow_rows == ()
    assert any(PORTFOLIO_NAME_NOT_IN_SOURCE_CODE in item for item in problems)


def test_다른_문서_조각의_이름은_빌려오지_않는다() -> None:
    row = FlowRow(
        cells=(
            "제품(전기전자 제품 및 산업용 장비)",
            "전력전자 제품 제조·판매",
            "매출 확대",
            "주력",
        ),
        citations=("45",),
    )
    fragments = (
        _fragment("45", _INCOME_STATEMENT_TEXT, document_identity=_FILING_DOCUMENT),
        # 같은 이름이 있지만 «다른 공시»다. 넓어진 범위가 문서 경계를 넘으면
        # 이 시험이 빨간불이 된다.
        _fragment(
            "46", _COMPANY_OVERVIEW_TEXT, document_identity=_OTHER_FILING_DOCUMENT
        ),
    )

    checked, problems = check_diagram_numbers(_report(row), fragments)

    assert checked.sections[0].flow_rows == ()
    assert any(PORTFOLIO_NAME_NOT_IN_SOURCE_CODE in item for item in problems)


@pytest.mark.parametrize("name", _GROUNDED_NAMES)
def test_공시에_근거가_있는_이름은_카드를_유지한다(name: str) -> None:
    row = FlowRow(
        cells=(name, "전력전자 제품 제조·판매", "매출 확대", "주력"),
        citations=("45",),
    )

    checked, problems = check_diagram_numbers(
        _report(row), _same_document_fragments()
    )

    assert _portfolio_row(checked) == row
    assert problems == ()


@pytest.mark.parametrize("name", _UNGROUNDED_NAMES)
def test_공시에_없는_이름은_카드를_제외한다(name: str) -> None:
    row = FlowRow(
        cells=(name, "전력전자 제품 제조·판매", "매출 확대", "주력"),
        citations=("45",),
    )

    checked, problems = check_diagram_numbers(
        _report(row), _same_document_fragments()
    )

    assert checked.sections[0].flow_rows == ()
    assert any(PORTFOLIO_NAME_NOT_IN_SOURCE_CODE in item for item in problems)


@pytest.mark.parametrize(("fixture_name", "own_name"), _OTHER_COMPANY_CASES)
def test_다른_회사_원문에서는_이_회사_고유_이름이_접지되지_않는다(
    fixture_name: str, own_name: str
) -> None:
    """부분 단위 검사가 «회사를 가리는 힘»을 잃지 않았는지 잰다.

    ★ 같은 원문에서 그 회사 «자기 이름»은 통과해야 한다. 그 단정이 없으면
      원문을 못 읽어 전부 False가 나와도 시험이 초록불이 된다.
    """

    text = (_OTHER_COMPANY_DIR / fixture_name).read_text(encoding="utf-8")

    assert portfolio_name_is_grounded(own_name, (text,)) is True
    for name in ("제품(전기전자 제품 및 산업용 장비)", "전기전자 제품 및 산업용 장비"):
        assert portfolio_name_is_grounded(name, (text,)) is False


def test_이름_표도_카드와_같은_잣대_함수를_쓴다() -> None:
    """잣대가 두 벌이 되면 「검사는 통과인데 화면은 빈」 칸이 다시 생긴다.

    ★ 대조로 확인한다 — 규칙을 한쪽에 복사하면 한쪽만 고쳐져 표류한다.
    """

    from src.features.composer import portfolio_name_table

    assert (
        portfolio_name_table.portfolio_name_is_grounded
        is portfolio_name_is_grounded
    )


def test_3장_새_안내문은_이름_접지_네_문장과_숫자_금지를_함께_둔다() -> None:
    guide = PORTFOLIO_TABLE_GUIDE_V2

    assert "인용한 근거 조각에 글자 그대로 있는 이름만 쓴다" in guide
    assert "줄임·번역·조합 금지" in guide
    assert (
        "원문위치에 이름 표 표기(제품·브랜드·사업부문·종속회사·주요 계약)가 "
        "있는 조각이 있으면 그 이름을 우선 쓴다"
    ) in guide
    assert (
        "이름이 종속회사나 주요 계약이면 「제품·서비스 범위」 칸에 그 성격"
        "(종속회사 사업 / 주요 계약)을 한 구절로 밝힌다"
    ) in guide
    assert "원문에서 이름을 못 찾으면 그 칸을 비운다" in guide
    assert "숫자·퍼센트·연도를 쓰지 않는다" in guide
