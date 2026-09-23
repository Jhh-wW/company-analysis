"""빈 장의 제한된 복구도 실제 문장 검수를 통과해야 한다."""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import replace

import pytest

from src.features.composer.constants import (
    GRADE_CONFIRMED, GRADE_INTERPRETED, SECTION_GUIDES, SECTION_IDS as _ALL_SECTION_IDS,
)
from src.features.composer.empty_section_recovery import (
    recover_empty_sections, recovery_evidence, rejected_sentence_fingerprint,
    requested_sections_from_response,
)
from src.features.composer.port import (
    AskFatalError, CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
    SectionEvidencePacket, SectionEvidencePacketSet,
)
from src.features.composer.tests.test_pipeline import _FakeReviewer
from src.features.composer.tests.test_verify import _FakeVerifier, _all_true, _verdict_json
from src.features.composer.verify import verify_report
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_quality.source_identity import document_identity_from_parts


PAST_TEXT = "가나다전자는 물류 사업을 분할하고 투자 사업을 영위하고 있다."
CULTURE_TEXT = "가나다전자는 구성원 역량 개발을 위해 직무 교육을 운영한다."
COMPETITIVE_TEXT = "가나다전자는 모듈형 검사 설계를 제품의 핵심 강점으로 설명한다."


def _fragment(section_id="past_changes", text=PAST_TEXT, fragment_id="1"):
    url = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=2026031600000" + fragment_id
    return CollectedFragment(
        fragment_id=fragment_id, kind="사업내용", text=text,
        source_url=url, document_identity=document_identity_from_parts(
            host="dart.fss.or.kr", document_id="2026031600000" + fragment_id, url=url),
        document_title="사업보고서", document_date="2026-03-16",
        source_collected_on="2026-09-14", source_publisher="가나다전자",
        source_document_id="2026031600000" + fragment_id,
        document_content_sha256=hashlib.sha256(text.encode()).hexdigest(),
        formal_source_kind="dart_business_report", identity_binding="dart_corp_code",
        supported_claim_slots=(CLAIM_SLOTS_BY_SECTION[section_id][0],),
    )


def _report(*section_ids):
    return ComposedReport(tuple(ComposedSection(section_id, (), notice="기존 한계 안내")
                                for section_id in section_ids))


def _response(sections, *, claim_slot=None):
    return json.dumps({"장들": {
        section_id: {"문장들": [
            {"글": text, "인용": [fragment_id], "등급": grade,
             **({"주장슬롯": claim_slot} if claim_slot else {})}
            for text, fragment_id, grade in sentences]}
        for section_id, sentences in sections.items()
    }}, ensure_ascii=False)


def test_selects_only_bound_official_section_evidence():
    valid = _fragment()
    culture = _fragment("culture", CULTURE_TEXT, "2")
    business = _fragment("culture", "당사는 수익성 극대화를 목표로 투자 사업을 확대한다.", "3")
    fragments = (valid, culture, business, replace(valid, fragment_id="4", identity_binding=""),
                 replace(valid, fragment_id="5", formal_source_kind="news"),
                 replace(valid, fragment_id="6", supported_claim_slots=()))
    assert recovery_evidence(fragments) == {"past_changes": (valid,), "culture": (culture,)}


def test_recovers_two_sections_with_one_write_and_review_preserving_existing_body():
    fragments = (_fragment(), _fragment("culture", CULTURE_TEXT, "2"))
    existing = ComposedSection("identity", (ComposedSentence(
        "가나다전자는 지주회사다.", ("3",), GRADE_CONFIRMED, verification_state="verified"),))
    report = replace(_report("identity", "past_changes", "culture"),
                     sections=(existing, *_report("past_changes", "culture").sections))
    writes = []
    reviewer = _FakeReviewer()
    def writer(prompt):
        writes.append(prompt)
        return _response({"past_changes": [(PAST_TEXT, "1", GRADE_CONFIRMED)],
                          "culture": [(CULTURE_TEXT, "2", GRADE_CONFIRMED)]})
    result = recover_empty_sections("가나다전자", report, targets=("past_changes", "culture"),
                                    evidence=recovery_evidence(fragments), writer=writer, reviewer=reviewer)
    assert len(writes) == len(reviewer.prompts) == 1
    assert result.sections[0] is existing
    assert [section.sentences[0].text for section in result.sections[1:]] == [PAST_TEXT, CULTURE_TEXT]
    assert all(section.sentences[0].verification_state == "verified" for section in result.sections[1:])


@pytest.mark.parametrize("text,grade", [
    ("가나다전자의 매출은 999억원이다.", GRADE_CONFIRMED),
    ("가나다전자는 투자를 중시하는 것으로 보인다.", GRADE_INTERPRETED),
])
def test_unfounded_numbers_and_interpretations_do_not_fill_section(text, grade):
    report = _report("past_changes")
    reviewer = _FakeReviewer()
    result = recover_empty_sections("가나다전자", report, targets=("past_changes",),
        evidence=recovery_evidence((_fragment(),)), reviewer=reviewer,
        writer=lambda _: _response({"past_changes": [(text, "1", grade)]}))
    assert result == report
    assert len(reviewer.prompts) == (1 if grade == GRADE_CONFIRMED else 0)


def test_recovered_culture_sentence_must_pass_source_clause_guard():
    text = CULTURE_TEXT + " 당사는 수익성 극대화를 목표로 투자 사업을 확대한다."
    report = _report("culture")
    result = recover_empty_sections("가나다전자", report, targets=("culture",),
        evidence=recovery_evidence((_fragment("culture", text),)), reviewer=_FakeReviewer(),
        writer=lambda _: _response({"culture": [("가나다전자는 수익성 극대화를 추구하는 조직문화를 갖고 있다.", "1", GRADE_CONFIRMED)]}))
    assert result == report


def test_review_parse_failure_never_retries_provider():
    calls = []
    def reviewer(prompt):
        calls.append(prompt)
        return "깨진 응답"
    with pytest.raises(AskFatalError):
        recover_empty_sections("가나다전자", _report("past_changes"), targets=("past_changes",),
            evidence=recovery_evidence((_fragment(),)), reviewer=reviewer,
            writer=lambda _: _response({"past_changes": [(PAST_TEXT, "1", GRADE_CONFIRMED)]}))
    assert len(calls) == 1


def test_does_not_copy_existing_fact_into_empty_section():
    existing = ComposedSection("identity", (ComposedSentence(PAST_TEXT, ("1",), GRADE_CONFIRMED),))
    report = ComposedReport((existing, *_report("past_changes").sections))
    result = recover_empty_sections("가나다전자", report, targets=("past_changes",),
        evidence=recovery_evidence((_fragment(),)), reviewer=_FakeReviewer(),
        writer=lambda _: _response({"past_changes": [(PAST_TEXT, "1", GRADE_CONFIRMED)]}))
    assert result == report
    assert result.sections[0] is existing


def test_empty_section_gate_replaces_sentence_rewrite_calls():
    report = ComposedReport((ComposedSection("past_changes", (
        ComposedSentence(PAST_TEXT, ("1",), GRADE_CONFIRMED),)),))
    ask = _FakeVerifier([_verdict_json({1: "거짓"})])
    empty = []
    result = verify_report(report, (_fragment(),), None, ask,
        sentence_rewrite_gate=lambda ids: empty.append(ids) or False)
    assert empty == [("past_changes", "summary")]
    assert not result.sections[0].sentences
    assert len(ask.review_prompts) == 1 and not ask.rewrite_prompts


def test_gate_observes_section_emptied_by_machine_check():
    report = ComposedReport((ComposedSection("past_changes", (
        ComposedSentence(PAST_TEXT, ("99",), GRADE_CONFIRMED),)),))
    empty = []
    ask = _FakeReviewer()
    result = verify_report(report, (_fragment(),), None, ask,
        sentence_rewrite_gate=lambda ids: empty.append(ids) or False)
    assert "past_changes" in empty[0]
    assert not result.sections[0].sentences and not ask.prompts


def test_keeps_optional_sentence_rewrite_when_section_is_not_empty():
    report = ComposedReport((ComposedSection("past_changes", (
        ComposedSentence(PAST_TEXT, ("1",), GRADE_CONFIRMED),
        ComposedSentence("가나다전자는 물류 사업을 영위한다.", ("1",), GRADE_CONFIRMED))),))
    ask = _FakeVerifier([_verdict_json({1: "참", 2: "거짓"}), _all_true(1)],
                        rewrite_response=json.dumps({"재작성": [{"번호": 2, "글": PAST_TEXT}]}))
    empty = []
    verify_report(report, (_fragment(),), None, ask,
                  sentence_rewrite_gate=lambda ids: empty.append(ids) or True)
    assert "past_changes" not in empty[0]
    assert len(ask.rewrite_prompts) == 1


@pytest.mark.parametrize("available", [True, False])
@pytest.mark.parametrize("repeat_rejected", [True, False])
def test_pipeline_recovers_only_new_facts_with_available_calls(monkeypatch, available, repeat_rejected):
    from src.features.composer import pipeline
    from src.features.composer.constants import SECTION_IDS
    from src.features.composer.evidence_availability import EvidenceAvailability
    from src.features.composer.tests.test_pipeline import _FakeWriter

    original = PAST_TEXT if repeat_rejected else "가나다전자는 물류 사업을 합병하고 제조 사업을 영위하고 있다."
    draft = ComposedReport(tuple(ComposedSection(section_id, (
        ComposedSentence(original, ("1",), GRADE_CONFIRMED,
                         planned_claim_slot=CLAIM_SLOTS_BY_SECTION[section_id][0]),
    ) if section_id == "past_changes" else ()) for section_id in SECTION_IDS))
    monkeypatch.setattr(pipeline, "compose_sections", lambda *args, **kwargs: draft)
    initial = _FakeVerifier([json.dumps({"판정": [{
        "번호": 1, "장": "past_changes", "근거": ["1"], "결과": "거짓",
        "근거대조": "1: 후보를 거절함",
    }]}, ensure_ascii=False)])
    writer_calls = []
    recovery_reviewer = _FakeReviewer()
    def recovery_writer(prompt):
        writer_calls.append(prompt)
        return _response({"past_changes": [(PAST_TEXT, "1", GRADE_CONFIRMED)]},
                         claim_slot=CLAIM_SLOTS_BY_SECTION["past_changes"][0])
    diagnostics = []
    output = pipeline.run_v2(
        "가나다전자", (_fragment(),), None, writer_ask=_FakeWriter(), reviewer_ask=initial,
        empty_recovery_writer_ask=recovery_writer,
        empty_recovery_reviewer_ask=recovery_reviewer,
        empty_recovery_can_start=lambda: available,
        evidence_availability=EvidenceAvailability("partial"),
        composition_diagnostics_sink=diagnostics,
    )
    assert len(writer_calls) == int(available)
    assert len(recovery_reviewer.prompts) == int(available and not repeat_rejected)
    past = next(section for section in output.report.sections if section.cell == "past_changes")
    assert any(PAST_TEXT in str(line) for line in past.prose_lines) is (available and not repeat_rejected)
    # 부분 보고서도 장별 소유 근거의 묶음 검수를 사용한다. 이 경로는 개별
    # 거짓 재작성 대신 아래의 제한된 빈 장 복구만 호출한다.
    assert len(initial.rewrite_prompts) == 0
    if available and not repeat_rejected:
        assert any(item.get("복구장") == ["past_changes"] for item in diagnostics)


def test_recovery_limits_two_sections_and_three_sentences():
    fragments = (_fragment("identity"), _fragment(), _fragment("culture", CULTURE_TEXT, "2"))
    calls = []
    def writer(prompt):
        calls.append(prompt)
        return _response({"identity": [(PAST_TEXT, "1", GRADE_CONFIRMED)] * 4,
                          "past_changes": []})
    result = recover_empty_sections("가나다전자", _report("identity", "past_changes", "culture"),
        targets=("identity", "past_changes", "culture"), evidence=recovery_evidence(fragments),
        writer=writer, reviewer=_FakeReviewer())
    assert len(calls) == 1
    assert len(result.sections[0].sentences) == 3
    assert not result.sections[2].sentences


@pytest.mark.parametrize("changed", [PAST_TEXT, PAST_TEXT.replace(" ", "  "), PAST_TEXT + " [조각 1]"])
def test_rejected_sentence_cannot_be_reapproved_with_cosmetic_changes(changed):
    report = _report("past_changes")
    reviewer = _FakeReviewer()
    result = recover_empty_sections("가나다전자", report, targets=("past_changes",),
        evidence=recovery_evidence((_fragment(),)), reviewer=reviewer,
        rejected_fingerprints={"past_changes": frozenset((rejected_sentence_fingerprint(PAST_TEXT),))},
        writer=lambda _: _response({"past_changes": [(changed, "1", GRADE_CONFIRMED)]}))
    assert result == report
    assert not reviewer.prompts


# ══════════════════════════════════════════════════════════
# 작성 형식 어긋남 — 통째 포기 대신 «요청한 장만» 골라 쓴다
#
# 실측(2026-09-14): 답에 요청 밖 장이 하나 섞였다는 이유로 정상적으로 쓰인 장까지
# 함께 버려져 8장이 빈 채로 나갔다. 아래 세 시험이 「골라 쓰기 · 1회 재요청 ·
# 그래도 못 읽으면 포기」를 나눠 지킨다.
# ══════════════════════════════════════════════════════════


def test_extra_sections_in_response_are_dropped_not_fatal():
    """요청 밖 장이 섞여도 요청한 장은 그대로 쓰고 개수만 진단에 남긴다."""
    fragments = (_fragment(), _fragment("culture", CULTURE_TEXT, "2"))
    diagnostics = []
    raw = json.loads(_response({"past_changes": [(PAST_TEXT, "1", GRADE_CONFIRMED)],
                                "culture": [(CULTURE_TEXT, "2", GRADE_CONFIRMED)],
                                "identity": [(PAST_TEXT, "1", GRADE_CONFIRMED)]}))
    calls = []
    def writer(prompt):
        calls.append(prompt)
        return json.dumps(raw, ensure_ascii=False)
    result = recover_empty_sections("가나다전자", _report("past_changes", "culture"),
        targets=("past_changes", "culture"), evidence=recovery_evidence(fragments),
        writer=writer, reviewer=_FakeReviewer(), protocol_diagnostics=diagnostics)
    assert len(calls) == 1, "요청한 장을 읽었으면 재요청하지 않는다"
    assert [section.sentences[0].text for section in result.sections] == [PAST_TEXT, CULTURE_TEXT]
    written = next(item for item in diagnostics if item.get("상태") == "작성완료")
    assert written["요청밖장수"] == 1


def test_missing_section_in_response_recovers_the_other_one():
    """요청 두 장 중 한 장만 답해도 그 한 장은 살린다(예전에는 둘 다 버렸다)."""
    fragments = (_fragment(), _fragment("culture", CULTURE_TEXT, "2"))
    diagnostics = []
    calls = []
    def writer(prompt):
        calls.append(prompt)
        return _response({"culture": [(CULTURE_TEXT, "2", GRADE_CONFIRMED)]})
    result = recover_empty_sections("가나다전자", _report("past_changes", "culture"),
        targets=("past_changes", "culture"), evidence=recovery_evidence(fragments),
        writer=writer, reviewer=_FakeReviewer(), protocol_diagnostics=diagnostics)
    assert len(calls) == 1
    assert not result.sections[0].sentences
    assert result.sections[1].sentences[0].text == CULTURE_TEXT
    assert next(item for item in diagnostics if item.get("상태") == "검수완료")["복구장"] == ["culture"]


def test_unreadable_response_retries_once_then_reports_format_failure():
    """JSON 자체가 깨졌을 때만 재요청하고, 재요청 프롬프트에 형식 요구를 덧붙인다."""
    from src.features.composer.empty_section_recovery_constants import EMPTY_RECOVERY_RETRY_GUIDE

    diagnostics = []
    calls = []
    def writer(prompt):
        calls.append(prompt)
        return "형식을 따르지 못했습니다"
    reviewer = _FakeReviewer()
    result = recover_empty_sections("가나다전자", _report("past_changes"), targets=("past_changes",),
        evidence=recovery_evidence((_fragment(),)), writer=writer, reviewer=reviewer,
        protocol_diagnostics=diagnostics)
    assert result == _report("past_changes")
    assert len(calls) == 2, "파싱 재요청 상한은 1회다"
    assert EMPTY_RECOVERY_RETRY_GUIDE not in calls[0] and EMPTY_RECOVERY_RETRY_GUIDE in calls[1]
    assert not reviewer.prompts
    assert diagnostics == [{"step": "8_빈장_복구", "상태": "작성형식실패",
                            "대상장": ["past_changes"], "시도": 2, "응답꼴": ["읽기실패", "읽기실패"]}]


def test_retry_response_is_accepted_when_the_first_one_was_unreadable():
    """재요청이 읽히면 그대로 복구한다 — 재요청이 형식뿐인 것을 확인한다."""
    answers = ["설명만 적었습니다", _response({"past_changes": [(PAST_TEXT, "1", GRADE_CONFIRMED)]})]
    diagnostics = []
    result = recover_empty_sections("가나다전자", _report("past_changes"), targets=("past_changes",),
        evidence=recovery_evidence((_fragment(),)), writer=lambda _: answers.pop(0),
        reviewer=_FakeReviewer(), protocol_diagnostics=diagnostics)
    assert not answers
    assert result.sections[0].sentences[0].text == PAST_TEXT
    assert next(item for item in diagnostics if item.get("상태") == "작성완료")["요청밖장수"] == 0


# ══════════════════════════════════════════════════════════
# 운영(FULL) 모드 — 복구는 모드와 무관하게 돈다
#
# 실측(2026-09-16): 운영 실행 진단의 단계 53개에 `8_빈장_복구`가 «아예 없었고»
# 6·8장이 빈 채로 나갔다. 원인 두 가지 — 복구 스위치가 SHADOW에서만 켜졌고,
# 대상 선정이 flat 검수 경로에만 달린 재작성 게이트 안에 있었다(FULL은 packet
# 경로라 그 게이트가 한 번도 불리지 않는다).
# ══════════════════════════════════════════════════════════

_ALL_CLAIM_SLOTS = tuple(
    slot_id for section_id in _ALL_SECTION_IDS
    for slot_id in CLAIM_SLOTS_BY_SECTION[section_id]
)
#: 8장 복구 후보로 쓸 «공식 결속» 조각. 얇은 FULL 후보(공식 홈페이지)는
#: formal_source_kind가 없어 복구 근거가 되지 못하므로 한 개만 따로 넣는다.
_RECOVERY_FRAGMENT_ID = "9"


def _culture_recovery_fragment():
    return replace(_fragment("culture", CULTURE_TEXT, _RECOVERY_FRAGMENT_ID),
                   supported_claim_slots=_ALL_CLAIM_SLOTS,
                   counts_toward_document_floor=True)


def _full_packets(*, culture_owns_recovery_fragment: bool):
    """얇은 FULL packet에 복구용 공식 조각을 더한다.

    ``culture_owns_recovery_fragment`` 가 거짓이면 그 조각을 8장 packet에서만
    뺀다 — union에는 남으므로 «장이 들고 있지 않은 근거»를 재현한다.
    """
    from src.features.composer.tests.test_pipeline import _strict_packet_set

    base = _strict_packet_set(evidence_texts=(COMPETITIVE_TEXT,))
    extra = _culture_recovery_fragment()
    return SectionEvidencePacketSet(
        company_id=base.company_id,
        evidence_generation_sha256=base.evidence_generation_sha256,
        packets=tuple(
            SectionEvidencePacket(
                company_id=packet.company_id,
                evidence_generation_sha256=packet.evidence_generation_sha256,
                section_id=packet.section_id,
                fragments=(
                    packet.fragments
                    if packet.section_id == "culture" and not culture_owns_recovery_fragment
                    else packet.fragments + (extra,)
                ),
            )
            for packet in base.packets
        ),
    )


def _full_writer_leaving_culture_empty():
    """8장만 빈 배열로 내는 얇은 FULL 작가.

    문장 목록은 기존 얇은 작가를 그대로 물려받는다 — 복제하면 한쪽만 고쳐져
    다른 쪽이 «비지 않는» 입력으로 조용히 바뀐다.
    """
    from src.features.composer.tests.test_evidence_available_report import _StrictThinWriter

    class _Writer(_StrictThinWriter):
        def __call__(self, prompt: str) -> str:
            if self.section_calls < len(_ALL_SECTION_IDS) and (
                _ALL_SECTION_IDS[self.section_calls] == "competitive_position"
            ):
                self.prompts.append(prompt)
                self.section_calls += 1
                return json.dumps({"문장들": [{
                    "글": COMPETITIVE_TEXT, "인용": ["2"], "등급": GRADE_CONFIRMED,
                    "주장슬롯": "competitive_position:stated_differentiator",
                }]}, ensure_ascii=False)
            if self.section_calls < len(_ALL_SECTION_IDS) and (
                _ALL_SECTION_IDS[self.section_calls] == "culture"
            ):
                self.prompts.append(prompt)
                self.section_calls += 1
                return json.dumps({"문장들": []}, ensure_ascii=False)
            return super().__call__(prompt)

    return _Writer()


def _run_full(packets, *, can_start=True, recovery_writer, diagnostics):
    from src.features.composer import pipeline

    return pipeline.run_v2(
        "가나다전자", {}, None,
        writer_ask=_full_writer_leaving_culture_empty(), reviewer_ask=_FakeReviewer(),
        empty_recovery_writer_ask=recovery_writer,
        empty_recovery_reviewer_ask=_FakeReviewer(),
        empty_recovery_can_start=lambda: can_start,
        release_mode=ReleaseMode.FULL,
        section_evidence_packets=packets,
        company_id="00123456",
        build_identity_sha256="b" * 64,
        evidence_available_fallback=True,
        composition_diagnostics_sink=diagnostics,
    )


def _recovery_steps(diagnostics):
    return [item for item in diagnostics if item.get("step") == "8_빈장_복구"]


def test_full_release_mode_recovers_the_empty_section():
    """운영 모드에서도 복구 작가가 «실제로» 불리고 8장이 채워진다."""
    recovery_calls = []
    def recovery_writer(prompt):
        recovery_calls.append(prompt)
        return _response({"culture": [(CULTURE_TEXT, _RECOVERY_FRAGMENT_ID, GRADE_CONFIRMED)]},
                         claim_slot=CLAIM_SLOTS_BY_SECTION["culture"][0])
    diagnostics = []
    output = _run_full(_full_packets(culture_owns_recovery_fragment=True),
                       recovery_writer=recovery_writer, diagnostics=diagnostics)
    assert len(recovery_calls) == 1, "FULL에서 복구 작가가 한 번도 불리지 않았습니다"
    recovered = next(item for item in _recovery_steps(diagnostics)
                     if item.get("상태") == "검수완료")
    assert recovered["복구장"] == ["culture"]
    culture_section = next(section for section in output.report.sections
                           if section.cell == "culture")
    assert any(CULTURE_TEXT in str(line) for line in culture_section.prose_lines)


def test_full_release_mode_records_no_budget_without_calling_the_writer():
    """예산이 없으면 조용히 넘어가지 않고 «예산부족»을 남긴다."""
    recovery_calls = []
    diagnostics = []
    _run_full(_full_packets(culture_owns_recovery_fragment=True), can_start=False,
              diagnostics=diagnostics,
              recovery_writer=lambda prompt: recovery_calls.append(prompt) or "")
    assert not recovery_calls
    assert _recovery_steps(diagnostics) == [
        {"step": "8_빈장_복구", "상태": "예산부족", "대상장": ["culture"]}]


def test_packet_path_ignores_evidence_the_section_does_not_own():
    """장이 들고 있지 않은 조각은 복구 근거가 아니다 — 근거후보없음으로 남는다.

    ★ 이 교집합을 빼면 복구 문장이 장별 근거 소유권 불변식을 깨뜨려 검증을 마친
      보고서 «전체»가 ValueError로 죽는다. 그래서 「기록이 남는가」와 「실행이
      살아 있는가」를 함께 본다.
    """
    recovery_calls = []
    diagnostics = []
    _run_full(_full_packets(culture_owns_recovery_fragment=False), diagnostics=diagnostics,
              recovery_writer=lambda prompt: recovery_calls.append(prompt) or "")
    assert not recovery_calls
    assert _recovery_steps(diagnostics) == [
        {"step": "8_빈장_복구", "상태": "근거후보없음", "대상장": ["culture"]}]


def test_full_release_mode_drops_recovered_sentence_without_a_claim_slot():
    """의미 칸 없는 복구 문장은 싣지 않는다 — 실으면 보고서 전체가 막힌다.

    ★ 음성 대조 — `require_claim_slot` 을 빼면 이 실행이
      `report_recovery:post_validation_safety_blocked` 로 «예외»가 된다
      (culture장에 fact_id와 결속되지 않은 공개 내용). 즉 빈 장 하나를 채우려다
      보고서를 통째로 막는다. 그래서 「빈 채로 끝나되 살아서 끝난다」를 본다.
    """
    recovery_calls = []
    def recovery_writer(prompt):
        recovery_calls.append(prompt)
        # 주장슬롯을 적지 않은 답. 형식은 멀쩡하므로 재요청 대상도 아니다.
        return _response({"culture": [(CULTURE_TEXT, _RECOVERY_FRAGMENT_ID, GRADE_CONFIRMED)]})
    diagnostics = []
    output = _run_full(_full_packets(culture_owns_recovery_fragment=True),
                       recovery_writer=recovery_writer, diagnostics=diagnostics)
    assert len(recovery_calls) == 1
    # packet 경로에는 재작성 게이트가 없어 «문장재작성대신예약» 기록도 없다.
    assert [item["상태"] for item in _recovery_steps(diagnostics)] == ["확인후보없음"]
    culture_section = next(section for section in output.report.sections
                           if section.cell == "culture")
    assert not any(CULTURE_TEXT in str(line) for line in culture_section.prose_lines)


def test_section_the_writer_never_attempted_is_still_a_recovery_target():
    """작가가 한 글자도 못 쓴 장이 오히려 복구가 가장 필요한 장이다.

    ★ 예전에는 자격이 「작가가 한 문장이라도 쓴 장」이어서, 초안이 통째로 빈 장은
      후보에서 아예 빠졌다. 그 조건을 빼면 이 시험이 초록이 되고, 되살리면
      복구 작가가 한 번도 불리지 않아 빨개진다.
    """
    from src.features.composer import pipeline
    from src.features.composer.evidence_availability import EvidenceAvailability
    from src.features.composer.tests.test_pipeline import _FakeWriter

    empty_draft = ComposedReport(tuple(ComposedSection(section_id, ())
                                       for section_id in _ALL_SECTION_IDS))
    original = pipeline.compose_sections
    pipeline.compose_sections = lambda *args, **kwargs: empty_draft
    try:
        writer_calls = []
        diagnostics = []
        output = pipeline.run_v2(
            "가나다전자", (_fragment(),), None,
            writer_ask=_FakeWriter(), reviewer_ask=_FakeReviewer(),
            empty_recovery_writer_ask=lambda prompt: writer_calls.append(prompt) or _response(
                {"past_changes": [(PAST_TEXT, "1", GRADE_CONFIRMED)]},
                claim_slot=CLAIM_SLOTS_BY_SECTION["past_changes"][0]),
            empty_recovery_reviewer_ask=_FakeReviewer(),
            empty_recovery_can_start=lambda: True,
            evidence_availability=EvidenceAvailability("partial"),
            composition_diagnostics_sink=diagnostics,
        )
    finally:
        pipeline.compose_sections = original
    assert len(writer_calls) == 1, "초안이 빈 장은 복구 후보에서 빠졌습니다"
    assert next(item for item in _recovery_steps(diagnostics)
                if item.get("상태") == "검수완료")["복구장"] == ["past_changes"]
    past = next(section for section in output.report.sections if section.cell == "past_changes")
    assert any(PAST_TEXT in str(line) for line in past.prose_lines)


def test_unwrapped_response_without_sections_key_is_accepted():
    """「장들」 포장을 뺀 ``{"<장 ID>": {"문장들": [...]}}`` 도 같은 내용이므로 받는다."""
    calls = []
    diagnostics = []
    payload = json.loads(_response({"past_changes": [(PAST_TEXT, "1", GRADE_CONFIRMED)]}))["장들"]
    def writer(prompt):
        calls.append(prompt)
        return json.dumps(payload, ensure_ascii=False)
    result = recover_empty_sections("가나다전자", _report("past_changes"), targets=("past_changes",),
        evidence=recovery_evidence((_fragment(),)), writer=writer, reviewer=_FakeReviewer(),
        protocol_diagnostics=diagnostics)
    assert len(calls) == 1, "포장만 빠진 답은 재요청 없이 받는다"
    assert result.sections[0].sentences[0].text == PAST_TEXT
    written = next(item for item in diagnostics if item.get("상태") == "작성완료")
    assert written["응답꼴"] == "포장없음"
    done = next(item for item in diagnostics if item.get("상태") == "검수완료")
    assert (done["검수통과"], done["안전검사후"], done["최종반영"]) == (1, 1, 1)


def test_flat_sentences_response_is_accepted_only_for_a_single_target():
    """요청 장이 하나면 ``{"문장들": [...]}`` 평면 꼴을 그 장으로 받고, 둘이면 받지 않는다."""
    flat = json.dumps({"문장들": [{"글": PAST_TEXT, "인용": ["1"], "등급": GRADE_CONFIRMED}]}, ensure_ascii=False)
    single = recover_empty_sections("가나다전자", _report("past_changes"), targets=("past_changes",),
        evidence=recovery_evidence((_fragment(),)), writer=lambda _: flat, reviewer=_FakeReviewer())
    assert single.sections[0].sentences[0].text == PAST_TEXT
    diagnostics = []
    fragments = (_fragment(), _fragment("culture", CULTURE_TEXT, "2"))
    double = recover_empty_sections("가나다전자", _report("past_changes", "culture"),
        targets=("past_changes", "culture"), evidence=recovery_evidence(fragments),
        writer=lambda _: flat, reviewer=_FakeReviewer(), protocol_diagnostics=diagnostics)
    assert double == _report("past_changes", "culture")
    assert diagnostics[-1]["상태"] == "작성형식실패"
    assert diagnostics[-1]["응답꼴"] == ["요청장없음", "요청장없음"]


def test_requested_sections_from_response_shapes():
    """응답 꼴 판별 함수의 계약 — 계약·포장없음·단일장평면·요청장없음·읽기실패."""
    from src.features.composer.empty_section_recovery import requested_sections_from_response

    body = {"문장들": []}
    assert requested_sections_from_response({"장들": {"a": body, "z": body}}, ("a",)) == ({"a": body}, 1, "계약")
    assert requested_sections_from_response({"장들": {"z": body}}, ("a",)) == ({}, 1, "요청장없음")
    assert requested_sections_from_response({"a": body, "z": body}, ("a",)) == ({"a": body}, 1, "포장없음")
    assert requested_sections_from_response(body, ("a",)) == ({"a": body}, 0, "단일장평면")
    assert requested_sections_from_response(body, ("a", "b")) == ({}, 0, "요청장없음")
    assert requested_sections_from_response([body], ("a",)) == ({}, 0, "읽기실패")
    assert requested_sections_from_response(None, ("a",)) == ({}, 0, "읽기실패")


# ══════════════════════════════════════════════════════════
# 응답 장 키 해석 — 장 ID 대신 표시명을 키로 쓴 정답도 받는다
#
# 실측(2026-09-23): 1차 답은 키가 «1장 <제목>»·«2장 <제목>», 재요청 답은 «1장»·
# «2장»에 주장슬롯까지 장 ID를 뗀 이름이었다. 내용은 맞았는데 둘 다
# «요청장없음»으로 버려져 두 장이 빈 채로 나갔다. 아래 시험은 그 두 꼴을 익명
# 픽스처로 재현하고, 해석이 넓어진 만큼 «받지 않을 것»도 함께 못 박는다.
# ══════════════════════════════════════════════════════════

IDENTITY_TEXT = "가나다전자는 산업용 검사 장비를 설계하고 판매하는 회사다."
BUSINESS_TEXT = "가나다전자는 검사 장비 판매와 유지보수 계약으로 수익을 얻는다."
_TWO_TARGETS = ("identity", "business_model")
_RECOVERY_LOGGER = "src.features.composer.empty_section_recovery"


def _two_section_evidence():
    return recovery_evidence((_fragment("identity", IDENTITY_TEXT, "1"),
                              _fragment("business_model", BUSINESS_TEXT, "2")))


def _keyed_response(sections):
    """키(장 ID·표시명 등)와 문장별 주장슬롯을 그대로 담은 «장들» 응답."""
    return json.dumps({"장들": {
        key: {"문장들": [{"글": text, "인용": [fragment_id], "등급": GRADE_CONFIRMED,
                        "주장슬롯": claim_slot}
                       for text, fragment_id, claim_slot in sentences]}
        for key, sentences in sections.items()
    }}, ensure_ascii=False)


def _recover_two(writer, diagnostics):
    """두 장(identity·business_model)을 운영(FULL)과 같은 의미 칸 조건으로 복구한다."""
    return recover_empty_sections("가나다전자", _report(*_TWO_TARGETS), targets=_TWO_TARGETS,
        evidence=_two_section_evidence(), writer=writer, reviewer=_FakeReviewer(),
        protocol_diagnostics=diagnostics, require_claim_slot=True)


def _recovery_states(diagnostics):
    return [item["상태"] for item in _recovery_steps(diagnostics)]


def test_표시명_키와_정식_주장슬롯_답을_재요청_없이_받는다():
    """실측 1차 답 꼴 — «N장 제목» 키 + 정식 주장슬롯이면 두 장 모두 채운다."""
    calls, diagnostics = [], []
    def writer(prompt):
        calls.append(prompt)
        return _keyed_response({
            "1장 기업 정체성": [(IDENTITY_TEXT, "1", "identity:corporate_identity")],
            "2장 사업 구조와 수익 모델": [(BUSINESS_TEXT, "2", "business_model:revenue_model")],
        })
    result = _recover_two(writer, diagnostics)
    assert len(calls) == 1, "표시명 키를 읽었으면 재요청하지 않는다"
    assert [section.sentences[0].text for section in result.sections] == [IDENTITY_TEXT, BUSINESS_TEXT]
    written = next(item for item in diagnostics if item.get("상태") == "작성완료")
    assert (written["응답꼴"], written["요청밖장수"], written["작성문장수"]) == ("계약", 0, 2)
    assert _recovery_states(diagnostics) == ["작성완료", "검수완료"]
    done = next(item for item in diagnostics if item.get("상태") == "검수완료")
    assert done["복구장"] == ["identity", "business_model"]


def test_장번호_키와_장ID를_뗀_주장슬롯도_정식_이름으로_받는다():
    """실측 재요청 답 꼴 — «1장»·«2장» 키 + 장 ID를 뗀 주장슬롯.

    ★ 운영(FULL)처럼 require_claim_slot=True 로 돈다. 주장슬롯 복원이 없으면 두
      문장 모두 빈 칸이 되어 걸러지고 «확인후보없음»으로 끝난다.
    """
    calls, diagnostics = [], []
    def writer(prompt):
        calls.append(prompt)
        return _keyed_response({
            "1장": [(IDENTITY_TEXT, "1", "corporate_identity")],
            "2장": [(BUSINESS_TEXT, "2", "revenue_model")],
        })
    result = _recover_two(writer, diagnostics)
    assert len(calls) == 1
    assert _recovery_states(diagnostics) == ["작성완료", "검수완료"]
    assert [(section.sentences[0].text, section.sentences[0].planned_claim_slot)
            for section in result.sections] == [
        (IDENTITY_TEXT, "identity:corporate_identity"),
        (BUSINESS_TEXT, "business_model:revenue_model"),
    ]


def test_두_요청_장을_한_키에_묶은_답은_버리고_요청밖으로_센다():
    """어느 장인지 가를 수 없는 키는 받지 않는다 — 제 키로 온 장만 살린다.

    ★ 묶은 키가 identity 장의 «유일한» 공급원이다. 묶은 키를 앞 장으로 풀어 주는
      해석(포함 검사에 첫 장 채택 등)이 들어오면 identity 장이 채워져 빨개진다.
    """
    calls, diagnostics = [], []
    def writer(prompt):
        calls.append(prompt)
        return _keyed_response({
            "1장 기업 정체성·2장 사업 구조와 수익 모델": [
                (IDENTITY_TEXT, "1", "identity:corporate_identity")],
            "business_model": [(BUSINESS_TEXT, "2", "business_model:revenue_model")],
        })
    result = _recover_two(writer, diagnostics)
    assert len(calls) == 1
    assert not result.sections[0].sentences, "묶은 키의 문장이 어느 장에도 실리면 안 된다"
    assert result.sections[1].sentences[0].text == BUSINESS_TEXT
    written = next(item for item in diagnostics if item.get("상태") == "작성완료")
    assert written["요청밖장수"] == 1


def test_한_해석_단계에서_두_요청_장에_걸리는_키는_모호해서_받지_않는다():
    """정규화하면 같아지는 두 요청 장이 있으면 그 키는 어느 쪽에도 주지 않는다.

    실제 장 ID끼리는 정규화 값이 겹치지 않는다. 이 시험은 «모호하면 버린다»는
    규칙 자체를 지킨다 — 규칙을 빼면 첫 번째 장이 남의 본문을 가져간다.
    """
    body = {"문장들": []}
    assert requested_sections_from_response({"장들": {"A B": body}}, ("a-b", "a_b")) == (
        {}, 1, "요청장없음")
    assert requested_sections_from_response({"장들": {"A B": body, "a_b": body}}, ("a-b", "a_b")) == (
        {"a_b": body}, 1, "계약")


def test_요청과_무관한_키뿐이면_두_번_모두_요청장없음으로_끝난다():
    """«z»나 요청하지 않은 장의 표시명(«3장»)만 있으면 지금처럼 재요청 1회 뒤 포기한다."""
    calls, diagnostics = [], []
    def writer(prompt):
        calls.append(prompt)
        return _keyed_response({
            "z": [(IDENTITY_TEXT, "1", "identity:corporate_identity")],
            "3장": [(BUSINESS_TEXT, "2", "business_model:revenue_model")],
        })
    result = _recover_two(writer, diagnostics)
    assert result == _report(*_TWO_TARGETS)
    assert len(calls) == 2, "파싱 재요청 상한은 1회다"
    assert diagnostics == [{"step": "8_빈장_복구", "상태": "작성형식실패",
                            "대상장": ["identity", "business_model"], "시도": 2,
                            "응답꼴": ["요청장없음", "요청장없음"]}]


def test_프롬프트는_요청_장_ID를_키로_명시하고_재요청은_키와_주장슬롯_모양을_다시_요구한다():
    """첫 요청부터 «장들»의 키로 쓸 장 ID 문자열을 그대로 보여 준다.

    ★ 지침 원문은 끊기지 않고 그대로 들어가야 한다 — 다른 시험의 가짜 작가가
      «지침이 들어 있는가»로 복구 요청을 알아본다.
    """
    from src.features.composer.empty_section_recovery_constants import EMPTY_RECOVERY_GUIDE

    calls = []
    def writer(prompt):
        calls.append(prompt)
        return "형식을 따르지 못했습니다"
    _recover_two(writer, [])
    id_line = '요청 장 ID(이 문자열을 그대로 "장들"의 키로 쓴다): identity, business_model\n'
    key_rule = '"장들"의 키는 위 «요청 장 ID» 줄의 문자열을 그대로 쓰고 장 번호나 장 제목으로 바꾸지 않는다'
    slot_rule = "주장슬롯에는 «의미칸» 목록의 값을 콜론 앞 장 ID까지 그대로 적는다"
    assert len(calls) == 2
    assert all(EMPTY_RECOVERY_GUIDE + id_line in prompt for prompt in calls)
    assert key_rule not in calls[0] and key_rule in calls[1]
    assert slot_rule not in calls[0] and slot_rule in calls[1]


@pytest.mark.parametrize("key", [
    "identity", "IDENTITY", " Identity ", "1장", "제1장", "제 1 장", "1장 기업 정체성",
    "1장 «기업 정체성»", "제1장: 기업 정체성", "기업 정체성", "기업정체성", "1. 기업 정체성",
    "[1장] 기업 정체성",
])
def test_장_키의_정확_정규화_표시형_변형을_요청_장으로_해석한다(key):
    body = {"문장들": []}
    assert requested_sections_from_response({"장들": {key: body}}, _TWO_TARGETS) == (
        {"identity": body}, 0, "계약")


@pytest.mark.parametrize("key", ["Business-Model", "business model", "BUSINESS_MODEL", "business-model"])
def test_대소문자_공백_하이픈_밑줄만_다른_장_ID를_받는다(key):
    body = {"문장들": []}
    assert requested_sections_from_response({"장들": {key: body}}, _TWO_TARGETS) == (
        {"business_model": body}, 0, "계약")


def test_포장_없는_답의_표시명_키도_해석한다():
    """«장들» 포장을 뺀 꼴도 같은 해석을 쓰고, 문장 목록 키는 장으로 세지 않는다."""
    body = {"문장들": []}
    assert requested_sections_from_response({"2장": body, "문장들": []}, _TWO_TARGETS) == (
        {"business_model": body}, 0, "포장없음")


def test_같은_장에_걸린_키가_둘이면_앞_단계_키를_쓰고_하나는_요청밖으로_센다():
    """정확 일치가 표시형보다 앞선다. 같은 단계끼리는 먼저 나온 키를 쓴다."""
    body, other = {"문장들": []}, {"문장들": [{"글": "다른 답"}]}
    assert requested_sections_from_response({"장들": {"1장": other, "identity": body}}, _TWO_TARGETS) == (
        {"identity": body}, 1, "계약")
    assert requested_sections_from_response({"장들": {"1장": body, "기업 정체성": other}}, _TWO_TARGETS) == (
        {"identity": body}, 1, "계약")


def test_요청하지_않은_장의_표시명은_받지_않는다():
    body = {"문장들": []}
    assert requested_sections_from_response({"장들": {"3장": body}}, _TWO_TARGETS) == (
        {}, 1, "요청장없음")
    assert requested_sections_from_response({"장들": {"1장": body}}, ("business_model",)) == (
        {}, 1, "요청장없음")


@pytest.mark.parametrize("section_id", _ALL_SECTION_IDS)
def test_작성범위_문구의_장_번호와_제목은_그_장으로만_풀린다(section_id):
    """작가가 보는 작성범위 머리(«N장 «제목»»)와 파서의 번호·제목이 같은지 대조한다.

    ★ 장 순서나 제목이 한쪽만 바뀌면 «2장»이 다른 장으로 풀려 문장이 엉뚱한 장에
      실린다. 아홉 장을 모두 요청해도 정확히 그 장 하나로만 풀려야 한다.
    """
    head = re.match(r"(\d+)장 «([^»]+)»", SECTION_GUIDES[section_id])
    assert head, "작성범위 문구가 «N장 «제목»» 으로 시작하지 않습니다"
    number, title = head.groups()
    body = {"문장들": []}
    for key in (head.group(0), f"{number}장", f"제{number}장", f"{number}장 {title}", title,
                f"{number}. {title}"):
        assert requested_sections_from_response({"장들": {key: body}}, _ALL_SECTION_IDS) == (
            {section_id: body}, 0, "계약"), key


def test_키_해석은_개수만_로그에_남긴다(caplog):
    """표시명·정규화로 받은 개수만 남기고 응답 문장·장 제목은 싣지 않는다."""
    body = {"문장들": [{"글": IDENTITY_TEXT, "인용": ["1"], "등급": GRADE_CONFIRMED}]}
    with caplog.at_level(logging.INFO, logger=_RECOVERY_LOGGER):
        requested_sections_from_response(
            {"장들": {"1장 기업 정체성": body, "Business-Model": body}}, _TWO_TARGETS)
        requested_sections_from_response({"장들": {"identity": body}}, _TWO_TARGETS)
    messages = [record.getMessage() for record in caplog.records if record.name == _RECOVERY_LOGGER]
    assert messages == ["빈 장 복구 응답 장 키 해석: 정규화 1개, 표시명 1개, 모호 0개"]


# ══════════════════════════════════════════════════════════
# 번호만 있는 키(«N장»·«제N장»)와 요청 순번의 충돌
#
# 작가가 장을 «요청한 순서»대로 «1장»·«2장»이라 부르면, 정본 번호로 푼 «2장»이
# 작가가 뜻한 장과 다를 수 있다. 독립 검토(2026-09-23)가 재현한 꼴: 요청 장이
# business_model·operations_partners 일 때 «2장»(작가 뜻은 두 번째 요청 장)이
# 정본 2장으로 풀려, 7장 사실이 2장에 실렸다(의미 칸 필수가 아닌 경로).
# 번호만 있는 키는 요청 순번으로 읽을 수 없거나(번호 > 요청 장 수) 두 읽기가 같은
# 장일 때만 받는다. 제목이 붙은 키는 번호와 제목이 서로 맞아야만 걸리므로 그대로다.
# ══════════════════════════════════════════════════════════

_LATER_TARGETS = ("past_changes", "culture")
_CONFLICT_TARGETS = ("business_model", "operations_partners")
_SHARED_BUSINESS_FACT = "가나다전자의 매출은 검사 장비 판매로 구성된다."
_SHARED_OPERATIONS_FACT = "가나다전자는 안성에 생산 공장을 두고 있다."
_BUSINESS_SLOT = "business_model:revenue_model"
_OPERATIONS_SLOT = "operations_partners:operating_role"


def _two_bodies():
    return {"문장들": [{"글": "첫째 장 본문"}]}, {"문장들": [{"글": "둘째 장 본문"}]}


def test_요청_순번_번호가_정본_번호와_다른_장을_가리키면_둘_다_받지_않는다():
    """과거·문화 두 장 요청에 «1장»·«2장» — 정본 1·2장은 요청 밖이고 순번 읽기와도 어긋난다."""
    first, second = _two_bodies()
    assert requested_sections_from_response(
        {"장들": {"1장": first, "2장": second}}, _LATER_TARGETS) == ({}, 2, "요청장없음")


@pytest.mark.parametrize("keys", [("1장", "2장"), ("제1장", "제2장"), ("1 장", "제 2 장")])
def test_정본_번호가_요청_장이어도_요청_순번이_다른_장이면_받지_않는다(keys):
    """독립 검토 재현 꼴 — «2장»은 정본으로 business_model, 순번으로 operations_partners 다.

    ★ 음성 대조 — 순번 충돌 검사를 빼면 «2장» 블록이 business_model 로 풀려
      ({business_model: 둘째}, 1, 계약) 이 되어 빨개진다.
    """
    first, second = _two_bodies()
    assert requested_sections_from_response(
        {"장들": {keys[0]: first, keys[1]: second}}, _CONFLICT_TARGETS,
    ) == ({}, 2, "요청장없음")


@pytest.mark.parametrize(("targets", "keys", "expected_ids"), [
    (_LATER_TARGETS, ("4장", "8장"), ("past_changes", "culture")),
    (_LATER_TARGETS, ("제4장", "제 8 장"), ("past_changes", "culture")),
    (_CONFLICT_TARGETS, ("7장",), ("operations_partners",)),
])
def test_번호가_요청_장_수보다_크면_정본_번호대로_받는다(targets, keys, expected_ids):
    """요청 순번으로 읽을 수 없는 번호는 모호하지 않다 — 예전처럼 정본 장으로 푼다."""
    bodies = {key: {"문장들": [{"글": key}]} for key in keys}
    expected = {section_id: bodies[key] for section_id, key in zip(expected_ids, keys)}
    assert requested_sections_from_response({"장들": bodies}, targets) == (
        expected, 0, "계약")


@pytest.mark.parametrize("keys", [
    ("1장 기업 정체성", "2장 사업 구조와 수익 모델"),
    ("1장", "2장"),
])
def test_실측_5차_두_꼴은_번호_순번_규칙_뒤에도_그대로_받는다(keys):
    """5차 1차 답(«N장 제목»)과 재요청 답(«N장»). 요청 1·2장이라 두 읽기가 같은 장이다."""
    first, second = _two_bodies()
    assert requested_sections_from_response(
        {"장들": {keys[0]: first, keys[1]: second}}, _TWO_TARGETS) == (
        {"identity": first, "business_model": second}, 0, "계약")


@pytest.mark.parametrize("keys", [
    ("2장 사업 구조와 수익 모델", "7장 사업 운영과 파트너 구조"),
    ("사업 구조와 수익 모델", "사업 운영과 파트너 구조"),
    ("2. 사업 구조와 수익 모델", "제7장: 사업 운영과 파트너 구조"),
])
def test_제목이_붙은_키는_요청_순번과_무관하게_정본대로_받는다(keys):
    """순번 충돌이 생기는 요청(2장·7장)이어도 제목이 붙은 키는 그 장으로 풀린다."""
    first, second = _two_bodies()
    assert requested_sections_from_response(
        {"장들": {keys[0]: first, keys[1]: second}}, _CONFLICT_TARGETS) == (
        {"business_model": first, "operations_partners": second}, 0, "계약")


def test_번호와_제목이_어긋난_키는_지금처럼_받지_않는다():
    """«1장 사업 구조와 수익 모델»은 번호(1장)와 제목(2장)이 달라 어느 표시형에도 없다."""
    first, _ = _two_bodies()
    assert requested_sections_from_response(
        {"장들": {"1장 사업 구조와 수익 모델": first}}, _CONFLICT_TARGETS) == ({}, 1, "요청장없음")


def test_순번_충돌로_버린_키는_개수만_모호로_로그에_남긴다(caplog):
    """충돌 키는 요청 밖으로 세고, 로그에는 개수만 남긴다 — 본문·장 제목은 싣지 않는다."""
    body = {"문장들": [{"글": BUSINESS_TEXT, "인용": ["2"], "등급": GRADE_CONFIRMED}]}
    with caplog.at_level(logging.INFO, logger=_RECOVERY_LOGGER):
        assert requested_sections_from_response(
            {"장들": {"2장": body}}, _CONFLICT_TARGETS) == ({}, 1, "요청장없음")
    messages = [record.getMessage() for record in caplog.records if record.name == _RECOVERY_LOGGER]
    assert messages == ["빈 장 복구 응답 장 키 해석: 정규화 0개, 표시명 0개, 모호 1개"]


def test_순번_번호_답은_의미칸_필수가_아니어도_다른_장에_실리지_않는다():
    """끝까지 — 두 장 의미 칸을 모두 지원하는 조각 하나를 인용한 순번 번호 답.

    ★ 음성 대조 — 순번 충돌 검사를 빼면 «2장» 블록(작가 뜻은 7장 사실)이 2장에
      실려 빨개진다. require_claim_slot 은 함수 기본값(False) 그대로다 — packet·
      부분근거 보기가 없는 경로가 이 값을 쓴다.
    """
    shared_text = f"{_SHARED_BUSINESS_FACT} {_SHARED_OPERATIONS_FACT}"
    shared = replace(_fragment("business_model", shared_text, "1"),
                     supported_claim_slots=(_BUSINESS_SLOT, _OPERATIONS_SLOT))
    calls, diagnostics = [], []
    def writer(prompt):
        calls.append(prompt)
        return _keyed_response({
            "1장": [(_SHARED_BUSINESS_FACT, "1", _BUSINESS_SLOT)],
            "2장": [(_SHARED_OPERATIONS_FACT, "1", _OPERATIONS_SLOT)],
        })
    result = recover_empty_sections(
        "가나다전자", _report(*_CONFLICT_TARGETS), targets=_CONFLICT_TARGETS,
        evidence=recovery_evidence((shared,)), writer=writer, reviewer=_FakeReviewer(),
        protocol_diagnostics=diagnostics)
    assert not any(section.sentences for section in result.sections)
    assert len(calls) == 2, "순번 충돌 키만 있으면 요청장없음으로 한 번 재요청한다"
    assert _recovery_states(diagnostics) == ["작성형식실패"]


# ══════════════════════════════════════════════════════════
# 정본 모드 — 순번으로 못 읽는 번호 키가 정본으로 요청 장에 걸리면
#
# 2026-09-23 무료 탐침: 요청 2·7장에 정본 번호 키 «2장»·«7장»을 준 답에서 «2장»이
# 순번 충돌로 버려졌고, «7장»이 풀려 쓸 장이 생겼으므로 재요청도 없이 2장이 빈
# 채로 끝났다. «7장»은 요청 두 개의 순번으로는 나올 수 없는 번호이고 요청 장이라
# 작가가 정본 번호를 쓴다는 증거다. 그런 키가 있으면 그 응답의 번호만 있는 키는
# 모두 정본대로 읽는다(총괄 규칙, 좁힌 판별). 요청 밖 장의 번호(덤 «3장»)는 근거가
# 아니다. 근거가 없으면 위 규칙 그대로다 — 요청 2·7장에 «1장»·«2장»은 여전히 받지
# 않고 재요청한다.
# ══════════════════════════════════════════════════════════

OPERATIONS_TEXT = "가나다전자는 안성 공장에서 검사 장비를 조립해 고객사에 납품한다."


def _conflict_section_evidence():
    """요청 2·7장(business_model·operations_partners)에 장마다 조각 하나."""
    return recovery_evidence((
        _fragment("business_model", BUSINESS_TEXT, "2"),
        replace(_fragment("operations_partners", OPERATIONS_TEXT, "7"),
                supported_claim_slots=(_OPERATIONS_SLOT,)),
    ))


@pytest.mark.parametrize(("keys", "expected_ids"), [
    (("2장", "7장"), ("business_model", "operations_partners")),
    (("7장", "2장"), ("operations_partners", "business_model")),
    (("제2장", "7장"), ("business_model", "operations_partners")),
    (("2 장", "제 7 장"), ("business_model", "operations_partners")),
    (("2장 사업 구조와 수익 모델", "7장"), ("business_model", "operations_partners")),
])
def test_순번으로_못_읽는_요청_장_번호_키가_있으면_번호_키를_모두_정본으로_받는다(
        keys, expected_ids):
    """«7장»은 요청 두 개의 순번으로 못 읽는 요청 장 번호다 — 같은 응답의 «2장»도 정본이다.

    ★ 음성 대조 — 정본 모드 판별을 빼면 «2장»이 순번 충돌로 버려져
      ({operations_partners: …}, 1, 계약) 이 되어 빨개진다. 키 순서를 뒤집은 꼴은
      판별이 키 해석 «전에» 응답 전체로 한 번 정해지는지를 지킨다. 제목이 붙은
      키(마지막 꼴)는 정본 모드와 무관하게 예전처럼 풀린다.
    """
    bodies = {key: {"문장들": [{"글": key}]} for key in keys}
    expected = {section_id: bodies[key] for key, section_id in zip(keys, expected_ids)}
    assert requested_sections_from_response({"장들": bodies}, _CONFLICT_TARGETS) == (
        expected, 0, "계약")


def test_정본_모드는_포장_없는_꼴에도_같다():
    """«장들» 포장을 뺀 답도 같은 판별을 쓴다 — 응답 꼴 코드는 «포장없음» 그대로다."""
    first, second = _two_bodies()
    response = {"2장": first, "7장": second}
    assert requested_sections_from_response(response, _CONFLICT_TARGETS) == (
        {"business_model": first, "operations_partners": second}, 0, "포장없음")


def test_요청_밖_장의_큰_번호는_정본_모드_근거가_아니다():
    """«5장»은 순번으로 못 읽지만 요청 밖 장이라 근거가 아니다 — «2장»은 순번 충돌이다.

    ★ 음성 대조 — 근거를 요청 장으로 좁히지 않으면(넓은 판별) «5장»이 정본 모드를
      켜서 ({business_model: …}, 1, 계약) 이 되어 빨개진다.
    """
    first, second = _two_bodies()
    assert requested_sections_from_response(
        {"장들": {"2장": first, "5장": second}}, _CONFLICT_TARGETS) == (
        {}, 2, "요청장없음")


def test_정본_번호_답_2장_7장은_두_장을_모두_채우고_재요청하지_않는다():
    """탐침으로 찾은 부작용 고정 — 운영(FULL)처럼 의미 칸 필수로 끝까지 돈다.

    ★ 음성 대조 — 정본 모드 판별을 빼면 2장 블록이 순번 충돌로 버려지고 7장만
      채워진다. 7장이 풀렸으므로 재요청도 나가지 않아(작가 1회) 2장이 빈 채로 끝난다.
    """
    calls, diagnostics = [], []
    def writer(prompt):
        calls.append(prompt)
        return _keyed_response({
            "2장": [(BUSINESS_TEXT, "2", _BUSINESS_SLOT)],
            "7장": [(OPERATIONS_TEXT, "7", _OPERATIONS_SLOT)],
        })
    result = recover_empty_sections(
        "가나다전자", _report(*_CONFLICT_TARGETS), targets=_CONFLICT_TARGETS,
        evidence=_conflict_section_evidence(), writer=writer, reviewer=_FakeReviewer(),
        protocol_diagnostics=diagnostics, require_claim_slot=True)
    assert len(calls) == 1, "정본 번호 답을 읽었으면 재요청하지 않는다"
    assert [[sentence.text for sentence in section.sentences]
            for section in result.sections] == [[BUSINESS_TEXT], [OPERATIONS_TEXT]]
    written = next(item for item in diagnostics if item.get("상태") == "작성완료")
    assert (written["응답꼴"], written["요청밖장수"], written["작성문장수"]) == ("계약", 0, 2)
    assert _recovery_states(diagnostics) == ["작성완료", "검수완료"]


@pytest.mark.parametrize("require_claim_slot", [True, False])
def test_덤_장_번호는_정본_모드를_켜지_않아_다른_장_사실을_싣지_않고_재요청한다(
        require_claim_slot):
    """요청 순번으로 «1장»·«2장»을 쓴 답에 덤 장 «3장»이 얹힌 꼴 — «3장»은 요청 밖이다.

    조각은 두 장 칸을 모두 지원하는 공유 조각이라 인용 검사로는 막히지 않는다.
    판별이 켜지지 않으므로 «2장»은 순번 충돌로 버려지고, 쓸 장이 없어 재요청한다.

    ★ 음성 대조 — 근거를 요청 장으로 좁히지 않으면(넓은 판별) «3장»이 정본 모드를
      켜서 «2장»(작가 뜻은 둘째 요청 장의 7장 사실)이 정본 2장으로 풀린다. 의미 칸
      필수가 아니면 7장 사실이 2장에 실리고, 필수여도 쓸 장이 생겨 재요청이
      사라진다(작가 1회). 두 매개변수 모두 빨개진다.
    """
    shared_text = f"{_SHARED_BUSINESS_FACT} {_SHARED_OPERATIONS_FACT}"
    shared = replace(_fragment("business_model", shared_text, "1"),
                     supported_claim_slots=(_BUSINESS_SLOT, _OPERATIONS_SLOT))
    calls, diagnostics = [], []
    def writer(prompt):
        calls.append(prompt)
        return _keyed_response({
            "1장": [(_SHARED_BUSINESS_FACT, "1", _BUSINESS_SLOT)],
            "2장": [(_SHARED_OPERATIONS_FACT, "1", _OPERATIONS_SLOT)],
            "3장": [(_SHARED_BUSINESS_FACT, "1", _BUSINESS_SLOT)],
        })
    result = recover_empty_sections(
        "가나다전자", _report(*_CONFLICT_TARGETS), targets=_CONFLICT_TARGETS,
        evidence=recovery_evidence((shared,)), writer=writer, reviewer=_FakeReviewer(),
        protocol_diagnostics=diagnostics, require_claim_slot=require_claim_slot)
    assert not any(section.sentences for section in result.sections), "다른 장 사실이 실렸다"
    assert len(calls) == 2, "쓸 장이 없으면 요청장없음으로 한 번 재요청한다"
    assert _recovery_states(diagnostics) == ["작성형식실패"]


# ══════════════════════════════════════════════════════════
# 섞인 번호 — 정본 모드로 푼 순번 범위 번호 키의 본문이 다른 요청 장 칸이면
#
# 2026-09-23 독립 검토(worker-a): 순번 «1장»(2장 글)·«2장»(7장 글)에 정본 «7장»이
# 얹히면 «7장»이 정본 모드를 켜서 «2장»의 7장 글이 2장으로 풀렸다. 의미 칸 필수가
# 아니면 7장 사실이 2장에 공개되고, 필수면 2장이 재요청 없이 빈다. 두 겹으로 막는다.
#   ① 정본 모드로 푼 번호만 있는 키라도 번호가 요청 장 수 이하(순번으로도 읽힘)이면,
#      본문 주장슬롯이 다른 요청 장을 가리킬 때 받지 않고 모호로 센다.
#   ② 포장 없는 꼴에서는 본문이 객체인 키만 정본 모드 근거로 센다.
# ══════════════════════════════════════════════════════════

_OPERATIONS_TAIL = _OPERATIONS_SLOT.partition(":")[2]
_OPERATIONS_TITLE = "사업 운영과 파트너 구조"


def _slot_body(text, slot=""):
    """공유 조각 1을 인용한 한 문장 장 본문. slot 이 비면 주장슬롯 칸을 두지 않는다."""
    sentence = {"글": text, "인용": ["1"], "등급": GRADE_CONFIRMED}
    if slot:
        sentence["주장슬롯"] = slot
    return {"문장들": [sentence]}


def _shared_two_section_evidence():
    """2·7장 칸을 함께 지원하는 공유 조각 하나 — 인용 검사로는 오배정을 못 막는 조건."""
    shared_text = f"{_SHARED_BUSINESS_FACT} {_SHARED_OPERATIONS_FACT}"
    shared = replace(_fragment("business_model", shared_text, "1"),
                     supported_claim_slots=(_BUSINESS_SLOT, _OPERATIONS_SLOT))
    return recovery_evidence((shared,))


def _mixed_response(case, operations_slot=_OPERATIONS_SLOT):
    """독립 검토의 섞인 번호 꼴 — 작가 뜻은 «1장»=2장 글, «2장»=7장 글이다."""
    business = _slot_body(_SHARED_BUSINESS_FACT, _BUSINESS_SLOT)
    operations = _slot_body(_SHARED_OPERATIONS_FACT, operations_slot)
    seventh = _slot_body(_SHARED_OPERATIONS_FACT, _OPERATIONS_SLOT)
    if case == "F8":
        return {"장들": {"1장": business, "2장": operations, "7장": seventh}}
    if case == "F10":
        return {"장들": {"2장": operations, "제7장": seventh}}
    # F9 — 포장 없는 꼴, «7장» 값은 장 제목 문자열이다. 본문 칸을 빼서 ①이 아니라
    # ②만 이 꼴을 막게 한다.
    return {"1장": _slot_body(_SHARED_BUSINESS_FACT),
            "2장": _slot_body(_SHARED_OPERATIONS_FACT), "7장": _OPERATIONS_TITLE}


@pytest.mark.parametrize(("case", "unused"), [("F8", 2), ("F10", 1)])
@pytest.mark.parametrize("operations_slot", [_OPERATIONS_SLOT, _OPERATIONS_TAIL],
                         ids=["정식칸", "꼬리칸"])
def test_섞인_번호_답의_순번_2장은_다른_요청_장_칸이면_2장으로_풀리지_않는다(
        case, unused, operations_slot):
    """F8·F10 꼴 — «2장»(7장 글)은 모호로 버리고, 7장은 정본 7장 키로 제자리에 받는다.

    ★ 음성 대조 — ①(본문 칸 교차 확인)을 빼면 «2장»이 business_model 로 풀려
      빨개진다. 꼬리칸 매개변수는 장 ID를 뗀 칸 이름도 장을 가리키는지 지킨다.
    """
    response = _mixed_response(case, operations_slot)
    seventh = response["장들"]["7장" if case == "F8" else "제7장"]
    assert requested_sections_from_response(response, _CONFLICT_TARGETS) == (
        {"operations_partners": seventh}, unused, "계약")


def test_포장_없는_꼴의_문자열_값_번호_키는_정본_모드_근거가_아니다():
    """F9 꼴 — «"7장": "<장 제목>"»은 장 본문이 아니다. 판별이 켜지지 않아 «2장»은
    순번 충돌로 버려지고, 쓸 장이 없어 재요청 대상이 된다.

    ★ 음성 대조 — ②를 빼면(문자열 값도 근거로 세면) 칸 없는 «2장»(7장 글)이
      business_model 로 풀려 빨개진다.
    """
    response = _mixed_response("F9")
    assert requested_sections_from_response(response, _CONFLICT_TARGETS) == (
        {}, 0, "요청장없음")


@pytest.mark.parametrize("require_claim_slot", [True, False])
@pytest.mark.parametrize("case", ["F8", "F9", "F10"], ids=["E2_F8", "E3_F9", "E4_F10"])
def test_섞인_번호_답은_끝까지_가도_7장_사실을_2장에_싣지_않는다(case, require_claim_slot):
    """독립 검토 E2·E3·E4 — 모두 «참»인 시험용 검수기, 두 장 칸을 함께 지원하는 공유 조각.

    F8·F10 은 7장만 제자리로 복구하고 2장은 빈 채로 둔다(재요청 없음). F9 는 쓸 장이
    없어 한 번 재요청하고, 같은 답이 다시 와서 작성형식실패로 끝난다.

    ★ 음성 대조 — 두 겹을 빼면(v2.1) 의미 칸 필수가 아닐 때 7장 사실이 2장에 실리고,
      필수일 때는 F9 가 재요청 없이 확인후보없음으로 끝나 빨개진다. 의미 칸 필수인
      F8·F10 은 v2.1 에서도 손실만 나므로 이 매개변수는 회귀 방지용이다.
    """
    calls, diagnostics = [], []
    def writer(prompt):
        calls.append(prompt)
        return json.dumps(_mixed_response(case), ensure_ascii=False)
    result = recover_empty_sections(
        "가나다전자", _report(*_CONFLICT_TARGETS), targets=_CONFLICT_TARGETS,
        evidence=_shared_two_section_evidence(), writer=writer,
        reviewer=_FakeReviewer(), protocol_diagnostics=diagnostics,
        require_claim_slot=require_claim_slot)
    placed = {section.section_id: [sentence.text for sentence in section.sentences]
              for section in result.sections}
    assert placed["business_model"] == [], "7장 사실이 2장에 실렸다"
    if case == "F9":
        assert (len(calls), _recovery_states(diagnostics)) == (2, ["작성형식실패"])
        assert placed["operations_partners"] == []
    else:
        assert len(calls) == 1
        assert placed["operations_partners"] == [_SHARED_OPERATIONS_FACT]
        assert _recovery_states(diagnostics) == ["작성완료", "검수완료"]


def test_주장슬롯_꼬리_이름은_장끼리_겹치지_않는다():
    """정본 표(CLAIM_SLOTS_BY_SECTION)의 불변식 — 칸은 «장 ID:꼬리» 꼴이고 꼬리는 한 장에만 있다.

    주장슬롯 앞부분 복원과 섞인 번호 답의 본문 칸 교차 확인이 이 전제에 기댄다. 두 장이
    같은 꼬리를 가지면 꼬리만 적은 다른 장 문장이 이 장의 정식 칸으로 복원되고, 교차
    확인도 그 문장의 장을 가리지 못한다.
    """
    malformed = [slot for section_id, slots in CLAIM_SLOTS_BY_SECTION.items()
                 for slot in slots
                 if slot.count(":") != 1 or slot.partition(":")[0] != section_id]
    tails = [slot.partition(":")[2]
             for slots in CLAIM_SLOTS_BY_SECTION.values() for slot in slots]
    assert malformed == []
    assert sorted({tail for tail in tails if tails.count(tail) > 1}) == []


def test_한_장만_풀리면_다른_장은_재요청_없이_빈다():
    """현재 동작 고정(2026-09-23 독립 검토 낮음 (가)) — 요청 1·8장에 «1장»·«2장»(순번).

    «1장»은 두 읽기가 같은 장이라 받고, «2장»은 정본 2장이 요청 밖이라 해석하지 못한다.
    쓸 장이 하나라도 있으면 재요청하지 않으므로(2026-09-14 규칙) culture 는 이번
    복구에서 빈 채로 남는다. 이 동작을 바꿀 때는 이 시험을 의도적으로 고친다.
    """
    targets = ("identity", "culture")
    identity_slot = CLAIM_SLOTS_BY_SECTION["identity"][0]
    culture_slot = CLAIM_SLOTS_BY_SECTION["culture"][0]
    calls, diagnostics = [], []
    def writer(prompt):
        calls.append(prompt)
        return _keyed_response({"1장": [(IDENTITY_TEXT, "1", identity_slot)],
                                "2장": [(CULTURE_TEXT, "8", culture_slot)]})
    result = recover_empty_sections(
        "가나다전자", _report(*targets), targets=targets,
        evidence=recovery_evidence((_fragment("identity", IDENTITY_TEXT, "1"),
                                    _fragment("culture", CULTURE_TEXT, "8"))),
        writer=writer, reviewer=_FakeReviewer(), protocol_diagnostics=diagnostics,
        require_claim_slot=True)
    assert len(calls) == 1, "쓸 장이 하나라도 있으면 재요청하지 않는다"
    assert [[sentence.text for sentence in section.sentences]
            for section in result.sections] == [[IDENTITY_TEXT], []]
    written = next(item for item in diagnostics if item.get("상태") == "작성완료")
    assert (written["요청밖장수"], written["작성문장수"]) == (1, 1)
    assert _recovery_states(diagnostics) == ["작성완료", "검수완료"]


# ══════════════════════════════════════════════════════════
# 포장 안에서도 문자열 값 번호 키는 정본 모드 근거가 아니다
#
# «장들» 포장 안의 «"7장": "<장 제목>"»도 장 본문이 아니다. 이전 규칙은 포장 없는
# 꼴에서만 객체 값 키를 근거로 셌고, 포장 안에서는 문자열 값도 셌다. 그래서 칸 없는
# «2장»(7장 글)과 함께 오면 정본 모드가 켜져 «2장»이 2장으로 풀렸다. 문자열 본문은
# 포장 안에서도 그 장 본문으로 고르지 않는다 — 쓸 장이 없으면 재요청한다.
# ══════════════════════════════════════════════════════════


def test_포장_안에서도_본문이_객체인_번호_키만_정본_모드_근거다():
    """같은 «2장»(2장 글)이 «7장» 값이 객체면 정본 모드로 2장에 풀리고, 문자열이면
    판별이 켜지지 않아 순번 충돌로 버려진다.

    문자열 값 자체도 7장 본문으로 고르지 않으므로 쓸 장이 없어 요청장없음(재요청
    대상)이 된다.

    ★ 음성 대조 — 포장 안 문자열 값도 근거로 세면 문자열 쪽에서 «2장»이 2장으로 풀려
      빨개진다. 문자열 본문을 고르면 요청장없음 대신 7장에 문자열이 골라져 빨개진다.
      객체 쪽 단정은 정본 모드가 포장 안에서 그대로 켜지는지 지킨다.
    """
    business = _slot_body(_SHARED_BUSINESS_FACT, _BUSINESS_SLOT)
    seventh = _slot_body(_SHARED_OPERATIONS_FACT, _OPERATIONS_SLOT)
    assert requested_sections_from_response(
        {"장들": {"2장": business, "7장": seventh}}, _CONFLICT_TARGETS) == (
        {"business_model": business, "operations_partners": seventh}, 0, "계약")
    assert requested_sections_from_response(
        {"장들": {"2장": business, "7장": _OPERATIONS_TITLE}}, _CONFLICT_TARGETS) == (
        {}, 1, "요청장없음")


def test_포장_안_문자열_근거와_칸_없는_2장은_끝까지_가도_2장에_싣지_않는다():
    """«장들» 안에 순번 «1장»(2장 글)·«2장»(7장 글, 칸 없음)과 문자열 «7장».

    판별이 켜지지 않아 «2장»은 순번 충돌로, «1장»은 정본 1장이 요청 밖이라 버려진다.
    문자열 «7장»도 7장 본문으로 고르지 않으므로 쓸 장이 없어 한 번 재요청한다. 같은
    답이 다시 와서 작성형식실패로 끝나고, 두 장 모두 빈 채로 남는다.

    ★ 음성 대조 — 포장 안 문자열 값도 근거로 세면 칸 없는 «2장»이 business_model 로
      풀려, 의미 칸 필수가 아닌 경로에서 7장 사실이 2장에 실린다. 문자열 본문을 고르면
      재요청 없이 확인후보없음으로 끝나 빨개진다.
    """
    response = {"장들": {"1장": _slot_body(_SHARED_BUSINESS_FACT),
                         "2장": _slot_body(_SHARED_OPERATIONS_FACT),
                         "7장": _OPERATIONS_TITLE}}
    calls, diagnostics = [], []
    def writer(prompt):
        calls.append(prompt)
        return json.dumps(response, ensure_ascii=False)
    result = recover_empty_sections(
        "가나다전자", _report(*_CONFLICT_TARGETS), targets=_CONFLICT_TARGETS,
        evidence=_shared_two_section_evidence(), writer=writer,
        reviewer=_FakeReviewer(), protocol_diagnostics=diagnostics,
        require_claim_slot=False)
    assert not any(section.sentences for section in result.sections), "다른 장 사실이 실렸다"
    assert (len(calls), _recovery_states(diagnostics)) == (2, ["작성형식실패"])
