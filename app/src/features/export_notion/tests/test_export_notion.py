"""노션 전송 기능 시험 — 블록 변환(logic) + 전송 흐름(notion)을 함께 본다.

★ 진짜 노션 서버에 접속하지 않는다. `notion.send_report_to_notion`에는 항상 가짜
  `send` 함수를 주입한다 (팀장 지시 §2 「실제 노션에 접속하지 마라」).

정본: 확정/07_출력/1_흐름/01_세형태.md · 확정/07_출력/2_규칙/01_배치와근거표기.md ·
      확정/07_출력/3_기준/01_성공기준.md (P3)
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
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
from src.features.report_standard import cover_metrics as cover_metrics_module
from src.features.report_standard import period_summary as period_summary_module
from src.features.report_standard import visualization as visualization_module
from src.features.report_standard.public_projection import build_public_projection
from src.features.report_standard.section_content import (
    masthead_lines,
    section_content_blocks,
)
from src.features.report_standard.publish import PublishBlockedError
from src.shared.report_generation.canonical import table_public_projection
from src.shared.report_generation.models import canonical_sha256
from src.shared.report_generation.public_projection import (
    PublicCoverMetricsBlock,
    PublicPeriodSummaryBlock,
    PublicSectionDisplay,
    PublicSectionLedger,
)

_LEGACY_SECRET = "LEGACY-JOB-POSTING-SECRET"


def test_노션_출처표는_attestation_only를_공개하지_않는다() -> None:
    report = build_demo_report()
    attester = replace(
        report.citations[0],
        number=999,
        source_id="dart-company-profile-internal",
        label="내부 OpenDART 기업개황",
        title="내부 OpenDART 기업개황",
        url="https://opendart.fss.or.kr/api/company.json?corp_code=00000001",
        provenance_role="attestation_only",
    )

    blocks = logic._source_list_blocks(
        replace(report, citations=[*report.citations, attester])
    )
    rendered = json.dumps(blocks, ensure_ascii=False)

    assert "내부 OpenDART 기업개황" not in rendered
    assert "company.json" not in rendered


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


def _partial_v1_report() -> Report:
    """8장을 통째로 뺀 canonical 부분 보고서 — v1 등급 고지 갈래의 표본."""

    report = _make_report()
    missing = "culture"
    removed_fact_ids = {
        fact.fact_id for fact in report.fact_records if fact.section_owner == missing
    }
    return replace(
        report,
        sections=[section for section in report.sections if section.cell != missing],
        fact_records=[
            fact
            for fact in report.fact_records
            if fact.fact_id not in removed_fact_ids
        ],
        summary_items=[
            item for item in report.summary_items if item.section_id != missing
        ],
    )


def _blocks_sha256(blocks: list[dict[str, Any]]) -> str:
    """블록 목록 전체를 한 값으로 굳힌다 — 한 글자만 달라져도 값이 바뀐다."""

    return hashlib.sha256(
        json.dumps(
            blocks, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


# ══════════════════════════════════════════════════════════
# v2 봉인 블록 표본 — Notion v2 갈래가 읽는 «유일한» 입력
# ══════════════════════════════════════════════════════════


def _sealed_v2_report(report: Report | None = None) -> Report:
    """봉인 블록(``public_projection``)을 실은 보고서.

    ★ 갈래는 ``schema_version``이 아니라 «봉인이 있는가»로 갈린다
      (설계 017 §02-6 — projection 없는 저장본은 전부 옛 경로). 그래서 이
      표본은 봉인만 붙여 v2 갈래를 부른다.
    ★ ``manifest_ref``는 FULL 실행의 공개 구조 seal이 붙이는 값이라 여기서는
      그 «모양»만 흉내 낸다 — 표 글자는 한 자도 바꾸지 않는다
      (``report_standard/tests/test_public_projection_builder.py::_sealed``와
      같은 이유).
    """

    base = _make_report() if report is None else report
    sections = [
        replace(
            section,
            tables=[
                replace(
                    table,
                    manifest_ref=canonical_sha256(table_public_projection(table)),
                )
                for table in section.tables
            ],
        )
        for section in base.sections
    ]
    sealed = replace(base, sections=sections)
    return replace(sealed, public_projection=build_public_projection(sealed))


def _two_paragraph_report() -> Report:
    """2장 본문을 «문장마다 한 문단»으로 나눈 봉인 보고서.

    ★ 시연 보고서는 장마다 문단이 하나뿐이라 「문단 «단위»로 낸다」가 시험되지
      않는다. 문장 글자는 그대로 두고 묶는 단위만 둘로 나눈다 — 이어붙인
      글자가 같아야 봉인이 성립한다(불변식 I2).
    """

    report = _make_report()
    sections = [
        (
            replace(
                section,
                prose_paragraphs=[text for text, _cite in section.prose_lines],
            )
            if section.cell == "business_model"
            else section
        )
        for section in report.sections
    ]
    return _sealed_v2_report(replace(report, sections=sections))


def _display_of(report: Report, cell: str) -> PublicSectionDisplay:
    projection = report.public_projection
    assert projection is not None, "봉인 없는 보고서로 v2 갈래를 시험할 수 없습니다"
    return next(
        block.display for block in projection.sections if block.display.cell == cell
    )


def _section_slice(
    blocks: list[dict[str, Any]], display: PublicSectionDisplay
) -> list[dict[str, Any]]:
    """한 장의 heading_2부터 다음 heading_2 직전까지를 잘라 온다."""

    prefix = f"{display.display_number}. {display.title}"
    start = next(
        index
        for index, block in enumerate(blocks)
        if block["type"] == "heading_2" and _text_of(block).startswith(prefix)
    )
    end = next(
        (
            index
            for index in range(start + 1, len(blocks))
            if blocks[index]["type"] == "heading_2"
        ),
        len(blocks),
    )
    return blocks[start:end]


# ══════════════════════════════════════════════════════════
# logic.build_blocks — 블록 변환 정확성
# ══════════════════════════════════════════════════════════


class TestBuildBlocks:
    def test_마스트헤드_다음에_회사명과_보고서명이_각각_한_줄로_온다(self):
        """표지 다음 첫 본문 페이지 마스트헤드(D-S4a)가 맨 앞 두 블록,
        그다음에 기존 회사명·보고서명 heading_1 쌍이 그대로 이어진다."""
        report = _make_report()
        expected_masthead_company, expected_masthead_meta = masthead_lines(report)

        blocks = logic.build_blocks(report, grade_note="자료가 부족해 일부만 채웠습니다")
        assert blocks[0]["type"] == "heading_2"
        assert _text_of(blocks[0]) == expected_masthead_company
        assert blocks[1]["type"] == "paragraph"
        assert _text_of(blocks[1]) == expected_masthead_meta

        assert blocks[2]["type"] == "heading_1"
        assert _text_of(blocks[2]) == "(주)진영"
        assert blocks[3]["type"] == "heading_1"
        assert _text_of(blocks[3]) == "분석 보고서"
        assert _text_of(blocks[4]) == (
            "상장사 · 2026-08-19 기준 · "
            "2023~2025 완료 사업연도(12월 결산·연결) / "
            "사건: 2023-08-19~2026-08-19 · "
            "최신 실적 2026년 반기 공식 공시"
        )
        assert "생성" not in _text_of(blocks[4])

    def test_완성도와_내부_검증_문구를_최종_보고서에_내지_않는다(self):
        report = _make_report()
        blocks = logic.build_blocks(report, grade_note="안 쓰여야 하는 문구")
        all_text = "\n".join(
            _text_of(block) for block in blocks if "rich_text" in block[block["type"]]
        )
        assert "안 쓰여야 하는 문구" not in all_text
        assert not any(block["type"] == "callout" for block in blocks)

    def test_canonical_부분보고서는_등급과_미제공사유를_표시한다(self):
        blocks = logic.build_blocks(_partial_v1_report())
        all_text = "\n".join(
            _text_of(block)
            for block in blocks
            if "rich_text" in block[block["type"]]
        )

        assert "검증된 부분 보고서(부분 완성)" in all_text
        assert "공식 근거로 확인된 항목만" in all_text
        assert "8장 인재상과 일하는 방식" in all_text

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
            "2장 · 3장 · 5장 · 7장 · 8장",
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
# logic.build_blocks — v2 갈래는 봉인 블록«만» 읽는다 (설계 017 §07 조각 S6)
# ══════════════════════════════════════════════════════════


class TestBuildBlocksV2:
    """공개 봉인 블록(``report.public_projection``)에서만 노션 블록을 만든다.

    ★ 지금까지 노션은 화면·PDF와 «각자» 계산했다. 그래서 같은 보고서인데
      노션만 본문을 사실 카드 표로 냈고(설계 §01-6 G1), 도식은 아예 없었고
      (G2), 공개본 투영을 한 번 더 돌렸다(G9). 이 갈래는 그 셋을 없앤다.
    """

    def test_v2_Notion은_같은_블록의_paragraphs를_문단_단위로_낸다(self) -> None:
        """G1 — 본문은 사실 카드 표가 아니라 봉인된 문단 그대로 나간다."""

        report = _two_paragraph_report()
        display = _display_of(report, "business_model")
        assert len(display.paragraphs) == 2, "재료가 두 문단이어야 «단위»를 볼 수 있다"

        blocks = logic.build_blocks(report)
        section_blocks = _section_slice(blocks, display)
        paragraphs = [
            _text_of(block)
            for block in section_blocks
            if block["type"] == "paragraph"
        ]

        assert paragraphs[:2] == [text for _ordinal, text in display.paragraphs]
        assert not any(
            block["type"] == "table"
            and _table_matrix(block)[0] == ["항목", "확인 내용"]
            for block in blocks
        ), "v2 갈래에 v1 사실 카드 표가 남아 있습니다"

    def test_v2_Notion_도식은_표와_reading_문단으로_낸다(self) -> None:
        """G2 — 모양은 채널마다 달라도 되지만 «글자»는 봉인 값 그대로다."""

        report = _sealed_v2_report()
        display = _display_of(report, "business_model")
        visual = display.visuals[0]
        table = display.tables[visual.table_index]
        assert visual.reading, "재료에 읽는 법이 있어야 이 시험이 뜻을 가진다"

        section_blocks = _section_slice(logic.build_blocks(report), display)
        expected_matrix = [list(table.headers)] + [list(row) for row in table.rows]
        table_index = next(
            index
            for index, block in enumerate(section_blocks)
            if block["type"] == "table" and _table_matrix(block) == expected_matrix
        )
        reading = section_blocks[table_index + 1]

        assert reading["type"] == "paragraph"
        assert _text_of(reading) == visual.reading

    def test_v2_Notion은_build_published_report를_부르지_않는다(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """G9 — 봉인 뒤에 렌더가 문자열을 다시 «만들면» 채널이 또 갈라진다.

        전역 순수 함수를 전부 예외로 바꿔도 v2 블록이 나와야 한다. 마스트헤드
        두 줄(``masthead_lines``)만 예외다 — 세 채널이 같은 두 줄을 쓰라고
        D-S4a가 일부러 공유시킨 함수이고, 봉인 블록에는 그 자리가 없다.
        """

        report = _sealed_v2_report()
        called: list[str] = []

        def forbidden(name: str):
            def _fail(*_args: Any, **_kwargs: Any):
                called.append(name)
                raise AssertionError(f"v2 갈래가 {name}을(를) 불렀습니다")

            return _fail

        for module, attribute in (
            (logic, "build_published_report"),
            (logic, "section_content_blocks"),
            (logic, "source_verification_label"),
            (logic, "summary_topic"),
            (logic, "visible_citations"),
            (visualization_module, "table_visualization"),
            (period_summary_module, "period_summary_from_table"),
            (cover_metrics_module, "cover_metrics"),
        ):
            monkeypatch.setattr(module, attribute, forbidden(attribute))

        blocks = logic.build_blocks(report)

        assert called == []
        assert blocks, "봉인 블록만으로는 노션 블록을 만들지 못했습니다"

    def test_v2_Notion_글자는_display_paragraphs와_같다(self) -> None:
        """렌더러는 «배치»만 한다 — 번호·꼬리표를 덧붙이지 않는다."""

        report = _two_paragraph_report()
        projection = report.public_projection
        assert projection is not None
        sealed_texts = [
            text
            for block in projection.sections
            for _ordinal, text in block.display.paragraphs
        ]
        paragraph_texts = [
            _text_of(block)
            for block in logic.build_blocks(report)
            if block["type"] == "paragraph"
        ]

        assert sealed_texts, "봉인된 문단이 하나도 없으면 이 시험은 아무것도 안 지킨다"
        for text in sealed_texts:
            assert text in paragraph_texts, "봉인된 문단이 그대로 나오지 않았습니다"
            assert not any(
                other != text and text in other for other in paragraph_texts
            ), "봉인된 문단에 렌더러가 글자를 덧붙였습니다"

    def test_v2_Notion은_봉인된_3개년_띠를_표로_낸다(self) -> None:
        """웹만 그리던 변화 요약 띠를 노션도 «같은 글자»로 낸다(설계 §04 #17).

        ★ 시연 보고서의 실적표는 띠 판정기가 받는 열 이름이 아니라 띠가 비어
          있다. 그래서 봉인 블록에 띠를 «직접» 넣어 배치만 확인한다 — 띠를
          만드는 규칙은 ``report_standard``가 소유하고 그쪽 시험이 지킨다.
        """

        report = _sealed_v2_report()
        projection = report.public_projection
        assert projection is not None
        band = PublicPeriodSummaryBlock(
            title="3개년 변화 요약",
            cite="[2]",
            items=(
                (
                    "매출액",
                    "2023",
                    "309.0",
                    "2025",
                    "324.2",
                    "억원",
                    "+4.9%",
                    "ratio",
                    "up",
                    "표의 첫 행과 마지막 행으로만 계산",
                ),
            ),
        )
        sections = tuple(
            (
                replace(block, display=replace(block.display, period_summary=band))
                if block.display.cell == "past_changes"
                else block
            )
            for block in projection.sections
        )
        banded = replace(
            report, public_projection=replace(projection, sections=sections)
        )

        display = _display_of(banded, "past_changes")
        section_blocks = _section_slice(logic.build_blocks(banded), display)
        matrices = [
            _table_matrix(block)
            for block in section_blocks
            if block["type"] == "table"
        ]

        assert matrices[0] == [
            list(constants.PERIOD_SUMMARY_TABLE_HEADERS),
            [
                "매출액",
                "2023",
                "309.0",
                "2025",
                "324.2",
                "억원",
                "+4.9%",
                "표의 첫 행과 마지막 행으로만 계산",
            ],
        ], "3개년 띠가 그 장의 표보다 먼저 오지 않았습니다"
        assert "3개년 변화 요약 〔2〕" in [
            _text_of(block)
            for block in section_blocks
            if block["type"] == "paragraph"
        ]

    def test_v2_Notion은_봉인된_표지_실적_띠를_표로_낸다(self) -> None:
        """표지 실적 띠도 채널 공통 필드다(설계 §04 #3)."""

        report = _sealed_v2_report()
        projection = report.public_projection
        assert projection is not None
        assert projection.cover_metrics is None, "시연 표본은 표지 띠가 없어야 한다"

        metrics = PublicCoverMetricsBlock(
            title="최근 실적",
            cite="[2]",
            items=(("매출액", "324.2", "억원"), ("영업이익", "-26.6", "억원")),
        )
        covered = replace(
            report,
            public_projection=replace(projection, cover_metrics=metrics),
        )

        blocks = logic.build_blocks(covered)
        summary_index = next(
            index
            for index, block in enumerate(blocks)
            if block["type"] == "heading_2"
            and _text_of(block) == constants.SUMMARY_HEADING
        )
        head = blocks[:summary_index]

        assert "최근 실적 〔2〕" in [
            _text_of(block) for block in head if block["type"] == "paragraph"
        ]
        assert [
            _table_matrix(block) for block in head if block["type"] == "table"
        ] == [
            [
                list(constants.COVER_METRICS_TABLE_HEADERS),
                ["매출액", "324.2", "억원"],
                ["영업이익", "-26.6", "억원"],
            ]
        ]

    def test_v2_Notion은_감사장부만_바꾸면_같은_블록을_낸다(self) -> None:
        """``.ledger``는 화면에 쓰이지 않는다 — 장부를 비워도 블록이 같아야 한다."""

        report = _sealed_v2_report()
        projection = report.public_projection
        assert projection is not None
        assert any(
            block.ledger.fact_ids for block in projection.sections
        ), "장부가 처음부터 비어 있으면 이 시험은 아무것도 안 지킨다"

        emptied = replace(
            projection,
            sections=tuple(
                replace(
                    block,
                    ledger=PublicSectionLedger(
                        fact_ids=(), fact_records=(), source_grade_contribution=()
                    ),
                )
                for block in projection.sections
            ),
            summary_source_grade_contribution=(),
        )

        assert _blocks_sha256(logic.build_blocks(report)) == _blocks_sha256(
            logic.build_blocks(replace(report, public_projection=emptied))
        )


def test_v1_Notion_블록은_불변이다() -> None:
    """봉인이 없는 보고서(v1)는 base와 «한 글자도 다르지 않은» 블록을 낸다.

    ★ 기준값은 base 커밋 ``5b525ee``에서 이 표본으로 실제 뽑은 SHA-256이다.
      생산 상수·생산 함수에 묶지 않고 리터럴로 적는다 — 생산이 바뀌면 기준값도
      같이 바뀌어 회귀를 못 잡는 순환 검증이 되기 때문이다.
    """

    full = _make_report()
    partial = _partial_v1_report()
    assert full.public_projection is None and partial.public_projection is None

    assert _blocks_sha256(logic.build_blocks(full, grade_note="무시되는 문구")) == (
        "e85efced9c3b2dd92698286129313f13f87acce01d4ec8daf267f0b314d59041"
    )
    assert _blocks_sha256(logic.build_blocks(partial)) == (
        "9f1cba6c679e6287dbcc9e23bd144c617e52d531657d22346367daf271a11ea1"
    )


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

    @pytest.mark.parametrize(
        "unsafe_url",
        [
            "javascript:alert(document.domain)",
            "data:text/html,<script>alert(1)</script>",
            "http://notion.example/page",
            "https://user:password@notion.example/page",
            "https://notion.example:444/page",
        ],
    )
    def test_외부응답의_위험한_페이지주소는_결과링크로_남기지_않는다(
        self, notion_env, unsafe_url
    ):
        spy = _RecordingSend(
            responses=[{"id": "page-abc123", "url": unsafe_url}]
        )

        result = notion.send_report_to_notion(_make_report(), send=spy)

        assert result.success is True
        assert result.page_id == "page-abc123"
        assert result.page_url == ""

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

        monkeypatch.setattr(notion, "_urlopen", timeout)
        send = notion._make_urllib_send("notion-secret-token")

        with caplog.at_level(logging.WARNING), pytest.raises(notion.NotionAPIError) as exc:
            send("POST", constants.PAGES_PATH, {"private": "report-original"})

        assert str(exc.value) == "노션 서버와 통신하지 못했습니다"
        assert secret not in caplog.text
        assert "notion-secret-token" not in caplog.text
        assert "report-original" not in caplog.text

    def test_응답_크기와_최종_URL을_고정해_Bearer_호출을_닫는다(
        self, monkeypatch
    ):
        class Response:
            headers = {}

            def __init__(self, *, final_url: str, body: bytes) -> None:
                self.final_url = final_url
                self.body = body
                self.read_sizes: list[int] = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self) -> str:
                return self.final_url

            def read(self, amount: int) -> bytes:
                self.read_sizes.append(amount)
                return self.body[:amount]

        oversized = Response(
            final_url=constants.NOTION_API_BASE + constants.PAGES_PATH,
            body=b"x" * (constants.API_RESPONSE_MAX_BYTES + 1),
        )
        redirected = Response(
            final_url="https://attacker.example/collect",
            body=b'{}',
        )
        send = notion._make_urllib_send("notion-secret-token")

        monkeypatch.setattr(notion, "_urlopen", lambda *_args, **_kwargs: oversized)
        with pytest.raises(notion.NotionAPIError) as too_large:
            send("POST", constants.PAGES_PATH, {"private": "report-original"})
        assert too_large.value.uncertain is True
        assert oversized.read_sizes == [constants.API_RESPONSE_MAX_BYTES + 1]

        monkeypatch.setattr(notion, "_urlopen", lambda *_args, **_kwargs: redirected)
        with pytest.raises(notion.NotionAPIError) as wrong_location:
            send("POST", constants.PAGES_PATH, {"private": "report-original"})
        assert wrong_location.value.uncertain is True
        assert redirected.read_sizes == []

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
