"""흐름표에서 «회사가 안 밝힌 칸»을 어떻게 내보내는지 못 박는다.

  화살표로 그리는 장은 「미확인」으로 채우고, 카드로 그리는 장은 빈 칸을 그대로
  둔다(카드 렌더러가 빼 준다). 두 규칙을 한 파일에서 함께 지킨다 — 한쪽만
  지키면 다른 쪽이 조용히 망가진다는 것이 실제로 확인됐다.

★ 왜 이 파일이 생겼나 (저장본 실측)
  ─────────────────────────────────────────────────────────
  (주)진영의 저장된 v2 보고서를 열어 보니 6장 「회사가 밝힌 성장 계획」 흐름표가
  4줄인데 그중 **3줄의 「시점」 칸이 빈 문자열**이었다.

  화살표 흐름도의 칸은 `style.css:1863-1883` 에서 `min-height: 76px` 에
  테두리·배경이 붙고 칸 사이에 «→» 화살표가 그려진다. 값이 빈 문자열이면
  화면에는 **작은 라벨만 있고 속이 텅 빈 76px 상자**가 화살표와 함께
  나온다 — 읽는 사람에게는 「글자가 안 불러와진 고장」으로 보인다.

  이건 「자료가 없다」를 정직하게 말한 게 아니라 «아무 말도 안 한» 것이다.

⚠️ 정정 (적대 검수) — 이 머리말은 원래 «(주)진영 6장이 그 빈 76px
  상자였다»고 적고 있었다. **틀린 서술이다.** 빈 칸이 있었던 것은 사실이지만
  6장 머리말(`STRATEGY_TABLE_HEADERS`)은 `report_standard/visualization.py` 의
  `_CARD_HEADER_SETS` 에 등록돼 있어 **카드로 그려지고**, 카드
  (`_flow_cards`)는 빈 칸을 아예 «빼고» 낸다 — 빈 상자가 될 수 없다.
  빈 76px 상자가 실제로 그려질 수 있는 장은 **화살표를 유지한 2·5·7장뿐**이다.

★ 그래서 채우기는 «화살표 장에만» 건다 (2차 수정)
  ─────────────────────────────────────────────────────────
  처음에는 모든 장의 빈 칸을 「미확인」으로 채웠는데, 그러면 카드 장에서
  두 가지가 깨진다(적대 검수가 코드로 재현):
    · 8장 「확인된 사례」는 «없을 수 있는» 칸인데 항상
      「확인된 사례: 미확인」이 인쇄된다 (예전엔 그 줄이 통째로 빠졌다).
    · 3장은 「제품·서비스명」이 카드 «제목»이라 그 칸이 비면
      **「미확인」이라는 제목의 카드**가 뜬다 (예전엔 제목 자체가 없었다).
  판정은 `composer.constants.FLOW_ARROW_SECTION_IDS` 한 곳에서만 한다.

★ 왜 (화살표 장에서) «칸을 숨기지» 않았나
  ─────────────────────────────────────────────────────────
  줄마다 칸 수가 달라지면 흐름도의 열과 화살표가 줄끼리 어긋난다.
  칸은 그대로 두고 「미확인」이라고 적는 쪽이 맞다. 카드에는 화살표도 열
  정렬도 없어서 이 이유가 성립하지 않는다 — 그래서 카드는 빼는 쪽이 맞다.

★ 왜 «줄을 버리지» 않았나
  ─────────────────────────────────────────────────────────
  「빈 칸이 있다고 줄을 버리지 않는다」가 이미 정해진 결정이다
  (`12_다음_할_일.md` 「안 하기로 정한 것」). 여기서도 아무것도 안 버린다.

★ 왜 «데이터 층»에서 채우나 (가장 중요)
  ─────────────────────────────────────────────────────────
  웹(`result.html`)과 PDF(`export_pdf/logic.py::_FlowGraphic`)가 각자 채우면
  한쪽만 고쳐져 갈린다. 문단 번호에서 정확히 그 사고가 났다
  (웹에 25개·PDF에 0개). `render.py::_flow_report_table` 한 곳에서 정해
  **두 렌더러가 같은 값을 받게** 한다.
"""

from __future__ import annotations

import pytest

from src.features.composer.constants import (
    BUSINESS_FLOW_SECTION_ID,
    CHALLENGE_FLOW_SECTION_ID,
    CULTURE_TABLE_SECTION_ID,
    FLOW_ARROW_SECTION_IDS,
    FLOW_UNCONFIRMED_CELL,
    OPERATIONS_FLOW_SECTION_ID,
    PORTFOLIO_TABLE_SECTION_ID,
    SECTION_IDS,
    STRATEGY_TABLE_SECTION_ID,
)
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
)
from src.features.composer.render import render_report
from src.features.report_standard.visualization import table_visualization


def _fragments() -> dict[int, dict[str, str]]:
    return {
        1: {
            "종류": "사업내용",
            "원문": (
                "회사는 원자재 가격 상승에 대응해 장기 공급계약 비중을 늘리고 있으며, "
                "시점을 밝히지 않은 계획도 함께 공시했다."
            ),
        }
    }


def _report(
    rows: tuple[FlowRow, ...],
    flow_section_id: str = CHALLENGE_FLOW_SECTION_ID,
) -> ComposedReport:
    """흐름표 줄을 «한 장에만» 실은 보고서. 다른 장은 문장만 갖는다."""

    return ComposedReport(
        sections=tuple(
            ComposedSection(
                section_id=section_id,
                sentences=(
                    ComposedSentence(
                        text="회사는 원자재 가격 상승에 대응하고 있다.",
                        citations=("1",),
                        grade="확인",
                    ),
                ),
                flow_rows=rows if section_id == flow_section_id else (),
            )
            for section_id in SECTION_IDS
        ),
        summary=(
            ComposedSentence(text="원자재 대응이 과제다.", citations=("1",), grade="확인"),
            ComposedSentence(text="시점은 안 밝혔다.", citations=("1",), grade="확인"),
            ComposedSentence(text="대응 체계가 갖춰지는 중이다.", citations=("1",), grade="해석"),
        ),
    )


def _flow_table_of(report, section_cell: str):
    section = next(s for s in report.sections if s.cell == section_cell)
    assert section.tables, f"{section_cell} 장에 표가 없습니다"
    # 흐름표는 «먼저» 실린다(render.py — 흐름 → 구성 순서).
    return section.tables[0]


def _flow_rows_of(report, section_cell: str) -> list[list[str]]:
    return _flow_table_of(report, section_cell).rows


def _cards_of(report, section_cell: str):
    """실제 렌더 경로 그대로 — 표를 카드 도식으로 바꿔 돌려준다.

    손으로 지은 ``ReportTable``이 아니라 ``render_report()``가 만든 표를
    그대로 넘긴다. 이 저장소는 손글 문자열 시험 때문에 결함을 두 번 놓쳤다.
    """

    visualization = table_visualization(_flow_table_of(report, section_cell))
    assert visualization is not None, f"{section_cell} 장 표가 도식이 안 됐습니다"
    assert visualization.kind == "card", (
        f"{section_cell} 장은 카드로 그려져야 합니다 (지금: {visualization.kind})"
    )
    return visualization.cards


# ══════════════════════════════════════════════════════════
# ① 빈 칸이 「미확인」으로 채워진다
# ══════════════════════════════════════════════════════════


def test_회사가_안_밝힌_칸은_미확인으로_채워진다() -> None:
    """★ (주)진영 6장에서 실제로 나온 모양 — 한 칸만 비어 있다."""
    rows = (FlowRow(cells=("원자재 가격 상승", ""), citations=("1",)),)

    report = render_report("가나다전자", _report(rows), _fragments(), None)

    assert _flow_rows_of(report, CHALLENGE_FLOW_SECTION_ID) == [
        ["원자재 가격 상승", FLOW_UNCONFIRMED_CELL]
    ]


def test_공백만_있는_칸도_미확인으로_본다() -> None:
    """AI가 스페이스 하나를 돌려주는 경우 — 화면에서는 빈 칸과 구분이 안 된다."""
    rows = (FlowRow(cells=("원자재 가격 상승", "   "), citations=("1",)),)

    report = render_report("가나다전자", _report(rows), _fragments(), None)

    assert _flow_rows_of(report, CHALLENGE_FLOW_SECTION_ID) == [
        ["원자재 가격 상승", FLOW_UNCONFIRMED_CELL]
    ]


def test_채워진_칸은_한_글자도_안_바뀐다() -> None:
    """★ 이 변경이 «빈 칸»에만 손대는지 못 박는다.

    회사가 실제로 쓴 글을 우리가 다듬으면 그것은 다른 종류의 거짓말이다.
    """
    원문 = "장기 공급계약 확대"
    rows = (FlowRow(cells=("원자재 가격 상승", 원문), citations=("1",)),)

    report = render_report("가나다전자", _report(rows), _fragments(), None)

    assert _flow_rows_of(report, CHALLENGE_FLOW_SECTION_ID) == [
        ["원자재 가격 상승", 원문]
    ]


# ══════════════════════════════════════════════════════════
# ② 아무것도 «버리지» 않는다
# ══════════════════════════════════════════════════════════


def test_빈_칸이_있어도_줄을_버리지_않는다() -> None:
    """「빈 칸이 있다고 줄 버리기」는 이미 «안 하기로 정한 것»이다.

    세 줄 중 두 줄에 빈 칸이 있어도 세 줄 그대로 나가야 한다.
    """
    rows = (
        FlowRow(cells=("원자재 가격 상승", "장기 공급계약 확대"), citations=("1",)),
        FlowRow(cells=("인력 이탈", ""), citations=("1",)),
        FlowRow(cells=("", "사내 육성 과정 신설"), citations=("1",)),
    )

    report = render_report("가나다전자", _report(rows), _fragments(), None)

    assert _flow_rows_of(report, CHALLENGE_FLOW_SECTION_ID) == [
        ["원자재 가격 상승", "장기 공급계약 확대"],
        ["인력 이탈", FLOW_UNCONFIRMED_CELL],
        [FLOW_UNCONFIRMED_CELL, "사내 육성 과정 신설"],
    ]


# ══════════════════════════════════════════════════════════
# ③ 웹·PDF가 «같은 값»을 받는다
# ══════════════════════════════════════════════════════════


def test_웹과_PDF가_같은_표를_받는다() -> None:
    """★ 두 렌더러가 각자 빈 칸을 채우면 한쪽만 고쳐져 갈린다.

    화면과 인쇄물은 같은 ``ReportTable.rows``를 읽는다 — 그러므로 데이터 층에서
    한 번 채우면 갈릴 자리가 «없다». 이 시험은 그 구조를 못으로 박는다:
    표를 만드는 함수가 하나이고, 그 결과에 빈 칸이 남지 않는다.

    ★ 범위 (정정) — 이 계약은 «화살표로 그리는 장»에만 해당한다.
      5장(과제와 대응)은 화살표 장이다. 카드 장에서는 반대로 빈 칸이
      «남아 있어야» 하고, 그건 아래 ④가 지킨다.
    ★ 이 입력(모든 칸이 빔)은 화살표 장에서는 `visualization._flow` 의
      「전부 빈 줄은 버린다」에 닿지 못한다 — 여기서 이미 「미확인」으로 차
      있기 때문이다. 그 규칙이 여전히 살아 있다는 것은 카드 장 입력으로
      확인한다(아래 `test_전부_빈_줄은_카드에서도_빠진다`).
    """
    rows = (FlowRow(cells=("", ""), citations=("1",)),)

    report = render_report("가나다전자", _report(rows), _fragments(), None)

    표 = _flow_rows_of(report, CHALLENGE_FLOW_SECTION_ID)
    비어_있는_칸 = [cell for row in 표 for cell in row if not str(cell).strip()]
    assert not 비어_있는_칸, (
        f"흐름표에 빈 칸이 남아 있습니다 {표} — 화면에는 «라벨만 있고 속이 빈 "
        f"76px 상자»로 그려집니다"
    )


@pytest.mark.parametrize(
    ("section_id", "cells", "expected"),
    (
        (
            BUSINESS_FLOW_SECTION_ID,  # 2장 — 4칸
            ("설비", "시트 가공", "", "장기 공급계약"),
            ["설비", "시트 가공", FLOW_UNCONFIRMED_CELL, "장기 공급계약"],
        ),
        (
            OPERATIONS_FLOW_SECTION_ID,  # 7장 — 3칸
            ("원자재 매입", "", "가구 제조사"),
            ["원자재 매입", FLOW_UNCONFIRMED_CELL, "가구 제조사"],
        ),
    ),
)
def test_화살표_장은_모두_미확인으로_채운다(
    section_id: str, cells: tuple[str, ...], expected: list[str]
) -> None:
    """★ 화살표 장 3개(2·5·7장) 전부에서 채우기가 살아 있어야 한다.

    5장은 위 ①이 이미 지키므로 여기서는 2·7장을 지킨다. 셋 중 하나라도
    빠지면 그 장에 빈 76px 상자가 되돌아온다.
    """
    assert section_id in FLOW_ARROW_SECTION_IDS

    report = render_report(
        "가나다전자", _report((FlowRow(cells=cells, citations=("1",)),), section_id), _fragments(), None
    )

    assert _flow_rows_of(report, section_id) == [expected]


# ══════════════════════════════════════════════════════════
# ④ 카드로 그리는 장은 «채우지 않는다» (2차 수정)
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("section_id", "cells"),
    (
        (CULTURE_TABLE_SECTION_ID, ("고객 우선", "기록으로 남긴다", "")),  # 8장
        (STRATEGY_TABLE_SECTION_ID, ("설비 증설", "", "이사회 결의")),  # 6장
        (
            PORTFOLIO_TABLE_SECTION_ID,  # 3장
            ("에지 시트", "가구용 마감재", "", "주력 매출원"),
        ),
    ),
)
def test_카드_장의_빈_칸은_그대로_둔다(section_id: str, cells: tuple[str, ...]) -> None:
    """카드 렌더러가 «빼야» 하므로 데이터 층에서 채우면 안 된다."""
    assert section_id not in FLOW_ARROW_SECTION_IDS

    report = render_report(
        "가나다전자", _report((FlowRow(cells=cells, citations=("1",)),), section_id), _fragments(), None
    )

    assert _flow_rows_of(report, section_id) == [list(cells)]
    표 = _flow_rows_of(report, section_id)
    assert FLOW_UNCONFIRMED_CELL not in 표[0], (
        f"카드 장에 「{FLOW_UNCONFIRMED_CELL}」이 채워졌습니다 {표}"
    )


def test_8장_확인된_사례가_비면_카드에서_그_줄이_빠진다() -> None:
    """★ 「확인된 사례」는 «없을 수 있는» 칸이다(composer/logic.py 주석).

    채워 버리면 화면에 항상 「확인된 사례: 미확인」이 인쇄된다 — 회사가
    사례를 밝히지 않았다는 사실을 «한 줄 더 늘려» 말하는 셈이다.
    """
    rows = (
        FlowRow(cells=("고객 우선", "기록으로 남긴다", ""), citations=("1",)),
    )

    report = render_report(
        "가나다전자", _report(rows, CULTURE_TABLE_SECTION_ID), _fragments(), None
    )

    카드 = _cards_of(report, CULTURE_TABLE_SECTION_ID)[0]
    라벨들 = [field.label for field in 카드.fields]
    assert "확인된 사례" not in 라벨들, f"빈 칸이 카드에 남았습니다 {카드.fields}"
    assert "내건 가치" in 라벨들 and "일하는 원칙" in 라벨들


def test_3장_제품이름이_비면_카드_제목이_미확인이_되지_않는다() -> None:
    """★ 3장은 「제품·서비스명」이 카드 «제목»이다(visualization._CARD_TITLE_...).

    그 칸을 채우면 제목이 문자열 「미확인」이 되어 result.html의
    `{% if card.title %}` 이 참이 되고 **「미확인」이라는 제목의 카드**가 뜬다.
    """
    rows = (
        FlowRow(
            cells=("", "가구용 마감재", "수요 확대", "주력 매출원"), citations=("1",)
        ),
    )

    report = render_report(
        "가나다전자", _report(rows, PORTFOLIO_TABLE_SECTION_ID), _fragments(), None
    )

    카드 = _cards_of(report, PORTFOLIO_TABLE_SECTION_ID)[0]
    assert 카드.title == "", f"카드 제목이 지어졌습니다: {카드.title!r}"


def test_전부_빈_줄은_카드에서도_빠진다() -> None:
    """★ `visualization._flow` 의 「전부 빈 줄은 버린다」가 살아 있는지 본다.

    모든 장을 채우던 시절에는 이 규칙에 «닿을 수가 없었다» — 데이터 층에서
    이미 「미확인」으로 차 있었기 때문이다. 카드 장에서 채우기를 걷어 낸
    지금은 다시 닿는다: 아무 말도 하지 않는 줄은 카드가 되지 않는다.
    """
    rows = (
        FlowRow(cells=("고객 우선", "기록으로 남긴다", "표창 제도"), citations=("1",)),
        FlowRow(cells=("", "", ""), citations=("1",)),
    )

    report = render_report(
        "가나다전자", _report(rows, CULTURE_TABLE_SECTION_ID), _fragments(), None
    )

    # 표(데이터)에는 두 줄 다 남는다 — 아무것도 «버리지» 않는다는 결정 그대로.
    assert len(_flow_rows_of(report, CULTURE_TABLE_SECTION_ID)) == 2
    # 화면에 그려지는 카드는 하나다.
    assert len(_cards_of(report, CULTURE_TABLE_SECTION_ID)) == 1
