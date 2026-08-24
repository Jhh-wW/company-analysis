"""흐름표에서 «회사가 안 밝힌 칸»이 빈 상자로 나가지 않는지 못 박는다.

★ 왜 이 파일이 생겼나 (2026-08-25, 저장본 실측)
  ─────────────────────────────────────────────────────────
  (주)진영의 저장된 v2 보고서를 열어 보니 6장 「회사가 밝힌 성장 계획」 흐름표가
  4줄인데 그중 **3줄의 「시점」 칸이 빈 문자열**이었다.

  흐름도의 칸은 `style.css:1863-1883` 에서 `min-height: 76px` 에 테두리·배경이
  붙고 칸 사이에 «→» 화살표가 그려진다. 값이 빈 문자열이면 화면에는
  **「시점」이라는 작은 라벨만 있고 속이 텅 빈 76px 상자**가 화살표와 함께
  나온다 — 읽는 사람에게는 「글자가 안 불러와진 고장」으로 보인다.

  이건 「자료가 없다」를 정직하게 말한 게 아니라 «아무 말도 안 한» 것이다.

★ 왜 «칸을 숨기지» 않았나
  ─────────────────────────────────────────────────────────
  줄마다 칸 수가 달라지면 흐름도의 열과 화살표가 줄끼리 어긋난다.
  칸은 그대로 두고 「미확인」이라고 적는 쪽이 맞다.

★ 왜 «줄을 버리지» 않았나
  ─────────────────────────────────────────────────────────
  「빈 칸이 있다고 줄을 버리지 않는다」가 이미 정해진 결정이다
  (`12_다음_할_일.md` 「안 하기로 정한 것」). 여기서도 아무것도 안 버린다.

★ 왜 «데이터 층»에서 채우나 (가장 중요)
  ─────────────────────────────────────────────────────────
  웹(`result.html`)과 PDF(`export_pdf/logic.py::_FlowGraphic`)가 각자 채우면
  한쪽만 고쳐져 갈린다. 2026-08-25에 문단 번호에서 정확히 그 사고가 났다
  (웹에 25개·PDF에 0개). `render.py::_flow_report_table` 한 곳에서 정해
  **두 렌더러가 같은 값을 받게** 한다.
"""

from __future__ import annotations

from src.features.composer.constants import (
    CHALLENGE_FLOW_SECTION_ID,
    FLOW_UNCONFIRMED_CELL,
    SECTION_IDS,
)
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
)
from src.features.composer.render import render_report


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


def _report(rows: tuple[FlowRow, ...]) -> ComposedReport:
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
                flow_rows=rows if section_id == CHALLENGE_FLOW_SECTION_ID else (),
            )
            for section_id in SECTION_IDS
        ),
        summary=(
            ComposedSentence(text="원자재 대응이 과제다.", citations=("1",), grade="확인"),
            ComposedSentence(text="시점은 안 밝혔다.", citations=("1",), grade="확인"),
            ComposedSentence(text="대응 체계가 갖춰지는 중이다.", citations=("1",), grade="해석"),
        ),
    )


def _flow_rows_of(report, section_cell: str) -> list[list[str]]:
    section = next(s for s in report.sections if s.cell == section_cell)
    assert section.tables, f"{section_cell} 장에 표가 없습니다"
    return section.tables[0].rows


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
    """
    rows = (FlowRow(cells=("", ""), citations=("1",)),)

    report = render_report("가나다전자", _report(rows), _fragments(), None)

    표 = _flow_rows_of(report, CHALLENGE_FLOW_SECTION_ID)
    비어_있는_칸 = [cell for row in 표 for cell in row if not str(cell).strip()]
    assert not 비어_있는_칸, (
        f"흐름표에 빈 칸이 남아 있습니다 {표} — 화면에는 «라벨만 있고 속이 빈 "
        f"76px 상자»로 그려집니다"
    )
