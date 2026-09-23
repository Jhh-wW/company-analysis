"""부분보고서 빈 검수의 호출만 줄이고 FULL 영수증·도식 검수는 유지한다."""

import json

import pytest

from src.features.composer import pipeline
from src.features.composer.constants import SECTION_IDS
from src.features.composer.evidence_availability import EvidenceAvailability
from src.features.composer.flow_review_binding import flow_review_problem
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, FlowRow,
)
from src.features.composer.tests.review_evidence_fixture import grounded_review_response
from src.features.composer.tests.test_pipeline import _strict_packet_set
from src.features.composer.validate import V2ValidationError
from src.features.composer.verify import verify_report
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_generation.models import ValidationRound


@pytest.mark.parametrize("kind,expected_writer_calls", [("분류 불명", 0), ("수익인식", 1)])
def test_부분보고서의_검수후보가_없으면_검수호출도_없다(kind, expected_writer_calls):
    writer_prompts, reviewer_prompts = [], []

    def writer(prompt):
        writer_prompts.append(prompt)
        return json.dumps({"문장들": [{
            "글": "자료에 없는 문장이다.", "인용": ["99"], "등급": "확인",
            "주장슬롯": "business_model:revenue_model",
        }], "경로표": []}, ensure_ascii=False)

    def reviewer(prompt):
        reviewer_prompts.append(prompt)
        return '{"판정":[]}'

    output = pipeline.run_v2(
        "가나다회사", {1: {"종류": kind, "원문": "용역은 진행기준에 따라 수익으로 인식한다."}},
        None, writer_ask=writer, reviewer_ask=reviewer,
        evidence_availability=EvidenceAvailability("partial"),
    )
    assert len(writer_prompts) == expected_writer_calls
    assert reviewer_prompts == []
    assert tuple(section.cell for section in output.report.sections) == SECTION_IDS
    assert output.report.fact_records == []
    assert output.report.summary_items == []


def test_FULL_후보가_모두_비어도_최초_영수증의_9대1_호출은_유지한다(monkeypatch):
    recorder = pipeline._CallLedgerRecorder()
    monkeypatch.setattr(pipeline, "_CallLedgerRecorder", lambda: recorder)
    with pytest.raises(V2ValidationError):
        pipeline.run_v2(
            "가나다전자", {}, None,
            writer_ask=lambda _prompt: '{"문장들":[],"경로표":[]}',
            reviewer_ask=lambda _prompt: '{"판정":[]}',
            release_mode=ReleaseMode.FULL,
            section_evidence_packets=_strict_packet_set(),
            company_id="00123456", build_identity_sha256="b" * 64,
        )
    assert recorder.calls_for(ValidationRound.PRIMARY, role="writer") == 9
    assert recorder.calls_for(ValidationRound.PRIMARY, role="reviewer") == 1


@pytest.mark.parametrize("skip_empty,expected_calls", [(False, 1), (True, 0)])
def test_빈_묶음_생략은_명시적으로_선택한_경우에만_적용한다(skip_empty, expected_calls):
    calls, protocol = [], []

    def ask(prompt):
        calls.append(prompt)
        return '{"판정":[]}'

    report = ComposedReport((ComposedSection("identity", ()),))
    result = verify_report(
        report, (), None, ask,
        allowed_fragment_ids_by_section={"identity": frozenset()},
        skip_empty_grouped_review=skip_empty,
        protocol_diagnostics=protocol,
    )
    assert len(calls) == expected_calls
    assert result == report
    assert len(protocol) == expected_calls


@pytest.mark.parametrize("candidate_kind", ["본문", "도식"])
def test_부분경로라도_본문이나_도식이_있으면_한번_검수한다(candidate_kind):
    section_id = "operations_partners"
    text = "회사는 수액세트를 의료기관에 공급한다."
    source = CollectedFragment("1", "사업내용", text,
                               document_identity="document:example:filing",
                               document_content_sha256="a" * 64)
    row = FlowRow(("수액세트", "의료기관에 공급", "의료기관"), ("1",))
    sentence = ComposedSentence(text, ("1",), "확인",
                                planned_claim_slot="operations_partners:supply_relation")
    report = ComposedReport((ComposedSection(
        section_id, (sentence,) if candidate_kind == "본문" else (),
        flow_rows=(row,) if candidate_kind == "도식" else (),
    ),))
    calls = []

    def reviewer(prompt):
        calls.append(prompt)
        return grounded_review_response(prompt)

    result = verify_report(
        report, (source,), None, reviewer,
        allowed_fragment_ids_by_section={section_id: frozenset({"1"})},
        skip_empty_grouped_review=True,
    )
    assert len(calls) == 1
    if candidate_kind == "본문":
        assert result.sections[0].sentences[0].verification_state == "verified"
    else:
        reviewed_row, = result.sections[0].flow_rows
        assert reviewed_row.review_binding is not None
        assert flow_review_problem(reviewed_row, section_id=section_id,
                                   fragments={"1": source}) == ""
