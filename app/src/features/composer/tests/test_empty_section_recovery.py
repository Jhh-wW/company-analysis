"""빈 장의 제한된 복구도 실제 문장 검수를 통과해야 한다."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from src.features.composer.constants import (
    GRADE_CONFIRMED, GRADE_INTERPRETED, SECTION_IDS as _ALL_SECTION_IDS,
)
from src.features.composer.empty_section_recovery import recover_empty_sections, recovery_evidence, rejected_sentence_fingerprint
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
