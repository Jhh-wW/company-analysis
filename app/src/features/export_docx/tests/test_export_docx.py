"""워드 내보내기 시험.

★ 확인해야 하는 것 (팀장 지시) — 만든 바이트를 python-docx로 다시 열어서:
  ① 항목 개수  ② 빈칸 사유 존재  ③ 표 존재  ④ 출처 목록 존재
  ⑤ 요구역량 존재  ⑥ 파일이 안 깨지는지

  + P3(화면 ↔ 워드 내용 일치) 관련 — 화면(result.html)이 쓰는 것과 «같은»
    문구 상수(`CELL_LABELS`)·같은 함수(`grade_message`·`render_sources`)를
    그대로 쓰는지를 값 단위로 검증한다. 형태별로 문구를 다시 짓지 않았다는
    증거다.
"""

from __future__ import annotations

import io

from docx import Document

from src.core.constants import CELL_LABELS, RAW_SOURCE_LABEL, RAW_SOURCE_NOTE
from src.features.export_docx.constants import (
    EMPTY_DEFAULT_REASON,
    EMPTY_PREFIX,
    FILENAME_FALLBACK,
    HEADING_COLLECTION,
    HEADING_SOURCES,
    REQUIREMENTS_NOTE,
)
from src.features.export_docx.logic import (
    build_content_disposition,
    build_docx,
    build_download_filename,
)
from src.features.grading.logic import grade_message
from src.features.pipeline.port import (
    Grade,
    Report,
    ReportSection,
    ReportTable,
    SourceStatus,
)
from src.features.provenance.sources import Source, SourceKind, render_sources

# ══════════════════════════════════════════════════════════
# 시험용 원본 — 화면(demo.py)과 같은 모양의 Report 하나
# ══════════════════════════════════════════════════════════


def _make_report(**overrides) -> Report:
    sections = [
        ReportSection(
            cell="1",
            title=CELL_LABELS["1"],
            lines=[("반도체 장비로 매출 70%를 올린다", "2")],
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
                    headers=["구분", "1인평균급여액", "평균근속연수"],
                    rows=[["미등기임원", "9천만원", "5년"], ["직원", "6천만원", "3년"]],
                    cite="전자공시 사업보고서",
                    numeric=False,
                )
            ],
        ),
    ]
    citations = [
        Source(
            number=2,
            kind=SourceKind.FILING,
            label="감사보고서 제16장 수익인식 주석",
            disclosed_at="2024-03-15",
            collected_at="2026-08-13",
        ),
        Source(
            number=5,
            kind=SourceKind.NEWS,
            label="OO경제 반도체 훈풍 기사",
            published_at="2025-03-12",
            domain="mk.co.kr",
        ),
    ]
    sources = [
        SourceStatus("전자공시", "ok", "감사보고서 2024-03-15"),
        SourceStatus("홈페이지", "failed", "접속 실패"),
        SourceStatus("뉴스", "none", "검색 3건 · 채택 0건"),
    ]
    base = dict(
        company="에스엠",
        job="마케팅",
        corp_type="상장사",
        grade=Grade.PARTIAL,
        sections=sections,
        requirements=["React·TypeScript 사용 경험", "3년 이상 실무 경력"],
        sources=sources,
        citations=citations,
        cells={"1": True, "2": False},
        shortfall_reasons=["채워진 항목이 3개입니다 (4개 이상 필요)"],
        generated_at="2026-08-13",
    )
    base.update(overrides)
    return Report(**base)


def _open(data: bytes) -> Document:
    return Document(io.BytesIO(data))


def _all_text(doc: Document) -> str:
    """문단·표 안의 글자를 전부 이어붙인다 — 존재 여부를 문자열 검색으로 확인한다."""
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    return "\n".join(parts)


# ══════════════════════════════════════════════════════════
# ① 파일이 안 깨지는가 — 바이트를 다시 열 수 있어야 한다
# ══════════════════════════════════════════════════════════


def test_바이트를_다시_열_수_있다():
    data = build_docx(_make_report())
    assert isinstance(data, bytes)
    assert len(data) > 0
    doc = _open(data)  # 예외가 나면 시험이 실패한다
    assert len(doc.paragraphs) > 0


# ══════════════════════════════════════════════════════════
# ② 항목 개수 — report.sections 하나마다 제목이 하나씩 나온다
# ══════════════════════════════════════════════════════════


def test_항목_개수가_섹션_개수와_같다():
    report = _make_report()
    doc = _open(build_docx(report))
    headings = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
    # 회사명(레벨0)은 Heading 스타일이 아니라 Title 스타일이라 여기 안 걸린다.
    section_headings = [h for h in headings if h[:1].isdigit() or h.startswith("附")]
    # ★ 5번(요구역량) 칸이 sections에 없으면 맨 끝에 한 번 보충된다 — result.html의
    #   `ns.shown_5` 처리와 같다(요구역량은 빠뜨리지 않는다).
    expected_count = len(report.sections)
    if not any(s.cell == "5" for s in report.sections):
        expected_count += 1
    assert len(section_headings) == expected_count
    for section in report.sections:
        assert f"{section.cell}. {section.title}" in section_headings


def test_회사명이_문서_제목으로_들어간다():
    report = _make_report()
    doc = _open(build_docx(report))
    assert doc.paragraphs[0].text == report.company


def test_검증된_본문과_문장별_출처와_근거원문을_모두_낸다():
    """P-117·P-118·P-127 — 워드도 실제 번호만 붙이고 내부 이름은 감춘다."""
    section = ReportSection(
        cell="1",
        title=CELL_LABELS["1"],
        lines=[("원문 사업 문장입니다.", "조각 2·사업보고서")],
        prose_lines=[("검증된 표시용 사업 문장입니다.", "조각 2·사업보고서")],
    )
    text = _all_text(_open(build_docx(_make_report(sections=[section]))))

    assert "검증된 표시용 사업 문장입니다. 〔2〕" in text
    assert "원문 사업 문장입니다. 〔2〕" in text
    assert "조각 2·사업보고서" not in text
    assert f"{RAW_SOURCE_LABEL} (1문장) — {RAW_SOURCE_NOTE}" in text


# ══════════════════════════════════════════════════════════
# ③ 빈칸 사유 존재 — S6, 지우지 않는다
# ══════════════════════════════════════════════════════════


def test_빈칸_사유가_그대로_들어간다():
    report = _make_report()
    text = _all_text(_open(build_docx(report)))
    empty_section = next(s for s in report.sections if not s.is_filled)
    assert EMPTY_PREFIX in text
    assert empty_section.empty_reason in text


def test_사유가_없는_빈칸은_기본_문구를_쓴다():
    report = _make_report(
        sections=[ReportSection(cell="3", title=CELL_LABELS["3"], empty_reason="")]
    )
    text = _all_text(_open(build_docx(report)))
    assert EMPTY_DEFAULT_REASON in text


# ══════════════════════════════════════════════════════════
# ④ 표 존재 — 숫자는 문장이 아니라 워드 표로 낸다 (D13)
# ══════════════════════════════════════════════════════════


def test_숫자_표가_워드_표로_들어간다():
    report = _make_report()
    doc = _open(build_docx(report))
    expected_table = next(
        t for s in report.sections for t in s.tables
    )
    # ★ 붙 표 + 수집현황 표 = 최소 2개. 이 시험용 원본에는 정확히 이 둘뿐이다.
    assert len(doc.tables) == 2
    found = doc.tables[0]
    assert found.cell(0, 0).text == expected_table.headers[0]
    assert found.cell(1, 0).text == expected_table.rows[0][0]
    assert found.cell(1, 1).text == expected_table.rows[0][1]


def test_표_열_개수가_안_맞아도_안_깨진다():
    """방어적 — 정상 데이터라면 안 생기지만, 어긋나도 예외로 죽지 않는다."""
    bad_table = ReportTable(
        caption="테스트 표",
        headers=["a", "b", "c"],
        rows=[["1", "2"]],  # 열 하나 모자람
        numeric=False,
    )
    report = _make_report(
        sections=[ReportSection(cell="附", title=CELL_LABELS["附"], tables=[bad_table])]
    )
    data = build_docx(report)  # 예외가 나면 실패
    doc = _open(data)
    assert doc.tables[0].cell(1, 2).text == ""


# ══════════════════════════════════════════════════════════
# ⑤ 출처 목록 존재 — render_sources() 결과를 그대로 옮긴다 (P3)
# ══════════════════════════════════════════════════════════


def test_출처_목록이_들어간다():
    report = _make_report()
    text = _all_text(_open(build_docx(report)))
    assert HEADING_SOURCES in text
    for source in report.citations:
        assert source.label in text


def test_출처_문구는_render_sources를_그대로_쓴다():
    """출처 한 줄 한 줄의 표기(공시일·수집일·언론사 등)를 새로 짓지 않았는지 —
    render_sources() 결과의 항목 줄이 워드 문서 안에 한 글자도 안 바뀌고
    들어 있어야 한다 (헤더 줄 `[출처]`만 중복이라 뺀다).
    """
    report = _make_report()
    expected_block = render_sources(report.citations)
    expected_lines = [line for line in expected_block.splitlines() if line != "[출처]"]

    doc_paragraphs = [p.text for p in _open(build_docx(report)).paragraphs]
    for line in expected_lines:
        assert line in doc_paragraphs, f"render_sources 줄이 그대로 없다: {line!r}"


def test_출처가_없으면_출처_구획을_안_만든다():
    report = _make_report(citations=[])
    text = _all_text(_open(build_docx(report)))
    assert HEADING_SOURCES not in text


# ══════════════════════════════════════════════════════════
# 수집 현황 — P2(출력 구성 누락 0건)와 직결
# ══════════════════════════════════════════════════════════


def test_수집_현황_표가_들어간다():
    report = _make_report()
    doc = _open(build_docx(report))
    text = _all_text(doc)
    assert HEADING_COLLECTION in text
    collection_table = doc.tables[-1]
    assert collection_table.cell(0, 0).text == "소스"
    assert collection_table.cell(1, 0).text == report.sources[0].name
    assert "⭕ 찾음" in collection_table.cell(1, 1).text
    assert "⚠️ 못 가져옴" in collection_table.cell(2, 1).text
    assert "❌ 없음" in collection_table.cell(3, 1).text


# ══════════════════════════════════════════════════════════
# ⑥ 요구역량 존재 — 5번 칸(공고 원문)
# ══════════════════════════════════════════════════════════


def test_요구역량이_원문_그대로_들어간다():
    report = _make_report()
    text = _all_text(_open(build_docx(report)))
    assert REQUIREMENTS_NOTE in text
    for requirement in report.requirements:
        assert requirement in text


def test_요구역량이_없으면_빈칸_사유가_붙는다():
    report = _make_report(requirements=[])
    text = _all_text(_open(build_docx(report)))
    assert "올려주신 공고에서 요구 조건을 뽑지 못했습니다" in text


def test_5번_섹션이_기록에_없어도_요구역량_칸이_한_번은_나온다():
    """demo.py의 5번 미포함 기록과 같은 상황 — result.html의 `ns.shown_5` 처리와 동치."""
    report = _make_report(
        sections=[ReportSection(cell="1", title=CELL_LABELS["1"], lines=[("문장", "1")])],
    )
    text = _all_text(_open(build_docx(report)))
    assert f"5. {CELL_LABELS['5']}" in text
    assert text.count(f"5. {CELL_LABELS['5']}") == 1


# ══════════════════════════════════════════════════════════
# 등급 라벨 — 완성이면 안 띄운다 (result.html과 같은 조건)
# ══════════════════════════════════════════════════════════


def test_부분_완성이면_등급_라벨이_붙는다():
    report = _make_report(grade=Grade.PARTIAL)
    text = _all_text(_open(build_docx(report)))
    expected_note = grade_message(Grade.PARTIAL, report.filled_count)
    assert expected_note in text
    assert "🟡" in text
    for reason in report.shortfall_reasons:
        assert reason in text


def test_완성이면_등급_라벨이_없다():
    report = _make_report(grade=Grade.COMPLETE, shortfall_reasons=[])
    text = _all_text(_open(build_docx(report)))
    assert "🟡" not in text
    assert "🔴" not in text


def test_미완성이면_빨간_라벨이_붙는다():
    report = _make_report(grade=Grade.INCOMPLETE)
    text = _all_text(_open(build_docx(report)))
    assert "🔴" in text


# ══════════════════════════════════════════════════════════
# 파일명 규칙 — 확정/07_출력/1_흐름/01_세형태.md §워드로 받기
# ══════════════════════════════════════════════════════════


def test_파일명_형식():
    report = _make_report(company="에스엠", job="마케팅", generated_at="2026-08-15")
    assert build_download_filename(report) == "에스엠_마케팅_2026-08-15.docx"


def test_파일명_금지문자를_지운다():
    report = _make_report(company='나*쁜/회:사?"<>|', job="영업", generated_at="2026-08-15")
    filename = build_download_filename(report)
    assert filename == "나쁜회사_영업_2026-08-15.docx"
    for ch in '\\/:*?"<>|':
        assert ch not in filename


def test_생성일이_없으면_오늘_날짜로_대신한다():
    report = _make_report(generated_at="")
    filename = build_download_filename(report)
    assert filename.endswith(".docx")
    # YYYY-MM-DD 10글자 날짜가 붙어 있어야 한다 (오늘 날짜는 시험 시각마다 달라 형식만 본다).
    date_part = filename[:-5].rsplit("_", 1)[-1]
    assert len(date_part) == 10 and date_part.count("-") == 2


def test_회사명이_전부_금지문자면_자리표시자를_쓴다():
    report = _make_report(company="///", job="영업", generated_at="2026-08-15")
    filename = build_download_filename(report)
    assert filename == f"{FILENAME_FALLBACK}_영업_2026-08-15.docx"


# ══════════════════════════════════════════════════════════
# 다운로드 헤더 — 한글 파일명이 깨지지 않는 법 (RFC 5987)
# ══════════════════════════════════════════════════════════


def test_content_disposition_한글_파일명():
    header = build_content_disposition("에스엠_마케팅_2026-08-15.docx")
    assert header.startswith("attachment; filename=")
    assert "filename*=UTF-8''" in header
    # ASCII 대체 이름에는 한글이 없어야 한다(오래된 클라이언트가 이 부분만 읽는다).
    ascii_part = header.split('filename="')[1].split('"')[0]
    assert all(ord(c) < 128 for c in ascii_part)
    # UTF-8 부분을 다시 풀면 원래 파일명이 나와야 한다.
    import urllib.parse

    encoded = header.split("UTF-8''")[1]
    assert urllib.parse.unquote(encoded) == "에스엠_마케팅_2026-08-15.docx"


def test_content_disposition_전부_한글이면_대체_이름을_쓴다():
    header = build_content_disposition("에스엠_마케팅.docx")
    ascii_part = header.split('filename="')[1].split('"')[0]
    assert ascii_part  # 비어 있지 않다 — 빈 filename은 일부 클라이언트에서 깨진다
