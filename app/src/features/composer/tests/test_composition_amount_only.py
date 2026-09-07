"""비중 열이 «없는» 매출 구성표가 화면·봉인까지 같은 결속으로 가는지 못 박는다.

★ 무엇을 지키나 — 셋이다.
  ① 비중 없는 표도 화면에 「구분 · 금액」 두 열로 실린다(빈 비중 칸을 만들지 않는다).
  ② 그 표도 «행마다» 원문 조각에 결속된다 — 비중이 있는 표와 같은 규칙이다.
  ③ 비중이 없으므로 100% 누적 막대는 그리지 않는다. 도식이 없어야 독자가
    「몫」을 봤다고 오해하지 않는다.

⚠️ 원문은 실제 공시가 아니라 모양만 옮긴 가공 자료다(회사 이름 「가나다전자」).
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from src.features.composer import render as render_module
from src.features.composer.constants import GRADE_CONFIRMED, SECTION_IDS
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    PerformanceTable,
    composition_tables_from_raw,
)
from src.features.composer.public_manifest import (
    PublicManifestError,
    _validate_composition_total,
    build_public_structure_seal,
)
from src.features.composer.validate import v2_validation_problems
from src.shared.revenue_table_provenance import (
    REVENUE_AMOUNT_ONLY_CAPTION_BY_AXIS,
    REVENUE_AMOUNT_ONLY_FOOTNOTE,
    REVENUE_AXIS_PRODUCT,
    REVENUE_AXIS_REGION,
    REVENUE_SHAPE_AMOUNT_ONLY_HORIZONTAL,
    REVENUE_SHAPE_AMOUNT_ONLY_VERTICAL,
    build_revenue_amount_only_row_evidence,
    canonical_json,
    revenue_amount_only_headers,
)
from src.shared.report_quality.source_identity import document_identity_from_parts


#: 지역은 가로형(열 머리말), 제품은 금액만 세로형이다. 둘 다 비중 열이 없다.
_원문 = (
    "제품별 매출액 (단위 : 천원) 구분 "
    "가전사업 제품 냉장고 44,000 "
    "통신사업 제품 휴대전화 36,000 "
    "합계 80,000 "
    "지역별 매출액 (단위 : 천원) 지역 지역 합계 국내 일본 기타 국가 매출액 "
    "60,000 15,000 5,000 80,000"
)
_URL = "https://manifest.example/document/4"


def _fragment(raw_text: str = _원문) -> CollectedFragment:
    return CollectedFragment(
        fragment_id="4",
        kind="회사 공식 자료",
        text=raw_text,
        source_url=_URL,
        document_identity=document_identity_from_parts(url=_URL),
    )


#: 요약 최소 문장 수(3)를 채우는 본문 문장. 추출식 요약이라 «본문에 있는»
#: 문장을 그대로 쓴다 — 없는 문장을 요약에 넣으면 소유 장을 못 정한다.
_요약문장 = tuple(
    ComposedSentence(
        text=f"가나다전자는 가전과 통신 두 부문에서 수익을 낸다({index}).",
        citations=("4",),
        grade=GRADE_CONFIRMED,
    )
    for index in range(3)
)


def _report(*, with_summary: bool = False) -> ComposedReport:
    sections = tuple(
        ComposedSection(
            section_id=section_id,
            sentences=_요약문장 if with_summary and section_id == "portfolio" else (),
        )
        for section_id in SECTION_IDS
    )
    return ComposedReport(
        sections=sections,
        summary=_요약문장 if with_summary else (),
    )


def _span(raw_text: str, value: str, start: int) -> tuple[int, int]:
    found = raw_text.index(value, start)
    return found, found + len(value)


def _raw_amount_only_tables(raw_text: str) -> tuple[dict[str, object], ...]:
    """composer가 소비하는 공유 provenance 계약 fixture 두 장을 만든다."""

    headers = revenue_amount_only_headers("천원")

    product_header_start = raw_text.index("제품별 매출액")
    product_names = ("가전사업 제품 냉장고", "통신사업 제품 휴대전화")
    product_amounts = ("44,000", "36,000")
    product_total_name = "영업수익합계" if "영업수익합계" in raw_text else "합계"
    cursor = product_header_start
    product_name_spans: list[tuple[int, int]] = []
    product_amount_spans: list[tuple[int, int]] = []
    for name, amount in zip(product_names, product_amounts):
        name_span = _span(raw_text, name, cursor)
        amount_span = _span(raw_text, amount, name_span[1])
        product_name_spans.append(name_span)
        product_amount_spans.append(amount_span)
        cursor = amount_span[1]
    product_total_name_span = _span(raw_text, product_total_name, cursor)
    product_total_amount_span = _span(raw_text, "80,000", product_total_name_span[1])
    product_header_end = product_name_spans[0][0]
    product_rows = [
        list(row)
        for row in zip(
            (*product_names, product_total_name),
            (*product_amounts, "80,000"),
        )
    ]
    product_evidence = [
        build_revenue_amount_only_row_evidence(
            filing_text=raw_text,
            header_start=product_header_start,
            header_end=product_header_end,
            excerpt_start=product_header_start,
            excerpt_end=product_total_amount_span[1],
            shape=REVENUE_SHAPE_AMOUNT_ONLY_VERTICAL,
            name_span=name_span,
            amount_span=amount_span,
            total_name_span=product_total_name_span,
            total_amount_span=product_total_amount_span,
            source_index=index,
            selected_index=index,
            row_count=len(product_names),
            public_row=product_rows[index],
            axis=REVENUE_AXIS_PRODUCT,
            headers=headers,
        )
        for index, (name_span, amount_span) in enumerate(
            zip(
                (*product_name_spans, product_total_name_span),
                (*product_amount_spans, product_total_amount_span),
            )
        )
    ]

    region_header_start = raw_text.index("지역별 매출액")
    region_label_start = raw_text.index(
        "매출액", region_header_start + len("지역별 매출액")
    )
    region_header_end = region_label_start + len("매출액")
    region_names = ("국내", "일본", "기타 국가")
    region_amounts = ("60,000", "15,000", "5,000")
    region_name_spans = tuple(
        _span(raw_text, name, region_header_start) for name in region_names
    )
    region_total_name_span = _span(raw_text, "합계", region_header_start)
    cursor = region_header_end
    region_amount_spans: list[tuple[int, int]] = []
    for amount in (*region_amounts, "80,000"):
        amount_span = _span(raw_text, amount, cursor)
        region_amount_spans.append(amount_span)
        cursor = amount_span[1]
    region_rows = [
        list(row)
        for row in zip((*region_names, "합계"), (*region_amounts, "80,000"))
    ]
    region_evidence = [
        build_revenue_amount_only_row_evidence(
            filing_text=raw_text,
            header_start=region_header_start,
            header_end=region_header_end,
            excerpt_start=region_header_start,
            excerpt_end=region_amount_spans[-1][1],
            shape=REVENUE_SHAPE_AMOUNT_ONLY_HORIZONTAL,
            name_span=name_span,
            amount_span=amount_span,
            total_name_span=region_total_name_span,
            total_amount_span=region_amount_spans[-1],
            source_index=index,
            selected_index=index,
            row_count=len(region_names),
            public_row=region_rows[index],
            axis=REVENUE_AXIS_REGION,
            headers=headers,
        )
        for index, (name_span, amount_span) in enumerate(
            zip(
                (*region_name_spans, region_total_name_span),
                region_amount_spans,
            )
        )
    ]

    def payload(
        *, axis: str, rows: list[list[str]], evidence_rows: list[str]
    ) -> dict[str, object]:
        return {
            "axis": axis,
            "caption": (
                f"{REVENUE_AMOUNT_ONLY_CAPTION_BY_AXIS[axis]}"
                f" · {REVENUE_AMOUNT_ONLY_FOOTNOTE}"
            ),
            "headers": list(headers),
            "rows": rows,
            "raw_rows": rows,
            "evidence_rows": evidence_rows,
            "cite": "[4]",
        }

    return (
        payload(
            axis=REVENUE_AXIS_PRODUCT,
            rows=product_rows,
            evidence_rows=product_evidence,
        ),
        payload(
            axis=REVENUE_AXIS_REGION,
            rows=region_rows,
            evidence_rows=region_evidence,
        ),
    )


def _composition_tables(raw_text: str = _원문) -> tuple[PerformanceTable, ...]:
    tables = composition_tables_from_raw(_raw_amount_only_tables(raw_text))
    assert len(tables) == 2, "가공 원문에서 제품·지역 두 표가 나와야 한다"
    return tables


def _seal(
    composition_tables: tuple[PerformanceTable, ...], raw_text: str = _원문
):
    return build_public_structure_seal(
        _report(),
        (_fragment(raw_text),),
        None,
        filing_meta=None,
        composition_tables=composition_tables,
        table_presentation="table",
        company_id="00123456",
        evidence_generation_sha256="a" * 64,
        evidence_packet_sha256s=tuple(
            (section_id, str(index) * 64)
            for index, section_id in enumerate(SECTION_IDS, start=1)
        ),
        company_name="가나다전자",
        corp_type="기업",
        generated_at="2026-09-06T00:00:00+09:00",
        as_of_date="2026-09-06",
        analysis_period="2023~2025",
        latest_performance_period="2025",
        citation_style="auto",
    )


def _tables_of(report, cell: str) -> list:
    return [
        table
        for section in report.sections
        if section.cell == cell
        for table in section.tables
    ]


# ══════════════════════════════════════════════════════════
# ① 화면(SHADOW) — 구분·금액 두 열, 각주, 출고 검증
# ══════════════════════════════════════════════════════════


def test_비중_없는_구성표가_구분_금액_두_열로_그려진다() -> None:
    rendered = render_module.render_report(
        "가나다전자",
        _report(),
        (_fragment(),),
        None,
        composition_tables=_composition_tables(),
    )

    제품표 = _tables_of(rendered, "portfolio")[0]
    지역표 = _tables_of(rendered, "business_model")[0]

    assert 제품표.headers == ["구분", "매출액 (천원)"]
    assert 제품표.rows == [
        ["가전사업 제품 냉장고", "44,000"],
        ["통신사업 제품 휴대전화", "36,000"],
        ["합계", "80,000"],
    ]
    assert 지역표.headers == ["구분", "매출액 (천원)"]
    assert 지역표.rows == [
        ["국내", "60,000"],
        ["일본", "15,000"],
        ["기타 국가", "5,000"],
        ["합계", "80,000"],
    ]


def test_캡션이_비중을_적지_않은_이유를_화면에_말한다() -> None:
    rendered = render_module.render_report(
        "가나다전자",
        _report(),
        (_fragment(),),
        None,
        composition_tables=_composition_tables(),
    )

    for cell in ("portfolio", "business_model"):
        캡션 = _tables_of(rendered, cell)[0].caption
        assert 캡션.endswith("· 비중은 공시에 없어 적지 않았습니다"), 캡션
        assert "비중 (" not in 캡션


def test_비중_없는_구성표는_출고_검증을_통과한다() -> None:
    rendered = render_module.render_report(
        "가나다전자",
        _report(with_summary=True),
        (_fragment(),),
        None,
        composition_tables=_composition_tables(),
    )

    assert v2_validation_problems(rendered) == ()


# ══════════════════════════════════════════════════════════
# ② FULL 봉인 — 렌더러 값 == 봉인 값, 행마다 결속
# ══════════════════════════════════════════════════════════


def test_FULL_봉인의_표와_렌더러의_표가_글자까지_같다() -> None:
    composition_tables = _composition_tables()
    seal = _seal(composition_tables)
    rendered = render_module.render_report(
        "가나다전자",
        _report(),
        (_fragment(),),
        None,
        composition_tables=composition_tables,
        public_structure_seal=seal,
        company_id="00123456",
    )

    manifest = json.loads(seal.canonical_json)
    봉인표 = {
        (table["section_id"], table["caption"]): table
        for table in manifest["tables"]
    }
    for cell in ("portfolio", "business_model"):
        표 = _tables_of(rendered, cell)[0]
        같은표 = 봉인표[(cell, 표.caption)]
        assert 같은표["headers"] == 표.headers
        assert 같은표["rows"] == 표.rows
        assert 표.manifest_ref


def test_비중_없는_표도_행마다_원문_조각에_결속된다() -> None:
    """★★ 「행마다 결속」이 이 표에서 비면 근거 없는 숫자가 조용히 나간다."""

    seal = _seal(_composition_tables())
    manifest = json.loads(seal.canonical_json)

    for table in manifest["tables"]:
        if table["section_id"] not in {"portfolio", "business_model"}:
            continue
        bindings = table["row_bindings"]
        assert len(bindings) == len(table["rows"])
        for binding in bindings:
            assert binding["source_fragment_ids"] == ["4"]
            assert binding["row_evidence_hash"]
            assert len(binding["typed_cells"]) == 2


def test_확장_합계명도_구성행_수가_아니라_합계행으로_센다() -> None:
    """v2가 인정한 합계명을 봉인기가 데이터 행으로 다시 세면 안 된다."""

    원문 = _원문.replace("합계 80,000 ", "영업수익합계 80,000 ", 1)
    표들 = _composition_tables(원문)

    seal = _seal(표들, 원문)
    manifest = json.loads(seal.canonical_json)

    assert any(
        table["rows"][-1][0] == "영업수익합계"
        for table in manifest["tables"]
        if table["section_id"] == "portfolio"
    )


def test_금액합계_검산은_매출표가_아닌_일반_2열표에_적용하지_않는다() -> None:
    table = PerformanceTable(
        caption="일반 설명 표",
        headers=("항목", "설명"),
        rows=(("첫째", "해당"), ("합계", "적용 안 함")),
    )

    _validate_composition_total(table)


# ══════════════════════════════════════════════════════════
# ③ 음성 대조 — 값을 건드리면 봉인이 막는다
# ══════════════════════════════════════════════════════════


def test_금액을_바꾸면_합계와_어긋나_봉인이_막는다() -> None:
    표들 = _composition_tables()
    위조 = tuple(
        replace(표, rows=(("국내", "99,000"),) + 표.rows[1:])
        if "지역" in 표.caption
        else 표
        for 표 in 표들
    )

    # ★ «왜» 막혔는지까지 못 박는다. 다른 이유로 막혀도 초록이면 가드가 아니다.
    with pytest.raises(PublicManifestError, match="공개 금액 합이 합계 행과 다릅니다"):
        _seal(위조)


def test_이름을_바꾸면_원문_칸과_달라_봉인이_막는다() -> None:
    표들 = _composition_tables()
    위조 = tuple(
        replace(표, rows=(("FORGED_REGION", "60,000"),) + 표.rows[1:])
        if "지역" in 표.caption
        else 표
        for 표 in 표들
    )

    with pytest.raises(PublicManifestError, match="인용 원문으로 재검산할 수 없습니다"):
        _seal(위조)


def test_행_하나를_빼면_봉인이_막는다() -> None:
    """★ 행을 빼면 남은 금액의 합이 합계와 어긋난다."""

    표들 = _composition_tables()
    위조 = tuple(
        replace(표, rows=표.rows[1:], raw_rows=표.raw_rows[1:],
                evidence_rows=표.evidence_rows[1:])
        if "지역" in 표.caption
        else 표
        for 표 in 표들
    )

    with pytest.raises(PublicManifestError, match="공개 금액 합이 합계 행과 다릅니다"):
        _seal(위조)


def test_세로형_두_행을_바꾸고_선택번호도_고쳐도_봉인이_막는다() -> None:
    """합계와 원문 칸은 모두 맞아도 공개 행 순서를 다시 쓸 수는 없다."""

    표들 = _composition_tables()
    제품표 = next(표 for 표 in 표들 if "제품·서비스" in 표.caption)
    첫_근거 = json.loads(제품표.evidence_rows[0])
    둘째_근거 = json.loads(제품표.evidence_rows[1])
    # 공격자는 canonical JSON까지 다시 만들 수 있다고 본다. selected_index만
    # 공개 순서에 맞추고 source_index·원문 좌표는 실제 다른 행을 가리킨다.
    둘째_근거["row"]["selected_index"] = 0
    첫_근거["row"]["selected_index"] = 1
    바꾼_제품표 = replace(
        제품표,
        rows=(제품표.rows[1], 제품표.rows[0], *제품표.rows[2:]),
        raw_rows=(제품표.raw_rows[1], 제품표.raw_rows[0], *제품표.raw_rows[2:]),
        evidence_rows=(
            canonical_json(둘째_근거),
            canonical_json(첫_근거),
            *제품표.evidence_rows[2:],
        ),
    )
    위조 = tuple(
        바꾼_제품표 if 표 is 제품표 else 표
        for 표 in 표들
    )

    with pytest.raises(PublicManifestError, match="인용 원문으로 재검산할 수 없습니다"):
        _seal(위조)
