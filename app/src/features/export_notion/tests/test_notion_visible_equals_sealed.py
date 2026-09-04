"""노션 본문에 «봉인 블록에 없는 문단»이 한 줄도 섞이지 않는지 본다.

★ 이 파일이 막는 것
  채널이 자기만 아는 문장을 한 줄 얹는 일이다. 기존 노션 시험은 «봉인 문장이
  나왔는가»(있음)와 앞 몇 블록의 «접두 모양»만 봤다. 그래서 「추가 조언」 같은
  한 문단을 본문에 끼워 넣어도 아무 시험이 깨지지 않았다.

★ 그래서 여기서는 «정확히 같음»을 본다
  장 제목(heading_2)과 다음 장 제목 사이가 그 장의 구역이다. 그 구역에 있는
  글자 블록 전부를 순서대로 모아, 봉인이 그 장에 준 글자 목록과 «길이까지»
  맞댄다. 한 줄이라도 더 넣으면 목록 길이가 달라져 깨진다.

★ 구역에 있어도 되는 글자는 셋뿐이고 전부 봉인 값이다
  ① 본문 문단 ② 3개년 띠·표의 설명 줄 ③ 도식을 글로 옮긴 「읽는 법」.
  노션에는 막대 그림을 그릴 자리가 없어 ②③이 문단으로 나가지만, 글자는 전부
  봉인 값이고 시험이 새로 짓는 것은 «설명 줄에 인용 번호를 붙이는 자리» 하나뿐이다.

★ 문단 블록만 세지 않는다
  구역 안의 «글자를 싣는 블록 전부»를 센다. 문단만 세면 제목 블록으로 한 줄을
  넣는 길이 열려 있어, 「한 줄이라도 더 넣으면 깨진다」가 참이 아니게 된다.

★ 재료는 다른 봉인 시험이 지어 둔 것을 그대로 쓴다. 재료가 갈라지면 「같은
  봉인에서 같은 글자가 나왔다」를 말할 수 없다.
"""

from __future__ import annotations

from src.core.citations import citation_marker
from src.features.export_notion import logic as notion_logic
from src.features.export_pdf.tests.test_pdf_visible_equals_sealed import (
    assert_two_paragraphs_per_section,
    channel_neutral,
    sealed_two_paragraph_report,
)
from src.shared.report_generation.public_projection import PublicSectionDisplay

#: 노션 블록 가운데 «사람이 읽는 글자»를 rich_text로 싣는 종류.
_TEXT_BLOCK_TYPES = ("paragraph", "heading_1", "heading_2", "heading_3")


def _block_text(block: dict) -> str:
    kind = block["type"]
    if kind not in _TEXT_BLOCK_TYPES:
        return ""
    return "".join(item["text"]["content"] for item in block[kind]["rich_text"])


def _captioned(title: str, cite: str) -> str:
    """설명 줄 한 줄 — 봉인된 제목 뒤에 봉인된 인용 번호를 붙인 모양."""

    marker = citation_marker(cite)
    return f"{title} {marker}" if marker else title


def _expected_paragraphs(display: PublicSectionDisplay) -> list[str]:
    """이 장 구역에 «나와도 되는» 문단 글자를 배치 순서대로 적는다."""

    expected = [text for _ordinal, text in display.paragraphs]
    band = display.period_summary
    if band is not None and band.items:
        caption = _captioned(band.title, band.cite)
        if caption.strip():
            expected.append(caption)
    visual_by_table = {visual.table_index: visual for visual in display.visuals}
    for index, table in enumerate(display.tables):
        expected.append(_captioned(table.caption, table.cite))
        visual = visual_by_table.get(index)
        if visual is not None and visual.reading:
            expected.append(visual.reading)
    return expected


def _section_region(blocks: list[dict], display: PublicSectionDisplay, start: int):
    """장 제목 다음부터 다음 제목 직전까지를 «그 장의 구역»으로 잘라 낸다.

    Returns:
        (구역 블록들, 다음 탐색을 시작할 위치).
    """

    heading = -1
    for index in range(start, len(blocks)):
        block = blocks[index]
        if block["type"] == "heading_2" and display.title in _block_text(block):
            heading = index
            break
    assert heading >= 0, f"{display.cell} 장 제목 블록을 찾지 못했다"

    end = len(blocks)
    for index in range(heading + 1, len(blocks)):
        if blocks[index]["type"] == "heading_2":
            end = index
            break
    return blocks[heading + 1 : end], end


def test_노션_장별_문단_목록은_봉인_값과_정확히_같다() -> None:
    report = sealed_two_paragraph_report()
    projection = report.public_projection
    assert projection is not None
    assert_two_paragraphs_per_section(projection)

    blocks = notion_logic.build_blocks(report)

    cursor = 0
    for block in projection.sections:
        display = block.display
        region, cursor = _section_region(blocks, display, cursor)
        # ★ 문단 블록만 세지 않는다 — 제목 블록으로 한 줄을 넣으면 문단만 세는
        #   그물을 통째로 빠져나간다. 구역을 가르는 장 제목(heading_2)은 구역 «밖»이라
        #   여기 들어오지 않는다.
        printed = [
            channel_neutral(_block_text(item))
            for item in region
            if item["type"] in _TEXT_BLOCK_TYPES
        ]
        expected = [channel_neutral(text) for text in _expected_paragraphs(display)]
        assert printed == expected, f"{display.cell} 구역 문단이 봉인 값과 다르다"
