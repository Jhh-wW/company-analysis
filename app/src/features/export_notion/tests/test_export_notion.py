"""노션 전송 기능 시험 — 블록 변환(logic) + 전송 흐름(notion)을 함께 본다.

★ 진짜 노션 서버에 접속하지 않는다. `notion.send_report_to_notion`에는 항상 가짜
  `send` 함수를 주입한다 (팀장 지시 §2 「실제 노션에 접속하지 마라」).

정본: 확정/07_출력/1_흐름/01_세형태.md · 확정/07_출력/2_규칙/01_배치와근거표기.md ·
      확정/07_출력/3_기준/01_성공기준.md (P3)
"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.constants import CELL_LABELS, RAW_SOURCE_LABEL, RAW_SOURCE_NOTE
from src.features.export_notion import constants, logic, notion
from src.features.pipeline.port import (
    Grade,
    Report,
    ReportSection,
    ReportTable,
    SourceStatus,
)
from src.features.provenance.sources import Source, SourceKind


# ══════════════════════════════════════════════════════════
# 공통 도구
# ══════════════════════════════════════════════════════════


def _text_of(block: dict[str, Any]) -> str:
    """블록 하나(paragraph/heading/bulleted/callout)의 글자를 이어 붙여 돌려준다."""
    block_type = block["type"]
    rich_text = block[block_type]["rich_text"]
    return "".join(rt["text"]["content"] for rt in rich_text)


def _headings(blocks: list[dict[str, Any]]) -> list[str]:
    return [_text_of(b) for b in blocks if b["type"] in ("heading_1", "heading_2")]


def _make_report(**overrides: Any) -> Report:
    """기본값을 채운 시험용 보고서. overrides로 필드를 바꿔 쓴다."""
    base = dict(
        company="에스엠",
        job="마케팅",
        corp_type="상장사",
        grade=Grade.PARTIAL,
        sections=[
            ReportSection(
                cell="1",
                title=CELL_LABELS["1"],
                lines=[("반도체 장비로 매출 70%를 올린다", "1")],
            ),
            ReportSection(
                cell="2",
                title=CELL_LABELS["2"],
                empty_reason="이 회사의 홈페이지에 접속하지 못해 확인하지 못했습니다",
            ),
            ReportSection(
                cell="附",
                title=CELL_LABELS["附"],
                tables=[
                    ReportTable(
                        caption="전자공시 사업보고서 임원 및 직원 현황",
                        headers=["구분", "1인평균급여액"],
                        rows=[["남", "8천5백만원"], ["여", "7천만원"]],
                        cite="전자공시 사업보고서",
                    )
                ],
            ),
        ],
        requirements=["재무제표 작성 경험자", "엑셀 능숙자"],
        sources=[
            SourceStatus(name="DART", state="ok", detail="감사보고서 2024-03-15"),
            SourceStatus(name="홈페이지", state="failed", detail="접속 실패"),
        ],
        citations=[
            Source(
                number=1,
                kind=SourceKind.FILING,
                label="감사보고서 제16장 수익인식 주석",
                disclosed_at="2024-03-15",
                collected_at="2026-08-13",
            )
        ],
        cells={"1": True, "2": False, "附": True},
        shortfall_reasons=["홈페이지 접속 실패로 2번 칸을 채우지 못했습니다"],
        generated_at="2026-08-15",
    )
    base.update(overrides)
    return Report(**base)


# ══════════════════════════════════════════════════════════
# logic.build_blocks — 블록 변환 정확성
# ══════════════════════════════════════════════════════════


class TestBuildBlocks:
    def test_회사명과_부제가_맨_앞에_온다(self):
        blocks = logic.build_blocks(_make_report(), grade_note="자료가 부족해 일부만 채웠습니다")
        assert blocks[0]["type"] == "heading_1"
        assert _text_of(blocks[0]) == "에스엠"
        assert _text_of(blocks[1]) == "마케팅 · 상장사 · 2026-08-15 생성"

    def test_완성이면_상단_라벨을_안_낸다(self):
        report = _make_report(grade=Grade.COMPLETE, shortfall_reasons=[])
        blocks = logic.build_blocks(report, grade_note="안 쓰여야 하는 문구")
        assert "안 쓰여야 하는 문구" not in [
            _text_of(b) for b in blocks if b["type"] == "callout"
        ]
        assert not any(b["type"] == "callout" for b in blocks)

    def test_부분_완성이면_노란색_아이콘과_사유_목록을_낸다(self):
        report = _make_report(grade=Grade.PARTIAL)
        blocks = logic.build_blocks(report, grade_note="자료가 부족해 일부만 채웠습니다")
        callout = next(b for b in blocks if b["type"] == "callout")
        assert callout["callout"]["icon"]["emoji"] == constants.GRADE_ICON_PARTIAL
        assert _text_of(callout) == "자료가 부족해 일부만 채웠습니다"
        bullets = [_text_of(b) for b in blocks if b["type"] == "bulleted_list_item"]
        assert "홈페이지 접속 실패로 2번 칸을 채우지 못했습니다" in bullets

    def test_미완성이면_빨간색_아이콘을_낸다(self):
        report = _make_report(grade=Grade.INCOMPLETE)
        blocks = logic.build_blocks(report, grade_note="자료가 많이 부족합니다")
        callout = next(b for b in blocks if b["type"] == "callout")
        assert callout["callout"]["icon"]["emoji"] == constants.GRADE_ICON_INCOMPLETE

    def test_채워진_칸은_문장_뒤에_출처_괄호를_붙인다(self):
        blocks = logic.build_blocks(_make_report())
        bullets = [_text_of(b) for b in blocks if b["type"] == "bulleted_list_item"]
        assert "반도체 장비로 매출 70%를 올린다 〔1〕" in bullets

    def test_검증된_본문과_문장별_출처와_근거원문을_모두_낸다(self):
        """P-117·P-118·P-127 — 노션도 실제 번호만 붙이고 내부 이름은 감춘다."""
        section = ReportSection(
            cell="1",
            title=CELL_LABELS["1"],
            lines=[("원문 사업 문장입니다.", "조각 1·사업보고서")],
            prose_lines=[("검증된 표시용 사업 문장입니다.", "조각 1·사업보고서")],
        )
        blocks = logic.build_blocks(_make_report(sections=[section]))
        texts = [
            _text_of(block)
            for block in blocks
            if "rich_text" in block[block["type"]]
        ]

        assert "검증된 표시용 사업 문장입니다. 〔1〕" in texts
        assert "원문 사업 문장입니다. 〔1〕" in texts
        assert not any("조각 1·사업보고서" in text for text in texts)
        assert f"{RAW_SOURCE_LABEL} (1문장) — {RAW_SOURCE_NOTE}" in texts

    def test_빈칸_사유가_붙는다(self):
        blocks = logic.build_blocks(_make_report())
        paragraphs = [_text_of(b) for b in blocks if b["type"] == "paragraph"]
        assert (
            "비어 있습니다 — 이 회사의 홈페이지에 접속하지 못해 확인하지 못했습니다"
            in paragraphs
        )

    def test_사유가_없는_빈칸은_기본_문구를_쓴다(self):
        report = _make_report(
            sections=[ReportSection(cell="3", title=CELL_LABELS["3"], empty_reason="")]
        )
        blocks = logic.build_blocks(report)
        paragraphs = [_text_of(b) for b in blocks if b["type"] == "paragraph"]
        assert f"비어 있습니다 — {constants.EMPTY_SECTION_FALLBACK}" in paragraphs

    def test_요구역량_목록이_5번_칸으로_들어간다(self):
        blocks = logic.build_blocks(_make_report())
        assert f"5. {CELL_LABELS['5']}" in _headings(blocks)
        bullets = [_text_of(b) for b in blocks if b["type"] == "bulleted_list_item"]
        assert "재무제표 작성 경험자" in bullets
        assert "엑셀 능숙자" in bullets

    def test_기록에_5번_칸이_있어도_중복으로_안_낸다(self):
        report = _make_report(
            sections=[
                ReportSection(cell="5", title=CELL_LABELS["5"], lines=[]),
            ],
            requirements=["엑셀 능숙자"],
        )
        blocks = logic.build_blocks(report)
        assert _headings(blocks).count(f"5. {CELL_LABELS['5']}") == 1

    def test_요구역량이_없으면_빈칸_문구를_낸다(self):
        report = _make_report(requirements=[])
        blocks = logic.build_blocks(report)
        paragraphs = [_text_of(b) for b in blocks if b["type"] == "paragraph"]
        assert constants.REQUIREMENTS_EMPTY_TEXT in paragraphs

    def test_숫자_표가_노션_표_블록으로_들어간다(self):
        blocks = logic.build_blocks(_make_report())
        # 이 보고서에는 표가 둘 있다 — 附(참고 숫자) 표와 「어디서 가져왔나」 표.
        # 열이 2개인 쪽이 附 표다 (「어디서 가져왔나」는 항상 3열).
        tables = [
            b for b in blocks if b["type"] == "table" and b["table"]["table_width"] == 2
        ]
        assert len(tables) == 1
        table = tables[0]["table"]
        assert table["table_width"] == 2
        assert table["has_column_header"] is True
        rows = table["children"]

        def _first_cell_text(row: dict) -> list[str]:
            return [rt["text"]["content"] for rt in row["table_row"]["cells"][0]]

        # 첫 행은 머리글, 그다음이 데이터 행이다.
        assert _first_cell_text(rows[0]) == ["구분"]
        assert _first_cell_text(rows[1]) == ["남"]
        assert _first_cell_text(rows[2]) == ["여"]
        # 표 위 설명(캡션)에는 근거(cite)가 괄호로 붙는다.
        paragraphs = [_text_of(b) for b in blocks if b["type"] == "paragraph"]
        assert "전자공시 사업보고서 임원 및 직원 현황 〔전자공시 사업보고서〕" in paragraphs

    def test_출처_목록이_render_sources_결과_그대로다(self):
        report = _make_report()
        blocks = logic.build_blocks(report)
        assert constants.SOURCES_HEADING in _headings(blocks)
        paragraphs = [_text_of(b) for b in blocks if b["type"] == "paragraph"]
        # render_sources()가 만드는 항목 줄과 날짜 줄이 그대로 문단으로 들어가야 한다 (P3).
        assert " [1] 감사보고서 제16장 수익인식 주석" in paragraphs
        assert "     2024-03-15 공시 · 수집 2026-08-13" in paragraphs

    def test_출처가_없으면_출처_구획을_안_낸다(self):
        report = _make_report(citations=[])
        blocks = logic.build_blocks(report)
        assert constants.SOURCES_HEADING not in _headings(blocks)

    def test_수집_현황이_소스별_표로_들어간다(self):
        blocks = logic.build_blocks(_make_report())
        assert constants.COLLECTION_HEADING in _headings(blocks)
        tables = [b for b in blocks if b["type"] == "table"]
        collection_table = tables[-1]["table"]
        assert collection_table["table_width"] == 3
        rows = collection_table["children"]
        header_texts = [
            "".join(rt["text"]["content"] for rt in cell)
            for cell in rows[0]["table_row"]["cells"]
        ]
        assert header_texts == list(constants.COLLECTION_TABLE_HEADERS)
        first_row_texts = [
            "".join(rt["text"]["content"] for rt in cell)
            for cell in rows[1]["table_row"]["cells"]
        ]
        assert first_row_texts == ["DART", "⭕ 찾음", "감사보고서 2024-03-15"]
        third_row_texts = [
            "".join(rt["text"]["content"] for rt in cell)
            for cell in rows[2]["table_row"]["cells"]
        ]
        assert third_row_texts == ["홈페이지", "⚠️ 못 가져옴", "접속 실패"]

    def test_긴_글자는_2000자씩_나눠_담는다(self):
        long_text = "가" * 4500
        # 다른 불릿(요구역량·부족 사유)이 안 섞이게 최소 구성으로 만든다.
        report = _make_report(
            grade=Grade.COMPLETE,
            shortfall_reasons=[],
            requirements=[],
            sections=[
                ReportSection(cell="1", title=CELL_LABELS["1"], lines=[(long_text, "")])
            ],
            sources=[],
            citations=[],
        )
        blocks = logic.build_blocks(report)
        bulleted = [b for b in blocks if b["type"] == "bulleted_list_item"]
        assert len(bulleted) == 1
        rich_text = bulleted[0]["bulleted_list_item"]["rich_text"]
        assert len(rich_text) == 3  # 4500 / 2000 → 2000 + 2000 + 500
        limit = constants.MAX_RICH_TEXT_LENGTH
        assert all(len(rt["text"]["content"]) <= limit for rt in rich_text)
        assert "".join(rt["text"]["content"] for rt in rich_text) == long_text


class TestBuildPageTitle:
    def test_생성일이_있으면_괄호로_붙인다(self):
        report = _make_report()
        assert logic.build_page_title(report) == "에스엠 · 마케팅 (2026-08-15)"

    def test_생성일이_없으면_괄호를_안_붙인다(self):
        report = _make_report(generated_at="")
        assert logic.build_page_title(report) == "에스엠 · 마케팅"


# ══════════════════════════════════════════════════════════
# notion._chunk_blocks — 100개 제한 나누기
# ══════════════════════════════════════════════════════════


class TestChunkBlocks:
    def test_100개_이하는_한_조각이다(self):
        blocks = [{"i": i} for i in range(80)]
        chunks = notion._chunk_blocks(blocks, constants.MAX_BLOCKS_PER_REQUEST)
        assert len(chunks) == 1
        assert len(chunks[0]) == 80

    def test_100개_넘으면_나눈다(self):
        blocks = [{"i": i} for i in range(250)]
        chunks = notion._chunk_blocks(blocks, constants.MAX_BLOCKS_PER_REQUEST)
        assert len(chunks) == 3
        assert [len(c) for c in chunks] == [100, 100, 50]

    def test_빈_목록도_조각_하나를_돌려준다(self):
        assert notion._chunk_blocks([], constants.MAX_BLOCKS_PER_REQUEST) == [[]]


# ══════════════════════════════════════════════════════════
# notion.send_report_to_notion — 전송 흐름 (가짜 send만 쓴다)
# ══════════════════════════════════════════════════════════


class _RecordingSend:
    """호출을 기록하는 가짜 `SendFn`. 진짜 네트워크에 절대 나가지 않는다."""

    def __init__(self, responses: list[dict] | None = None, fail_at: int | None = None):
        self.calls: list[tuple[str, str, dict]] = []
        self._responses = responses or []
        self._fail_at = fail_at  # 몇 번째 호출(1부터)에서 실패시킬지

    def __call__(self, method: str, path: str, body: dict) -> dict:
        call_index = len(self.calls) + 1
        self.calls.append((method, path, body))
        if self._fail_at == call_index:
            raise notion.NotionAPIError("가짜 노션 오류 (시험용)")
        if self._responses:
            return self._responses.pop(0)
        return {"id": "page-abc123", "url": "https://notion.so/page-abc123"}


@pytest.fixture
def notion_env(monkeypatch):
    monkeypatch.setenv(constants.ENV_NOTION_TOKEN, "test-token-do-not-log")
    monkeypatch.setenv(constants.ENV_NOTION_PARENT_PAGE_ID, "parent-page-xyz")


class TestSendReportToNotion:
    def test_비밀키가_없으면_네트워크를_시도하지_않고_실패를_돌려준다(self, monkeypatch):
        monkeypatch.delenv(constants.ENV_NOTION_TOKEN, raising=False)
        monkeypatch.delenv(constants.ENV_NOTION_PARENT_PAGE_ID, raising=False)
        spy = _RecordingSend()

        result = notion.send_report_to_notion(_make_report(), send=spy)

        assert result.success is False
        assert constants.ENV_NOTION_TOKEN in result.error
        assert constants.ENV_NOTION_PARENT_PAGE_ID in result.error
        assert spy.calls == []  # 네트워크 시도조차 안 했다

    def test_토큰만_없으면_그것만_알려준다(self, monkeypatch):
        monkeypatch.delenv(constants.ENV_NOTION_TOKEN, raising=False)
        monkeypatch.setenv(constants.ENV_NOTION_PARENT_PAGE_ID, "parent-page-xyz")

        result = notion.send_report_to_notion(_make_report(), send=_RecordingSend())

        assert result.success is False
        assert constants.ENV_NOTION_TOKEN in result.error
        assert constants.ENV_NOTION_PARENT_PAGE_ID not in result.error

    def test_정상_전송이면_페이지_ID와_조각_수를_돌려준다(self, notion_env):
        spy = _RecordingSend()
        result = notion.send_report_to_notion(
            _make_report(), grade_note="자료가 부족해 일부만 채웠습니다", send=spy
        )

        assert result.success is True
        assert result.page_id == "page-abc123"
        assert result.page_url == "https://notion.so/page-abc123"
        assert result.chunk_count == 1
        assert len(spy.calls) == 1
        method, path, body = spy.calls[0]
        assert method == "POST"
        assert path == constants.PAGES_PATH
        assert body["parent"] == {"type": "page_id", "page_id": "parent-page-xyz"}
        title_text = body["properties"]["title"]["title"][0]["text"]["content"]
        assert title_text == "에스엠 · 마케팅 (2026-08-15)"
        assert "test-token-do-not-log" not in str(body)  # 토큰이 본문에 섞여 들어가면 안 된다

    def test_블록이_100개_넘으면_나눠_보내고_조각_수를_돌려준다(self, notion_env):
        many_sections = [
            ReportSection(cell=f"x{i}", title=f"항목{i}", lines=[(f"내용{i}", "")])
            for i in range(60)
        ]
        report = _make_report(sections=many_sections)
        expected_blocks = logic.build_blocks(report)
        assert len(expected_blocks) > constants.MAX_BLOCKS_PER_REQUEST  # 전제 조건 확인

        spy = _RecordingSend()
        result = notion.send_report_to_notion(report, send=spy)

        assert result.success is True
        assert result.chunk_count >= 2
        assert len(spy.calls) == result.chunk_count  # POST 1회 + PATCH (조각수-1)회
        assert spy.calls[0][0] == "POST"
        assert all(call[0] == "PATCH" for call in spy.calls[1:])
        total_sent = sum(len(call[2]["children"]) for call in spy.calls)
        assert total_sent == len(expected_blocks)

    def test_페이지_생성_자체가_실패하면_부분_생성이_아니다(self, notion_env):
        spy = _RecordingSend(fail_at=1)
        result = notion.send_report_to_notion(_make_report(), send=spy)

        assert result.success is False
        assert result.partial is False
        assert result.page_id == ""
        assert result.error  # 사용자에게 보여줄 메시지가 있어야 한다

    def test_두번째_조각부터_실패하면_반쯤_만들어졌다고_알린다(self, notion_env):
        many_sections = [
            ReportSection(cell=f"x{i}", title=f"항목{i}", lines=[(f"내용{i}", "")])
            for i in range(60)
        ]
        report = _make_report(sections=many_sections)
        spy = _RecordingSend(fail_at=2)  # 첫 조각(페이지 생성)은 성공, 두 번째부터 실패

        result = notion.send_report_to_notion(report, send=spy)

        assert result.success is False
        assert result.partial is True
        assert result.page_id == "page-abc123"  # 페이지는 이미 만들어졌다
        assert "일부 내용만" in result.error
        assert "test-token-do-not-log" not in result.error  # 토큰이 오류 메시지에 남으면 안 된다

    def test_인자로_준_토큰이_환경변수보다_우선한다(self, notion_env):
        spy = _RecordingSend()
        result = notion.send_report_to_notion(
            _make_report(), send=spy, token="직접-준-토큰", parent_page_id="직접-준-부모"
        )
        assert result.success is True
        _, _, body = spy.calls[0]
        assert body["parent"]["page_id"] == "직접-준-부모"
