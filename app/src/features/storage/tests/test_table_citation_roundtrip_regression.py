"""실제 작성기의 표 전용 인용을 최초 JSON·새 DB·출고 검사·PDF까지 잇는다."""

from __future__ import annotations

import io
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from pypdf import PdfReader

from src.core import news_intake_switch
from src.features.composer.constants import (
    GRADE_CONFIRMED,
    NOTICE_INSUFFICIENT_EVIDENCE,
    SECTION_IDS,
)
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    NewsRow,
    PerformanceTable,
)
from src.features.composer.portfolio_name_table import PortfolioNameTable
from src.features.composer.render import render_report
from src.features.composer.tests.test_news_block_channels import _news_fragment
from src.features.composer.tests.test_section_public_manifest import _run_full
from src.features.composer.validate import v2_validation_problems
from src.features.export_pdf.automatic_release import report_sha256
from src.features.export_pdf.logic import build_pdf
from src.features.storage import db, reports


def composer_report():
    """외부 호출 없이 가공 원자료와 작성 문장을 실제 렌더러에 넣는다."""
    fragments = (
        CollectedFragment("1", "사업내용", "가나다전자는 검사 장비를 만든다."),
        CollectedFragment("2", "사업내용", "제품 이름은 가온검사기다."),
        CollectedFragment("3", "사업내용", "서비스 이름은 나래정비다."),
        CollectedFragment(
            "4", "공식 IR", "2025년 매출액은 1,200억원이다.",
            source_url="https://ganada.example/ir.pdf",
            document_title="2025 공식 실적", location="PDF p.3",
        ),
        _news_fragment("41", "2026-09-02", "가나다전자는 물류 자동화 제품을 공급한다."),
        _news_fragment("42", "2026-08-20", "가나다전자는 검사 장비 유지보수를 제공한다."),
    )
    identity = ComposedSentence("가나다전자는 검사 장비를 만든다.", ("1",), GRADE_CONFIRMED)
    performance = ComposedSentence("2025년 매출액은 1,200억원이다.", ("4",), GRADE_CONFIRMED)
    sections = []
    for section_id in SECTION_IDS:
        sentences = (identity,) if section_id == "identity" else (
            (performance,) if section_id == "past_changes" else ()
        )
        news = tuple(
            NewsRow(
                (fragment.document_date, "가나다경제", fragment.text),
                (fragment.fragment_id,), (fragment.text,),
            )
            for fragment in fragments[-2:]
        ) if section_id == "identity" else ()
        sections.append(ComposedSection(
            section_id, sentences,
            notice="" if sentences or section_id == "portfolio" else NOTICE_INSUFFICIENT_EVIDENCE,
            news_rows=news,
        ))
    return render_report(
        "가나다전자", ComposedReport(tuple(sections), (identity, performance, identity)),
        fragments,
        PerformanceTable(
            caption="3개년 주요 실적", headers=("항목", "2023", "2024", "2025"),
            rows=(("매출액", "900", "1,000", "1,200"),), unit="억원", cite="조각 4·공식 IR",
        ),
        name_table=PortfolioNameTable(
            caption="제품·서비스 이름 2개", headers=("종류", "이름"),
            rows=(("제품", "가온검사기"), ("서비스", "나래정비")),
            row_fragment_ids=(("2",), ("3",)), name_count=2,
            counts_by_label=(("제품", 1), ("서비스", 1)), table_titles=("공식 제품·서비스",),
        ),
        corp_type="상장사", generated_at="2026-09-09", as_of_date="2026-09-09",
        analysis_period="2023~2025 완료 회계연도", latest_performance_period="2025년 4분기",
    )


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


@pytest.fixture
def news_enabled(monkeypatch):
    news_intake_switch._reset_process_news_intake_switch_for_tests()
    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()


def test_composer_source_only_cites_survive_first_json_db_validation_and_pdf(tmp_path, news_enabled):
    original = composer_report()
    assert v2_validation_problems(original) == ()
    source_only = [
        table for section in original.sections for table in section.tables
        if table.source_cites and not table.row_cites and not table.manifest_ref
    ]
    assert {tuple(table.source_cites) for table in source_only} == {
        ("[2]", "[3]"), ("[41]", "[42]"),
    }
    names = next(table for table in source_only if table.source_cites == ["[2]", "[3]"])
    news = next(table for table in source_only if table.source_cites == ["[41]", "[42]"])
    assert names.rows == [["제품", "가온검사기"], ["서비스", "나래정비"]]
    assert [row[0] for row in news.rows] == ["2026-09-02", "2026-08-20"]
    # 최초 저장을 보기 때문에 이미 손실된 JSON의 멱등성으로 통과할 수 없다.
    serialized = reports.report_to_json(original)
    payload_tables = [table for section in json.loads(serialized)["sections"] for table in section["tables"]]
    for table in source_only:
        encoded = next(item for item in payload_tables if item["caption"] == table.caption)
        assert encoded["source_cites"] == table.source_cites
        assert "row_cites" not in encoded and "manifest_ref" not in encoded
    target = tmp_path / "composer-citations.db"
    with db.connect(target) as conn:
        reports.save(conn, "fresh-composer", "CORP-FIXTURE", "", original)
    with db.connect(target) as conn:
        from_db = reports.load(conn, "fresh-composer")
    for restored in (reports.report_from_json(serialized), from_db):
        assert restored == original
        assert v2_validation_problems(restored) == ()
        assert report_sha256(restored) == report_sha256(original)
    pdf = build_pdf(from_db)
    (tmp_path / "composer-citations.pdf").write_bytes(pdf)
    page_texts = [page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages]
    pdf_text = _compact("\n".join(page_texts))
    # 행 값·기간·단위·행 순서·표만 인용한 부록 출처를 실제 PDF에서 확인한다.
    for token in ("가온검사기", "나래정비", "2023", "2024", "2025", "900", "1,000", "1,200", "억원", "2026-09-02", "2026-08-20"):
        assert _compact(token) in pdf_text
    assert pdf_text.index("가온검사기") < pdf_text.index("나래정비")
    assert "〔2〕" in pdf_text and "〔41〕" in pdf_text
    appendix = "\n".join(text for text in page_texts if "본문의 번호가 아래 원문을 가리킵니다." in text)
    appendix_numbers = [int(line.strip()) for line in appendix.splitlines() if line.strip().isdigit()]
    assert appendix_numbers[-6:] == [1, 2, 3, 4, 41, 42]
    # ★ 2026-09-10 갱신 — 예전에는 부록 글자에서 `news-fragment-41`을 직접
    #   찾았다. `984bcacf`(「산문 자기 인용 검수와 PDF 웹 출처 표시 보완」)가
    #   `core.citations.location_display`로 「원문 위치」 칸의 내부 조각 id를
    #   «일부러» 지웠다 — 근거는 `docs/reviews/2026-09-10-night-review.md`의
    #   「뉴스 위치는 저장된 사람용 접두부만 표시하고 내부 조각 ID를 지우며,
    #   원래 저장값과 봉인 해시는 유지한다」이다.
    # ★ 그래서 이 시험이 지키던 것을 «저장»과 «표시» 두 겹으로 나눠 그대로
    #   지킨다. 저장값 단정을 따로 두는 이유: 누가 id를 작성·저장 단계에서
    #   지워 버리면 `original`도 같이 바뀌어 위쪽 `restored == original`은
    #   초록으로 남는다 — 그 경우를 이 리터럴만 잡는다.
    stored_locations = {item.number: item.location for item in from_db.citations}
    assert stored_locations[41] == "기사 본문 · news-fragment-41"
    assert stored_locations[42] == "기사 본문 · news-fragment-42"
    # 표시 겹: 사람이 읽는 접두부는 두 행 모두에 남고, 내부 id는 인쇄되지 않는다.
    assert _compact(appendix).count(_compact("기사 본문")) == 2
    assert "news-fragment" not in _compact(appendix)
    for table in (names, news):
        for row in table.rows:
            for value in row:
                assert _compact(value) in pdf_text


def test_empty_source_cites_keep_legacy_table_bytes():
    from src.features.pipeline.port import ReportTable

    table = ReportTable(caption="실적", headers=["연도", "매출액"], rows=[["2025", "1,200"]], cite="[1]")
    actual = json.dumps(reports._table_to_dict(table), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    expected = '{"caption":"실적","headers":["연도","매출액"],"rows":[["2025","1,200"]],"cite":"[1]","numeric":false,"raw_rows":[],"scale_divisor":"","scale_places":0,"display_unit":""}'
    assert actual == expected.encode("utf-8")


def test_full_manifest_row_bindings_survive_real_database(tmp_path: Path):
    output, _writer, _reviewer, _diagram = _run_full(flow=True)
    original = output.report
    assert v2_validation_problems(original) == ()
    with db.connect(tmp_path / "full-manifest.db") as conn:
        reports.save(conn, "fresh-full", "CORP-FULL", "", original)
    with db.connect(tmp_path / "full-manifest.db") as conn:
        restored = reports.load(conn, "fresh-full")
    assert restored is not None
    assert v2_validation_problems(restored) == ()
    assert reports.report_to_json(restored) == reports.report_to_json(original)
    assert report_sha256(restored) == report_sha256(original)
    assert restored.public_structure_manifest == original.public_structure_manifest
    for before_section, after_section in zip(original.sections, restored.sections, strict=True):
        for before, after in zip(before_section.tables, after_section.tables, strict=True):
            # 원본 evidence_rows는 공개 저장 대상이 아니므로 기존 규약대로 비워진다.
            assert after == replace(before, evidence_rows=[])
    assert any(table.row_cites and table.manifest_ref for section in restored.sections for table in section.tables)
