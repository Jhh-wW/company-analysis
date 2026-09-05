"""FULL 장별 packet과 독립 공개 구조 manifest의 공격 회귀."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from src.features.composer import pipeline as pipeline_module
from src.features.composer import render as render_module
from src.features.composer.constants import (
    DART_FINANCIAL_API_DOCUMENT_ID,
    DART_FINANCIAL_API_HOST,
    DART_FINANCIAL_API_PREFIX,
    DART_FINANCIAL_API_URL,
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
    SectionEvidencePacket,
    SectionEvidencePacketSet,
    StructuredClaim,
)
from src.features.composer.public_manifest import (
    PublicManifestError,
    _expected_source,
    build_public_structure_seal,
)
from src.features.revenuemix.logic import build as build_revenue_mix
from src.shared.report_generation.public_projection import PublicProjectionError
from src.features.composer.validate import V2ValidationError
from src.features.pipeline.port import Grade, ReportTable
from src.features.provenance.sources import has_valid_provenance_seal
from src.features.storage.reports import (
    report_from_dict,
    report_from_json,
    report_to_dict,
    report_to_json,
)
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.constants import (
    ReleaseMode,
    SOURCE_KIND_DART_BUSINESS_REPORT,
)
from src.shared.report_generation.canonical import (
    assert_report_matches_generation_evidence,
    public_content_digests,
    report_verification_payload,
)
from src.shared.report_generation.models import canonical_sha256
from src.shared.report_quality.source_identity import document_identity_from_parts
from src.shared.revenue_table_provenance import canonical_json


_MARKS = "가나다라마바사아자"
# 기준 보고서(진영) 실측 총 54문장을 그대로 만드는 정상 FULL 도구다.
# 특히 9장의 정책상 필수 여섯 칸을 결과값에 맞춰 생략하지 않는다.
_ENDINGS = ("첫째", "둘째", "셋째", "넷째", "다섯째", "여섯째")
_GROUPED_ITEM_RE = re.compile(
    r"\[(\d+)\] \(장: ([^,]+), 종류: ([^,]+), 인용: ([^)]+)\)"
)
_COMPOSITION_SOURCE = (
    "제품별 매출액 구 분 2025년 제1기 매 출 액 비 중 "
    "핵심 제품 7,000 70.00% 기타 3,000 30.00% 합계 10,000 100.00%"
)


@pytest.mark.parametrize("source_shape", ("filing", "financial_api"))
def test_public_manifest의_DART_Source도_발행회사와_DART_host를_분리한다(
    source_shape: str,
) -> None:
    company_name = "가나다전자"
    if source_shape == "filing":
        receipt = "20260315000123"
        url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}"
        fragment = CollectedFragment(
            fragment_id="1",
            kind="typed-evidence-v1:dart_business_report",
            text="가나다전자는 반도체 검사 장비 사업을 영위한다.",
            source_url=url,
            document_title="사업보고서 (2025.12)",
            location="II. 사업의 내용",
            document_date="2026-03-15",
            document_identity=document_identity_from_parts(
                document_id=receipt,
                host="dart.fss.or.kr",
                url=url,
            ),
        )
        expected_host = "dart.fss.or.kr"
        expected_document_id = receipt
    else:
        fragment = CollectedFragment(
            fragment_id="1",
            kind="재무",
            text=f"{DART_FINANCIAL_API_PREFIX} 2025 매출액 1200",
            document_identity=document_identity_from_parts(
                document_id=DART_FINANCIAL_API_DOCUMENT_ID,
                host=DART_FINANCIAL_API_HOST,
                url=DART_FINANCIAL_API_URL,
            ),
        )
        expected_host = DART_FINANCIAL_API_HOST
        expected_document_id = DART_FINANCIAL_API_DOCUMENT_ID

    source = _expected_source(
        fragment,
        number=1,
        company_name=company_name,
        used_in=("identity",),
        filing_meta=None,
    )

    # typed 수집 문서의 publisher는 자료를 제공한 DART로 남을 수 있지만,
    # 공개 Source는 공시 내용의 책임 주체인 분석 대상 법인을 표시한다.
    assert source.publisher == company_name
    assert source.host == expected_host
    assert source.document_id == expected_document_id


@pytest.mark.parametrize(
    "source_shape",
    ("formal", "financial_api", "generic_url", "fallback", "legacy"),
)
def test_모든_Source분기는_문서전체지문을_한경계에서_봉인한다(
    source_shape: str,
) -> None:
    company_name = "가나다전자"
    text = f"{source_shape} 종류의 서로 다른 문서 전체 원문이다."
    content_sha256 = "" if source_shape == "legacy" else hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()
    common = {
        "fragment_id": "1",
        "kind": "회사 공식 자료",
        "text": text,
        "document_title": f"{source_shape} 원문",
        "location": "본문",
        "document_date": "2026-03-15",
        "document_content_sha256": content_sha256,
    }
    if source_shape == "formal":
        receipt = "20260315000123"
        url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}"
        fragment = CollectedFragment(
            **common,
            source_url=url,
            document_identity=document_identity_from_parts(
                document_id=receipt,
                host="dart.fss.or.kr",
                url=url,
            ),
            formal_source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
            source_document_id=(
                f"{SOURCE_KIND_DART_BUSINESS_REPORT}:{receipt}"
            ),
            source_publisher="금융감독원 전자공시시스템",
            identity_binding=(
                f"corp_code=00123456;rcept_no={receipt};identity_check=verified"
            ),
            source_collected_on="2026-09-04",
        )
    elif source_shape == "financial_api":
        fragment = CollectedFragment(
            **{
                **common,
                "text": f"{DART_FINANCIAL_API_PREFIX} {text}",
            },
        )
    elif source_shape in {"generic_url", "legacy"}:
        fragment = CollectedFragment(
            **common,
            source_url=f"https://company.example/{source_shape}",
        )
    else:
        fragment = CollectedFragment(**common)

    source = _expected_source(
        fragment,
        number=1,
        company_name=company_name,
        used_in=("identity",),
        filing_meta=None,
    )

    assert source.document_content_sha256 == content_sha256
    assert has_valid_provenance_seal(source)


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
    source_table = f" {_COMPOSITION_SOURCE}" if mark == _MARKS[2] else ""
    return f"{common} {sentences} {flow}{source_table}"


def _document_content_sha256(text: str) -> str:
    """시험 원문 전체 바이트를 production 필드와 같은 SHA-256으로 고정한다."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _packets(*, two_flow_sources: bool = False):
    packets: list[SectionEvidencePacket] = []
    generation = "a" * 64
    for index, section_id in enumerate(SECTION_IDS, start=1):
        url = f"https://manifest.example/document/{index}"
        document_text = _fragment_text(_MARKS[index - 1])
        fragments = [
            CollectedFragment(
                fragment_id=str(index),
                kind="회사 공식 자료",
                text=document_text,
                source_url=url,
                document_title=f"공식 자료 {index}",
                document_identity=document_identity_from_parts(url=url),
                document_content_sha256=_document_content_sha256(document_text),
                supported_claim_slots=CLAIM_SLOTS_BY_SECTION[section_id],
            )
        ]
        if section_id == "business_model" and two_flow_sources:
            secondary_text = (
                document_text
                + " 보조 제품은 유통 협력을 거쳐 소비자에게 닿는다."
            )
            fragments.append(
                CollectedFragment(
                    fragment_id="20",
                    kind="회사 공식 자료",
                    text=secondary_text,
                    source_url="https://manifest.example/document/20",
                    document_title="공식 자료 이십",
                    document_identity=document_identity_from_parts(
                        url="https://manifest.example/document/20"
                    ),
                    document_content_sha256=_document_content_sha256(
                        secondary_text
                    ),
                    supported_claim_slots=CLAIM_SLOTS_BY_SECTION[section_id],
                )
            )
        packets.append(
            SectionEvidencePacket(
                company_id="00123456",
                evidence_generation_sha256=generation,
                section_id=section_id,
                fragments=tuple(fragments),
            )
        )
    return SectionEvidencePacketSet(
        company_id="00123456",
        evidence_generation_sha256=generation,
        packets=tuple(packets),
    )


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


class _RecoveringPacketWriter:
    """첫 묶음의 지정 장만 얇게 쓰고, 승인받은 재호출에서만 보충한다."""

    def __init__(
        self,
        targets: tuple[str, ...],
        *,
        remain_thin: bool = False,
    ) -> None:
        self.targets = targets
        self.remain_thin = remain_thin
        self.prompts: list[str] = []
        self.section_calls: dict[str, int] = {}

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        fragment_ids = re.findall(r"\[조각 (\d+)\] \(", prompt)
        assert len(fragment_ids) == 1
        section_id = SECTION_IDS[int(fragment_ids[0]) - 1]
        section_call = self.section_calls.get(section_id, 0) + 1
        self.section_calls[section_id] = section_call
        mark = _MARKS[int(fragment_ids[0]) - 1]
        slots = CLAIM_SLOTS_BY_SECTION[section_id]
        if section_id in self.targets and (
            section_call == 1 or self.remain_thin
        ):
            # 두 번째도 얇게 두는 시험에서는 원문 안의 다른 문장으로 바꿔
            # section/candidate가 실제로 달라진 뒤 quality 실패를 보게 한다.
            endings = (_ENDINGS[min(section_call - 1, 1)],)
        else:
            endings = _ENDINGS
        return json.dumps(
            {
                "문장들": [
                    {
                        "글": (
                            f"{mark} 회사 사업 고객 제품 전략 운영 문화 경쟁 과제 "
                            f"대응 협력 실적 {ending} 공식 자료에서 확인했다."
                        ),
                        "인용": fragment_ids,
                        "등급": GRADE_CONFIRMED,
                        "주장슬롯": slots[index % len(slots)],
                    }
                    for index, ending in enumerate(endings)
                ]
            },
            ensure_ascii=False,
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
        if not verdicts:
            verdicts = [
                {"번호": int(number), "결과": "참"}
                for number in re.findall(
                    r"\[(\d+)\] \(등급: [^,\n]+, 인용:", prompt
                )
            ]
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
        company_id="00123456",
        build_identity_sha256="b" * 64,
    )
    return output, writer, reviewer, diagram


def _run_recovering_full(
    targets: tuple[str, ...],
    *,
    remain_thin: bool = False,
    packets=None,
):
    writer = _RecoveringPacketWriter(targets, remain_thin=remain_thin)
    reviewer = _BoundGroupedReviewer()
    output = run_v2(
        "가나다전자",
        (),
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
        diagram_ask=_NoDiagram(),
        release_mode=ReleaseMode.FULL,
        section_evidence_packets=packets or _packets(),
        company_id="00123456",
        build_identity_sha256="b" * 64,
    )
    return output, writer, reviewer


def _valid_composition_table() -> PerformanceTable:
    raw = build_revenue_mix(_COMPOSITION_SOURCE, cite="[3]")[0]
    return PerformanceTable(
        caption="제품군 구성",
        headers=("구분", "비중"),
        rows=tuple((row[0], row[2]) for row in raw["rows"][:-1]),
        unit="%",
        cite="[3]",
        raw_rows=tuple((row[0], row[2]) for row in raw["raw_rows"][:-1]),
        evidence_rows=tuple(raw["evidence_rows"][:-1]),
    )


def _composition_table_from_source(source: str) -> PerformanceTable:
    raw = build_revenue_mix(source, cite="[3]")[0]
    return PerformanceTable(
        caption="제품군 구성",
        headers=("구분", "비중"),
        rows=tuple((row[0], row[2]) for row in raw["rows"][:-1]),
        raw_rows=tuple((row[0], row[2]) for row in raw["raw_rows"][:-1]),
        evidence_rows=tuple(raw["evidence_rows"][:-1]),
        unit="%",
        cite="[3]",
    )


def _packets_with_portfolio_source(source: str):
    packets = _packets()
    replaced_packets = []
    for packet in packets.packets:
        if packet.section_id != "portfolio":
            replaced_packets.append(packet)
            continue
        fragment = packet.fragments[0]
        combined_text = f"{fragment.text} {source}"
        replaced_packets.append(
            replace(
                packet,
                fragments=(
                    replace(
                        fragment,
                        text=combined_text,
                        document_content_sha256=_document_content_sha256(
                            combined_text
                        ),
                    ),
                ),
            )
        )
    return replace(packets, packets=tuple(replaced_packets))


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
    assert flow.evidence_rows == []
    assert len(flow.row_evidence_refs) == len(flow.rows) == 2
    assert len(flow.row_binding_refs) == len(flow.rows)
    assert all(len(refs) == len(flow.headers) for refs in flow.cell_binding_refs)
    evidence = output.generation_evidence
    assert evidence is not None
    assert evidence.writer_calls == 9
    assert evidence.reviewer_calls == 1
    assert len(evidence.validation_receipts) == 1
    assert evidence.validation_receipts[0].writer_calls == 9
    assert evidence.validation_receipts[0].reviewer_calls == 1
    assert output.generation_metrics == output.report.generation_metrics


@pytest.mark.parametrize(
    ("targets", "expected_calls"),
    (
        (("identity",), 12),
        (("identity", "business_model"), 13),
    ),
)
def test_FULL은_승인장_한두개만_한번_보충하고_round장부를_잇는다(
    targets,
    expected_calls,
):
    output, writer, reviewer = _run_recovering_full(targets)
    evidence = output.generation_evidence
    assert evidence is not None
    assert len(writer.prompts) == 9 + len(targets)
    assert len(reviewer.prompts) == 2
    assert len(evidence.call_ledger.records) == expected_calls
    assert len(evidence.validation_receipts) == 2
    primary, supplement = evidence.validation_receipts
    assert supplement.supplemented_section_ids == targets
    assert supplement.base_receipt_sha256 == primary.receipt_sha256
    assert tuple(record.sequence for record in evidence.call_ledger.records) == tuple(
        range(1, expected_calls + 1)
    )
    supplement_records = evidence.call_ledger.records[10:]
    assert tuple(record.validation_round.value for record in supplement_records) == (
        ("SUPPLEMENT",) * (len(targets) + 1)
    )
    assert tuple(
        record.role_index
        for record in supplement_records
        if record.role == "writer"
    ) == tuple(range(1, len(targets) + 1))
    assert supplement_records[-1].role == "reviewer"
    assert supplement_records[-1].role_index == 1
    assert supplement_records[-1].section_id == "bundled"
    base_hashes = dict(primary.section_sha256s)
    final_hashes = dict(supplement.section_sha256s)
    for section_id in SECTION_IDS:
        assert (base_hashes[section_id] != final_hashes[section_id]) == (
            section_id in targets
        )
    for target, prompt in zip(targets, writer.prompts[-len(targets) :]):
        expected_fragment = str(SECTION_IDS.index(target) + 1)
        assert re.findall(r"\[조각 (\d+)\] \(", prompt) == [expected_fragment]


def test_보충병합뒤_비대상본문도식fact와_section_hash는_exact불변이다(
    monkeypatch,
):
    snapshots = []
    original = pipeline_module.build_generation_quality_candidate

    def capture(rendered, composed):
        snapshots.append(
            (
                composed,
                tuple(rendered.sections),
                tuple(rendered.fact_records),
            )
        )
        return original(rendered, composed)

    monkeypatch.setattr(
        pipeline_module,
        "build_generation_quality_candidate",
        capture,
    )
    output, _writer, _reviewer = _run_recovering_full(("identity",))

    assert len(snapshots) == 2
    primary_report, primary_rendered_sections, primary_facts = snapshots[0]
    final_report, final_rendered_sections, final_facts = snapshots[1]
    primary_sections = {
        section.section_id: section for section in primary_report.sections
    }
    final_sections = {section.section_id: section for section in final_report.sections}
    primary_rendered = {
        section.cell: section for section in primary_rendered_sections
    }
    final_rendered = {
        section.cell: section for section in final_rendered_sections
    }
    for section_id in SECTION_IDS[1:]:
        assert final_sections[section_id] == primary_sections[section_id]
        # 웹·PDF가 소비하는 표시 문단·표·도식까지 같은 dataclass bytes
        # projection을 유지하며, 아래 section SHA도 exact 동일해야 한다.
        assert final_rendered[section_id] == primary_rendered[section_id]
        assert tuple(
            fact for fact in final_facts if fact.section_owner == section_id
        ) == tuple(fact for fact in primary_facts if fact.section_owner == section_id)
    evidence = output.generation_evidence
    assert evidence is not None
    primary, supplement = evidence.validation_receipts
    assert dict(primary.section_sha256s)["identity"] != dict(
        supplement.section_sha256s
    )["identity"]
    assert primary.section_sha256s[1:] == supplement.section_sha256s[1:]


def test_보충병합본에서_전역파생물과_평가를_모두다시계산한다(monkeypatch):
    names = (
        "enforce_public_numeric_safety",
        "select_extractive_summary",
        "build_public_structure_seal",
        "render_report",
        "build_generation_quality_candidate",
        "assess_and_observe_generation",
    )
    originals = {name: getattr(pipeline_module, name) for name in names}
    counts = {name: 0 for name in names}

    def counted(name):
        def wrapper(*args, **kwargs):
            counts[name] += 1
            return originals[name](*args, **kwargs)

        return wrapper

    for name in names:
        monkeypatch.setattr(pipeline_module, name, counted(name))

    _run_recovering_full(("identity",))

    assert counts == {
        "enforce_public_numeric_safety": 3,
        "select_extractive_summary": 2,
        "build_public_structure_seal": 2,
        "render_report": 4,
        "build_generation_quality_candidate": 2,
        "assess_and_observe_generation": 2,
    }


def test_보충뒤에도_얇으면_세번째호출없이_닫힌사유로_끝난다():
    writer = _RecoveringPacketWriter(("identity",), remain_thin=True)
    reviewer = _BoundGroupedReviewer()

    with pytest.raises(
        V2ValidationError,
        match="report_recovery:post_supplement_quality_failed",
    ) as caught:
        run_v2(
            "가나다전자",
            (),
            None,
            writer_ask=writer,
            reviewer_ask=reviewer,
            release_mode=ReleaseMode.FULL,
            section_evidence_packets=_packets(),
            company_id="00123456",
            build_identity_sha256="b" * 64,
        )

    assert len(writer.prompts) == 10
    assert len(reviewer.prompts) == 2
    assert writer.section_calls["identity"] == 2
    assert "missing_required_public_claim_slots" in caught.value.problem_codes
    assert "low_public_sentence_coverage" in caught.value.problem_codes


def test_보충검수에서_안전실패하면_세번째회차없이_즉시중단한다():
    class SecondRoundSafetyBlockedReviewer(_BoundGroupedReviewer):
        def __call__(self, prompt: str) -> str:
            payload = json.loads(super().__call__(prompt))
            if len(self.prompts) == 2:
                for verdict in payload["판정"]:
                    verdict["결과"] = "애매"
            return json.dumps(payload, ensure_ascii=False)

    writer = _RecoveringPacketWriter(("identity",))
    reviewer = SecondRoundSafetyBlockedReviewer()
    with pytest.raises(
        V2ValidationError,
        match="report_recovery:post_supplement_safety_blocked",
    ):
        run_v2(
            "가나다전자",
            (),
            None,
            writer_ask=writer,
            reviewer_ask=reviewer,
            release_mode=ReleaseMode.FULL,
            section_evidence_packets=_packets(),
            company_id="00123456",
            build_identity_sha256="b" * 64,
        )

    assert len(writer.prompts) == 10
    assert len(reviewer.prompts) == 2
    assert writer.section_calls["identity"] == 2


def test_보충후보지문이_같으면_재보충없이_중단한다(monkeypatch):
    monkeypatch.setattr(
        pipeline_module,
        "canonical_sha256",
        lambda _value: "c" * 64,
    )
    writer = _RecoveringPacketWriter(("identity",))
    reviewer = _BoundGroupedReviewer()

    with pytest.raises(
        V2ValidationError,
        match="report_recovery:supplement_candidate_unchanged",
    ):
        run_v2(
            "가나다전자",
            (),
            None,
            writer_ask=writer,
            reviewer_ask=reviewer,
            release_mode=ReleaseMode.FULL,
            section_evidence_packets=_packets(),
            company_id="00123456",
            build_identity_sha256="b" * 64,
        )

    assert len(writer.prompts) == 10
    assert len(reviewer.prompts) == 2


def test_얇은장이_세개면_primary10회뒤_보충하지않는다():
    targets = ("identity", "business_model", "portfolio")
    writer = _RecoveringPacketWriter(targets)
    reviewer = _BoundGroupedReviewer()

    with pytest.raises(
        V2ValidationError,
        match="report_recovery:too_many_underfilled_sections",
    ):
        run_v2(
            "가나다전자",
            (),
            None,
            writer_ask=writer,
            reviewer_ask=reviewer,
            release_mode=ReleaseMode.FULL,
            section_evidence_packets=_packets(),
            company_id="00123456",
            build_identity_sha256="b" * 64,
        )

    assert len(writer.prompts) == 9
    assert len(reviewer.prompts) == 1


def test_안전실패는_primary10회뒤_보충없이_즉시중단한다():
    class SafetyBlockedReviewer(_BoundGroupedReviewer):
        def __call__(self, prompt: str) -> str:
            payload = json.loads(super().__call__(prompt))
            for verdict in payload["판정"]:
                verdict["결과"] = "애매"
            return json.dumps(payload, ensure_ascii=False)

    writer = _CompletePacketWriter()
    reviewer = SafetyBlockedReviewer()
    with pytest.raises(
        V2ValidationError,
        match="report_recovery:post_validation_safety_blocked",
    ):
        run_v2(
            "가나다전자",
            (),
            None,
            writer_ask=writer,
            reviewer_ask=reviewer,
            release_mode=ReleaseMode.FULL,
            section_evidence_packets=_packets(),
            company_id="00123456",
            build_identity_sha256="b" * 64,
        )

    assert len(writer.prompts) == 9
    assert len(reviewer.prompts) == 1


def test_독립문서수가_부족하면_primary10회뒤_보충하지않는다():
    original = _packets()
    shared_url = "https://manifest.example/same-document"
    shared_identity = document_identity_from_parts(url=shared_url)
    shared_text = " ".join(_fragment_text(mark) for mark in _MARKS)
    packets = replace(
        original,
        packets=tuple(
            replace(
                packet,
                fragments=tuple(
                    replace(
                        fragment,
                        text=shared_text,
                        source_url=shared_url,
                        document_identity=shared_identity,
                        document_content_sha256=_document_content_sha256(
                            shared_text
                        ),
                    )
                    for fragment in packet.fragments
                ),
            )
            for packet in original.packets
        ),
    )
    writer = _CompletePacketWriter()
    reviewer = _BoundGroupedReviewer()

    with pytest.raises(
        V2ValidationError,
        match="report_recovery:post_validation_nonrecoverable_quality",
    ):
        run_v2(
            "가나다전자",
            (),
            None,
            writer_ask=writer,
            reviewer_ask=reviewer,
            release_mode=ReleaseMode.FULL,
            section_evidence_packets=packets,
            company_id="00123456",
            build_identity_sha256="b" * 64,
        )

    assert len(writer.prompts) == 9
    assert len(reviewer.prompts) == 1


def test_production_composer가_shared정본_decide_post_validation을_실제호출한다():
    tree = ast.parse(Path(pipeline_module.__file__).read_text(encoding="utf-8"))
    imports = [node for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert any(
        node.module == "src.shared.report_recovery"
        and "decide_post_validation" in {alias.name for alias in node.names}
        for node in imports
    )
    assert not any(
        node.module == "src.features.report_recovery" for node in imports
    )
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "decide_post_validation"
        for node in ast.walk(tree)
    )


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
        company_id="00123456",
        build_identity_sha256="b" * 64,
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


def test_FULL_packet_작성응답이_전부_깨져도_기본묶음검수는_정확히_1회다():
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
            company_id="00123456",
            build_identity_sha256="b" * 64,
        )

    assert writer.calls == 9
    assert reviewer.calls == 1
    assert diagram.calls == 0


def test_FULL_without_typed_packets는_AI_호출전_0_0으로_차단한다():
    writer = _CompletePacketWriter()
    reviewer = _BoundGroupedReviewer()
    with pytest.raises(V2ValidationError, match="typed|packet|아홉"):
        run_v2(
            "가나다전자",
            (),
            None,
            writer_ask=writer,
            reviewer_ask=reviewer,
            release_mode=ReleaseMode.FULL,
            section_evidence_packets=None,
            company_id="00123456",
            build_identity_sha256="b" * 64,
        )
    assert writer.prompts == []
    assert reviewer.prompts == []


def test_FULL_cross_company_packet은_AI_호출전_0_0으로_차단한다():
    writer = _CompletePacketWriter()
    reviewer = _BoundGroupedReviewer()
    with pytest.raises(V2ValidationError, match="회사|company"):
        run_v2(
            "가나다전자",
            (),
            None,
            writer_ask=writer,
            reviewer_ask=reviewer,
            release_mode=ReleaseMode.FULL,
            section_evidence_packets=_packets(),
            company_id="87654321",
            build_identity_sha256="b" * 64,
        )
    assert writer.prompts == []
    assert reviewer.prompts == []


def test_FULL_packet간_fragment충돌도_AI호출전_닫힌사유로_차단한다():
    original = _packets()
    second = original.packets[1]
    conflicting = replace(
        original,
        packets=(
            original.packets[0],
            replace(
                second,
                fragments=(
                    replace(second.fragments[0], fragment_id="1"),
                ),
            ),
            *original.packets[2:],
        ),
    )
    writer = _CompletePacketWriter()
    reviewer = _BoundGroupedReviewer()
    with pytest.raises(
        V2ValidationError,
        match="report_recovery:preflight_packet_invalid",
    ):
        run_v2(
            "가나다전자",
            (),
            None,
            writer_ask=writer,
            reviewer_ask=reviewer,
            release_mode=ReleaseMode.FULL,
            section_evidence_packets=conflicting,
            company_id="00123456",
            build_identity_sha256="b" * 64,
        )

    assert writer.prompts == []
    assert reviewer.prompts == []


def test_최종출고검증이_실패하면_ProducerEvidence를_만들지않는다(
    monkeypatch,
):
    created = []

    def fail_validation(_report):
        raise V2ValidationError(("시험용 최종 출고 실패",))

    def forbidden_evidence(**kwargs):
        created.append(kwargs)
        raise AssertionError("최종 출고 검증 전에 생산 증거를 만들면 안 됩니다")

    monkeypatch.setattr(pipeline_module, "validate_v2", fail_validation)
    monkeypatch.setattr(
        pipeline_module,
        "GenerationProducerEvidence",
        forbidden_evidence,
    )
    writer = _CompletePacketWriter()
    reviewer = _BoundGroupedReviewer()
    with pytest.raises(V2ValidationError, match="시험용 최종 출고 실패"):
        run_v2(
            "가나다전자",
            (),
            None,
            writer_ask=writer,
            reviewer_ask=reviewer,
            release_mode=ReleaseMode.FULL,
            section_evidence_packets=_packets(),
            company_id="00123456",
            build_identity_sha256="b" * 64,
        )

    assert created == []
    assert len(writer.prompts) == 9
    assert len(reviewer.prompts) == 1


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
            company_id="00123456",
            build_identity_sha256="b" * 64,
        )

    assert len(writer.prompts) == 9
    assert reviewer.calls == 1
    assert diagram.calls == 0


def test_typed_packet의_문서일과_전체지문은_Source와_품질평가에_보존된다():
    original = _packets()
    first_packet = original.packets[0]
    first_fragment = replace(
        first_packet.fragments[0],
        document_date="2026-08-30",
        location="회사소개 > 개요",
    )
    packets = replace(
        original,
        packets=(
            replace(first_packet, fragments=(first_fragment,)),
            *original.packets[1:],
        ),
    )

    output, _writer, _reviewer, _diagram = _run_full(packets=packets)
    source = next(source for source in output.report.citations if source.number == 1)
    assert source.collected_at == "2026-08-30"
    assert source.document_content_sha256 == first_fragment.document_content_sha256
    expected_hashes = {
        fragment.document_content_sha256
        for packet in packets.packets
        for fragment in packet.fragments
    }
    assert {
        source.document_content_sha256 for source in output.report.citations
    } == expected_hashes
    assert len(expected_hashes) == 9
    assert output.quality_observation.document_sources == 9


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
            row_refs = list(table.row_binding_refs)
            if len(row_refs) < 2:
                return table
            row_refs[0], row_refs[1] = row_refs[1], row_refs[0]
            return replace(table, row_binding_refs=row_refs)

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
        cite="[3]",
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
        if section.cell == "portfolio"
        for table in section.tables
    )
    assert table.rows[0] == ["핵심 제품", "70.00%"]
    assert table.evidence_rows
    assert table.source_cites == ["[3]"]
    assert table.manifest_ref


def test_공개행만_되풀이한_가짜_evidence_JSON은_인용원문_근거가_아니다():
    valid = _valid_composition_table()
    fabricated = replace(
        valid,
        evidence_rows=tuple(
            canonical_json(dict(zip(valid.headers, row))) for row in valid.rows
        ),
    )

    with pytest.raises(PublicManifestError, match="인용 원문|재검산"):
        _run_full(composition_tables=(fabricated,))


@pytest.mark.parametrize(("container", "field"), (("row", "start"), ("source", "sha256")))
def test_매출구성_원문_offset이나_hash를_바꾸면_manifest가_막는다(
    container: str, field: str
):
    valid = _valid_composition_table()
    payload = json.loads(valid.evidence_rows[0])
    if field == "start":
        payload[container][field] += 1
    else:
        payload[container][field] = "0" * 64
    forged = replace(
        valid,
        evidence_rows=(canonical_json(payload),) + valid.evidence_rows[1:],
    )

    with pytest.raises(PublicManifestError, match="인용 원문|재검산"):
        _run_full(composition_tables=(forged,))


def test_매출구성_원문행이_없는_다른_cite조각으로_바꾸면_막는다():
    packets = _packets()
    replaced_packets = tuple(
        replace(
            packet,
            fragments=tuple(
                replace(fragment, text=fragment.text.replace(_COMPOSITION_SOURCE, ""))
                for fragment in packet.fragments
            ),
        )
        if packet.section_id == "portfolio"
        else packet
        for packet in packets.packets
    )

    with pytest.raises(PublicManifestError, match="인용 원문|재검산"):
        _run_full(
            composition_tables=(_valid_composition_table(),),
            packets=replace(packets, packets=replaced_packets),
        )


def test_서로_다른_두_원문표의_행을_이어붙인_구성표는_막는다():
    other_source = (
        "제품별 매출액 구 분 2025년 제1기 매 출 액 비 중 "
        "다른 제품 7,000 70.00% 기타 3,000 30.00% 합계 10,000 100.00%"
    )
    valid = _valid_composition_table()
    other = _composition_table_from_source(other_source)
    stitched = replace(
        valid,
        evidence_rows=(valid.evidence_rows[0], other.evidence_rows[1]),
    )

    with pytest.raises(PublicManifestError, match="인용 원문|재검산"):
        _run_full(
            composition_tables=(stitched,),
            packets=_packets_with_portfolio_source(other_source),
        )


def test_표시된_소수둘째자리에서_가능한_99점99_반올림은_허용한다():
    source = (
        "제품별 매출액 구 분 2025년 제1기 매 출 액 비 중 "
        "제품가 3,333 33.33% 제품나 3,333 33.33% 제품다 3,333 33.33% "
        "합계 9,999 100.00%"
    )
    table = _composition_table_from_source(source)

    output, *_ = _run_full(
        composition_tables=(table,),
        packets=_packets_with_portfolio_source(source),
    )

    assert output.report.public_structure_manifest


def test_두_항목이라_3열_원표로_남아도_합계행과_원문근거를_검증한다():
    source = (
        "제품별 매출액 구 분 2025년 제1기 매 출 액 비 중 "
        "제품가 7,000 70.00% 제품나 3,000 30.00% 합계 10,000 100.00%"
    )
    raw = build_revenue_mix(source, cite="[3]")[0]
    table = PerformanceTable(
        caption=raw["caption"],
        headers=tuple(raw["headers"]),
        rows=tuple(tuple(row) for row in raw["rows"]),
        raw_rows=tuple(tuple(row) for row in raw["raw_rows"]),
        evidence_rows=tuple(raw["evidence_rows"]),
        cite="[3]",
    )

    output, *_ = _run_full(
        composition_tables=(table,),
        packets=_packets_with_portfolio_source(source),
    )

    assert output.report.public_structure_manifest


def test_표시반올림으로_설명할수없는_90퍼센트_부분표는_막는다():
    source = (
        "제품별 매출액 구 분 2025년 제1기 매 출 액 비 중 "
        "제품가 4,000 40.00% 제품나 3,000 30.00% 제품다 2,000 20.00% "
        "제품라 1,000 10.00% "
        "합계 10,000 100.00%"
    )
    complete = _composition_table_from_source(source)
    table = replace(
        complete,
        rows=complete.rows[:-1],
        raw_rows=complete.raw_rows[:-1],
        evidence_rows=complete.evidence_rows[:-1],
    )

    with pytest.raises(PublicManifestError, match="반올림 범위|비중 합계"):
        _run_full(
            composition_tables=(table,),
            packets=_packets_with_portfolio_source(source),
        )


def test_검증된_injected_fact_ID는_출처와_모든_공개셀까지_맞아야_통과한다():
    url = "https://manifest.example/document/3"
    identity = document_identity_from_parts(url=url)
    fragment = CollectedFragment(
        fragment_id="3",
        kind="회사 공식 자료",
        text="핵심 제품 70%, 기타 30%로 구성된다.",
        source_url=url,
        document_identity=identity,
    )

    def sentence(fact_id: str, product: str, value: str) -> ComposedSentence:
        claim = StructuredClaim(
            fact_id=fact_id,
            claim_slot="portfolio:revenue_link",
            section_owner="portfolio",
            source_fragment_id="3",
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
            citations=("3",),
            grade=GRADE_CONFIRMED,
            planned_claim_slot=claim.claim_slot,
            verification_state="verified",
            structured_claim=claim,
        )

    portfolio_sentences = (
        sentence("fact-product-70", "핵심 제품", "70"),
        sentence("fact-other-30", "기타", "30"),
    )
    report = ComposedReport(
        sections=tuple(
            ComposedSection(
                section_id=section_id,
                sentences=portfolio_sentences if section_id == "portfolio" else (),
            )
            for section_id in SECTION_IDS
        )
    )
    table = PerformanceTable(
        caption="제품군 구성",
        headers=("제품군", "비중"),
        rows=(("핵심 제품", "70%"), ("기타", "30%")),
        unit="%",
        cite="[3]",
        row_fact_ids=("fact-product-70", "fact-other-30"),
    )

    seal = build_public_structure_seal(
        report,
        (fragment,),
        None,
        filing_meta=None,
        composition_tables=(table,),
        table_presentation="table",
        company_id="00123456",
        evidence_generation_sha256="a" * 64,
        evidence_packet_sha256s=tuple(
            (section_id, str(index) * 64)
            for index, section_id in enumerate(SECTION_IDS, start=1)
        ),
        company_name="가나다전자",
        corp_type="기업",
        generated_at="2026-08-30T00:00:00+09:00",
        as_of_date="2026-08-30",
        analysis_period="2025-01-01~2026-08-30",
        latest_performance_period="2026-06-30",
        citation_style="auto",
    )
    assert seal.ref_for("portfolio", 0)

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
            company_id="00123456",
            evidence_generation_sha256="a" * 64,
            evidence_packet_sha256s=tuple(
                (section_id, str(index) * 64)
                for index, section_id in enumerate(SECTION_IDS, start=1)
            ),
            company_name="가나다전자",
            corp_type="기업",
            generated_at="2026-08-30T00:00:00+09:00",
            as_of_date="2026-08-30",
            analysis_period="2025-01-01~2026-08-30",
            latest_performance_period="2026-06-30",
            citation_style="auto",
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
    assert restored.generation_evidence == original.generation_evidence
    assert restored.generation_metrics == original.generation_metrics
    assert restored.quality_observation == original.quality_observation
    assert restored_flow.evidence_rows == []
    assert restored_flow.source_cites == original_flow.source_cites
    assert restored_flow.manifest_ref == original_flow.manifest_ref
    assert restored_flow.row_evidence_refs == original_flow.row_evidence_refs
    assert restored_flow.row_binding_refs == original_flow.row_binding_refs
    assert restored_flow.cell_binding_refs == original_flow.cell_binding_refs

    missing_manifest = report_to_dict(original)
    missing_manifest.pop("public_structure_manifest")
    with pytest.raises(ValueError, match="manifest"):
        report_from_dict(missing_manifest)

    downgraded_contract = report_to_dict(original)
    downgraded_contract["quality_contract_version"] = "generation-forged"
    with pytest.raises((ValueError, PublicManifestError), match="계약|contract"):
        report_from_dict(downgraded_contract)

    missing_rows = report_to_dict(original)
    flow_payload = next(
        table
        for section in missing_rows["sections"]
        for table in section["tables"]
        if table.get("presentation") == "flow"
    )
    flow_payload.pop("row_binding_refs")
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

    # 같은 저장 JSON 안의 manifest 내부 checksum만 다시 계산해도, 별도 producer
    # transport가 가진 exact manifest bytes 지문은 바뀌지 않으므로 통과 못 한다.
    self_forged = report_to_dict(original)
    manifest = json.loads(self_forged["public_structure_manifest"])
    manifest["evidence_generation_sha256"] = "f" * 64
    manifest["digest"] = canonical_sha256(
        {key: value for key, value in manifest.items() if key != "digest"}
    )
    self_forged["public_structure_manifest"] = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with pytest.raises(PublicManifestError, match="manifest bytes|지문|generation"):
        report_from_dict(self_forged)


def test_ENFORCE_NO_PARTIAL은_generation_evidence없이_release_mode와_지표를_왕복한다():
    writer = _CompletePacketWriter()
    reviewer = _BoundGroupedReviewer()
    output = run_v2(
        "가나다전자",
        (),
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
        diagram_ask=_NoDiagram(),
        release_mode=ReleaseMode.ENFORCE_NO_PARTIAL,
        section_evidence_packets=_packets(),
    )
    restored = report_from_json(report_to_json(output.report))
    assert restored.release_mode == ReleaseMode.ENFORCE_NO_PARTIAL.value
    assert restored.generation_evidence is None
    assert restored.generation_metrics == output.generation_metrics
    assert restored.quality_observation == output.quality_observation


def test_ENFORCE_NO_PARTIAL_packetless는_도식_AI_0회이고_flow를_미공개한다():
    fragments = tuple(packet.fragments[0] for packet in _packets().packets)

    class FlatStrictWriter:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, prompt: str) -> str:
            section_id = SECTION_IDS[self.calls]
            fragment_id = str(self.calls + 1)
            mark = _MARKS[self.calls]
            slots = CLAIM_SLOTS_BY_SECTION[section_id]
            self.calls += 1
            rows = []
            if section_id == "business_model":
                rows = [
                    {
                        "칸": ["핵심 자산", "핵심 제품", "기업 고객", "반복 수익"],
                        "인용": [fragment_id],
                    }
                ]
            return json.dumps(
                {
                    "문장들": [
                        {
                            "글": (
                                f"{mark} 회사 사업 고객 제품 전략 운영 문화 경쟁 "
                                f"과제 대응 협력 실적 {ending} 공식 자료에서 확인했다."
                            ),
                            "인용": [fragment_id],
                            "등급": GRADE_CONFIRMED,
                            "주장슬롯": slots[index % len(slots)],
                        }
                        for index, ending in enumerate(_ENDINGS)
                    ],
                    "경로표": rows,
                },
                ensure_ascii=False,
            )

    writer = FlatStrictWriter()
    reviewer = _BoundGroupedReviewer()
    diagram = _NoDiagram()
    output = run_v2(
        "가나다전자",
        fragments,
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
        diagram_ask=diagram,
        release_mode=ReleaseMode.ENFORCE_NO_PARTIAL,
        section_evidence_packets=None,
    )

    assert writer.calls == 9
    assert len(reviewer.prompts) == 1
    assert diagram.calls == 0
    assert all(
        table.presentation != "flow"
        for section in output.report.sections
        for table in section.tables
    )


@pytest.mark.parametrize("target", ("prose", "paragraph", "summary"))
def test_renderer가_최종_공개본문_문단_요약을_위조하면_content_digest가_막는다(
    monkeypatch,
    target,
):
    original = render_module.render_report

    def forge_public_content(*args, **kwargs):
        report = original(*args, **kwargs)
        if target == "summary":
            items = list(report.summary_items)
            if not items:
                return report
            items[0] = replace(items[0], text="FORGED_SUMMARY")
            return replace(report, summary_items=items)
        sections = list(report.sections)
        section = sections[0]
        if target == "prose":
            lines = list(section.prose_lines)
            lines[0] = ("FORGED_PUBLIC_PROSE", lines[0][1])
            sections[0] = replace(section, prose_lines=lines)
        else:
            paragraphs = list(section.prose_paragraphs)
            paragraphs[0] = "FORGED_PUBLIC_PARAGRAPH"
            sections[0] = replace(section, prose_paragraphs=paragraphs)
        return replace(report, sections=sections)

    monkeypatch.setattr(pipeline_module, "render_report", forge_public_content)
    # ★ 지키는 것은 그대로다 — 위조된 공개 본문은 «어느 경우에도 출고되지
    #   않는다». 막는 «자리»만 두 가지다(S3c, 실측):
    #     · summary 위조 → 최종 공개 content digest 대조(PublicManifestError)
    #     · prose·paragraph 위조 → 그보다 앞선 공개 봉인 builder의 I2
    #       (「문단과 문장을 이어붙인 글자가 다릅니다」, PublicProjectionError)
    #   S3c가 영수증에 장별 봉인 블록 지문을 실으면서 봉인 builder가 렌더 직후로
    #   당겨졌고, 그 결과 문단/문장 불일치는 digest 대조보다 먼저 걸린다. 둘 다
    #   fail-closed라 보호는 늘었지 줄지 않았다. 어느 쪽이든 통과하지 못한다는
    #   사실을 여기서 단정한다.
    with pytest.raises(
        (PublicManifestError, PublicProjectionError),
        match="공개 content|본문|문단|요약|I2",
    ):
        _run_full()


def test_public_content_digest는_비공개_lines_fact_ids를_제외하고_공개산문을_포함한다():
    output, _writer, _reviewer, _diagram = _run_full()
    report = output.report
    original_digest, _sections = public_content_digests(report)
    first = report.sections[0]
    private_changed = replace(
        report,
        sections=[
            replace(first, lines=[("PRIVATE_INTERNAL_CHANGED", "")], fact_ids=["hidden"]),
            *report.sections[1:],
        ],
    )
    assert public_content_digests(private_changed)[0] == original_digest

    public_changed = replace(
        report,
        sections=[
            replace(first, prose_lines=[("PUBLIC_CHANGED", "")]),
            *report.sections[1:],
        ],
    )
    assert public_content_digests(public_changed)[0] != original_digest


@pytest.mark.parametrize(
    ("field", "forged_value"),
    (
        ("grade", Grade.PARTIAL),
        ("shortfall_reasons", ["FORGED_SHORTFALL"]),
        ("quality_contract_version", "generation-forged"),
        ("safety_decision", "공개 차단"),
        ("publication_policy", "legacy-shadow-exception-v1"),
    ),
)
def test_FULL_공개_출고표시_변조는_public_content_digest가_막는다(
    field,
    forged_value,
):
    output, _writer, _reviewer, _diagram = _run_full()
    evidence = output.generation_evidence
    assert evidence is not None
    forged = replace(output.report, **{field: forged_value})

    assert public_content_digests(forged)[0] != evidence.public_content_sha256
    with pytest.raises(PublicManifestError):
        assert_report_matches_generation_evidence(
            report_verification_payload(forged),
            evidence,
            manifest_bytes=output.report.public_structure_manifest.encode("utf-8"),
        )


def test_구성표_열값_교환은_머리글별_typed_binding이_막는다():
    valid = _valid_composition_table()
    swapped = replace(
        valid,
        rows=(("70%", "핵심 제품"), ("30%", "기타")),
    )
    with pytest.raises(PublicManifestError, match="재검산|머리글|원자료"):
        _run_full(composition_tables=(swapped,))


def test_구성표_중복열은_typed_binding이_막는다():
    valid = _valid_composition_table()
    duplicated = replace(valid, headers=("제품군", "제품군"))
    with pytest.raises(PublicManifestError):
        _run_full(composition_tables=(duplicated,))


def test_구성표_비중열의_비숫자_우회는_typed_binding이_막는다():
    valid = _valid_composition_table()
    nonnumeric = replace(
        valid,
        rows=(("핵심 제품", "칠십%"), ("기타", "삼십%")),
    )
    with pytest.raises(PublicManifestError, match="비율|숫자|재검산"):
        _run_full(composition_tables=(nonnumeric,))


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
