# -*- coding: utf-8 -*-
"""핵심 요약은 «독립 검수 통과 표식 + 인용»을 가진 본문 문장만 후보로 둔다.

★ 왜 이 시험이 생겼나 (2026-09-23 운영 PDF 27e9f03 실측, 독립 검수 G2) —
  표지 04 「핵심결론」에 「회사는 공식 자료에서 자신의 차별점을 명시적으로
  선언하지 않았다. — 해석」이 실렸다. 저장본의 그 항목은 section_id·fact_ids·
  verification_status가 전부 빈 값이었다. 원인은 요약 잣대
  (`is_release_ready_summary_sentence`)가 «숫자 없는 문장은 무조건 통과»였다는
  것이다 — 인용 0개·검수를 받은 적 없는 «해석» 문장이 요약 후보로 나가 AI
  고르기가 골랐다. 요약 계약(`docs/출력물 기준/00_핵심_요약/README.md`)은
  검수를 통과한 본문 문장만 후보로 두고, 3개 미만이면 억지로 채우지 않는다.

★ 세 경로를 «같은 재료»로 단정한다 — AI 고르기(legacy)·AI 없는 규칙 요약
  (fallback)·수치 안전 뒷문. 한 경로만 지키면 다른 경로로 새어 나간다.
"""

from __future__ import annotations

import json

from src.features.composer.constants import GRADE_CONFIRMED, GRADE_INTERPRETED
from src.features.composer.pipeline import (
    _legacy_summary_stage,
    _rule_summary_stage,
)
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.structured_claims import (
    NumericSafetyFiltering,
    enforce_public_numeric_safety,
    is_release_ready_summary_sentence,
    safe_numeric_owners_by_fact_id,
)

#: 운영 PDF 표지 04·9장 1번에 실렸던 문장 — 글자 그대로.
LIVE_COVER_ITEM_04_SENTENCE = "회사는 공식 자료에서 자신의 차별점을 명시적으로 선언하지 않았다."


def _verified_sentence(text: str, *, grade: str = GRADE_CONFIRMED) -> ComposedSentence:
    """verify_report를 통과한 본문 문장 — 인용 1개·검수 표식 있음."""

    return ComposedSentence(
        text=text, citations=("1",), grade=grade, verification_state="verified",
    )


#: 운영 실측과 같은 모양 — 인용 0개, «해석» 등급, 검수를 받은 적이 없다.
UNCITED_UNVERIFIED_INTERPRETATION = ComposedSentence(
    text=LIVE_COVER_ITEM_04_SENTENCE, citations=(), grade=GRADE_INTERPRETED,
)
#: 인용은 있으나 검수가 «애매»로 판정해 표식이 벗겨진 해석 문장(verify._demoted 꼴).
CITED_UNVERIFIED_INTERPRETATION = ComposedSentence(
    text="회사는 해외 플랫폼 진출로 추가 수익 경로를 확보한 것으로 보인다.",
    citations=("1",), grade=GRADE_INTERPRETED, verification_state="unverified",
)
#: 표식은 있으나 인용이 없는 문장 — 어느 근거에서 왔는지 되짚을 수 없다.
UNCITED_VERIFIED_SENTENCE = ComposedSentence(
    text="회사는 인공지능 콘텐츠 사업을 확대하고 있다.",
    citations=(), grade=GRADE_CONFIRMED, verification_state="verified",
)

VERIFIED_THREE_SECTIONS = (
    ComposedSection("identity", (_verified_sentence("가나다전자는 반도체 검사 장비 전문기업이다."),)),
    ComposedSection("business_model", (_verified_sentence("가나다전자는 장비 판매와 유지보수로 돈을 번다."),)),
    ComposedSection("portfolio", (_verified_sentence("가나다전자의 주력 제품은 웨이퍼 검사기다."),)),
)


def _live_shaped_report() -> ComposedReport:
    """검증 3장 + 미검증·무인용 문장만 남은 9장 — 운영 실측의 축소판."""

    return ComposedReport(sections=VERIFIED_THREE_SECTIONS + (
        ComposedSection("competitive_position", (
            UNCITED_UNVERIFIED_INTERPRETATION,
            CITED_UNVERIFIED_INTERPRETATION,
            UNCITED_VERIFIED_SENTENCE,
        )),
    ))


class _SelectEverythingSelector:
    """후보 번호를 «전부» 답하는 가짜 AI — 걸러지지 않은 후보가 있으면 반드시 고른다."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        count = prompt.count("\n") + 1
        return json.dumps(list(range(1, count + 1)))


# ══════════════════════════════════════════════════════════
# ① 잣대 자체
# ══════════════════════════════════════════════════════════


def test_summary_gate_requires_verified_state_and_citation_even_without_numbers():
    owners = safe_numeric_owners_by_fact_id(_live_shaped_report().sections)

    assert is_release_ready_summary_sentence(
        _verified_sentence("가나다전자는 반도체 검사 장비 전문기업이다."),
        safe_owner_by_fact_id=owners,
    )
    # 검수를 통과한 «해석»은 등급만으로 빠지지 않는다 — 계약이 우선순위를 낮출 뿐이다.
    assert is_release_ready_summary_sentence(
        _verified_sentence("가나다전자는 검사 장비에 집중한다.", grade=GRADE_INTERPRETED),
        safe_owner_by_fact_id=owners,
    )
    for sentence in (
        UNCITED_UNVERIFIED_INTERPRETATION,
        CITED_UNVERIFIED_INTERPRETATION,
        UNCITED_VERIFIED_SENTENCE,
    ):
        assert not is_release_ready_summary_sentence(
            sentence, safe_owner_by_fact_id=owners,
        ), sentence.text


# ══════════════════════════════════════════════════════════
# ② AI 고르기 경로 — 후보 목록에 애초에 실리지 않는다
# ══════════════════════════════════════════════════════════


def test_selector_never_sees_unverified_or_uncited_sentences():
    selector = _SelectEverythingSelector()
    diagnostics: list[dict] = []

    final, draft_count, filtering = _legacy_summary_stage(
        _live_shaped_report(),
        writer_ask=selector,
        body_numeric_filtering=NumericSafetyFiltering(),
        summary_diagnostics=diagnostics,
    )

    assert len(selector.prompts) == 1, "검증 3장이 있으니 고르기는 실제로 불린다"
    prompt = selector.prompts[0]
    assert LIVE_COVER_ITEM_04_SENTENCE not in prompt, "무인용 미검수 해석이 후보로 나갔다"
    assert CITED_UNVERIFIED_INTERPRETATION.text not in prompt
    assert UNCITED_VERIFIED_SENTENCE.text not in prompt
    summary_texts = [sentence.text for sentence in final.summary]
    assert LIVE_COVER_ITEM_04_SENTENCE not in summary_texts
    assert set(summary_texts) == {
        section.sentences[0].text for section in VERIFIED_THREE_SECTIONS
    }
    assert draft_count == 3
    assert filtering.removed_summary_count == 0, "후보 단계에서 이미 걸렀으므로 뒷문이 뺄 것이 없다"
    assert diagnostics[0]["본문후보수"] == 6  # 본문 문장 수는 그대로 센다
    assert diagnostics[0]["최종수"] == 3


def test_two_verified_sections_are_not_padded_with_unverified_interpretation():
    """계약 「근거가 충분한 결론이 3개 미만이면 억지로 채우지 않는다」."""

    report = ComposedReport(sections=VERIFIED_THREE_SECTIONS[:2] + (
        ComposedSection("competitive_position", (
            UNCITED_UNVERIFIED_INTERPRETATION, CITED_UNVERIFIED_INTERPRETATION,
        )),
    ))
    selector = _SelectEverythingSelector()

    final, draft_count, _filtering = _legacy_summary_stage(
        report, writer_ask=selector, body_numeric_filtering=NumericSafetyFiltering(),
    )

    assert selector.prompts == [], "후보가 두 장뿐인데 유료 고르기를 불렀다"
    assert draft_count == 0
    summary_texts = [sentence.text for sentence in final.summary]
    assert len(summary_texts) == 2
    assert LIVE_COVER_ITEM_04_SENTENCE not in summary_texts


# ══════════════════════════════════════════════════════════
# ③ AI 없는 규칙 요약(fallback) — 같은 잣대
# ══════════════════════════════════════════════════════════


def test_rule_summary_also_excludes_unverified_or_uncited_sentences():
    final, _filtering = _rule_summary_stage(
        _live_shaped_report(), NumericSafetyFiltering(),
    )

    summary_texts = [sentence.text for sentence in final.summary]
    assert summary_texts, "검증 문장이 있으니 규칙 요약은 비지 않는다"
    assert LIVE_COVER_ITEM_04_SENTENCE not in summary_texts
    assert CITED_UNVERIFIED_INTERPRETATION.text not in summary_texts
    assert UNCITED_VERIFIED_SENTENCE.text not in summary_texts
    for sentence in final.summary:
        assert sentence.verification_state == "verified"
        assert sentence.citations


# ══════════════════════════════════════════════════════════
# ④ 수치 안전 뒷문 — 다른 경로가 잣대를 놓쳐도 여기서 빠진다
# ══════════════════════════════════════════════════════════


def test_numeric_safety_backstop_drops_leaked_unverified_summary_sentences():
    report = _live_shaped_report()
    leaked = ComposedReport(
        sections=report.sections,
        summary=(
            VERIFIED_THREE_SECTIONS[0].sentences[0],
            UNCITED_UNVERIFIED_INTERPRETATION,
            CITED_UNVERIFIED_INTERPRETATION,
        ),
    )

    safe, filtering = enforce_public_numeric_safety(leaked)

    assert [sentence.text for sentence in safe.summary] == [
        VERIFIED_THREE_SECTIONS[0].sentences[0].text
    ]
    assert filtering.removed_summary_count == 2
    # 본문 공개 여부는 이 잣대가 바꾸지 않는다 — 9장 문장은 본문에 그대로 남아 있다.
    assert safe.sections[-1].sentences == report.sections[-1].sentences
