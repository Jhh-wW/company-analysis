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


@pytest.mark.parametrize(
    "name",
    (
        "카카오T",
        # ★ 괄호를 넣는 것만으로 이 방어가 뚫리면 안 된다 (2026-09-11 독립 검토).
        #   머리 「카카오」는 조각 1에, 괄호 안 「T」는 조각 2에 있으므로 부분
        #   단위 검사만으로는 통과해 버렸다. 머리는 인용 조각에서 확인하고
        #   모든 부분이 최소 길이를 넘어야 한다는 두 규칙이 함께 막는다.
        "카카오(T)",
        "카카오 (T)",
        "카카오[T]",
        # 괄호 안이 최소 길이를 넘어도, 그 표현이 근거 어디에도 없으면 막힌다.
        "카카오(TV)",
    ),
)
def test_서로_다른_인용_조각의_경계에_걸친_이름은_접지가_아니다(
    name: str,
) -> None:
    row = FlowRow(
        cells=(name, "서비스 범위", "운영 확대", "주력"),
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

#: 이 회사 공시에 실제로 근거가 있고 «머리»가 인용 조각에 있는 이름.
_GROUNDED_NAMES = (
    "제품(전기전자 제품 및 산업용 장비)",
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


def test_괄호_없는_이름은_인용하지_않은_조각에서_빌려오지_않는다() -> None:
    """머리 부분은 완화 대상이 아니다 — 조합 금지 원칙을 그대로 지킨다.

    같은 문서 넓힘은 «괄호 안 설명»에만 준다. 괄호가 없는 이름은 통째로 머리라
    인용한 조각에 글자 그대로 있어야 한다.
    """

    name = "전기전자 제품 및 산업용 장비"
    fragments = _same_document_fragments()

    빌려온_카드 = FlowRow(
        cells=(name, "전력전자 제품 제조·판매", "매출 확대", "주력"),
        citations=("45",),
    )
    checked, problems = check_diagram_numbers(_report(빌려온_카드), fragments)
    assert checked.sections[0].flow_rows == ()
    assert any(PORTFOLIO_NAME_NOT_IN_SOURCE_CODE in item for item in problems)

    # 그 이름이 실제로 있는 조각을 인용하면 통과한다.
    인용한_카드 = FlowRow(
        cells=(name, "전력전자 제품 제조·판매", "매출 확대", "주력"),
        citations=("46",),
    )
    checked, problems = check_diagram_numbers(_report(인용한_카드), fragments)
    assert _portfolio_row(checked) == 인용한_카드
    assert problems == ()


def test_괄호_안_설명은_같은_문서에_있을_때만_통과한다() -> None:
    """같은 자료로 «있을 때»와 «없을 때»를 나란히 잰다.

    ★ 이 두 단정이 함께 있어야 규칙이 못 박힌다. 차단만 재면 「무엇이든 막는」
      구현도 초록불이고, 통과만 재면 「무엇이든 통과시키는」 구현도 초록불이다.
    """

    row = FlowRow(
        cells=("카카오(TV)", "서비스 범위", "운영 확대", "주력"),
        citations=("1",),
    )
    문서 = "document:dart.fss.or.kr:20260101000001"

    없을_때, problems = check_diagram_numbers(
        _report(row),
        (
            _fragment("1", "카카오", document_identity=문서),
            _fragment("2", "T를 운영한다.", document_identity=문서),
        ),
    )
    assert 없을_때.sections[0].flow_rows == ()
    assert any(PORTFOLIO_NAME_NOT_IN_SOURCE_CODE in item for item in problems)

    있을_때, problems = check_diagram_numbers(
        _report(row),
        (
            _fragment("1", "카카오", document_identity=문서),
            _fragment("2", "TV 서비스를 운영한다.", document_identity=문서),
        ),
    )
    assert _portfolio_row(있을_때) == row
    assert problems == ()


#: 법인격 표기·괄호 밖 짧은 꼬리가 붙은 «정상» 이름. 셋 다 공시 원문에 글자
#: 그대로 있는 모양이고, 기반 커밋에서도 통과하던 것이다.
_ENTITY_DOCUMENT = "document:dart.fss.or.kr:20260320000802"
_SUBSIDIARY_ROW = "종속기업 | (주)수퍼톤 | 영업 거래"
_AFFILIATE_ROW = "종속기업 | 카카오(유) | 영업 거래"
_PRODUCT_ETC_ROW = "주요 제품 및 서비스: 기타(A/S) 등"


def _entity_fragments() -> tuple[CollectedFragment, ...]:
    return (
        _fragment("60", _SUBSIDIARY_ROW, document_identity=_ENTITY_DOCUMENT),
        _fragment("61", _AFFILIATE_ROW, document_identity=_ENTITY_DOCUMENT),
        _fragment("62", _PRODUCT_ETC_ROW, document_identity=_ENTITY_DOCUMENT),
    )


@pytest.mark.parametrize(
    ("name", "cite"),
    (
        ("(주)수퍼톤", "60"),
        # 같은 뜻의 한 글자 표기. 가르기를 정규화 «뒤»에 해야 같은 판정이 된다.
        ("㈜수퍼톤", "60"),
        # 원문은 「(주)수퍼톤」인데 작가가 뒤에 붙여 쓴 모양 — 빠른 길이 아니라
        # 「머리 + 법인격 표기」 경로로 통과한다.
        ("수퍼톤(주)", "60"),
        ("카카오(유)", "61"),
        # 괄호 밖 꼬리 「등」이 한 글자다. 저장소가 스스로 정당하다고 못 박아 둔
        # 이름 모양이라, 꼬리를 따로 재면 정상 이름이 통째로 막힌다.
        ("기타(A/S) 등", "62"),
    ),
)
def test_법인격_표기와_괄호_밖_짧은_꼬리가_붙은_이름은_막지_않는다(
    name: str, cite: str
) -> None:
    row = FlowRow(
        cells=(name, "종속회사 사업", "운영 확대", "주력"), citations=(cite,)
    )

    checked, problems = check_diagram_numbers(_report(row), _entity_fragments())

    assert _portfolio_row(checked) == row
    assert problems == ()


def test_같은_뜻의_법인격_표기는_한_글자든_세_글자든_같은_판정이다() -> None:
    """★ 가르기를 정규화 «전»으로 되돌리면 이 시험이 빨간불이 된다.

    「㈜」는 NFKC로 「(주)」가 된다. 가르기가 먼저면 「㈜수퍼톤」은 안 갈려
    머리가 「주수퍼톤」이 되고, 「(주)수퍼톤」만 갈려 머리가 「수퍼톤」이 된다.
    같은 뜻의 두 표기가 다른 판정을 받는다.

    ★ 마지막 짝이 그 차이를 실제로 드러낸다 — 원문이 이름을 뒤집어 적어
      «빠른 길»이 안 걸리므로, 판정이 머리·괄호 안 경로로만 결정된다.
    """

    세_글자_원문 = (_SUBSIDIARY_ROW,)
    한_글자_원문 = ("종속기업 | ㈜수퍼톤 | 영업 거래",)
    뒤집힌_원문 = ("종속기업 | 수퍼톤(주) | 영업 거래",)

    assert portfolio_name_is_grounded("㈜수퍼톤", 세_글자_원문) is True
    assert portfolio_name_is_grounded("(주)수퍼톤", 세_글자_원문) is True
    assert portfolio_name_is_grounded("㈜수퍼톤", 한_글자_원문) is True
    assert portfolio_name_is_grounded("(주)수퍼톤", 한_글자_원문) is True
    assert portfolio_name_is_grounded("㈜수퍼톤", 뒤집힌_원문) is True
    assert portfolio_name_is_grounded("(주)수퍼톤", 뒤집힌_원문) is True


@pytest.mark.parametrize(
    ("name", "source"),
    (
        # 공시 표는 「매출액(단위:천원)」 같은 머리말을 늘 쓴다 — 가장 현실적인 모양.
        ("제품매출", "제품(단위:천원)매출"),
        ("카카오T", "카카오(주요)T를 운영한다."),
        ("제품 및 산업용 장비", "전기전자 제품 및(주요) 산업용 장비"),
    ),
)
def test_근거_원문의_괄호를_사이에_둔_앞뒤를_이어_붙이지_않는다(
    name: str, source: str
) -> None:
    """★ 근거 글에서도 괄호 구간을 지우면 이 시험이 빨간불이 된다.

    지운 자리는 공백이 되고 압축에서 공백이 사라진다. 그래서 괄호를 사이에 두고
    떨어져 있던 앞뒤가 붙어, 원문에 없던 이름이 「글자 그대로 있다」로 읽힌다.
    괄호 구간 제거는 «이름 쪽에만» 준다.
    """

    row = FlowRow(
        cells=(name, "매출 구분", "운영 확대", "주력"), citations=("70",)
    )

    checked, problems = check_diagram_numbers(
        _report(row),
        (_fragment("70", source, document_identity=_ENTITY_DOCUMENT),),
    )

    assert checked.sections[0].flow_rows == ()
    assert any(PORTFOLIO_NAME_NOT_IN_SOURCE_CODE in item for item in problems)


def test_이름_전체가_인용_조각에_있으면_괄호_안이_한_글자여도_통과한다() -> None:
    """★ 빠른 길 — 원문에 «글자 그대로» 있는 이름은 하한으로 막지 않는다.

    공시 표는 주석 번호를 이름 뒤에 괄호로 단다(「기타(1) 등」). 그 한 글자
    때문에 원문에 그대로 있는 이름이 막히면 안 된다. 빠른 길을 빼면 머리·괄호
    안 경로로 내려가 「1」이 하한에 걸린다 — 그때 이 시험이 빨간불이 된다.
    """

    footnote_row = "주요 제품 및 서비스: 기타(1) 등"
    row = FlowRow(
        cells=("기타(1) 등", "기타 매출", "운영 확대", "주력"), citations=("63",)
    )

    checked, problems = check_diagram_numbers(
        _report(row),
        (_fragment("63", footnote_row, document_identity=_ENTITY_DOCUMENT),),
    )

    assert _portfolio_row(checked) == row
    assert problems == ()


def test_법인격_표기가_아닌_한_글자_괄호말은_같은_자리에서도_막힌다() -> None:
    """★ `PORTFOLIO_NAME_ENTITY_MARKERS`를 비우면 위 시험이 빨간불이 된다.

    같은 원문·같은 자리에서 「(주)」는 통과하고 「(T)」·「(1)」은 막힌다 —
    예외가 «법인격 표기 목록»에서만 나온다는 뜻이다.
    """

    source = (_SUBSIDIARY_ROW,)

    assert portfolio_name_is_grounded("수퍼톤(주)", source) is True
    for name in ("수퍼톤(T)", "수퍼톤(1)"):
        assert portfolio_name_is_grounded(name, source) is False, name


def test_법인격_표기는_그_문서가_괄호_안에_쓸_때만_인정한다() -> None:
    """★ 아무 이름에나 「(주)」를 붙여 한 글자 하한을 우회하지 못한다.

    「제품」은 손익계산서 조각에 있고 「주」는 「주식회사」에 있지만, 이 공시는
    괄호 안에 법인격 표기를 쓰지 않는다. 그런 문서에서 「제품(주)」를 인정하면
    하한이 사실상 사라진다 — 예외는 «그 문서가 실제로 그렇게 적을 때»만 준다.
    """

    row = FlowRow(
        cells=("제품(주)", "전력전자 제품 제조·판매", "매출 확대", "주력"),
        citations=("45",),
    )

    막힘, problems = check_diagram_numbers(
        _report(row), _same_document_fragments()
    )
    assert 막힘.sections[0].flow_rows == ()
    assert any(PORTFOLIO_NAME_NOT_IN_SOURCE_CODE in item for item in problems)

    # 같은 이름도 그 문서가 괄호 안에 법인격 표기를 쓰면 통과한다.
    통과, problems = check_diagram_numbers(
        _report(row),
        _same_document_fragments()
        + (
            _fragment(
                "47",
                "종속기업 | (주)수퍼톤 | 영업 거래",
                document_identity=_FILING_DOCUMENT,
            ),
        ),
    )
    assert _portfolio_row(통과) == row
    assert problems == ()


def test_한_글자_부분만_다른_이름은_회사를_못_가려서_막는다() -> None:
    """★ `PORTFOLIO_NAME_MIN_PART_CHARS`를 1로 낮추면 이 시험이 깨진다.

    아래 이름들의 한 글자 부분은 «근거 원문에 실제로 있다»(「주」는 「주식회사」
    에, 「1」은 「1. 당사의 개요」에). 그래서 길이 하한이 사라지는 순간 전부
    통과한다 — 느슨해지는 방향을 막는 것이 이 시험의 목적이다.
    「제품(주)」는 법인격 표기지만 이 공시가 괄호 안에 그 표기를 쓰지 않아
    예외를 못 받는다(바로 위 시험이 그 경계를 따로 잰다).
    """

    fragments = _same_document_fragments()
    for name in ("제품(주)", "제품(1)", "제품(A)"):
        row = FlowRow(
            cells=(name, "전력전자 제품 제조·판매", "매출 확대", "주력"),
            citations=("45",),
        )

        checked, problems = check_diagram_numbers(_report(row), fragments)

        assert checked.sections[0].flow_rows == (), name
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
    # 안내문과 검사 잣대가 반대면 「비워진다」는 약속이 괄호 이름에 대해
    # 거짓이 된다. 괄호 안 설명에만 문서 범위를 허용한다는 것을 함께 적는다.
    assert "괄호 안 설명은 같은 공시 문서에 글자 그대로 있는 표현만 쓴다" in guide
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
