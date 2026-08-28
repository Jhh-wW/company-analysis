"""렌더 변환을 못 박는다 (엔진 v2 소단계 3-4a).

★ 여기서 지키는 것:
  ① 변환 정합 — 문장 글·인용 번호·등급 표지가 그대로 pipeline Report에 실린다.
  ② 장 삭제 없음 — 안내문만 있는 장도 prose로 렌더되고 is_filled다.
  ③ 부록은 «인용된 조각만», 번호는 조각 번호 그대로 (본문 [n]과 1:1).
  ④ 4장 실적표는 기존 ReportTable로 변환된다.
  ⑤ 깨진 인용 id는 틀린 번호 대신 표기 제외로 방어한다.
"""

from __future__ import annotations

from typing import Any, Optional

from src.features.composer.constants import (
    CITATION_STYLE_INLINE,
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
    NOTICE_INSUFFICIENT_EVIDENCE,
    SECTION_IDS,
    SECTION_TITLES,
)
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    PerformanceTable,
    fragments_from_raw,
)
from src.features.composer.render import (
    ENGINE_V2_SCHEMA_VERSION,
    INTERPRETATION_MARKER,
    render_report,
    sentence_display_text,
)
from src.features.pipeline.port import Grade
from src.features.provenance.sources import Source, SourceKind


# ══════════════════════════════════════════════════════════
# 시험 재료
# ══════════════════════════════════════════════════════════


def _raw_fragments() -> dict[int, dict[str, Any]]:
    return {
        1: {"종류": "사업내용", "원문": "가나다전자는 반도체 검사 장비 전문기업이다."},
        2: {
            "종류": "홈페이지",
            "원문": "고객의 성공이 최우선 가치다.",
            "출처": "https://www.ganada.example/about",
            "문서일": "2026-08-01",
        },
        3: {
            "종류": "공식 IR",
            "원문": "2025년 매출액은 1,200억원이다.",
            "출처": "https://www.ganada.example/ir.pdf",
            "문서명": "2025 IR자료",
            "원문위치": "PDF p.3 1문단",
        },
        4: {"종류": "재무", "원문": ""},  # 빈 원문 — 조각 집합에서 빠져야 한다
    }


def _table(cite: str = "조각 3·공식 IR") -> PerformanceTable:
    return PerformanceTable(
        caption="3개년 주요 실적",
        headers=("항목", "2023", "2024", "2025"),
        rows=(("매출액", "900", "1,000", "1,200"),),
        unit="억원",
        cite=cite,
    )


def _sentence(
    text: str,
    citations: tuple[str, ...] = (),
    grade: str = GRADE_CONFIRMED,
) -> ComposedSentence:
    return ComposedSentence(text=text, citations=citations, grade=grade)


def _composed_report() -> ComposedReport:
    sections = []
    for section_id in SECTION_IDS:
        if section_id == "identity":
            sections.append(
                ComposedSection(
                    section_id=section_id,
                    sentences=(
                        _sentence("반도체 검사 장비를 주력으로 한다.", ("1", "3")),
                        _sentence(
                            "검사 장비 축이 사업의 무게중심으로 읽힌다.",
                            ("1",),
                            GRADE_INTERPRETED,
                        ),
                    ),
                )
            )
        elif section_id == "past_changes":
            sections.append(
                ComposedSection(
                    section_id=section_id,
                    sentences=(
                        _sentence("2025년 매출액은 1,200억원이다.", ("3",)),
                    ),
                )
            )
        else:
            sections.append(
                ComposedSection(
                    section_id=section_id,
                    sentences=(),
                    notice=NOTICE_INSUFFICIENT_EVIDENCE,
                )
            )
    summary = (
        _sentence("반도체 검사 장비 중심의 사업 구조다.", ("1",)),
        _sentence("최근 매출은 성장 흐름이다.", ("3",)),
        _sentence("고객 가치를 앞세운 문화를 내세운다.", ("2",)),
    )
    return ComposedReport(sections=tuple(sections), summary=summary)


def _rendered(
    performance_table: Optional[PerformanceTable] = None,
    fragments: Any = None,
):
    return render_report(
        "가나다전자",
        _composed_report(),
        _raw_fragments() if fragments is None else fragments,
        performance_table if performance_table is not None else _table(),
        corp_type="상장사",
        as_of_date="2026-08-24",
        # ★ 이 시험은 «문장마다 근거가 붙는가»를 본다. 화면 기본값은 절충안
        #   (같은 출처 묶음은 마지막에만 번호)이라 표기 방식을 여기서 못 박는다
        #   — 기본값이 또 바뀌어도 이 시험의 의도가 흔들리지 않게.
        citation_style=CITATION_STYLE_INLINE,
    )


# ══════════════════════════════════════════════════════════
# ① 변환 정합 — 문장·인용·라벨 보존
# ══════════════════════════════════════════════════════════


def test_문장과_인용번호와_해석표지가_그대로_실린다():
    report = _rendered()
    identity = report.sections[0]
    assert identity.cell == "identity"
    first, second = identity.prose_lines
    # 확인 문장: 원문 + [n][m], 해석 표지는 없다
    assert first[0] == "반도체 검사 장비를 주력으로 한다. [1][3]"
    assert INTERPRETATION_MARKER not in first[0]
    # 해석 문장: 인용 뒤에 « — 해석» 표지
    assert second[0] == (
        "검사 장비 축이 사업의 무게중심으로 읽힌다. [1]" + INTERPRETATION_MARKER
    )
    # cite 칸은 비운다 — 번호는 글 안의 [n]이 정본이다
    assert all(cite == "" for _text, cite in identity.prose_lines)


def test_아홉_장이_정본_순서와_번호로_전부_나온다():
    report = _rendered()
    assert [section.cell for section in report.sections] == list(SECTION_IDS)
    assert [section.display_number for section in report.sections] == [
        str(index) for index in range(1, 10)
    ]
    for section in report.sections:
        assert section.title == SECTION_TITLES[section.cell]


def test_자료부족_장은_안내문이_본문으로_남고_is_filled다():
    report = _rendered()
    culture = next(s for s in report.sections if s.cell == "culture")
    assert culture.prose_lines == [(NOTICE_INSUFFICIENT_EVIDENCE, "")]
    # PDF·웹 렌더는 is_filled가 아니면 제목만 찍는다 — 안내문이 사라지면 안 된다
    assert culture.is_filled


def test_요약도_같은_인용_표기로_실린다():
    report = _rendered()
    texts = [item.text for item in report.summary_items]
    assert texts[0] == "반도체 검사 장비 중심의 사업 구조다. [1]"
    assert len(texts) == 3


# ══════════════════════════════════════════════════════════
# ③ 출처 부록 — 인용된 조각만, 번호 1:1
# ══════════════════════════════════════════════════════════


def test_부록은_인용된_조각만_조각번호_그대로_싣는다():
    report = _rendered()
    numbers = [source.number for source in report.citations]
    # 본문 [1][3] + 요약 [2] — 빈 원문 조각 4는 애초에 조각 집합에 없다
    assert numbers == [1, 2, 3]
    by_number = {source.number: source for source in report.citations}
    ir = by_number[3]
    assert isinstance(ir, Source)
    assert ir.kind is SourceKind.OTHER  # 출처 URL이 있는 조각
    assert ir.title == "2025 IR자료"
    assert ir.url == "https://www.ganada.example/ir.pdf"
    assert ir.location == "PDF p.3 1문단"
    assert ir.publisher == "가나다전자"
    filing = by_number[1]
    assert filing.kind is SourceKind.FILING  # URL 없는 전자공시 절
    assert "사업내용" in filing.label
    homepage = by_number[2]
    assert homepage.collected_at == "2026-08-01"  # 원시 dict의 «문서일» 보존


def test_부록_사용_장_목록이_실제_인용_장과_같다():
    report = _rendered()
    by_number = {source.number: source for source in report.citations}
    assert by_number[1].used_in == ["identity"]
    # 조각 3: identity 본문 + past_changes 본문 + 4장 실적표 cite
    assert by_number[3].used_in == ["identity", "past_changes"]
    # 조각 2는 요약에서만 인용 — 부록에는 실리되 사용 장은 비운다
    assert by_number[2].used_in == []


def test_깨진_인용은_표기와_부록에서_빠진다():
    broken = ComposedReport(
        sections=(
            ComposedSection(
                section_id="identity",
                sentences=(
                    _sentence("실존 근거와 깨진 근거를 함께 단 문장.", ("1", "99")),
                ),
            ),
        ),
        summary=(),
    )
    report = render_report("가나다전자", broken, _raw_fragments(), None)
    text = report.sections[0].prose_lines[0][0]
    assert "[1]" in text and "[99]" not in text
    assert [source.number for source in report.citations] == [1]


# ══════════════════════════════════════════════════════════
# ④ 4장 실적표
# ══════════════════════════════════════════════════════════


def test_실적표는_past_changes에_기존_ReportTable로_실린다():
    report = _rendered()
    past = next(s for s in report.sections if s.cell == "past_changes")
    assert len(past.tables) == 1
    table = past.tables[0]
    assert table.headers == ["항목", "2023", "2024", "2025"]
    assert table.rows == [["매출액", "900", "1,000", "1,200"]]
    assert table.numeric is True
    assert "단위: 억원" in table.caption
    assert table.cite == "조각 3·공식 IR"
    # 다른 장에는 표가 없다
    assert all(not s.tables for s in report.sections if s.cell != "past_changes")


def test_실적표_cite가_없는_조각을_가리키면_표기를_뺀다():
    report = _rendered(performance_table=_table(cite="조각 77·공식 IR"))
    past = next(s for s in report.sections if s.cell == "past_changes")
    assert past.tables[0].cite == ""  # 틀린 번호를 인쇄하지 않는다
    assert 77 not in [source.number for source in report.citations]


# ══════════════════════════════════════════════════════════
# 기타 계약
# ══════════════════════════════════════════════════════════


def test_schema_version과_메타가_v2로_찍힌다():
    report = _rendered()
    assert report.schema_version == ENGINE_V2_SCHEMA_VERSION
    assert report.company == "가나다전자"
    assert report.corp_type == "상장사"
    assert report.as_of_date == "2026-08-24"
    # 평가 전 생성물을 근거 없이 «완성»으로 도장 찍지 않는다.
    assert report.grade is Grade.PARTIAL
    assert report.fact_records == []  # 사실 원장을 위조하지 않는다


def test_어댑터_튜플_입력도_같은_번호로_렌더된다():
    fragments = fragments_from_raw(_raw_fragments())
    report = render_report(
        "가나다전자", _composed_report(), fragments, _table()
    )
    assert [source.number for source in report.citations] == [1, 2, 3]
    # 어댑터에는 «문서일»이 없으므로 날짜는 비운다 (지어내지 않는다)
    homepage = next(s for s in report.citations if s.number == 2)
    assert homepage.collected_at == ""


def test_sentence_display_text_숫자아닌_조각id도_안전하다():
    numbers = {"1": 1, "web": 2}
    sentence = _sentence("본문.", ("web",))
    assert sentence_display_text(sentence, numbers) == "본문. [2]"
