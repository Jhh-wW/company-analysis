"""``build_public_projection``이 «렌더가 하던 계산»을 봉인 시점으로 옮겼는지 지킨다.

★ 왜 손으로 지은 Report를 쓰지 않나 — 옆 파일
  ``test_source_verification_label_v2_pipeline.py``가 남긴 교훈이다. 손으로
  ``prose_lines`` 문자열을 지으면 render가 «인용 번호를 언제 숨기는가»라는
  진짜 규칙이 재현되지 않아, 바로 그 규칙 때문에 생긴 결함을 그물이 통과했다.
  여기서도 ``render_report()``를 실제로 통과시킨 v2 보고서만 쓴다.

★ 이 파일이 지키는 것 (설계 §02-3 불변식)
    I2 — 문단을 이어붙인 글자 == 문장을 이어붙인 글자, 문단 번호는 1부터
    I3 — 모든 FactRecord가 «정확히 한 장»의 ledger에 속한다
    I4 — 장별 등급 기여 ∪ 요약 기여 == ``report.source_grades``
    I5 — 봉인된 도식 == ``table_visualization()`` 결과
    I6 — 봉인된 검증 라벨 == ``source_verification_label()`` 결과

★ 표의 ``manifest_ref``를 시험에서 채워 넣는 이유는 ``_sealed`` 주석에 적었다.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from src.features.composer.constants import (
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
    IDENTITY_TABLE_SECTION_ID,
    SECTION_IDS,
)
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
    PerformanceTable,
)
from src.features.composer.render import COMPOSITION_TABLE_SECTION_ID, render_report
from src.features.pipeline.port import FactRecord, Report
from src.features.provenance.sources import visible_citations
from src.features.report_standard.constants import SECTION_BY_ID
from src.features.report_standard.cover_metrics import cover_metrics
from src.features.report_standard.period_summary import period_summary_from_table
from src.features.report_standard.public_projection import build_public_projection
from src.features.report_standard.section_content import (
    summary_topic,
    source_verification_label,
)
from src.features.report_standard.visualization import table_visualization
from src.shared.report_generation.canonical import (
    report_public_content_projection,
    table_public_projection,
)
from src.shared.report_generation.models import canonical_sha256, canonical_value
from src.shared.report_generation.public_projection import (
    PUBLIC_PROJECTION_VERSION,
    PublicProjectionError,
    build_report_digest,
)


# ══════════════════════════════════════════════════════════
# 재료 — render_report를 «실제로» 통과한 v2 보고서 하나
# ══════════════════════════════════════════════════════════

#: 조각 1은 본문 문장이 쓰고, 조각 2는 «1장 표만» 쓴다. 두 갈래(문장 등급이
#: 있는 자료 / 표·도식만 쓴 자료)를 한 보고서에서 함께 확인하기 위해서다.
_원문_문장용 = "회사는 2024년에 매출 100억원을 기록했으며, 이는 직전 회계연도보다 늘어난 수치다."
_원문_표용 = "회사는 스스로를 글로벌 콘텐츠 기업으로 규정하며 음악·영상 사업을 영위한다."

_확인_문장 = "회사는 음악·영상 사업을 영위한다."
_해석_문장 = "이는 업계 평균을 웃도는 성과로 해석된다."


def _fragments() -> dict[int, dict[str, str]]:
    return {
        1: {"종류": "사업내용", "원문": _원문_문장용},
        2: {"종류": "사업내용", "원문": _원문_표용},
    }


def _composed(*, uncited_summary: bool = False) -> ComposedReport:
    """아홉 장 전부에 «확인 + 해석» 두 문장을 담은 v2 보고서.

    ★ 해석 문장이 확인 문장과 «같은 조각»을 인용한다 — render의 절충안 규칙이
      해석 쪽 ``[1]``을 숨기는 바로 그 배치다. 봉인된 등급 기여가 화면 글자가
      아니라 ``source_grades``에서 나왔는지 이 배치가 갈라 준다.
    """

    sections = []
    for section_id in SECTION_IDS:
        flow_rows: tuple[FlowRow, ...] = ()
        if section_id == IDENTITY_TABLE_SECTION_ID:
            # 1장 칸 이름은 카드 도식(kind="card")을 부른다.
            flow_rows = (
                FlowRow(
                    cells=("글로벌 콘텐츠 기업", "음악·영상", "해석 없음"),
                    citations=("2",),
                ),
            )
        if section_id == COMPOSITION_TABLE_SECTION_ID:
            # 2장 칸 이름은 화살표 흐름 도식(kind="flow")을 부른다.
            flow_rows = (
                FlowRow(
                    cells=("음악 자산", "음반", "구독", "반복 수익"),
                    citations=("1",),
                ),
            )
        sections.append(
            ComposedSection(
                section_id=section_id,
                sentences=(
                    ComposedSentence(
                        text=_확인_문장, citations=("1",), grade=GRADE_CONFIRMED
                    ),
                    ComposedSentence(
                        text=_해석_문장, citations=("1",), grade=GRADE_INTERPRETED
                    ),
                ),
                flow_rows=flow_rows,
            )
        )
    return ComposedReport(
        sections=tuple(sections),
        summary=(
            ComposedSentence(
                text="콘텐츠 기업이다.", citations=("1",), grade=GRADE_CONFIRMED
            ),
            ComposedSentence(
                text="해외를 넓힌다.", citations=("1",), grade=GRADE_CONFIRMED
            ),
            ComposedSentence(
                text="성장 국면으로 읽힌다.",
                # 인용이 없으면 render가 «어느 장 이야기인지» 못 짚어
                # ``SummaryItem.section_id``를 빈 글자로 둔다
                # (``_summary_source_section``). 그 경우는 아래 전용
                # 시험에서만 만든다 — 이유는 그 시험 docstring 참조.
                citations=() if uncited_summary else ("1",),
                grade=GRADE_INTERPRETED,
            ),
        ),
    )


def _performance_table() -> PerformanceTable:
    """4장 실적표 — 추이 도식·표지 지표 띠·3개년 변화 띠가 전부 여기서 나온다."""

    return PerformanceTable(
        caption="완료 사업연도 주요 재무",
        headers=("사업연도", "매출액", "영업이익", "당기순이익"),
        rows=(
            ("2025", "5,940", "1,550", "1,200"),
            ("2024", "5,800", "1,400", "-34"),
            ("2023", "5,665", "1,700", "1,834"),
        ),
        unit="억원",
        cite="조각 1·사업내용",
    )


def _composition_table() -> PerformanceTable:
    """2장 구성표 — 100% 구성 도식(kind="composition")을 부른다."""

    return PerformanceTable(
        caption="2025년 부문별 매출 구성",
        headers=("부문", "매출 비중"),
        rows=(("음반·음원", "31.4"), ("매니지먼트", "48.6"), ("기타", "20.0")),
        unit="",
        cite="조각 1·사업내용",
    )


def _sealed(report: Report) -> Report:
    """모든 표에 ``manifest_ref``를 채운다.

    ★ 왜 시험이 채우나 — ``render_report``는 FULL의 ``public_structure_seal``을
      받았을 때만 ``manifest_ref``를 붙인다(render.py의 sealed_tables 갈래).
      seal 없이 부른 보고서의 표는 ``manifest_ref``가 빈 문자열이고, S1
      ``PublicTableBlock``은 그 값이 SHA-256 64자리일 것을 요구한다. 여기서는
      seal이 붙였을 «모양»만 흉내 내 표 자체는 한 글자도 바꾸지 않는다.
      (seal 없는 보고서를 봉인할 수 있어야 하는가는 S3가 정할 문제다 —
      이 시험의 판정 대상이 아니다.)
    """

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
        for section in report.sections
    ]
    return replace(report, sections=sections)


def _report(*, uncited_summary: bool = False) -> Report:
    rendered = render_report(
        "가나다전자",
        _composed(uncited_summary=uncited_summary),
        _fragments(),
        _performance_table(),
        table_presentation="trend",
        composition_tables=(_composition_table(),),
        as_of_date="2026-09-01",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2026년 2분기 잠정",
    )
    return _sealed(rendered)


def _fact(fact_id: str, section_owner: str) -> FactRecord:
    return FactRecord(
        fact_id=fact_id,
        legal_entity="가나다전자",
        subject_scope="내부 감사용 범위 — 공개 화면에 나오면 안 된다",
        relationship_or_action="내부 감사용 관계",
        claim=_확인_문장,
        claim_type="identity_summary",
        section_owner=section_owner,
    )


def _with_facts(report: Report) -> Report:
    """장부를 실은 보고서 — 1장에 두 건, 4장에 한 건."""

    facts = [
        _fact("fact-identity-1", "identity"),
        _fact("fact-identity-2", "identity"),
        _fact("fact-past-1", "past_changes"),
    ]
    owners = {"identity": ["fact-identity-1", "fact-identity-2"], "past_changes": ["fact-past-1"]}
    sections = [
        replace(section, fact_ids=list(owners.get(section.cell, [])))
        for section in report.sections
    ]
    return replace(report, sections=sections, fact_records=facts)


def _section_of(projection, cell: str):
    for block in projection.sections:
        if block.display.cell == cell:
            return block
    raise AssertionError(f"{cell} 장 블록이 없습니다")


def _report_section(report: Report, cell: str):
    for section in report.sections:
        if section.cell == cell:
            return section
    raise AssertionError(f"{cell} 장이 없습니다")


# ══════════════════════════════════════════════════════════
# ① I5 — 도식은 순수 함수 결과와 같아야 한다
# ══════════════════════════════════════════════════════════


def test_builder의_visual은_table_visualization_결과와_같다() -> None:
    """구성·추이·흐름·카드 네 종류가 모두 순수 함수 값 그대로 봉인된다."""

    report = _report()
    projection = build_public_projection(report)

    kinds: list[str] = []
    for section in report.sections:
        block = _section_of(projection, section.cell)
        expected_visuals = [
            (index, table_visualization(table))
            for index, table in enumerate(section.tables)
        ]
        expected_visuals = [
            (index, chart) for index, chart in expected_visuals if chart is not None
        ]
        assert len(block.display.visuals) == len(expected_visuals)
        for visual, (table_index, chart) in zip(block.display.visuals, expected_visuals):
            kinds.append(chart.kind)
            assert visual.table_index == table_index
            assert visual.kind == chart.kind
            assert visual.caption == chart.caption
            assert visual.unit == chart.unit
            assert visual.note == chart.note
            assert visual.reading == chart.reading
            assert visual.items == tuple(
                (
                    point.label,
                    point.display,
                    f"{point.ratio:.4f}",
                    point.below,
                )
                for point in chart.items
            )
            assert visual.series == tuple(
                (
                    series.label,
                    "risk" if series.risk else "",
                    tuple(
                        {
                            "label": point.label,
                            "display": point.display,
                            "ratio_text": f"{point.ratio:.4f}",
                            "below": point.below,
                        }
                        for point in series.points
                    ),
                )
                for series in chart.series
            )
            assert visual.flows == chart.flows
            assert visual.cards == tuple(
                (
                    card.title,
                    {"fields": [[field.label, field.value] for field in card.fields]},
                )
                for card in chart.cards
            )

    # 네 종류가 실제로 한 번씩 나왔는지 — 하나라도 빠지면 이 시험은 그 종류를
    # 지켜 주지 못한다.
    assert set(kinds) == {"composition", "trend", "flow", "card"}


def test_builder의_도식_비율은_Decimal_문자열이고_float가_아니다() -> None:
    """I8 — 화면(result.html의 ``'%.4f'|format``)과 같은 소수 넷째 자리 문자열."""

    projection = build_public_projection(_report())
    ratios = [
        item[2]
        for block in projection.sections
        for visual in block.display.visuals
        for item in visual.items
    ] + [
        point["ratio_text"]
        for block in projection.sections
        for visual in block.display.visuals
        for series in visual.series
        for point in series[2]
    ]

    assert ratios, "도식 비율이 하나도 없으면 이 시험은 아무것도 지키지 않는다"
    for ratio in ratios:
        assert type(ratio) is str
        assert Decimal(ratio) == Decimal(ratio)  # 파싱 가능한 십진 문자열
        assert ratio == f"{Decimal(ratio):.4f}"


# ══════════════════════════════════════════════════════════
# ② I6 — 부록 검증 라벨은 순수 함수 결과와 같아야 한다
# ══════════════════════════════════════════════════════════


def test_builder의_검증라벨은_source_verification_label과_같다() -> None:
    """v2 ``source_grades`` 경로와 «표만 쓴 자료» 라벨을 한 보고서에서 함께 본다."""

    report = _report()
    projection = build_public_projection(report)

    assert len(projection.citations) == len(report.citations)
    labels: list[str] = []
    for row in projection.citations:
        source_id = str(row.source["source_id"])
        expected = source_verification_label(report, source_id)
        assert row.verification_label == expected
        labels.append(expected)

    # 두 갈래가 실제로 재료에 들어 있어야 이 시험이 둘 다 지킨다.
    assert "부분 검증" in labels
    assert "표·도식 근거" in labels


# ══════════════════════════════════════════════════════════
# ③ I3 — FactRecord는 «정확히 한 장»에 배정된다
# ══════════════════════════════════════════════════════════


def test_builder는_fact_records를_장별로_정확히_한_번_배정한다() -> None:
    report = _with_facts(_report())
    projection = build_public_projection(report)

    identity = _section_of(projection, "identity")
    past = _section_of(projection, "past_changes")
    assert identity.ledger.fact_ids == ("fact-identity-1", "fact-identity-2")
    assert past.ledger.fact_ids == ("fact-past-1",)
    assert tuple(
        str(record["fact_id"]) for record in identity.ledger.fact_records
    ) == identity.ledger.fact_ids
    # 장부 원자료는 canonical dict 그대로다 — 요약하거나 골라 담지 않는다.
    assert identity.ledger.fact_records[0] == canonical_value(report.fact_records[0])

    seen: list[str] = []
    for block in projection.sections:
        seen.extend(block.ledger.fact_ids)
    assert sorted(seen) == sorted(fact.fact_id for fact in report.fact_records)
    assert len(seen) == len(set(seen))


def test_builder는_주인_없는_FactRecord를_닫는다() -> None:
    """어느 장의 ``fact_ids``에도 없는 장부는 봉인이 거부한다 — 조용히 빠지면
    감사 장부가 화면과 어긋난 채로 저장된다."""

    report = _with_facts(_report())
    orphan = _fact("fact-orphan", "identity")
    broken = replace(report, fact_records=[*report.fact_records, orphan])

    with pytest.raises(PublicProjectionError):
        build_public_projection(broken)


def test_builder는_두_장이_같은_FactRecord를_나눠_가지면_닫는다() -> None:
    report = _with_facts(_report())
    sections = [
        replace(section, fact_ids=[*section.fact_ids, "fact-past-1"])
        if section.cell == "identity"
        else section
        for section in report.sections
    ]

    with pytest.raises(PublicProjectionError):
        build_public_projection(replace(report, sections=sections))


def test_builder는_장부에_없는_fact_id를_가리키는_장을_닫는다() -> None:
    report = _with_facts(_report())
    sections = [
        replace(section, fact_ids=["없는-fact"]) if section.cell == "culture" else section
        for section in report.sections
    ]

    with pytest.raises(PublicProjectionError):
        build_public_projection(replace(report, sections=sections))


# ══════════════════════════════════════════════════════════
# ④ I4 — 등급 기여를 합치면 report.source_grades와 같다
# ══════════════════════════════════════════════════════════


def test_builder의_source_grade_기여를_합치면_report_source_grades와_같다() -> None:
    report = _report()
    projection = build_public_projection(report)

    merged: dict[str, set[str]] = {}
    for block in projection.sections:
        for number, grades in block.ledger.source_grade_contribution:
            merged.setdefault(number, set()).update(grades)
    for number, grades in projection.summary_source_grade_contribution:
        merged.setdefault(number, set()).update(grades)

    expected = {
        number: set(grades) for number, grades in report.source_grades.items() if grades
    }
    assert merged == expected
    # 재료가 「확인 + 해석」 두 등급을 실제로 갖고 있어야 합치기가 의미를 갖는다.
    assert any(len(grades) > 1 for grades in expected.values())


def test_builder는_등급이_가리키는_출처가_부록에_없으면_닫는다() -> None:
    """부록에 없는 번호에 등급이 달려 있으면 「어디서 왔는지」를 말할 수 없다."""

    report = _report()
    grades = dict(report.source_grades)
    grades["99"] = ["확인"]

    with pytest.raises(PublicProjectionError):
        build_public_projection(replace(report, source_grades=grades))


# ══════════════════════════════════════════════════════════
# ⑤ I2 — 문단 번호는 1부터, 문장 글자는 그대로
# ══════════════════════════════════════════════════════════


def test_builder는_paragraphs_ordinal을_1부터_매기고_문장_글자는_안_바꾼다() -> None:
    report = _report()
    projection = build_public_projection(report)

    for section in report.sections:
        block = _section_of(projection, section.cell)
        paragraphs = block.display.paragraphs
        assert [ordinal for ordinal, _text in paragraphs] == [
            f"{position}." for position in range(1, len(paragraphs) + 1)
        ]
        # 문단 «글자»는 render가 만든 prose_paragraphs 그대로여야 한다.
        assert [text for _ordinal, text in paragraphs] == list(
            section.prose_paragraphs
        )
        # 문장도 그대로다 — 봉인이 글자를 손대면 인용 1:1 검사가 무너진다.
        assert block.display.sentences == tuple(
            (text, cite) for text, cite in section.prose_lines
        )
        # I2 — 두 이어붙임이 같아야 한다.
        assert " ".join(text for _ordinal, text in paragraphs) == " ".join(
            text for text, _cite in block.display.sentences
        )

    # 재료에 문단이 실제로 있어야 이 시험이 무언가를 지킨다.
    assert any(block.display.paragraphs for block in projection.sections)


# ══════════════════════════════════════════════════════════
# ⑥ 결정론과 「없는 문자열을 만들지 않는다」
# ══════════════════════════════════════════════════════════


def test_같은_Report로_두_번_만들면_digest가_같다() -> None:
    report = _with_facts(_report())

    first = build_report_digest(build_public_projection(report))
    second = build_report_digest(build_public_projection(report))

    assert first == second
    assert first.version == PUBLIC_PROJECTION_VERSION
    assert tuple(cell for cell, _digest in first.section_sha256s) == SECTION_IDS


def test_builder는_report에_없는_문자열을_만들지_않는다() -> None:
    """header·표·요약·부록·표지 띠가 전부 report 값 또는 순수 함수 결과다."""

    report = _report()
    projection = build_public_projection(report)

    # ① header — 지문 A의 보고서 메타와 «같은 값»이어야 한다(따로 조립 금지).
    content = report_public_content_projection(report)
    for key in (
        "company",
        "company_id",
        "job",
        "corp_type",
        "generated_at",
        "schema_version",
        "as_of_date",
        "analysis_period",
        "latest_performance_period",
        "grade",
        "shortfall_reasons",
        "quality_contract_version",
        "safety_decision",
        "publication_policy",
    ):
        assert projection.header[key] == content[key]
    assert projection.header["release_mode"] == report.release_mode

    # ② 표 7필드 — 지문 A의 table_public_projection과 canonical 동일(I7).
    for section in report.sections:
        block = _section_of(projection, section.cell)
        assert len(block.display.tables) == len(section.tables)
        for sealed, table in zip(block.display.tables, section.tables):
            assert canonical_value(sealed) == {
                **table_public_projection(table),
                "manifest_ref": table.manifest_ref,
            }

    # ③ 요약 — 글자는 report.summary_items, 주제어·장번호는 순수 함수·정본 표.
    assert len(projection.summary) == len(report.summary_items)
    for position, (row, item) in enumerate(
        zip(projection.summary, report.summary_items), start=1
    ):
        assert row.text == item.text
        assert row.section_id == item.section_id
        assert row.topic == summary_topic(item.section_id)
        assert row.ordinal == f"{position:02d}"
        assert row.section_display_number == (
            SECTION_BY_ID[item.section_id].display_number
        )

    # ④ 부록 — Source 원자료는 canonical dict 그대로.
    for row, source in zip(projection.citations, visible_citations(report.citations)):
        assert row.source == canonical_value(source)
        assert row.number == source.number
        assert row.url == source.url
        assert row.location == source.location

    # ⑤ 표지 띠·3개년 띠 — 순수 함수 결과 그대로.
    metrics = cover_metrics(report)
    assert projection.cover_metrics is not None
    assert projection.cover_metrics.title == metrics.title
    assert projection.cover_metrics.cite == metrics.cite
    assert projection.cover_metrics.items == tuple(
        (item.label, item.value, item.unit) for item in metrics.items
    )

    past = _report_section(report, "past_changes")
    band = period_summary_from_table(past.tables[0])
    block = _section_of(projection, "past_changes")
    assert block.display.period_summary is not None
    assert block.display.period_summary.title == band.title
    assert block.display.period_summary.items == tuple(
        (
            item.label,
            item.base_period,
            item.base_value,
            item.latest_period,
            item.latest_value,
            item.unit,
            item.change,
            item.change_kind,
            item.direction,
            item.note,
        )
        for item in band.items
    )
    # 3개년 띠는 4장에만 붙는다 — 다른 장에 새 띠를 만들지 않는다.
    for other in projection.sections:
        if other.display.cell != "past_changes":
            assert other.display.period_summary is None


def test_인용_없는_요약_문장은_지금_봉인이_거부한다() -> None:
    """★ 이건 «지금 그렇다»는 기록이지 «그래야 한다»는 주장이 아니다.

    render는 인용이 없는 요약 문장의 ``section_id``를 빈 글자로 둔다
    (``_summary_source_section``: 틀린 장을 가리키느니 비운다). 그런데 S1
    ``PublicSummaryRow``는 ``section_id``가 정본 아홉 장 «안»일 것을 요구한다.
    그래서 그런 보고서는 지금 봉인 자체가 안 된다.

    ★ S3에게 — 이 자리를 여는 결정(빈 장 허용)이 나면 이 시험을 「빈 장도
      봉인된다」로 «뒤집어라». 조용히 지우지 마라. 지우면 어느 보고서가 봉인
      안 되는지 아무도 모르게 된다.
    """

    report = _report(uncited_summary=True)
    assert any(not item.section_id for item in report.summary_items)

    with pytest.raises(PublicProjectionError):
        build_public_projection(report)


def test_builder는_장_제목_태그_번호를_report에서_그대로_옮긴다() -> None:
    report = _report()
    projection = build_public_projection(report)

    for section in report.sections:
        block = _section_of(projection, section.cell)
        assert block.display.title == section.title
        assert block.display.tag == section.tag
        assert block.display.display_number == section.display_number
        assert block.display.empty_reason == section.empty_reason
        assert block.display.guidance_lines == tuple(section.guidance_lines)
        assert block.version == PUBLIC_PROJECTION_VERSION


def test_장부만_바꾸면_display_digest는_불변이고_content만_바뀐다() -> None:
    """봉인이 display와 ledger를 실제로 갈라 놓았는지 digest로 확인한다."""

    report = _with_facts(_report())
    changed_facts = [
        replace(fact, subject_scope=f"{fact.subject_scope} (바뀐 감사 범위)")
        for fact in report.fact_records
    ]
    changed = replace(report, fact_records=changed_facts)

    before = build_report_digest(build_public_projection(report))
    after = build_report_digest(build_public_projection(changed))

    assert before.display_sha256 == after.display_sha256
    assert before.content_sha256 != after.content_sha256
