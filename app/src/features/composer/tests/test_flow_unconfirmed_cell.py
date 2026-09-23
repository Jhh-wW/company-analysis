"""흐름표에서 «회사가 안 밝힌 칸»을 어떻게 내보내는지 못 박는다.

★ 3차 규칙(화살표 장의 빈 칸 → 「미확인」 채움)이 왜 뒤집혔나
  ─────────────────────────────────────────────────────────
  2026-09-23 4차 실측 — 실제 2장 도식에서 작가 행은
  [플랫폼, 콘텐츠, 제공받음, ""] 로 끝 칸이 빈 문자열이었다. 묶음 검수
  프롬프트는 값이 있는 칸만 싣기 때문에(diagram_check.labelled_flow_cells)
  검수 AI는 앞 3칸만 보고 «참»을 줬다. 그런데 render·public_manifest가
  끝 빈 칸을 「미확인」으로 채워, **검수 AI가 본 적 없는 노드**가 승인된
  도식처럼 PDF에 실렸다. 채우기는 «빈 76px 상자» 문제를 풀려던 조치였지만
  검수 승인 경계를 넘는 부작용이 더 크다.

★ 새 계약 (4차 수정)
  ─────────────────────────────────────────────────────────
  ① 데이터 층(render._flow_report_table·public_manifest)은 어떤 장에서도
     빈 칸을 채우지 않는다 — 검수된 원행과 영수증(review_binding)은 폭
     그대로 보존된다. 행 폭 = 머리말 폭 계약(pipeline.port.is_valid)도
     그대로다.
  ② 표시 축소는 report_standard/visualization._flow «한 곳»이 한다:
     · 끝 빈 칸 행 — 값이 있는 앞 칸만 흐름으로 표시(머리말은 원래 앞 열).
     · 내부/앞 빈 칸·표시 길이 혼합·한 칸 행 — 라벨:값 카드로 표시.
       빈 칸을 건너뛰어 화살표로 이으면 원문에 없던 «새 관계»가 생긴다.
     웹(result.html)·PDF(_FlowGraphic)·봉인(public_projection)이 모두 이 한
     결과를 소비하므로 두 렌더러가 갈릴 자리가 없다.
  ③ 카드 장(1·3·6·8)의 기존 동작 — 빈 칸은 남기고 카드가 뺀다 — 은 그대로다.

★ 「미확인」 상수 자체는 남는다 — 이미 봉인된 과거 보고서가 그 글자를 담고
  있고, 웹·PDF는 그 글자를 «옅은 빈 칸» 모양으로 그리는 분기를 유지한다.
  과거 봉인은 새 코드로 재계산하지 않는다.
"""

from __future__ import annotations

import json

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
from src.features.composer.flow_review_binding import flow_review_problem
from src.features.composer.logic import _normalize_fragments
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
)
from src.features.composer.public_manifest import build_public_structure_seal
from src.features.composer.tests.flow_fixtures import reviewed_flow_fixture
from src.features.composer.render import render_report
from src.features.report_standard.visualization import table_visualization
from src.shared.report_quality.source_identity import document_identity_from_parts

_SOURCE_URL = "https://manifest.example/document/1"


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


def _identity_fragment() -> CollectedFragment:
    """봉인(build_public_structure_seal)까지 통과하는 신원 있는 조각."""

    base = _normalize_fragments(_fragments())[0]
    return CollectedFragment(
        fragment_id=base.fragment_id,
        kind=base.kind,
        text=base.text,
        source_url=_SOURCE_URL,
        document_identity=document_identity_from_parts(url=_SOURCE_URL),
    )


def _report(
    rows: tuple[FlowRow, ...],
    flow_section_id: str = CHALLENGE_FLOW_SECTION_ID,
    fragments=None,
) -> ComposedReport:
    """흐름표 줄을 «한 장에만» 실은 보고서. 다른 장은 문장만 갖는다."""

    return reviewed_flow_fixture(ComposedReport(
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
    ), fragments if fragments is not None else _fragments())


def _flow_table_of(report, section_cell: str):
    section = next(s for s in report.sections if s.cell == section_cell)
    assert section.tables, f"{section_cell} 장에 표가 없습니다"
    # 흐름표는 «먼저» 실린다(render.py — 흐름 → 구성 순서).
    return section.tables[0]


def _flow_rows_of(report, section_cell: str) -> list[list[str]]:
    return _flow_table_of(report, section_cell).rows


def _visual_of(report, section_cell: str):
    visualization = table_visualization(_flow_table_of(report, section_cell))
    assert visualization is not None, f"{section_cell} 장 표가 도식이 안 됐습니다"
    return visualization


# ══════════════════════════════════════════════════════════
# ① 데이터 층은 빈 칸을 채우지 않는다 — 원행·영수증 보존
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("section_id", "cells"),
    (
        (CHALLENGE_FLOW_SECTION_ID, ("원자재 가격 상승", "")),  # 5장 — 2칸
        (BUSINESS_FLOW_SECTION_ID, ("설비", "시트 가공", "장기 공급계약", "")),  # 2장 — 4칸
        (OPERATIONS_FLOW_SECTION_ID, ("원자재 매입", "시트 가공", "")),  # 7장 — 3칸
    ),
)
def test_화살표_장의_빈_칸은_채우지_않고_원행_그대로_내보낸다(
    section_id: str, cells: tuple[str, ...]
) -> None:
    """★ 4차 실측 정정 — 「미확인」 채움은 검수 안 받은 노드를 만든다.

    표(ReportTable)의 행은 검수된 원행과 같은 폭·같은 글자여야 한다.
    공백뿐인 칸도 채우지 않는다(빈 칸과 화면에서 구분되지 않으므로 같은 규칙).
    """
    assert section_id in FLOW_ARROW_SECTION_IDS
    rows = (FlowRow(cells=cells, citations=("1",)),)

    report = render_report("가나다전자", _report(rows, section_id), _fragments(), None)

    표 = _flow_rows_of(report, section_id)
    assert 표 == [[str(cell).strip() for cell in cells]]
    assert FLOW_UNCONFIRMED_CELL not in 표[0], (
        f"빈 칸이 「{FLOW_UNCONFIRMED_CELL}」으로 채워졌습니다 {표} — 검수 AI가 "
        f"본 적 없는 노드가 승인된 도식처럼 공개됩니다"
    )


def test_공백만_있는_칸도_빈_칸과_같이_비워_둔다() -> None:
    """AI가 스페이스 하나를 돌려주는 경우 — strip만 하고 채우지 않는다."""
    rows = (FlowRow(cells=("원자재 가격 상승", "   "), citations=("1",)),)

    report = render_report("가나다전자", _report(rows), _fragments(), None)

    assert _flow_rows_of(report, CHALLENGE_FLOW_SECTION_ID) == [
        ["원자재 가격 상승", ""]
    ]


def test_채워진_칸은_한_글자도_안_바뀐다() -> None:
    """★ 회사가 실제로 쓴 글을 우리가 다듬으면 다른 종류의 거짓말이다."""
    원문 = "장기 공급계약 확대"
    rows = (FlowRow(cells=("원자재 가격 상승", 원문), citations=("1",)),)

    report = render_report("가나다전자", _report(rows), _fragments(), None)

    assert _flow_rows_of(report, CHALLENGE_FLOW_SECTION_ID) == [
        ["원자재 가격 상승", 원문]
    ]


def test_표시_축소_후에도_검수_영수증은_원행에_그대로_유효하다() -> None:
    """★ 검수 후 행을 바꿔 영수증을 재사용하면 안 된다 — 행 자체가 불변이다."""
    rows = (
        FlowRow(
            cells=("설비", "시트 가공", "장기 공급계약", ""), citations=("1",)
        ),
    )
    composed = _report(rows, BUSINESS_FLOW_SECTION_ID)
    reviewed = next(
        section for section in composed.sections
        if section.section_id == BUSINESS_FLOW_SECTION_ID
    ).flow_rows[0]

    render_report("가나다전자", composed, _fragments(), None)

    # 렌더가 행을 변형하지 않았고(끝 칸은 여전히 빈 문자열), 영수증은
    # «원행 그대로»에만 유효하다.
    assert reviewed.cells[-1] == ""
    sources = {
        item.fragment_id: item for item in _normalize_fragments(_fragments())
    }
    assert flow_review_problem(
        reviewed, section_id=BUSINESS_FLOW_SECTION_ID, fragments=sources,
    ) == ""


# ══════════════════════════════════════════════════════════
# ② 표시 축소는 visualization._flow 한 곳이 한다
# ══════════════════════════════════════════════════════════


def test_끝_빈_칸_행은_앞칸만_흐름으로_표시된다() -> None:
    """★ 실제 4차 결함 재현 모양 — 4칸 끝 빈 행은 앞 3칸 흐름으로 표시된다.

    머리말 대응은 원래 앞 열 순서 그대로다(웹 t.headers[i]·PDF headers[column]).
    """
    rows = (
        FlowRow(
            cells=("생산 설비", "가공 제품", "고객이 구매", ""), citations=("1",)
        ),
    )

    report = render_report(
        "가나다전자", _report(rows, BUSINESS_FLOW_SECTION_ID), _fragments(), None
    )

    visual = _visual_of(report, BUSINESS_FLOW_SECTION_ID)
    assert visual.kind == "flow"
    assert visual.flows == (("생산 설비", "가공 제품", "고객이 구매"),)
    values = [value for row in visual.flows for value in row]
    assert FLOW_UNCONFIRMED_CELL not in values and "" not in values
    # 읽는 법의 「끝 칸」은 실제 마지막 표시 칸이다 — 잘려 나간 네 번째
    # 머리말(「반복·확장 수익」)의 의미를 붙이지 않는다.
    assert "반복·확장 수익" not in visual.reading


def test_7장_끝_빈_칸_행은_앞_두_칸_흐름으로_표시된다() -> None:
    rows = (FlowRow(cells=("원자재 매입", "시트 가공", ""), citations=("1",)),)

    report = render_report(
        "가나다전자", _report(rows, OPERATIONS_FLOW_SECTION_ID), _fragments(), None
    )

    visual = _visual_of(report, OPERATIONS_FLOW_SECTION_ID)
    assert visual.kind == "flow"
    assert visual.flows == (("원자재 매입", "시트 가공"),)


def test_정상_네_역할_4칸_행은_그대로_4칸_흐름이다() -> None:
    """★ 참 반례 — 고객/제품/과금/수익 네 역할이 다 있는 행은 잘리지 않는다."""
    cells = ("생산 설비", "가공 제품", "고객이 구매", "장기 공급계약")
    rows = (FlowRow(cells=cells, citations=("1",)),)

    report = render_report(
        "가나다전자", _report(rows, BUSINESS_FLOW_SECTION_ID), _fragments(), None
    )

    visual = _visual_of(report, BUSINESS_FLOW_SECTION_ID)
    assert visual.kind == "flow"
    assert visual.flows == (cells,)
    assert "반복·확장 수익" in visual.reading or "경로가 하나다" in visual.reading


def test_내부_빈_칸_행은_카드로_라벨_원위치_표시된다() -> None:
    """★ 내부 빈 칸을 건너뛰어 A→C로 이으면 원문에 없던 «새 관계»가 생긴다.

    카드는 각 칸을 원래 머리말 라벨과 함께 내므로 칸 위치의 뜻이 옮겨지지
    않는다. 빈 칸(세 번째 「고객 행동·과금」)만 빠진다.
    """
    rows = (
        FlowRow(
            cells=("생산 설비", "가공 제품", "", "장기 공급계약"), citations=("1",)
        ),
    )

    report = render_report(
        "가나다전자", _report(rows, BUSINESS_FLOW_SECTION_ID), _fragments(), None
    )

    visual = _visual_of(report, BUSINESS_FLOW_SECTION_ID)
    assert visual.kind == "card", (
        f"내부 빈 칸 행이 카드가 아니라 {visual.kind}로 표시됩니다 — 화살표로 "
        f"이으면 빈 칸을 건너뛴 새 관계가 생깁니다"
    )
    (card,) = visual.cards
    라벨들 = [field.label for field in card.fields]
    assert 라벨들 == ["핵심 자산", "제품·서비스", "반복·확장 수익"]
    assert all(field.value for field in card.fields)
    assert FLOW_UNCONFIRMED_CELL not in [field.value for field in card.fields]


def test_표시_길이가_섞인_표는_전체를_카드로_낸다() -> None:
    """★ 웹·PDF는 머리말을 열 위치로 그린다 — 길이가 섞이면 표시가 어긋난다."""
    rows = (
        FlowRow(
            cells=("생산 설비", "가공 제품", "고객이 구매", ""), citations=("1",)
        ),
        FlowRow(
            cells=("연구 인력", "설계 용역", "발주사가 검수", "유지보수 계약"),
            citations=("1",),
        ),
    )

    report = render_report(
        "가나다전자", _report(rows, BUSINESS_FLOW_SECTION_ID), _fragments(), None
    )

    visual = _visual_of(report, BUSINESS_FLOW_SECTION_ID)
    assert visual.kind == "card"
    assert len(visual.cards) == 2
    # 행별 인용도 행 수 그대로 따라간다.
    assert len(visual.row_cites) == 2


def test_한_칸만_남는_행은_카드로_낸다() -> None:
    """한 칸짜리 «흐름»은 흐름이 아니다 — 화살표 없이 라벨:값 카드로 낸다."""
    rows = (FlowRow(cells=("원자재 매입", "", ""), citations=("1",)),)

    report = render_report(
        "가나다전자", _report(rows, OPERATIONS_FLOW_SECTION_ID), _fragments(), None
    )

    visual = _visual_of(report, OPERATIONS_FLOW_SECTION_ID)
    assert visual.kind == "card"
    (card,) = visual.cards
    assert [field.label for field in card.fields] == ["무엇으로 시작하나"]


def test_5장_빈_대응_칸_행은_관계도로_그려지지_않는다() -> None:
    """5장은 관계도(_relation_pairs) 경로다 — 빈 칸 행은 도식 없이 원표로 남는다.

    (생산 경로에서는 challenge 가드가 빈 대응 행을 검수에서 이미 빼므로
    여기까지 오는 일이 드물다. 왔더라도 «미확인» 원을 그리지 않는다.)
    """
    rows = (FlowRow(cells=("원자재 가격 상승", ""), citations=("1",)),)

    report = render_report("가나다전자", _report(rows), _fragments(), None)

    assert table_visualization(_flow_table_of(report, CHALLENGE_FLOW_SECTION_ID)) is None


# ══════════════════════════════════════════════════════════
# ③ 봉인과 렌더가 같은 행을 만든다 — 둘 다 채우지 않는다
# ══════════════════════════════════════════════════════════


def test_봉인과_렌더의_흐름표_행이_같고_미확인이_없다() -> None:
    """★ 봉인(public_manifest)과 렌더(render)는 같은 행 규칙이어야 지문
    대조가 유지된다. 한쪽만 고치면 출고에서 지문 불일치로 막힌다."""
    fragment = _identity_fragment()
    rows = (
        FlowRow(
            cells=("생산 설비", "가공 제품", "고객이 구매", ""), citations=("1",)
        ),
    )
    # 봉인은 요약 문장의 본문 소유를 검사한다 — 이 시험은 흐름표 행 규칙만
    # 보므로 요약 없는 최소 보고서를 따로 만든다.
    composed = reviewed_flow_fixture(ComposedReport(
        sections=tuple(
            ComposedSection(
                section_id=section_id,
                sentences=(
                    (ComposedSentence(
                        text="회사는 원자재 가격 상승에 대응하고 있다.",
                        citations=("1",),
                        grade="확인",
                    ),)
                    if section_id == BUSINESS_FLOW_SECTION_ID
                    else ()
                ),
                flow_rows=rows if section_id == BUSINESS_FLOW_SECTION_ID else (),
            )
            for section_id in SECTION_IDS
        ),
    ), (fragment,))

    seal = build_public_structure_seal(
        composed, (fragment,), None, filing_meta=None, composition_tables=(),
        table_presentation="table", company_id="00123456",
        evidence_generation_sha256="a" * 64,
        evidence_packet_sha256s=tuple(
            (key, str(index) * 64) for index, key in enumerate(SECTION_IDS, 1)
        ),
        company_name="가나다전자", corp_type="", generated_at="",
        as_of_date="", analysis_period="", latest_performance_period="",
        citation_style="auto",
    )
    rendered = render_report(
        "가나다전자", composed, (fragment,), None,
        public_structure_seal=seal, company_id="00123456",
    )

    sealed_tables = [
        table for table in json.loads(seal.canonical_json)["tables"]
        if table["section_id"] == BUSINESS_FLOW_SECTION_ID
    ]
    assert sealed_tables, "봉인에 2장 흐름표가 없습니다"
    sealed_rows = sealed_tables[0]["rows"]
    assert sealed_rows == [["생산 설비", "가공 제품", "고객이 구매", ""]]
    assert sealed_rows == _flow_rows_of(rendered, BUSINESS_FLOW_SECTION_ID)
    assert FLOW_UNCONFIRMED_CELL not in sealed_rows[0]


# ══════════════════════════════════════════════════════════
# ④ 카드로 그리는 장은 예전과 같다 — 빈 칸은 남기고 카드가 뺀다
# ══════════════════════════════════════════════════════════


def _cards_of(report, section_cell: str):
    """실제 렌더 경로 그대로 — 표를 카드 도식으로 바꿔 돌려준다."""

    visualization = table_visualization(_flow_table_of(report, section_cell))
    assert visualization is not None, f"{section_cell} 장 표가 도식이 안 됐습니다"
    assert visualization.kind == "card", (
        f"{section_cell} 장은 카드로 그려져야 합니다 (지금: {visualization.kind})"
    )
    return visualization.cards


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
    """★ `visualization._flow` 의 「전부 빈 줄은 버린다」가 살아 있는지 본다."""
    rows = (
        FlowRow(cells=("고객 우선", "기록으로 남긴다", "표창 제도"), citations=("1",)),
        FlowRow(cells=("", "", ""), citations=("1",)),
    )

    report = render_report(
        "가나다전자", _report(rows, CULTURE_TABLE_SECTION_ID), _fragments(), None
    )

    # 전부 빈 행은 의미 검수 결속을 만들지 못하므로 공개 전 필터에서 빠진다.
    assert len(_flow_rows_of(report, CULTURE_TABLE_SECTION_ID)) == 1
    assert len(_cards_of(report, CULTURE_TABLE_SECTION_ID)) == 1


# ══════════════════════════════════════════════════════════
# ⑤ 구형 봉인 호환 — 봉인된 원행의 실제 텍스트는 공백으로 추정하지 않는다
# ══════════════════════════════════════════════════════════


def test_검수를_통과한_실제_미확인_글자_칸은_잘리지_않는다() -> None:
    """이미 봉인된 값은 재해석하지 않는다 — 원행에 실제로 있는 텍스트를
    빈 값으로 추정해 자르면 과거 봉인의 표시가 새 코드에서 달라진다."""
    rows = (
        FlowRow(
            cells=("생산 설비", "가공 제품", "고객이 구매", FLOW_UNCONFIRMED_CELL),
            citations=("1",),
        ),
    )

    report = render_report(
        "가나다전자", _report(rows, BUSINESS_FLOW_SECTION_ID), _fragments(), None
    )

    visual = _visual_of(report, BUSINESS_FLOW_SECTION_ID)
    assert visual.kind == "flow"
    assert visual.flows == (
        ("생산 설비", "가공 제품", "고객이 구매", FLOW_UNCONFIRMED_CELL),
    )
