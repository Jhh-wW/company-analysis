"""7장 운영 경로 흐름도를 못 박는다 — «한 행 = 한 경로».

★ 왜 이 모양인가 (도식 적대 검증에서 배운 것) — 관계 도식의 결함 7건이 전부
  «경로를 하나로 뭉갠 것»에서 나왔다:
    ① 본체가 고객에게 직접 파는 주 경로(매출 79%)를 빼먹고 전부 종속회사를
       거치게 그림
    ② 자원순환 고객(폐기물 배출 사업장)과 가구 고객을 한 상자로 합침
    ③ 제조를 돕는 기술 파트너를 판매 경로에 놓음
★ 기존 flow 렌더러(웹 `.flow-row` / PDF `_FlowGraphic`)는 «표의 한 행을
  왼쪽→오른쪽 한 흐름»으로 그린다. 그래서 경로마다 행을 나누면 ①②가
  구조적으로 불가능해지고, ③은 「고객에 닿지 않는 관계는 표에 넣지 않는다」는
  작가 지침이 막는다. 새 도식을 만들지 않고 이 계약을 쓴 이유다.
★ 여기서 지키는 것:
  - 경로표가 7장에만 붙고 다른 장에 번지지 않는다
  - 근거 없는 줄은 싣지 않는다 (도식은 본문보다 눈에 먼저 들어온다)
  - 칸 수·길이 계약을 어긴 줄은 버린다
  - 근거가 하나도 없으면 «빈 도식»을 만들지 않는다
"""

from __future__ import annotations

import json
from typing import Any

from src.features.composer.constants import (
    FLOW_PRESENTATION,
    GRADE_CONFIRMED,
    OPERATIONS_FLOW_CAPTION,
    OPERATIONS_FLOW_HEADERS,
    OPERATIONS_FLOW_MAX_CELL_CHARS,
    OPERATIONS_FLOW_MAX_ROWS,
    OPERATIONS_FLOW_SECTION_ID,
    SECTION_IDS,
)
from src.features.composer.logic import (
    build_section_prompt,
    compose_sections,
    parse_flow_rows,
)
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
)
from src.features.composer.render import render_report


def _fragments() -> dict[int, dict[str, Any]]:
    return {
        1: {"종류": "사업내용", "원문": "진영은 시트를 가공해 가구사에 납품한다."},
        2: {"종류": "사업내용", "원문": "한국에코에너지가 폐플라스틱 열분해유를 만든다."},
    }


def _row(cells: tuple[str, ...], citations: tuple[str, ...] = ("1",)) -> FlowRow:
    return FlowRow(cells=cells, citations=citations)


def _composed(flow_rows: tuple[FlowRow, ...]) -> ComposedReport:
    sections = []
    for section_id in SECTION_IDS:
        sentences: tuple[ComposedSentence, ...] = ()
        rows: tuple[FlowRow, ...] = ()
        if section_id == OPERATIONS_FLOW_SECTION_ID:
            sentences = (
                ComposedSentence(
                    text="본체가 시트를 가공해 가구사에 직접 납품한다.",
                    citations=("1",),
                    grade=GRADE_CONFIRMED,
                ),
            )
            rows = flow_rows
        sections.append(
            ComposedSection(
                section_id=section_id, sentences=sentences, flow_rows=rows
            )
        )
    return ComposedReport(
        sections=tuple(sections),
        summary=(
            ComposedSentence(
                text="시트 가공이 본업이다.", citations=("1",), grade=GRADE_CONFIRMED
            ),
        ),
    )


def _section_of(report, cell: str):
    for section in report.sections:
        if section.cell == cell:
            return section
    raise AssertionError(f"{cell} 장이 없습니다")


def _render(flow_rows):
    return render_report("진영(주)", _composed(flow_rows), _fragments(), None)


_두_경로 = (
    _row(("플라스틱 수지", "시트·필름 가공", "가구·인테리어 제조사"), ("1",)),
    _row(("폐플라스틱", "열분해유 생산", "폐기물 배출 사업장"), ("2",)),
)


# ══════════════════════════════════════════════════════════
# 경로표가 흐름도로 나간다
# ══════════════════════════════════════════════════════════


def test_경로표가_7장에_흐름도로_실린다():
    section = _section_of(_render(_두_경로), OPERATIONS_FLOW_SECTION_ID)

    assert len(section.tables) == 1
    표 = section.tables[0]
    assert 표.presentation == FLOW_PRESENTATION
    assert 표.caption == OPERATIONS_FLOW_CAPTION
    assert 표.headers == list(OPERATIONS_FLOW_HEADERS)


def test_경로가_다르면_행이_나뉜다():
    """이것이 «고객 혼동»을 구조적으로 막는 장치다."""
    표 = _section_of(_render(_두_경로), OPERATIONS_FLOW_SECTION_ID).tables[0]

    assert len(표.rows) == 2
    assert 표.rows[0][-1] == "가구·인테리어 제조사"
    assert 표.rows[1][-1] == "폐기물 배출 사업장"
    assert 표.rows[0][-1] != 표.rows[1][-1]


def test_첫_줄이_주_경로로_그대로_보존된다():
    """작가가 매출 최대 경로를 첫 줄에 두면 렌더도 순서를 안 바꾼다."""
    표 = _section_of(_render(_두_경로), OPERATIONS_FLOW_SECTION_ID).tables[0]

    assert 표.rows[0][1] == "시트·필름 가공"


def test_경로표는_다른_장에_번지지_않는다():
    report = _render(_두_경로)

    for section in report.sections:
        if section.cell != OPERATIONS_FLOW_SECTION_ID:
            assert section.tables == [], section.cell


# ══════════════════════════════════════════════════════════
# 근거 없는 도식을 만들지 않는다
# ══════════════════════════════════════════════════════════


def test_경로가_없으면_도식을_만들지_않는다():
    section = _section_of(_render(()), OPERATIONS_FLOW_SECTION_ID)

    assert section.tables == []


def test_실존하지_않는_조각을_가리키는_줄은_버린다():
    행 = (
        _row(("플라스틱 수지", "시트 가공", "가구사"), ("1",)),
        _row(("폐플라스틱", "열분해유", "폐기물 사업장"), ("999",)),
    )

    표 = _section_of(_render(행), OPERATIONS_FLOW_SECTION_ID).tables[0]

    assert len(표.rows) == 1
    assert 표.rows[0][0] == "플라스틱 수지"


def test_모든_줄의_근거가_가짜면_도식이_아예_없다():
    행 = (_row(("가", "나", "다"), ("999",)),)

    assert _section_of(_render(행), OPERATIONS_FLOW_SECTION_ID).tables == []


# ══════════════════════════════════════════════════════════
# 응답 파싱 계약
# ══════════════════════════════════════════════════════════


def _response(flow: list[dict]) -> str:
    return json.dumps(
        {
            "문장들": [{"글": "본문 문장이다.", "인용": ["1"], "등급": "확인"}],
            "경로표": flow,
        },
        ensure_ascii=False,
    )


def test_정상_경로표를_읽는다():
    rows = parse_flow_rows(
        _response([{"칸": ["수지", "가공", "가구사"], "인용": ["1"]}])
    )

    assert len(rows) == 1
    assert rows[0].cells == ("수지", "가공", "가구사")
    assert rows[0].citations == ("1",)


def test_근거가_없는_줄은_읽지_않는다():
    """근거 없는 경로를 그리면 그림이 본문보다 먼저 거짓말을 한다."""
    rows = parse_flow_rows(_response([{"칸": ["수지", "가공", "가구사"]}]))

    assert rows == ()


def test_칸_개수가_다르면_그_줄만_버린다():
    rows = parse_flow_rows(
        _response(
            [
                {"칸": ["수지", "가공"], "인용": ["1"]},
                {"칸": ["폐플라스틱", "열분해", "폐기물 사업장"], "인용": ["2"]},
            ]
        )
    )

    assert len(rows) == 1
    assert rows[0].cells[0] == "폐플라스틱"


def test_빈_칸이_있어도_줄을_살린다():
    """★ 사용자 결정 (2026-08-24) — 한 칸이 비었다고 줄을 버리지 않는다.

    8장 「확인된 사례」처럼 «없을 수 있는» 칸 때문에 쓸 만한 줄이 통째로
    사라졌다. 「내건 가치」와 「일하는 원칙」만 있어도 볼 만한 표다.
    """
    rows = parse_flow_rows(_response([{"칸": ["수지", "", "가구사"], "인용": ["1"]}]))

    assert len(rows) == 1
    assert rows[0].cells == ("수지", "", "가구사")


def test_모든_칸이_비면_그_줄은_버린다():
    """빈 칸은 허용하되, «전부» 빈 줄은 아무 말도 하지 않는다."""
    rows = parse_flow_rows(_response([{"칸": ["", "", ""], "인용": ["1"]}]))

    assert rows == ()


def test_칸이_길어도_줄을_버리지_않는다():
    """★ 사용자 결정 — 24자 상한이 너무 빡빡했다.

    「글로벌 사업 확대에 따른 환율변동위험」이 이미 19자다. 조금만 길어도
    쓸 만한 줄이 사라졌다.
    ★ 원래 이유(「긴 주장이 표로 숨어 문장 검증을 피해 간다」)는 그 사이
      도식 검증(diagram_check)이 생겨 해소됐다 — 표의 칸도 숫자 근거와
      의미 검수를 받는다. 상한이 없어도 검증을 피해 갈 수 없다.
    """
    긴칸 = "가" * (OPERATIONS_FLOW_MAX_CELL_CHARS + 20)
    rows = parse_flow_rows(_response([{"칸": ["수지", 긴칸, "가구사"], "인용": ["1"]}]))

    assert len(rows) == 1
    assert rows[0].cells[1] == 긴칸


def test_줄_수_상한을_지킨다():
    넘침 = [
        {"칸": [f"시작{i}", f"일{i}", f"도달{i}"], "인용": ["1"]}
        for i in range(OPERATIONS_FLOW_MAX_ROWS + 3)
    ]

    assert len(parse_flow_rows(_response(넘침))) == OPERATIONS_FLOW_MAX_ROWS


def test_경로표_키가_없으면_빈_튜플이다():
    raw = json.dumps(
        {"문장들": [{"글": "글이다.", "인용": ["1"], "등급": "확인"}]},
        ensure_ascii=False,
    )

    assert parse_flow_rows(raw) == ()


# ══════════════════════════════════════════════════════════
# 프롬프트·작성 흐름
# ══════════════════════════════════════════════════════════


def _fragment_objs() -> tuple[CollectedFragment, ...]:
    return (
        CollectedFragment(fragment_id="1", kind="사업내용", text="시트를 가공한다."),
    )


def test_표_지침은_정해진_장에만_붙는다():
    """★ 표를 내는 장이 늘었다 — 1·5·6·7·8장.

    목업(사용자가 완성 기준으로 정한 것)의 표들은 숫자 표가 아니라
    «AI가 쓰는 말을 칸에 나눠 담은 것»이었다. 재료가 없어서 못 만든 게
    아니라 우리가 받는 그릇이 «문장 배열» 하나뿐이었다.
    그릇을 늘리되, 표를 «안 내는» 장에는 지침이 새지 않아야 한다.
    """
    from src.features.composer.constants import FLOW_HEADERS_BY_SECTION

    표를_내는_장 = set(FLOW_HEADERS_BY_SECTION)
    assert OPERATIONS_FLOW_SECTION_ID in 표를_내는_장
    # 표를 안 내는 장이 «반드시» 남아 있어야 한다 — 전부 표면 이 시험이 헛돈다.
    표_없는_장 = [sid for sid in SECTION_IDS if sid not in 표를_내는_장]
    assert 표_없는_장, "모든 장이 표를 냅니다 — 이 시험이 지킬 것이 없습니다"

    for section_id in SECTION_IDS:
        prompt = build_section_prompt(
            "진영(주)", section_id, _fragment_objs(), None
        )
        if section_id in 표를_내는_장:
            assert "경로표" in prompt, section_id
        else:
            assert "경로표" not in prompt, section_id


def test_7장_프롬프트에_출력형식_안내가_하나뿐이다():
    """★ 진영 실측 결함 — 기본 스키마 안내가 「이 JSON«만» 출력한다」고 못 박은
    뒤에 경로표 안내를 «덧붙이면» 작가가 앞의 강한 지시를 따라 경로표를
    빼먹는다. 재료가 충분했는데도 경로표가 통째로 안 나왔다.
    그래서 7장은 스키마 안내를 «대체»한다 — 두 개가 있으면 안 된다."""
    prompt = build_section_prompt(
        "진영(주)", OPERATIONS_FLOW_SECTION_ID, _fragment_objs(), None
    )

    assert prompt.count("설명·머리말 없이") == 1, "출력 형식 안내가 둘 이상입니다"
    assert "«두 키를 모두»" in prompt


def test_7장_스키마가_두_키를_모두_보여_준다():
    prompt = build_section_prompt(
        "진영(주)", OPERATIONS_FLOW_SECTION_ID, _fragment_objs(), None
    )
    형식 = prompt[prompt.index("출력 형식") :]

    assert '"문장들"' in 형식
    assert '"경로표"' in 형식


def test_표를_안_내는_장은_기본_스키마를_그대로_쓴다():
    from src.features.composer.constants import FLOW_HEADERS_BY_SECTION

    표_없는_장 = next(
        sid for sid in SECTION_IDS if sid not in FLOW_HEADERS_BY_SECTION
    )
    prompt = build_section_prompt("진영(주)", 표_없는_장, _fragment_objs(), None)

    assert prompt.count("설명·머리말 없이") == 1
    assert "경로표" not in prompt


def test_표를_내는_모든_장이_출력형식_안내를_하나만_갖는다():
    """★ 두 개면 작가가 앞의 「이 JSON만 출력한다」를 따라 표를 빼먹는다.

    장이 늘어날 때마다 이 사고가 되풀이될 수 있어 «모든 장»을 함께 본다.
    """
    from src.features.composer.constants import FLOW_HEADERS_BY_SECTION

    for section_id in FLOW_HEADERS_BY_SECTION:
        prompt = build_section_prompt(
            "진영(주)", section_id, _fragment_objs(), None
        )
        assert prompt.count("설명·머리말 없이") == 1, section_id
        assert "«두 키를 모두»" in prompt, section_id


def test_장마다_자기_칸_이름이_스키마에_나온다():
    """머리말과 스키마가 어긋나면 작가가 다른 칸을 채운다."""
    from src.features.composer.constants import FLOW_HEADERS_BY_SECTION

    for section_id, headers in FLOW_HEADERS_BY_SECTION.items():
        prompt = build_section_prompt(
            "진영(주)", section_id, _fragment_objs(), None
        )
        형식 = prompt[prompt.index("출력 형식") :]
        for name in headers:
            assert name in 형식, f"{section_id}: 「{name}」이 스키마에 없습니다"


def test_지침이_고객이_다르면_줄을_나누라고_말한다():
    prompt = build_section_prompt(
        "진영(주)", OPERATIONS_FLOW_SECTION_ID, _fragment_objs(), None
    )

    assert "고객이 다르면" in prompt
    assert "고객에게 닿지 않는" in prompt


def test_작성_단계가_표를_같은_응답에서_읽는다():
    """★ 표를 따로 받으려고 AI를 «한 번 더» 부르지 않는다.

    표를 내는 장이 다섯이라 따로 부르면 호출이 5회 늘어난다. 본조사 예산이
    900원이고 실측 실행비가 이미 348~585원이라 그만한 여유가 없다.
    """
    from src.features.composer.constants import FLOW_HEADERS_BY_SECTION

    calls: list[str] = []

    def ask(prompt: str) -> str:
        calls.append(prompt)
        # 그 장이 요구하는 «칸 수»에 맞춰 답한다 — 실제 작가처럼.
        칸수 = 3
        for headers in FLOW_HEADERS_BY_SECTION.values():
            for name in headers:
                if name in prompt:
                    칸수 = len(headers)
                    break
            else:
                continue
            break
        flow = (
            [{"칸": [f"칸{i + 1}" for i in range(칸수)], "인용": ["1"]}]
            if "경로표" in prompt
            else []
        )
        return _response(flow)

    report = compose_sections("진영(주)", _fragment_objs(), None, ask)

    assert len(calls) == len(SECTION_IDS), "장마다 1회여야 한다 — 추가 호출이 있습니다"
    for section in report.sections:
        if section.section_id in FLOW_HEADERS_BY_SECTION:
            assert len(section.flow_rows) == 1, section.section_id
            assert len(section.flow_rows[0].cells) == len(
                FLOW_HEADERS_BY_SECTION[section.section_id]
            ), f"{section.section_id}: 칸 수가 그 장 계약과 다릅니다"
        else:
            assert section.flow_rows == (), section.section_id
