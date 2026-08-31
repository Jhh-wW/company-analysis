"""FULL 장별 packet과 독립 공개 구조 manifest의 공격 회귀."""

from __future__ import annotations

import json
import re
from dataclasses import replace

import pytest

from src.features.composer import pipeline as pipeline_module
from src.features.composer import render as render_module
from src.features.composer.constants import (
    FLOW_HEADERS_BY_SECTION,
    GRADE_CONFIRMED,
    SECTION_IDS,
)
from src.features.composer.logic import (
    compose_sections,
    contains_inline_citation_marker,
    parse_section_response,
)
from src.features.composer.pipeline import run_v2
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    PerformanceTable,
    StructuredClaim,
)
from src.features.composer.public_manifest import (
    PublicManifestError,
    build_public_structure_seal,
)
from src.features.composer.validate import V2ValidationError
from src.features.pipeline.port import ReportTable
from src.features.storage.reports import (
    report_from_dict,
    report_from_json,
    report_to_dict,
    report_to_json,
)
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_quality.source_identity import document_identity_from_parts


_MARKS = "가나다라마바사아자"
_ENDINGS = ("첫째", "둘째", "셋째", "넷째", "다섯째")
_GROUPED_ITEM_RE = re.compile(
    r"\[(\d+)\] \(장: ([^,]+), 종류: ([^,]+), 인용: ([^)]+)\)"
)


def _fragment_text(mark: str) -> str:
    common = (
        f"{mark} 회사 사업 고객 제품 전략 운영 문화 경쟁 과제 대응 협력 실적 "
        "공식 자료에서 확인했다."
    )
    sentences = " ".join(
        f"{mark} 회사 사업 고객 제품 전략 운영 문화 경쟁 과제 대응 협력 실적 "
        f"{ending} 공식 자료에서 확인했다."
        for ending in _ENDINGS
    )
    flow = (
        "핵심 제품은 고객 제공을 거쳐 기업 고객에게 닿는다. "
        "보조 제품은 유통 협력을 거쳐 소비자에게 닿는다."
    )
    return f"{common} {sentences} {flow}"


def _packets(*, two_flow_sources: bool = False):
    packets: dict[str, tuple[CollectedFragment, ...]] = {}
    for index, section_id in enumerate(SECTION_IDS, start=1):
        fragments = [
            CollectedFragment(
                fragment_id=str(index),
                kind="회사 공식 자료",
                text=_fragment_text(_MARKS[index - 1]),
                source_url=f"https://manifest.example/document/{index}",
                document_title=f"공식 자료 {index}",
            )
        ]
        if section_id == "business_model" and two_flow_sources:
            fragments.append(
                CollectedFragment(
                    fragment_id="20",
                    kind="회사 공식 자료",
                    text=(
                        _fragment_text(_MARKS[index - 1])
                        + " 보조 제품은 유통 협력을 거쳐 소비자에게 닿는다."
                    ),
                    source_url="https://manifest.example/document/20",
                    document_title="공식 자료 이십",
                )
            )
        packets[section_id] = tuple(fragments)
    return packets


class _CompletePacketWriter:
    def __init__(self, *, flow: bool = False) -> None:
        self.prompts: list[str] = []
        self.calls = 0
        self.flow = flow

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        assert "핵심 요약" not in prompt
        section_id = SECTION_IDS[self.calls]
        mark = _MARKS[self.calls]
        self.calls += 1
        fragment_ids = re.findall(r"\[조각 (\d+)\] \(", prompt)
        assert fragment_ids
        slots = CLAIM_SLOTS_BY_SECTION[section_id]
        sentences = [
            {
                "글": (
                    f"{mark} 회사 사업 고객 제품 전략 운영 문화 경쟁 과제 대응 "
                    f"협력 실적 {ending} 공식 자료에서 확인했다."
                ),
                "인용": [fragment_ids[0]],
                "등급": GRADE_CONFIRMED,
                "주장슬롯": slots[index % len(slots)],
            }
            for index, ending in enumerate(_ENDINGS)
        ]
        rows: list[dict[str, object]] = []
        if self.flow and section_id == "business_model":
            headers = FLOW_HEADERS_BY_SECTION[section_id]
            first = ["핵심 자산", "핵심 제품", "기업 고객 과금", "반복 수익"][
                : len(headers)
            ]
            second = ["보조 자산", "보조 제품", "소비자 과금", "확장 수익"][
                : len(headers)
            ]
            rows = [
                {"칸": first, "인용": [fragment_ids[0]]},
                {
                    "칸": second,
                    "인용": [fragment_ids[-1]],
                },
            ]
        return json.dumps(
            {"문장들": sentences, "경로표": rows}, ensure_ascii=False
        )


class _BoundGroupedReviewer:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        verdicts = []
        for number, section_id, _kind, citations in _GROUPED_ITEM_RE.findall(
            prompt
        ):
            evidence_ids = re.findall(r"조각 (\d+)", citations)
            verdicts.append(
                {
                    "번호": int(number),
                    "장": section_id,
                    "근거": evidence_ids,
                    "결과": "참",
                }
            )
        assert verdicts
        return json.dumps({"판정": verdicts}, ensure_ascii=False)


class _NoDiagram:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, _prompt: str) -> str:
        self.calls += 1
        raise AssertionError("FULL packet은 별도 diagram AI를 부르면 안 됩니다")


def _run_full(
    *,
    flow: bool = False,
    composition_tables: tuple[PerformanceTable, ...] = (),
    packets=None,
):
    writer = _CompletePacketWriter(flow=flow)
    reviewer = _BoundGroupedReviewer()
    diagram = _NoDiagram()
    output = run_v2(
        "가나다전자",
        (),
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
        diagram_ask=diagram,
        release_mode=ReleaseMode.FULL,
        section_evidence_packets=(
            packets if packets is not None else _packets(two_flow_sources=flow)
        ),
        composition_tables=composition_tables,
    )
    return output, writer, reviewer, diagram


def _valid_composition_table() -> PerformanceTable:
    return PerformanceTable(
        caption="제품군 구성",
        headers=("제품군", "비중"),
        rows=(("핵심 제품", "70%"), ("기타", "30%")),
        unit="%",
        cite="[2]",
        evidence_rows=(
            json.dumps({"제품군": "핵심 제품", "비중": "70%"}, ensure_ascii=False),
            json.dumps({"제품군": "기타", "비중": "30%"}, ensure_ascii=False),
        ),
    )


def _replace_table(report, section_id: str, transform):
    sections = list(report.sections)
    index = next(
        index for index, section in enumerate(sections) if section.cell == section_id
    )
    section = sections[index]
    tables = list(section.tables)
    table_index = next(
        index
        for index, table in enumerate(tables)
        if table.presentation == "flow" or section_id == "business_model"
    )
    tables[table_index] = transform(tables[table_index])
    sections[index] = replace(section, tables=tables)
    return replace(report, sections=sections)


def test_full_packet은_작성9_묶음검수1_요약0_도식0이다():
    output, writer, reviewer, diagram = _run_full(flow=True)

    assert output.report.public_structure_manifest
    assert len(writer.prompts) == 9
    assert len(reviewer.prompts) == 1
    assert "[도식 검수]" not in reviewer.prompts[0]
    assert "종류: 도식" in reviewer.prompts[0]
    assert diagram.calls == 0
    flow = next(
        table
        for section in output.report.sections
        for table in section.tables
        if table.presentation == "flow"
    )
    assert flow.manifest_ref
    assert flow.source_cites == ["[2]", "[20]"]
    assert len(flow.evidence_rows) == len(flow.rows) == 2


@pytest.mark.parametrize("damage", ("section", "evidence"))
def test_bundled_reviewer의_flow_행결과는_장과_허용근거에_결속된다(damage):
    writer = _CompletePacketWriter(flow=True)

    class DamagedReviewer(_BoundGroupedReviewer):
        def __call__(self, prompt: str) -> str:
            raw = json.loads(super().__call__(prompt))
            flow_numbers = {
                int(number)
                for number, _section, kind, _citations in _GROUPED_ITEM_RE.findall(
                    prompt
                )
                if kind == "도식"
            }
            for verdict in raw["판정"]:
                if verdict["번호"] not in flow_numbers:
                    continue
                if damage == "section":
                    verdict["장"] = "identity"
                else:
                    verdict["근거"] = ["1"]
            return json.dumps(raw, ensure_ascii=False)

    reviewer = DamagedReviewer()
    output = run_v2(
        "가나다전자",
        (),
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
        diagram_ask=_NoDiagram(),
        release_mode=ReleaseMode.FULL,
        section_evidence_packets=_packets(two_flow_sources=True),
    )

    business = next(
        section for section in output.report.sections if section.cell == "business_model"
    )
    assert all(table.presentation != "flow" for table in business.tables)
    assert len(reviewer.prompts) == 1


def test_packet_작성응답이_깨져도_재호출하지_않아_writer는_정확히_9회다():
    class BrokenWriter:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, _prompt: str) -> str:
            self.calls += 1
            return "{깨진 JSON"

    writer = BrokenWriter()
    report = compose_sections(
        "가나다전자",
        (),
        None,
        writer,
        section_evidence_packets=_packets(),
    )

    assert writer.calls == 9
    assert all(not section.sentences and section.notice for section in report.sections)


def test_FULL_packet_작성응답이_전부_깨져도_묶음검수는_정확히_1회다():
    class BrokenWriter:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, _prompt: str) -> str:
            self.calls += 1
            return "{깨진 JSON"

    class EmptyReviewer:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, _prompt: str) -> str:
            self.calls += 1
            return json.dumps({"판정": []}, ensure_ascii=False)

    writer = BrokenWriter()
    reviewer = EmptyReviewer()
    diagram = _NoDiagram()
    with pytest.raises(V2ValidationError):
        run_v2(
            "가나다전자",
            (),
            None,
            writer_ask=writer,
            reviewer_ask=reviewer,
            diagram_ask=diagram,
            release_mode=ReleaseMode.FULL,
            section_evidence_packets=_packets(),
        )

    assert writer.calls == 9
    assert reviewer.calls == 1
    assert diagram.calls == 0


def test_packet_묶음검수응답이_깨져도_reviewer는_정확히_1회다():
    writer = _CompletePacketWriter(flow=True)

    class BrokenReviewer:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, _prompt: str) -> str:
            self.calls += 1
            return "{깨진 JSON"

    reviewer = BrokenReviewer()
    diagram = _NoDiagram()
    with pytest.raises(V2ValidationError):
        run_v2(
            "가나다전자",
            (),
            None,
            writer_ask=writer,
            reviewer_ask=reviewer,
            diagram_ask=diagram,
            release_mode=ReleaseMode.FULL,
            section_evidence_packets=_packets(two_flow_sources=True),
        )

    assert len(writer.prompts) == 9
    assert reviewer.calls == 1
    assert diagram.calls == 0


def test_packet_raw_mapping의_문서일은_Source에_보존된다():
    packets = _packets()
    packets["identity"] = {
        1: {
            "종류": "회사 홈페이지",
            "원문": _fragment_text("가"),
            "출처": "https://manifest.example/document/1",
            "문서명": "공식 자료 1",
            "원문위치": "회사소개 > 개요",
            "문서일": "2026-08-30",
        }
    }

    output, _writer, _reviewer, _diagram = _run_full(packets=packets)
    source = next(source for source in output.report.citations if source.number == 1)
    assert source.collected_at == "2026-08-30"


def test_SHADOW_flat은_legacy_요약과_검수호출을_유지하고_manifest를_싣지_않는다():
    class FlatWriter:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def __call__(self, prompt: str) -> str:
            self.prompts.append(prompt)
            if "핵심 요약" in prompt:
                return json.dumps(
                    {
                        "문장들": [
                            {
                                "글": f"{mark} 회사 자료를 종합해 핵심을 확인했다.",
                                "인용": ["1"],
                                "등급": GRADE_CONFIRMED,
                            }
                            for mark in _MARKS[:3]
                        ]
                    },
                    ensure_ascii=False,
                )
            index = len(self.prompts) - 1
            section_id = SECTION_IDS[index]
            mark = _MARKS[index]
            slots = CLAIM_SLOTS_BY_SECTION[section_id]
            return json.dumps(
                {
                    "문장들": [
                        {
                            "글": f"{mark} 회사의 공식 자료를 확인했다.",
                            "인용": ["1"],
                            "등급": GRADE_CONFIRMED,
                            "주장슬롯": slots[0],
                        }
                    ]
                },
                ensure_ascii=False,
            )

    class LegacyReviewer:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def __call__(self, prompt: str) -> str:
            self.prompts.append(prompt)
            numbers = [int(value) for value in re.findall(r"\[(\d+)\] \(", prompt)]
            return json.dumps(
                {"판정": [{"번호": number, "결과": "참"} for number in numbers]},
                ensure_ascii=False,
            )

    writer = FlatWriter()
    reviewer = LegacyReviewer()
    diagram = _NoDiagram()
    fragment = CollectedFragment(
        fragment_id="1",
        kind="회사 공식 자료",
        text=" ".join(f"{mark} 회사의 공식 자료를 확인했다." for mark in _MARKS),
        source_url="https://manifest.example/flat",
    )
    output = run_v2(
        "가나다전자",
        (fragment,),
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
        diagram_ask=diagram,
        release_mode=ReleaseMode.SHADOW,
    )

    assert len(writer.prompts) == 10  # 본문 9 + legacy AI 요약 1
    assert len(reviewer.prompts) == 2  # 본문 1 + legacy 요약 검수 1
    assert diagram.calls == 0
    assert output.report.public_structure_manifest == ""
    assert "public_structure_manifest" not in report_to_dict(output.report)


def test_renderer_helper가_FORGED_FLOW를_만들어도_독립_manifest가_막는다(
    monkeypatch,
):
    original = render_module._flow_report_table

    def forged_helper(*args, **kwargs):
        table = original(*args, **kwargs)
        if table is None:
            return None
        rows = [list(row) for row in table.rows]
        rows[0][0] = "FORGED_FLOW"
        return replace(table, rows=rows)

    monkeypatch.setattr(render_module, "_flow_report_table", forged_helper)
    with pytest.raises(PublicManifestError, match="manifest|행/셀/숫자/출처|출처"):
        _run_full(flow=True)


def test_renderer_반환을_FORGED_FLOW로_바꿔도_막는다(monkeypatch):
    original = render_module.render_report

    def forged_render(*args, **kwargs):
        report = original(*args, **kwargs)

        def forge(table: ReportTable) -> ReportTable:
            rows = [list(row) for row in table.rows]
            rows[0][0] = "FORGED_FLOW"
            return replace(table, rows=rows)

        return _replace_table(report, "business_model", forge)

    monkeypatch.setattr(pipeline_module, "render_report", forged_render)
    with pytest.raises(PublicManifestError, match="manifest|행/셀/숫자/출처"):
        _run_full(flow=True)


def test_flow_source_cites_부분집합은_누락출처가_부록에_남아도_막는다(
    monkeypatch,
):
    original = render_module.render_report

    def omit_source_cite(*args, **kwargs):
        report = original(*args, **kwargs)
        return _replace_table(
            report,
            "business_model",
            lambda table: replace(table, source_cites=table.source_cites[:1]),
        )

    monkeypatch.setattr(pipeline_module, "render_report", omit_source_cite)
    with pytest.raises(PublicManifestError, match="manifest|행/셀/숫자/출처|출처"):
        _run_full(flow=True)


def test_flow_행_source_swap은_전체출처집합이_같아도_막는다(monkeypatch):
    original = render_module.render_report

    def swap_row_sources(*args, **kwargs):
        report = original(*args, **kwargs)

        def swap(table: ReportTable) -> ReportTable:
            evidence = list(table.evidence_rows)
            if len(evidence) < 2:
                return table
            evidence[0], evidence[1] = evidence[1], evidence[0]
            return replace(table, evidence_rows=evidence)

        return _replace_table(report, "business_model", swap)

    monkeypatch.setattr(pipeline_module, "render_report", swap_row_sources)
    with pytest.raises(PublicManifestError, match="manifest|행/셀/숫자/출처"):
        _run_full(flow=True)


def test_flow_행_Source의_exact_evidence_hash_바꿔치기도_막는다(monkeypatch):
    original = render_module.render_report

    def forge_source_hash(*args, **kwargs):
        report = original(*args, **kwargs)
        citations = [
            (
                replace(source, exact_evidence_hashes=["0" * 64])
                if source.number == 20
                else source
            )
            for source in report.citations
        ]
        return replace(report, citations=citations)

    monkeypatch.setattr(pipeline_module, "render_report", forge_source_hash)
    with pytest.raises(PublicManifestError, match="exact hash|출처"):
        _run_full(flow=True)


def test_empty_evidence_rows의_FORGED_PRODUCT_999퍼센트는_FULL에서_차단한다():
    forged = PerformanceTable(
        caption="제품군 구성",
        headers=("제품군", "비중"),
        rows=(("FORGED_PRODUCT", "999%"),),
        unit="%",
        cite="[2]",
        evidence_rows=(),
    )
    with pytest.raises(PublicManifestError, match="evidence_rows|비중 합계"):
        _run_full(composition_tables=(forged,))


def test_구성표_공개값_999퍼센트_위조는_원자료와_달라_차단한다():
    valid = _valid_composition_table()
    forged = replace(
        valid,
        rows=(("핵심 제품", "999%"), ("기타", "30%")),
    )
    with pytest.raises(PublicManifestError, match="비중 합계|재검산"):
        _run_full(composition_tables=(forged,))


def test_구성표_FORGED_PRODUCT가_evidence의_열값과_다르면_막는다():
    valid = _valid_composition_table()
    forged = replace(
        valid,
        rows=(("FORGED_PRODUCT", "70%"), ("기타", "30%")),
    )
    with pytest.raises(PublicManifestError, match="재검산"):
        _run_full(composition_tables=(forged,))


def test_정상_구성표는_원자료_재검산과_manifest를_통과한다():
    output, _writer, _reviewer, _diagram = _run_full(
        composition_tables=(_valid_composition_table(),)
    )
    table = next(
        table
        for section in output.report.sections
        if section.cell == "business_model"
        for table in section.tables
    )
    assert table.rows[0] == ["핵심 제품", "70%"]
    assert table.evidence_rows
    assert table.source_cites == ["[2]"]
    assert table.manifest_ref


def test_검증된_injected_fact_ID는_출처와_모든_공개셀까지_맞아야_통과한다():
    url = "https://manifest.example/document/2"
    identity = document_identity_from_parts(url=url)
    fragment = CollectedFragment(
        fragment_id="2",
        kind="회사 공식 자료",
        text="핵심 제품 70%, 기타 30%로 구성된다.",
        source_url=url,
    )

    def sentence(fact_id: str, product: str, value: str) -> ComposedSentence:
        claim = StructuredClaim(
            fact_id=fact_id,
            claim_slot="business_model:revenue_structure",
            section_owner="business_model",
            source_fragment_id="2",
            source_identity=identity,
            verification_state="verified",
            state_evidence=f"{product} {value}%",
            subject_scope=product,
            metric="비중",
            unit="%",
            raw_value=value,
            display_value=value,
        )
        return ComposedSentence(
            text=f"{product} 비중은 {value}%다.",
            citations=("2",),
            grade=GRADE_CONFIRMED,
            planned_claim_slot=claim.claim_slot,
            verification_state="verified",
            structured_claim=claim,
        )

    business_sentences = (
        sentence("fact-product-70", "핵심 제품", "70"),
        sentence("fact-other-30", "기타", "30"),
    )
    report = ComposedReport(
        sections=tuple(
            ComposedSection(
                section_id=section_id,
                sentences=business_sentences if section_id == "business_model" else (),
            )
            for section_id in SECTION_IDS
        )
    )
    table = PerformanceTable(
        caption="제품군 구성",
        headers=("제품군", "비중"),
        rows=(("핵심 제품", "70%"), ("기타", "30%")),
        unit="%",
        cite="[2]",
        row_fact_ids=("fact-product-70", "fact-other-30"),
    )

    seal = build_public_structure_seal(
        report,
        (fragment,),
        None,
        filing_meta=None,
        composition_tables=(table,),
        table_presentation="table",
    )
    assert seal.ref_for("business_model", 0)

    forged = replace(
        table,
        rows=(("FORGED_PRODUCT", "70%"), ("기타", "30%")),
    )
    with pytest.raises(PublicManifestError, match="injected fact"):
        build_public_structure_seal(
            report,
            (fragment,),
            None,
            filing_meta=None,
            composition_tables=(forged,),
            table_presentation="table",
        )


def test_storage는_evidence_source_cites_manifest를_왕복하고_누락은_닫는다():
    output, _writer, _reviewer, _diagram = _run_full(flow=True)
    original = output.report
    restored = report_from_json(report_to_json(original))
    original_flow = next(
        table
        for section in original.sections
        for table in section.tables
        if table.presentation == "flow"
    )
    restored_flow = next(
        table
        for section in restored.sections
        for table in section.tables
        if table.presentation == "flow"
    )
    assert restored.public_structure_manifest == original.public_structure_manifest
    assert restored_flow.evidence_rows == original_flow.evidence_rows
    assert restored_flow.source_cites == original_flow.source_cites
    assert restored_flow.manifest_ref == original_flow.manifest_ref

    missing_manifest = report_to_dict(original)
    missing_manifest.pop("public_structure_manifest")
    with pytest.raises(ValueError, match="manifest"):
        report_from_dict(missing_manifest)

    missing_rows = report_to_dict(original)
    flow_payload = next(
        table
        for section in missing_rows["sections"]
        for table in section["tables"]
        if table.get("presentation") == "flow"
    )
    flow_payload.pop("evidence_rows")
    with pytest.raises(ValueError, match="manifest|행 근거"):
        report_from_dict(missing_rows)

    forged_cell = report_to_dict(original)
    forged_flow = next(
        table
        for section in forged_cell["sections"]
        for table in section["tables"]
        if table.get("presentation") == "flow"
    )
    forged_flow["rows"][0][0] = "FORGED_FLOW"
    with pytest.raises(PublicManifestError, match="manifest|행/셀/숫자/출처"):
        report_from_dict(forged_cell)


@pytest.mark.parametrize(
    "marker",
    (
        "⟦99⟧",
        "〘99〙",
        "❲99❳",
        "⟦99〙",
        "〘99❳",
        "❲99⟧",
        "⟦1, 99⟧",
        "앞 ⟦1⟧ 중간 〘22〙 뒤 ❲333❳",
        "［９９］",
    ),
)
def test_NFKC_뒤_일반_Ps_Pe와_혼합_짧은_인용표식은_차단한다(marker):
    assert contains_inline_citation_marker(marker)
    raw = json.dumps(
        {
            "문장들": [
                {
                    "글": f"공식 자료의 사실이다 {marker}",
                    "인용": ["1"],
                    "등급": GRADE_CONFIRMED,
                }
            ]
        },
        ensure_ascii=False,
    )
    assert parse_section_response(
        raw,
        "identity",
        reject_inline_citation_markers=True,
    ) == ()


@pytest.mark.parametrize("year", ("(2026)", "[2026]", "（２０２６）", "⟦2026⟧"))
def test_네자리_연도_괄호는_인용표식으로_오탐하지_않는다(year):
    assert not contains_inline_citation_marker(year)
