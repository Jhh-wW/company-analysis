"""노션 전송 기능 시험 — 블록 변환(logic) + 전송 흐름(notion)을 함께 본다.

★ 진짜 노션 서버에 접속하지 않는다. `notion.send_report_to_notion`에는 항상 가짜
  `send` 함수를 주입한다 (팀장 지시 §2 「실제 노션에 접속하지 마라」).

정본: 확정/07_출력/1_흐름/01_세형태.md · 확정/07_출력/2_규칙/01_배치와근거표기.md ·
      확정/07_출력/3_기준/01_성공기준.md (P3)
"""

from __future__ import annotations

from dataclasses import replace
import logging
from typing import Any

import pytest

from src.core.constants import CELL_LABELS
from src.features.export_notion import constants, logic, notion
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.port import (
    Grade,
    Report,
    ReportSection,
    ReportTable,
)
from src.features.report_standard.section_content import section_content_blocks
from src.features.report_standard.publish import PublishBlockedError

_LEGACY_SECRET = "LEGACY-JOB-POSTING-SECRET"


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


def _cell_text(row: dict[str, Any], index: int) -> str:
    return "".join(
        rich["text"]["content"] for rich in row["table_row"]["cells"][index]
    )


def _table_matrix(block: dict[str, Any]) -> list[list[str]]:
    assert block["type"] == "table"
    return [
        [_cell_text(row, index) for index in range(block["table"]["table_width"])]
        for row in block["table"]["children"]
    ]


def _make_report(**overrides: Any) -> Report:
    """실제 공개 게이트를 통과한 canonical 1~9 시험 보고서."""

    return replace(build_demo_report(), **overrides)


def _legacy_partial_report() -> Report:
    """canonical 이전의 부분 보고서가 공개 경로에서 닫히는지 확인하는 표본."""

    return Report(
        company="레거시 주식회사",
        job=_LEGACY_SECRET,
        corp_type="상장사",
        grade=Grade.PARTIAL,
        sections=[
            ReportSection(
                cell="1",
                title=CELL_LABELS["1"],
                lines=[("근거 계약이 없는 옛 부분 보고서", "1")],
            )
        ],
        generated_at="2026-08-15",
    )


# ══════════════════════════════════════════════════════════
# logic.build_blocks — 블록 변환 정확성
# ══════════════════════════════════════════════════════════


class TestBuildBlocks:
    def test_회사명과_보고서명이_각각_한_줄로_맨_앞에_온다(self):
        blocks = logic.build_blocks(_make_report(), grade_note="자료가 부족해 일부만 채웠습니다")
        assert blocks[0]["type"] == "heading_1"
        assert _text_of(blocks[0]) == "(주)진영"
        assert blocks[1]["type"] == "heading_1"
        assert _text_of(blocks[1]) == "분석 보고서"
        assert _text_of(blocks[2]) == (
            "상장사 · 2026-08-19 기준 · "
            "2023~2025 완료 사업연도(12월 결산·연결) / "
            "사건: 2023-08-19~2026-08-19 · "
            "최신 실적 2026년 반기 공식 공시"
        )
        assert "생성" not in _text_of(blocks[2])

    def test_완성도와_내부_검증_문구를_최종_보고서에_내지_않는다(self):
        report = _make_report()
        blocks = logic.build_blocks(report, grade_note="안 쓰여야 하는 문구")
        all_text = "\n".join(
            _text_of(block) for block in blocks if "rich_text" in block[block["type"]]
        )
        assert "안 쓰여야 하는 문구" not in all_text
        assert not any(block["type"] == "callout" for block in blocks)

    def test_채워진_장은_문장_뒤에_출처_괄호를_붙인다(self):
        report = _make_report()
        identity = next(section for section in report.sections if section.cell == "identity")
        identity_fact = next(
            fact for fact in report.fact_records if fact.fact_id == "identity-01"
        )
        blocks = logic._section_blocks(report, identity)

        assert [block["type"] for block in blocks] == [
            "heading_2",
            "heading_3",
            "table",
        ]
        assert _text_of(blocks[1]) == "회사 한눈에 보기 [2]"
        assert _table_matrix(blocks[2]) == [
            ["항목", "확인 내용"],
            ["정체성 요약", identity.prose_lines[0][0]],
            ["근거 사업 범위", identity_fact.subject_scope],
            ["산업 내 역할", identity_fact.relationship_or_action],
        ]

    def test_검증된_본문만_한번_내고_근거원문은_반복하지_않는다(self):
        """표시용 본문이 있으면 같은 사실의 원문은 최종 출력에서 되풀이하지 않는다."""
        section = ReportSection(
            cell="identity",
            title="기업 정체성",
            lines=[("원문 사업 문장입니다.", "조각 1·사업보고서")],
            prose_lines=[("검증된 표시용 사업 문장입니다.", "[1]")],
            fact_ids=["layout-only-fact"],
        )
        report = _make_report(sections=[section], fact_records=[])
        blocks = logic._section_blocks(report, section)
        texts = [
            _text_of(block)
            for block in blocks
            if "rich_text" in block[block["type"]]
        ]

        assert "검증된 표시용 사업 문장입니다. 〔1〕" in texts
        assert "원문 사업 문장입니다. 〔1〕" not in texts
        assert not any("조각 1·사업보고서" in text for text in texts)

    def test_빈_섹션_도우미는_부분_보고서_문구를_만들지_않는다(self):
        section = ReportSection(
            cell="portfolio",
            title="핵심 제품·서비스와 포트폴리오 역할",
            empty_reason="근거가 부족합니다",
        )
        report = _make_report(sections=[section], fact_records=[])
        blocks = logic._section_blocks(report, section)
        assert [block["type"] for block in blocks] == ["heading_2"]
        assert "근거가 부족합니다" not in str(blocks)
        assert constants.EMPTY_SECTION_FALLBACK not in str(blocks)

    def test_옛_직무공고_필드는_노션에_들어가지_않는다(self):
        blocks = logic.build_blocks(
            _make_report(job=_LEGACY_SECRET, requirements=[_LEGACY_SECRET])
        )
        all_text = "\n".join(_text_of(b) for b in blocks if "rich_text" in b[b["type"]])
        assert _LEGACY_SECRET not in all_text
        headings = _headings(blocks)
        assert all(any(heading.startswith(f"{number}.") for heading in headings) for number in range(1, 10))
        assert constants.SOURCES_HEADING in headings

    def test_회사_사실만_내고_프로그램의_작성_안내는_숨긴다(self):
        section = ReportSection(
            cell="identity",
            title="기업 정체성",
            lines=[("내부 감사용 원문", "[1]")],
            prose_lines=[("검증된 회사 사실", "[1]")],
            guidance_lines=["활용 질문 — 출력하면 안 되는 작성 안내"],
            fact_ids=["layout-only-fact"],
        )
        report = _make_report(sections=[section], fact_records=[])
        blocks = logic._section_blocks(report, section)
        texts = [_text_of(b) for b in blocks if "rich_text" in b[b["type"]]]
        assert "검증된 회사 사실 〔1〕" in texts
        assert "내부 감사용 원문 〔1〕" not in texts
        assert not any("활용 질문" in text for text in texts)

    def test_legacy_부분_보고서는_빈_노션이나_부분_노션으로_내보내지_않는다(self):
        with pytest.raises(PublishBlockedError):
            logic.build_blocks(_legacy_partial_report())

    def test_요구역량이_없어도_공고_빈칸문구를_내지_않는다(self):
        report = _make_report(requirements=[])
        blocks = logic.build_blocks(report)
        paragraphs = [_text_of(b) for b in blocks if b["type"] == "paragraph"]
        assert constants.REQUIREMENTS_EMPTY_TEXT not in paragraphs

    def test_숫자_표가_노션_표_블록으로_들어간다(self):
        report = _make_report()
        blocks = logic.build_blocks(report)
        revenue_mix = next(
            table
            for section in report.sections
            for table in section.tables
            if table.caption == "2026년 상반기 매출 구성 (단위: %)"
        )
        assert revenue_mix.headers == ["사업", "매출 비중"]
        # 구조 카드도 2열 표이므로 열 수가 아니라 원표의 머리글로 식별한다.
        tables = [
            block
            for block in blocks
            if block["type"] == "table"
            and _table_matrix(block) == [revenue_mix.headers, *revenue_mix.rows]
        ]
        assert len(tables) == 1
        table = tables[0]["table"]
        assert table["table_width"] == 2
        assert table["has_column_header"] is True
        rows = table["children"]

        def _first_cell_text(row: dict[str, Any]) -> list[str]:
            return [rt["text"]["content"] for rt in row["table_row"]["cells"][0]]

        # 첫 행은 머리글, 그다음이 데이터 행이다.
        assert _first_cell_text(rows[0]) == ["사업"]
        assert _first_cell_text(rows[1]) == ["가구용 시트·엣지"]
        assert _first_cell_text(rows[2]) == ["산업용 시트"]
        paragraphs = [_text_of(b) for b in blocks if b["type"] == "paragraph"]
        assert "2026년 상반기 매출 구성 (단위: %) 〔1〕" in paragraphs

    def test_출처_목록은_자료와_기준일을_한눈에_보이는_표로_낸다(self):
        base_report = _make_report()
        # 출처 표의 중복 제거는 공개 게이트와 분리된 순수 렌더러 단위로 본다.
        report = replace(
            base_report,
            citations=[*base_report.citations, *base_report.citations],
        )
        blocks = logic._source_list_blocks(report)
        assert len(blocks) == 1
        source_table = blocks[0]["table"]

        rows = source_table["children"]
        assert len(rows) == len(base_report.citations) + 1
        assert [_cell_text(rows[0], index) for index in range(6)] == [
            "#",
            "자료",
            "기준일·자료 상태",
            "사실 검증",
            "원문 위치",
            "본문 사용 장",
        ]
        assert [_cell_text(rows[1], index) for index in range(6)] == [
            "1",
            "주식회사 진영 반기보고서 (2026.06)",
            "2026-08-13 공시 · 공식 공시 · 실제·현재",
            "사실 검증 완료",
            "II. 사업의 내용; III. 재무에 관한 사항",
            "2장 · 3장 · 5장 · 7장",
        ]
        assert "수집" not in str(source_table)

    def test_출처가_없으면_출처_구획을_안_낸다(self):
        assert logic._source_list_blocks(_make_report(citations=[])) == []

    def test_수집_과정_표는_최종_보고서에_들어가지_않는다(self):
        report = _make_report()
        blocks = logic.build_blocks(report)
        assert constants.COLLECTION_HEADING not in _headings(blocks)
        assert len(report.fact_records) == 26

        tables = [block for block in blocks if block["type"] == "table"]
        matrices = [_table_matrix(table) for table in tables]
        expected_card_count = sum(
            len(section_content_blocks(report, section))
            for section in report.sections
        )
        assert sum(matrix[0] == ["항목", "확인 내용"] for matrix in matrices) == (
            expected_card_count
        )
        assert all(
            matrix[0] != list(constants.COLLECTION_TABLE_HEADERS)
            for matrix in matrices
        )
        for section in report.sections:
            for report_table in section.tables:
                assert [report_table.headers, *report_table.rows] in matrices

    def test_긴_글자는_2000자씩_나눠_담는다(self):
        long_text = "가" * 4500
        section = ReportSection(
            cell="identity",
            title="기업 정체성",
            lines=[(long_text, "")],
            prose_lines=[(long_text, "")],
            fact_ids=["layout-only-fact"],
        )
        report = _make_report(sections=[section], fact_records=[])
        blocks = logic._section_blocks(report, section)
        paragraphs = [block for block in blocks if block["type"] == "paragraph"]
        assert len(paragraphs) == 1
        rich_text = paragraphs[0]["paragraph"]["rich_text"]
        assert len(rich_text) == 3  # 4500 / 2000 → 2000 + 2000 + 500
        limit = constants.MAX_RICH_TEXT_LENGTH
        assert all(len(rt["text"]["content"]) <= limit for rt in rich_text)
        assert "".join(rt["text"]["content"] for rt in rich_text) == long_text


class TestBuildPageTitle:
    def test_생성일이_있으면_괄호로_붙인다(self):
        report = _make_report()
        assert logic.build_page_title(report) == "(주)진영 분석 보고서 (2026-08-19)"

    def test_생성일이_없으면_괄호를_안_붙인다(self):
        report = _make_report(generated_at="")
        assert logic.build_page_title(report) == "(주)진영 분석 보고서"


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
    def test_설정여부는_비밀값을_꺼내지_않고_두값을_함께_확인한다(self, monkeypatch):
        monkeypatch.delenv(constants.ENV_NOTION_TOKEN, raising=False)
        monkeypatch.delenv(constants.ENV_NOTION_PARENT_PAGE_ID, raising=False)
        assert notion.is_notion_configured() is False

        monkeypatch.setenv(constants.ENV_NOTION_TOKEN, "secret-never-returned")
        assert notion.is_notion_configured() is False

        monkeypatch.setenv(constants.ENV_NOTION_PARENT_PAGE_ID, "parent-never-returned")
        assert notion.is_notion_configured() is True

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
        assert title_text == "(주)진영 분석 보고서 (2026-08-19)"
        assert "test-token-do-not-log" not in str(body)  # 토큰이 본문에 섞여 들어가면 안 된다

    def test_블록이_100개_넘으면_나눠_보내고_조각_수를_돌려준다(
        self, notion_env, monkeypatch
    ):
        report = _make_report()
        expected_blocks = [logic._paragraph(f"내용 {index}") for index in range(201)]
        monkeypatch.setattr(logic, "build_blocks", lambda *_args, **_kwargs: expected_blocks)
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

    def test_두번째_조각부터_실패하면_반쯤_만들어졌다고_알린다(
        self, notion_env, monkeypatch
    ):
        report = _make_report()
        expected_blocks = [logic._paragraph(f"내용 {index}") for index in range(101)]
        monkeypatch.setattr(logic, "build_blocks", lambda *_args, **_kwargs: expected_blocks)
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

    def test_예상밖_어댑터오류는_500이나_비밀노출없이_실패결과가_된다(
        self, notion_env, caplog
    ):
        secret = "secret-token-and-report-original"

        def broken_send(_method: str, _path: str, _body: dict) -> dict:
            raise RuntimeError(secret)

        with caplog.at_level(logging.WARNING):
            result = notion.send_report_to_notion(_make_report(), send=broken_send)

        assert result.success is False
        assert result.partial is False
        assert "예상하지 못한 오류" in result.error
        assert secret not in result.error
        assert secret not in caplog.text

    def test_응답이_JSON객체가_아니어도_500대신_안전한_실패결과가_된다(
        self, notion_env
    ):
        result = notion.send_report_to_notion(
            _make_report(), send=lambda _method, _path, _body: []  # type: ignore[return-value]
        )

        assert result.success is False
        assert result.partial is False
        assert result.error == "노션 응답 형식이 올바르지 않습니다"

    def test_urllib가_직접_timeout을_올려도_내부내용을_숨긴다(
        self, monkeypatch, caplog
    ):
        secret = "secret-timeout-detail"

        def timeout(*_args, **_kwargs):
            raise TimeoutError(secret)

        monkeypatch.setattr(notion.urllib.request, "urlopen", timeout)
        send = notion._make_urllib_send("notion-secret-token")

        with caplog.at_level(logging.WARNING), pytest.raises(notion.NotionAPIError) as exc:
            send("POST", constants.PAGES_PATH, {"private": "report-original"})

        assert str(exc.value) == "노션 서버와 통신하지 못했습니다"
        assert secret not in caplog.text
        assert "notion-secret-token" not in caplog.text
        assert "report-original" not in caplog.text

    def test_명시적_429만_Retry_After만큼_제한재시도한다(self, notion_env):
        calls = 0
        sleeps: list[float] = []

        def rate_limited_then_ok(_method: str, _path: str, _body: dict) -> dict:
            nonlocal calls
            calls += 1
            if calls <= 2:
                raise notion.NotionAPIError(
                    "rate limited",
                    status_code=429,
                    retry_after=1.5,
                )
            return {"id": "page-after-429", "url": "https://notion.so/after-429"}

        result = notion.send_report_to_notion(
            _make_report(), send=rate_limited_then_ok, sleep=sleeps.append
        )

        assert result.success is True
        assert calls == 3
        assert sleeps == [1.5, 1.5]

    @pytest.mark.parametrize(
        ("failure", "expected_uncertain"),
        [
            (
                notion.NotionAPIError(
                    "server error", status_code=503, uncertain=True
                ),
                True,
            ),
            (notion.NotionAPIError("timeout", uncertain=True), True),
            (
                notion.NotionAPIError(
                    "rate limit without retry-after", status_code=429
                ),
                False,
            ),
        ],
    )
    def test_5xx_timeout_또는_Retry_After없는_429는_자동재시도하지_않는다(
        self, notion_env, failure, expected_uncertain
    ):
        calls = 0
        sleeps: list[float] = []

        def fail_once(_method: str, _path: str, _body: dict) -> dict:
            nonlocal calls
            calls += 1
            raise failure

        result = notion.send_report_to_notion(
            _make_report(), send=fail_once, sleep=sleeps.append
        )

        assert result.success is False
        assert result.uncertain is expected_uncertain
        assert calls == 1
        assert sleeps == []

    def test_429_재시도도_상한을_넘지_않는다(self, notion_env):
        calls = 0

        def always_rate_limited(_method: str, _path: str, _body: dict) -> dict:
            nonlocal calls
            calls += 1
            raise notion.NotionAPIError(
                "rate limited", status_code=429, retry_after=1
            )

        sleeps: list[float] = []
        result = notion.send_report_to_notion(
            _make_report(), send=always_rate_limited, sleep=sleeps.append
        )

        assert result.success is False
        assert result.uncertain is False
        assert calls == constants.MAX_429_RETRIES + 1
        assert sleeps == [1.0] * constants.MAX_429_RETRIES

    def test_후속블록_timeout은_부분성공과_원격결과미확정을_함께_남긴다(
        self, notion_env, monkeypatch
    ):
        monkeypatch.setattr(
            notion.logic,
            "build_blocks",
            lambda *_args, **_kwargs: [
                {"object": "block", "type": "paragraph"}
                for _ in range(constants.MAX_BLOCKS_PER_REQUEST + 1)
            ],
        )
        calls = 0

        def create_then_timeout(_method: str, _path: str, _body: dict) -> dict:
            nonlocal calls
            calls += 1
            if calls == 1:
                return {"id": "page-id", "url": "https://notion.so/page-id"}
            raise notion.NotionAPIError("timeout", uncertain=True)

        result = notion.send_report_to_notion(
            _make_report(), send=create_then_timeout
        )

        assert result.success is False
        assert result.partial is True
        assert result.uncertain is True
        assert result.page_id == "page-id"
        assert calls == 2

    def test_페이지_ID가_문자열이_아니면_malformed응답으로_막는다(
        self, notion_env
    ):
        result = notion.send_report_to_notion(
            _make_report(),
            send=lambda *_args: {"id": {"unexpected": "object"}},
        )

        assert result.success is False
        assert result.uncertain is True
        assert result.error == "노션이 페이지 ID를 돌려주지 않았습니다"
