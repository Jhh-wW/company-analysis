"""최종 검수 진단은 살아남은 후보나 원문을 실패 기록에 섞지 않는다."""

from hashlib import sha256

from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence, FlowRow
from src.features.composer.review_outcomes import final_review_outcomes
from src.shared.report_quality.review_diagnostics import observed_review_outcomes


def _event(text="제외 후보", *, section="identity", kind="본문", **extra):
    return {"section_id": section, "kind": kind,
            "reason_code": "semantic_grounding_missing",
            "candidate_sha256": sha256(text.encode()).hexdigest(),
            "verification_items": ("수치",), **extra}


def test_diagnostics_keep_only_closed_fields_and_deduplicate_candidates():
    report = ComposedReport((ComposedSection("identity", ()),))
    event = _event(raw_text="보관하지 않을 원문", response="검수 응답 원문")
    results = final_review_outcomes(report, [event, event])
    assert len(results) == 1
    assert set(results[0]) == {"section_id", "kind", "reason_code", "candidate_sha256", "verification_items"}


def test_recovered_body_summary_and_diagram_are_not_final_exclusions():
    sentence = ComposedSentence("살아남은 후보", ("1",), "확인")
    flow = FlowRow(("시작", "끝"), ("1",))
    report = ComposedReport((ComposedSection("identity", (sentence,), flow_rows=(flow,)),), (sentence,))
    diagnostics = [_event(sentence.text), _event(sentence.text, section="summary", kind="요약"),
                   _event(" ".join(flow.cells), kind="도식")]
    assert final_review_outcomes(report, diagnostics) == ()


def test_unrecognized_reason_and_unhashed_text_are_not_transportable():
    report = ComposedReport((ComposedSection("identity", ()),))
    events = [_event(reason_code="원문이 노출된 임의 사유"),
              _event(candidate_sha256="금액이 든 원문"),
              _event(verification_items=("원문 정보",)), _event(section="알 수 없는 장")]
    assert final_review_outcomes(report, events) == ()


def test_empty_verification_items_preserve_actual_invalid_observation():
    report = ComposedReport((ComposedSection("identity", ()),))
    event = _event(
        reason_code="semantic_grounding_invalid",
        verification_items=(),
    )

    assert observed_review_outcomes([event]) == final_review_outcomes(report, [event])
    assert final_review_outcomes(report, [event])[0]["verification_items"] == ()


def test_intermediate_boundary_rejects_type_pollution_and_strips_raw_fields():
    event = _event(
        raw_text="1,234억원 원문",
        response="검수 응답 전체",
        amount="1,234억원",
    )
    polluted = [
        event,
        "문자열 사건",
        {**event, "section_id": 1},
        {**event, "kind": ["본문"]},
        {**event, "verification_items": "수치"},
        {**event, "section_id": "summary", "kind": "본문"},
    ]

    outcomes = observed_review_outcomes(polluted)

    assert len(outcomes) == 1
    assert set(outcomes[0]) == {
        "section_id", "kind", "reason_code", "candidate_sha256",
        "verification_items",
    }
    assert "1,234억원" not in repr(outcomes)
    assert "검수 응답 전체" not in repr(outcomes)
