# -*- coding: utf-8 -*-
"""2026-09-23 운영 PDF(배포 27e9f03) 결함 G1·G2·G3를 «실제 SHADOW 경로»로 재현한다.

★ 독립 검수(review-fable-final.md)가 실측한 세 결함:
    G1 9장 2번 「감사보고서와 공시 자료에는 … 특허 보유 … 공식 선언이 없다. [1][13][18]」
       — 인용 3개·확인 등급·검수 «참»인데 문서 전체의 부재를 조각이 뒷받침할 수 없다.
    G2 표지 04 「회사는 공식 자료에서 자신의 차별점을 명시적으로 선언하지 않았다. — 해석」
       — 인용 0개·검수 없음. 저장본 항목의 section_id·fact_ids·verification_status 전부 빈 값.
    G3 8장 1·2번 부재 단언 2문장이 «해석» 등급으로 인쇄됨.
  단위 시험은 `test_absence_claim_guard.py`·`test_summary_verified_only.py`가 맡는다.
  이 파일은 compose → verify → 요약 → render → validate_v2 를 «가짜 작가·검수만으로»
  끝까지 지나(AI·네트워크 0회) 화면에 닿는 값(prose_lines·summary_items)과
  운영 진단(review_diagnostics_sink)을 함께 단정한다.
★ 세 요약 경로를 다 지난다 — AI 고르기(legacy), 고르기 호출이 «강등 가능» 한도에
  걸려 legacy 단계 안에서 규칙 보충으로 채우는 경로, 그리고 «강등 불가» 전역
  장애로 run_v2가 규칙 요약(`_rule_summary_stage`)·확보 근거(evidence-available)
  보고서로 내려가는 fallback.
"""

from __future__ import annotations

import json
import re

from src.features.composer.absence_claim_constants import ABSENCE_CLAIM_UNSUPPORTED
from src.features.composer.constants import (
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
    SECTION_IDS,
)
from src.features.composer.pipeline import run_v2
from src.features.composer.port import AskFatalError
from src.features.composer.render import INTERPRETATION_MARKER
from src.features.composer.tests.test_pipeline import (
    _FakeReviewer,
    _SECTION_MARKS,
    _raw_fragments,
    _section_json,
    _summary_selection_json,
    section_id_in_prompt,
)
from src.features.composer.verify import DIAGNOSTIC_KIND_BODY
from src.shared.report_quality.output_constants import (
    SUMMARY_MAX_SENTENCES,
    SUMMARY_MIN_SENTENCES,
)

#: 운영 PDF의 문장을 «글자 그대로» 옮긴 것.
LIVE_CITED_ABSENCE_CLAIM_SECTION_9 = (
    "감사보고서와 공시 자료에는 회사의 기술 우위, 특허 보유, 독자 개발 기술, "
    "또는 경쟁사 대비 선도성에 관한 공식 선언이 없다."
)
LIVE_UNCITED_INTERPRETATION_COVER_04 = (
    "회사는 공식 자료에서 자신의 차별점을 명시적으로 선언하지 않았다."
)
LIVE_ABSENCE_CLAIMS_SECTION_8 = (
    "조직개편, 부서 구성, 의사결정 및 승인 절차에 관한 공식 공시 자료가 없다.",
    "노사 관계 및 노동 조건에 관한 공식 선언이나 정책 공시가 확인되지 않는다.",
)
LIVE_ABSENCE_SENTENCES = (
    LIVE_CITED_ABSENCE_CLAIM_SECTION_9,
    LIVE_UNCITED_INTERPRETATION_COVER_04,
    *LIVE_ABSENCE_CLAIMS_SECTION_8,
)

_CITATION_MARKER_RE = re.compile(r"\s*\[\d+\]")


def _display_text(text: str) -> str:
    """인용 표식과 «— 해석» 꼬리를 뗀 화면 문장 — 본문·요약 대조용."""

    plain = _CITATION_MARKER_RE.sub("", text)
    if plain.endswith(INTERPRETATION_MARKER):
        plain = plain[: -len(INTERPRETATION_MARKER)]
    return " ".join(plain.split())


class _LiveDefectWriter:
    """정상 두 문장 위에 운영 실측 부재 단언을 8장·9장에 «그대로» 얹는 가짜 작가.

    · 8장: 인용 0개 «해석» 2문장 (G3)
    · 9장: 인용 0개 «해석» 1문장 (G2) + 인용 1개 «확인» 1문장 (G1)
    ``summary_failure``: 요약 고르기 호출의 실패 모양 —
      · ``""``          정상 응답(번호 고르기)
      · ``"degradable"`` 호출 한도(call_limit) — legacy 단계가 삼키고 규칙 보충으로 채운다
      · ``"fatal"``      강등 불가 전역 장애 — run_v2가 규칙 요약·확보 근거 보고서로 내려간다
    """

    def __init__(self, *, summary_failure: str = "") -> None:
        self.summary_failure = summary_failure
        self.summary_prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        if "핵심 요약" in prompt:
            self.summary_prompts.append(prompt)
            if self.summary_failure == "degradable":
                raise AskFatalError(RuntimeError("요약 고르기 호출 한도"), call_limit=True)
            if self.summary_failure == "fatal":
                raise AskFatalError(
                    RuntimeError("요약 고르기 전역 장애"),
                    call_limit=False, request_budget=False,
                )
            return _summary_selection_json(prompt)
        section_id = section_id_in_prompt(prompt)
        assert section_id, "장 작성 프롬프트에서 장을 못 가렸다"
        payload = json.loads(_section_json(_SECTION_MARKS[SECTION_IDS.index(section_id)]))
        if section_id == "culture":
            payload["문장들"].extend(
                {"글": text, "인용": [], "등급": GRADE_INTERPRETED}
                for text in LIVE_ABSENCE_CLAIMS_SECTION_8
            )
        elif section_id == "competitive_position":
            payload["문장들"].append(
                {"글": LIVE_UNCITED_INTERPRETATION_COVER_04, "인용": [], "등급": GRADE_INTERPRETED}
            )
            payload["문장들"].append(
                {"글": LIVE_CITED_ABSENCE_CLAIM_SECTION_9, "인용": ["1"], "등급": GRADE_CONFIRMED}
            )
        return json.dumps(payload, ensure_ascii=False)


def _run_shadow(*, summary_failure: str):
    writer = _LiveDefectWriter(summary_failure=summary_failure)
    diagnostics: list[dict] = []
    output = run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=writer,
        reviewer_ask=_FakeReviewer(),
        corp_type="상장사",
        as_of_date="2026-08-24",
        review_diagnostics_sink=diagnostics,
        evidence_available_fallback=bool(summary_failure),
    )
    return output, writer, diagnostics


def _body_display_lines(report) -> dict[str, list[str]]:
    return {
        section.cell: [_display_text(line[0]) for line in section.prose_lines]
        for section in report.sections
    }


def _assert_live_defects_are_gone(output, writer, diagnostics) -> None:
    report = output.report

    # ① 본문 — 네 문장 모두 어느 장의 화면 줄에도 없다. 같은 장의 정상 문장은 남는다.
    body = _body_display_lines(report)
    for absence_sentence in LIVE_ABSENCE_SENTENCES:
        for cell, lines in body.items():
            assert absence_sentence not in lines, f"{cell}장에 부재 단언이 인쇄됐다: {absence_sentence}"
    assert body["competitive_position"], "9장의 정상 문장까지 사라졌다"
    assert body["culture"], "8장의 정상 문장까지 사라졌다"

    # ② 운영 진단 — 제외 사유가 장별로 남는다(원문 없이).
    rejected = [
        entry for entry in diagnostics
        if entry.get("reason_code") == ABSENCE_CLAIM_UNSUPPORTED
    ]
    counts_by_section: dict[str, int] = {}
    for entry in rejected:
        counts_by_section[entry["section_id"]] = counts_by_section.get(entry["section_id"], 0) + 1
        assert entry["kind"] == DIAGNOSTIC_KIND_BODY
    assert counts_by_section == {"culture": 2, "competitive_position": 2}, counts_by_section
    diagnostics_blob = json.dumps(diagnostics, ensure_ascii=False)
    for absence_sentence in LIVE_ABSENCE_SENTENCES:
        assert absence_sentence not in diagnostics_blob, "진단에 원문이 새어 나갔다"

    # ③ 표지 요약 — 3~5문장, 항목마다 소유 장이 있고, 실제 본문 줄을 글자 그대로 쓴다.
    #   ⚠️ 소유 장 «값»은 여기서 단정하지 않는다 — SHADOW 렌더는 인용 겹침으로
    #   장을 되짚는 휴리스틱(render._summary_source_section)이고, 이 fixture는
    #   아홉 장이 같은 조각 2를 인용해 첫 장으로 몰린다. 운영 실측 G2는 그 값이
    #   «빈 문자열»이었다는 것이 결함이므로, 비어 있지 않음만 못 박는다.
    assert SUMMARY_MIN_SENTENCES <= len(report.summary_items) <= SUMMARY_MAX_SENTENCES
    all_body_lines = {line for lines in body.values() for line in lines}
    for item in report.summary_items:
        text = _display_text(item.text)
        assert text not in LIVE_ABSENCE_SENTENCES, f"표지에 부재 단언이 실렸다: {item.text}"
        assert item.section_id, f"표지 항목의 소유 장이 비었다(운영 실측 G2 재현): {item.text}"
        assert text in all_body_lines, f"표지 항목이 본문 줄이 아니다: {item.text}"
    # 요약 고르기 프롬프트에도 그 문장이 후보로 나간 적이 없다.
    for prompt in writer.summary_prompts:
        assert LIVE_UNCITED_INTERPRETATION_COVER_04 not in prompt
        assert LIVE_CITED_ABSENCE_CLAIM_SECTION_9 not in prompt


def test_shadow_ai_selection_path_keeps_absence_claims_out_of_body_cover_and_diagnostics():
    output, writer, diagnostics = _run_shadow(summary_failure="")

    _assert_live_defects_are_gone(output, writer, diagnostics)
    assert len(writer.summary_prompts) == 1, "고르기는 정확히 1회다"
    assert output.report.publication_policy == "legacy-shadow-exception-v1"


def test_shadow_degradable_selector_limit_fills_from_verified_body_with_same_result():
    """강등 가능 한도 — legacy 단계 안에서 검증 본문 문장으로 채우는 경로."""

    output, writer, diagnostics = _run_shadow(summary_failure="degradable")

    _assert_live_defects_are_gone(output, writer, diagnostics)
    assert len(writer.summary_prompts) == 1, "한도에 걸린 뒤 다시 부르지 않는다"
    assert output.report.publication_policy == "legacy-shadow-exception-v1"


def test_shadow_fatal_selector_failure_falls_back_to_rule_summary_with_same_result():
    """강등 불가 장애 — run_v2가 `_rule_summary_stage`·확보 근거 보고서로 내려간다."""

    output, writer, diagnostics = _run_shadow(summary_failure="fatal")

    _assert_live_defects_are_gone(output, writer, diagnostics)
    assert len(writer.summary_prompts) == 1, "장애 뒤 다시 부르지 않는다"
    assert output.report.publication_policy == "evidence-available-v1"
