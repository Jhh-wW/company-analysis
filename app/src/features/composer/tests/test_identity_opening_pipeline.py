"""실제 run_v2에서 첫 소개의 독립 검수·최종 Fact 결속·출고 고지를 확인한다."""

import json
import hashlib
from dataclasses import replace

import pytest

from src.features.composer.pipeline import run_v2
from src.features.composer.tests import test_pipeline as fixtures
from src.features.composer.tests.injected_program_fixture import make_numeric_performance_evidence
from src.features.composer.tests.test_empty_section_recovery import _fragment
from src.features.composer.tests.review_evidence_fixture import grounded_review_response
from src.features.composer.validate import V2ValidationError
from src.features.pipeline.port import Grade
from src.shared.report_evidence.constants import ReleaseMode


OPENING = "가나다전자는 제조기업에 반도체 검사장비를 공급하는 장비 제조사다."
ORIGIN = "가나다전자는 검사장비 자회사와 합병을 완료했다."
OPERATION = "가나다전자는 장비 납품 대금을 받은 뒤 부품 공급사에 지급하는 절차를 운영한다."
IDENTITY_SLOT = "identity:business_definition"


class RecoveryCalls:
    def __init__(self, *, text=OPENING, rejected=False, citation="3"):
        self.text = text
        self.rejected = rejected
        self.citation = citation
        self.writes = []
        self.reviews = []

    def writer(self, prompt):
        self.writes.append(prompt)
        return json.dumps({"장들": {"identity": {"문장들": [{
            "글": self.text, "인용": [self.citation], "등급": "확인", "주장슬롯": IDENTITY_SLOT,
        }]}}}, ensure_ascii=False)

    def reviewer(self, prompt):
        self.reviews.append(prompt)
        return grounded_review_response(prompt, false_texts=(self.text,) if self.rejected else ())


def _inputs(monkeypatch, *, already_present=False):
    monkeypatch.setitem(fixtures._SECTION_CLAIMS, "identity", (
        ("identity:corporate_identity", OPENING if already_present else ORIGIN),
        (IDENTITY_SLOT, OPERATION),
    ))
    raw = fixtures._raw_fragments()
    from src.features.composer.pipeline import _normalize_fragments
    fragments = tuple(_normalize_fragments(raw))
    source = replace(_fragment("identity", OPENING, "3"), supported_claim_slots=(IDENTITY_SLOT,))
    return (*fragments, source)


def _run(monkeypatch, calls, *, already_present=False, writer=None, **options):
    fragments = _inputs(monkeypatch, already_present=already_present)
    return run_v2(
        "가나다전자", fragments, None,
        writer_ask=writer or fixtures._FakeWriter(), reviewer_ask=fixtures._FakeReviewer(),
        empty_recovery_writer_ask=calls.writer,
        empty_recovery_reviewer_ask=calls.reviewer,
        empty_recovery_can_start=lambda: True,
        as_of_date="2026-10-23", **options,
    )


def test_한번_작성검수한_소개가_실제_최종첫문단과_Fact로_나가고_기존본문은_남는다(monkeypatch):
    calls = RecoveryCalls()
    output = _run(monkeypatch, calls)
    identity = next(section for section in output.report.sections if section.cell == "identity")
    assert identity.prose_paragraphs[0].startswith(OPENING)
    assert OPENING in identity.prose_lines[0][0]
    assert ORIGIN in " ".join(text for text, _cite in identity.prose_lines)
    assert OPERATION in " ".join(text for text, _cite in identity.prose_lines)
    fact = next(fact for fact in output.report.fact_records if fact.claim == OPENING)
    assert fact.verification_status == "verified"
    assert fact.evidence_binding
    assert fact.fact_id in identity.fact_ids
    assert len(calls.writes) == len(calls.reviews) == 1


def test_이미_현재사업_첫소개가_있으면_추가호출이_없다(monkeypatch):
    calls = RecoveryCalls()
    output = _run(monkeypatch, calls, already_present=True)
    identity = next(section for section in output.report.sections if section.cell == "identity")
    assert identity.prose_lines[0][0].startswith(OPENING)
    assert calls.writes == calls.reviews == []


def test_새소개_재결속으로_기존_정체성_미결속탈락_진단을_지우지않는다(monkeypatch):
    class SlotlessIdentityWriter(fixtures._FakeWriter):
        def __call__(self, prompt):
            payload = json.loads(super().__call__(prompt))
            if self.section_calls == 1:
                payload["문장들"][0].pop("주장슬롯")
            return json.dumps(payload, ensure_ascii=False)

    calls = RecoveryCalls()
    output = _run(monkeypatch, calls, writer=SlotlessIdentityWriter())
    identity = next(section for section in output.report.sections if section.cell == "identity")
    assert identity.prose_lines[0][0].startswith(OPENING)
    assert ORIGIN not in " ".join(text for text, _cite in identity.prose_lines)
    assert OPERATION in " ".join(text for text, _cite in identity.prose_lines)
    excluded = [entry for entry in output.review_diagnostics
                if entry.get("section_id") == "identity"
                and entry.get("reason_code") == "public_sentence_fact_unbound"]
    assert len(excluded) == 1
    assert excluded[0]["candidate_sha256"] == hashlib.sha256(ORIGIN.encode("utf-8")).hexdigest()
    assert len(calls.writes) == len(calls.reviews) == 1


@pytest.mark.parametrize("rejected", (False, True))
def test_부적격작성이나_검수탈락은_부분출고에_고지하고_미승인소개를_싣지않는다(monkeypatch, rejected):
    calls = RecoveryCalls(text=OPENING if rejected else OPERATION, rejected=rejected)
    output = _run(monkeypatch, calls)
    report = output.report
    identity = next(section for section in report.sections if section.cell == "identity")
    assert report.grade is Grade.PARTIAL
    assert any("한 줄 소개" in reason for reason in report.shortfall_reasons)
    assert "한 줄 소개" in " ".join(identity.guidance_lines)
    assert all(fact.claim != OPENING for fact in report.fact_records)
    assert len(calls.writes) == 1
    assert len(calls.reviews) == int(rejected)


def test_엄격모드는_첫소개없이_추가승인이나_부분출고로_넘어가지않는다(monkeypatch):
    calls = RecoveryCalls()
    with pytest.raises(V2ValidationError):
        _run(monkeypatch, calls, release_mode=ReleaseMode.ENFORCE_NO_PARTIAL)
    assert calls.writes == calls.reviews == []


@pytest.mark.parametrize("fallback_allowed", (False, True))
def test_FULL_첫소개누락은_원래영수증에_호출을숨기지않고_중단또는_확보근거로_내린다(monkeypatch, fallback_allowed):
    opening_source = replace(_inputs(monkeypatch)[-1], fragment_id="91")
    calls = RecoveryCalls(citation="91")
    texts = tuple(text for claims in fixtures._SECTION_CLAIMS.values() for _slot, text in claims)
    performance, _fragment_value, filing_meta = make_numeric_performance_evidence(fragment_number=9)
    writer, reviewer = fixtures._FakeWriter(), fixtures._FakeReviewer()
    original_packets = fixtures._strict_packet_set(evidence_texts=(*texts, OPENING))
    packets = replace(original_packets, packets=tuple(
        replace(packet, fragments=(*packet.fragments, opening_source))
        if packet.section_id == "identity" else packet
        for packet in original_packets.packets
    ))

    def run():
        return run_v2(
            "가나다전자", fixtures._strict_fragments(), performance,
            writer_ask=writer, reviewer_ask=reviewer,
            release_mode=ReleaseMode.FULL,
            section_evidence_packets=packets,
            filing_meta=filing_meta, company_id="00123456", build_identity_sha256="b" * 64,
            empty_recovery_writer_ask=calls.writer,
            empty_recovery_reviewer_ask=calls.reviewer,
            empty_recovery_can_start=lambda: True,
            evidence_available_fallback=fallback_allowed,
            as_of_date="2026-10-23",
        )

    if not fallback_allowed:
        with pytest.raises(V2ValidationError):
            run()
        assert calls.writes == calls.reviews == []
    else:
        output = run()
        assert output.report.grade is Grade.PARTIAL
        assert output.effective_release_mode == ReleaseMode.SHADOW.value
        assert output.downgraded_from_release_mode == ReleaseMode.FULL.value
        assert output.report.publication_policy == "evidence-available-v1"
        identity = next(section for section in output.report.sections if section.cell == "identity")
        assert identity.prose_lines[0][0].startswith(OPENING)
        assert ORIGIN in " ".join(text for text, _cite in identity.prose_lines)
        assert OPERATION in " ".join(text for text, _cite in identity.prose_lines)
        assert len(calls.writes) == len(calls.reviews) == 1
        assert len(writer.prompts) == 9
        assert output.generation_evidence is None
