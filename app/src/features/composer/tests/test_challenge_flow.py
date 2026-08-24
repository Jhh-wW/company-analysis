"""5장 «당면 과제와 대응» 대응표를 못 박는다.

★ 왜 이 장에도 도식을 넣나 (사용자 요구) — 「섹션마다 도식이 들어가기로 한 것」.
  다만 억지로 넣지 않는다: 회사가 «대응을 밝히지 않았으면» 줄을 넣지 않고,
  줄이 하나도 없으면 도식을 안 그릴 뿐 장은 그대로 남는다.

★ 왜 새 도식 종류를 만들지 않았나 — 흐름도는 「한 행 = 왼쪽에서 오른쪽으로
  가는 한 흐름」이라는 계약이라 두 칸짜리 «과제 → 대응»도 그대로 담긴다.
  새 종류를 만들면 웹·PDF 렌더러를 각각 새로 짜야 하고, 그때마다 한쪽만
  고쳐 화면과 인쇄물이 어긋나는 사고가 났다(실측 2회).

★ 여기서 지키는 것:
  ① 5장은 «2칸» 대응표를 읽는다 (7장은 3칸 — 장마다 다르다).
  ② 칸 수가 안 맞는 줄은 그 줄만 버린다.
  ③ 5장 프롬프트에 출력 형식 안내가 «하나»뿐이다 (두 개면 작가가 표를 빼먹는다).
  ④ 대응표가 5장에만 실린다 — 다른 장으로 번지지 않는다.
  ⑤ 근거 없는 줄은 화면까지 못 간다.
"""

from __future__ import annotations

import json

from src.features.composer.constants import (
    CHALLENGE_FLOW_CAPTION,
    CHALLENGE_FLOW_HEADERS,
    CHALLENGE_FLOW_SECTION_ID,
    FLOW_HEADERS_BY_SECTION,
    FLOW_PRESENTATION,
    OPERATIONS_FLOW_HEADERS,
    OPERATIONS_FLOW_SECTION_ID,
    SECTION_IDS,
)
from src.features.composer.logic import build_section_prompt, parse_flow_rows
from src.features.composer.port import (
    CollectedFragment,
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
                "인력 이탈에는 사내 육성 과정을 신설해 대응하고 있다."
            ),
        }
    }


def _report(rows: tuple[FlowRow, ...], section_id: str = CHALLENGE_FLOW_SECTION_ID):
    return ComposedReport(
        sections=tuple(
            ComposedSection(
                section_id=sid,
                sentences=(
                    ComposedSentence(
                        text="회사는 원자재 가격 상승에 대응하고 있다.",
                        citations=("1",),
                        grade="확인",
                    ),
                ),
                flow_rows=rows if sid == section_id else (),
            )
            for sid in SECTION_IDS
        ),
        summary=(
            ComposedSentence(text="원자재 대응이 과제다.", citations=("1",), grade="확인"),
            ComposedSentence(text="인력 육성도 함께 본다.", citations=("1",), grade="확인"),
            ComposedSentence(text="대응 체계가 갖춰지는 중이다.", citations=("1",), grade="해석"),
        ),
    )


_두_줄 = (
    FlowRow(cells=("원자재 가격 상승", "장기 공급계약 확대"), citations=("1",)),
    FlowRow(cells=("인력 이탈", "사내 육성 과정 신설"), citations=("1",)),
)


def _section_of(report, cell: str):
    return next(s for s in report.sections if s.cell == cell)


# ══════════════════════════════════════════════════════════
# ① 장마다 칸 수가 다르다
# ══════════════════════════════════════════════════════════


def test_5장은_두_칸_7장은_세_칸이다():
    assert len(FLOW_HEADERS_BY_SECTION[CHALLENGE_FLOW_SECTION_ID]) == 2
    assert len(FLOW_HEADERS_BY_SECTION[OPERATIONS_FLOW_SECTION_ID]) == 3
    assert CHALLENGE_FLOW_HEADERS != OPERATIONS_FLOW_HEADERS


def _response(flow: list[dict]) -> str:
    return json.dumps(
        {
            "문장들": [{"글": "회사는 대응 중이다.", "인용": ["1"], "등급": "확인"}],
            "경로표": flow,
        },
        ensure_ascii=False,
    )


def test_5장_두_칸_대응표를_읽는다():
    raw = _response(
        [
            {"칸": ["원자재 가격 상승", "장기 공급계약 확대"], "인용": ["1"]},
            {"칸": ["인력 이탈", "사내 육성 과정 신설"], "인용": ["1"]},
        ]
    )

    rows = parse_flow_rows(raw, CHALLENGE_FLOW_SECTION_ID)

    assert len(rows) == 2
    assert rows[0].cells == ("원자재 가격 상승", "장기 공급계약 확대")


def test_5장에서_세_칸_줄은_버린다():
    """칸 수가 안 맞으면 그 줄만 버린다 — 장은 그대로 남는다."""
    raw = _response(
        [
            {"칸": ["원자재 가격 상승", "장기 공급계약 확대"], "인용": ["1"]},
            {"칸": ["가", "나", "다"], "인용": ["1"]},
        ]
    )

    rows = parse_flow_rows(raw, CHALLENGE_FLOW_SECTION_ID)

    assert len(rows) == 1


def test_7장에서_두_칸_줄은_버린다():
    """반대 방향도 지킨다 — 장마다 계약이 다르다."""
    raw = _response([{"칸": ["가", "나"], "인용": ["1"]}])

    assert parse_flow_rows(raw, OPERATIONS_FLOW_SECTION_ID) == ()


def test_흐름표를_안_내는_장은_빈_튜플이다():
    raw = _response([{"칸": ["가", "나"], "인용": ["1"]}])

    assert parse_flow_rows(raw, "identity") == ()


# ══════════════════════════════════════════════════════════
# ③ 프롬프트에 출력 형식 안내가 하나뿐이다
# ══════════════════════════════════════════════════════════


def _fragment_objs():
    return (CollectedFragment(fragment_id="1", kind="사업내용", text="원자재 대응"),)


def test_5장_프롬프트에_출력형식_안내가_하나뿐이다():
    """★ 두 개면 작가가 「이 JSON만 출력한다」를 따라 표를 빼먹는다 (진영 실측)."""
    prompt = build_section_prompt(
        "가나다전자", CHALLENGE_FLOW_SECTION_ID, _fragment_objs(), None
    )

    assert prompt.count("출력 형식") == 1
    assert "대응표 규칙" in prompt
    assert "회사가 밝힌 대응" in prompt


def test_대응표_지침은_짐작한_대응을_금지한다():
    prompt = build_section_prompt(
        "가나다전자", CHALLENGE_FLOW_SECTION_ID, _fragment_objs(), None
    )

    assert "짐작한" in prompt
    assert "「없음」" in prompt or "없음" in prompt


def test_대응표_지침은_5장에만_붙는다():
    for section_id in SECTION_IDS:
        prompt = build_section_prompt(
            "가나다전자", section_id, _fragment_objs(), None
        )
        if section_id == CHALLENGE_FLOW_SECTION_ID:
            assert "대응표 규칙" in prompt
        else:
            assert "대응표 규칙" not in prompt, section_id


# ══════════════════════════════════════════════════════════
# ④⑤ 화면까지 도달하고, 다른 장으로 번지지 않는다
# ══════════════════════════════════════════════════════════


def test_대응표가_5장에_도식으로_실린다():
    report = render_report("가나다전자", _report(_두_줄), _fragments(), None)

    과제장 = _section_of(report, CHALLENGE_FLOW_SECTION_ID)
    assert 과제장.tables, "5장에 대응표가 없습니다"
    표 = 과제장.tables[0]
    assert 표.presentation == FLOW_PRESENTATION
    assert 표.caption == CHALLENGE_FLOW_CAPTION
    assert 표.headers == list(CHALLENGE_FLOW_HEADERS)
    assert len(표.rows) == 2


def test_대응표는_다른_장에_번지지_않는다():
    report = render_report("가나다전자", _report(_두_줄), _fragments(), None)

    for section in report.sections:
        if section.cell == CHALLENGE_FLOW_SECTION_ID:
            continue
        assert all(
            t.presentation != FLOW_PRESENTATION for t in section.tables
        ), section.cell


def test_실존하지_않는_조각을_가리키는_줄은_화면까지_못_간다():
    가짜 = (FlowRow(cells=("과제", "대응"), citations=("99",)),)

    report = render_report("가나다전자", _report(가짜), _fragments(), None)

    assert _section_of(report, CHALLENGE_FLOW_SECTION_ID).tables == []


def test_대응이_없으면_도식을_만들지_않는다():
    """회사가 대응을 안 밝혔으면 빈 도식 자리를 남기지 않는다."""
    report = render_report("가나다전자", _report(()), _fragments(), None)

    assert _section_of(report, CHALLENGE_FLOW_SECTION_ID).tables == []
