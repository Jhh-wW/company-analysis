# -*- coding: utf-8 -*-
"""검수 AI 가 참·애매를 줘도 역할·과금 결속을 각 공개 경로에서 다시 확인한다.

여기서 부르는 것은 가드 함수가 아니라 «실제 진입점»이다 — verify_report(평문·묶음),
verify_sentences(요약), check_diagrams(구형 단독 도식). 검수 응답도 실제 프롬프트를
읽어서 만든다. fixture 는 외부 정보가 없는 작은 문장뿐이다.
"""

import json
import re

import pytest

from src.features.composer.diagram_check import check_diagrams
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, FlowRow,
)
from src.features.composer.review_outcomes import final_review_outcomes
from src.features.composer.role_binding_constants import (
    ROLE_BINDING_MISSING,
    ROLE_BINDING_REVIEW_GUIDE,
    ROLE_BINDING_UNBOUND_IN_SOURCE,
)
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import (
    REWRITE_PROMPT_HEADER, verify_report, verify_sentences,
)


MADE_SOURCE = "가람은 음반을 제작합니다."
SOLD_SOURCE = "나래는 음반을 판매합니다."
GOOD_TEXT = "가람이 음반을 제작한다."
BAD_TEXT = "나래가 음반을 제작한다."
SECTION = "operations_partners"
#: 묶음 packet 프롬프트만 끼워 넣는 등급 줄. fixture 파서 계약에 맞춰 지운다.
_ITEM_GRADE_RE = re.compile(r"(?m)^  등급: [^\n]+\n")


def _fragments():
    return (
        CollectedFragment("made", "제작 설명", MADE_SOURCE),
        CollectedFragment("sold", "판매 설명", SOLD_SOURCE),
    )


def _binding(citation):
    """검수 AI 가 «자기 인용»으로 낸 결속 항목. 원문은 그 조각을 그대로 옮긴다."""

    return {
        "유형": "역할", "대상": "음반", "역할값": "제작", "근거": citation,
        "원문": MADE_SOURCE if citation == "made" else SOLD_SOURCE,
    }


def _reviewer(calls, verdict="참", *, omit_binding=False, garbage_first=False,
              duplicate=False):
    def ask(prompt):
        calls.append(prompt)
        if garbage_first and len(calls) == 1:
            return "형식이 아닌 응답"
        rows = []
        # 묶음 packet 프롬프트는 등급 줄을 끼워 넣는다. fixture 파서 계약에 맞춰 지운다.
        for item in review_items(_ITEM_GRADE_RE.sub("", prompt)):
            entry = {"번호": item.number, "장": item.section,
                     "근거": list(item.citations), "결과": verdict}
            if not omit_binding and item.citations:
                entry["검증근거"] = {"관계": [_binding(item.citations[0])]}
            rows.append(entry)
            if duplicate:
                # 같은 번호를 한 번 더 — 어느 쪽이 그 후보의 근거인지 정할 수 없다.
                rows.append(dict(entry, 검증근거={"관계": [_binding("made")]}))
        return json.dumps({"판정": rows}, ensure_ascii=False)
    return ask


def _body_report():
    sentences = (
        ComposedSentence(GOOD_TEXT, ("made",), "확인"),
        ComposedSentence(BAD_TEXT, ("sold",), "확인"),
    )
    return ComposedReport((ComposedSection(SECTION, sentences),))


def _kept(report):
    return [sentence.text for sentence in report.sections[0].sentences]


# ── 본문: 평문 · 묶음 · 참 · 애매 ────────────────────────────────────
@pytest.mark.parametrize("grouped", (False, True))
@pytest.mark.parametrize("verdict", ("참", "애매"))
def test_body_drops_the_unbound_role_and_keeps_the_bound_one(grouped, verdict):
    calls, diagnostics = [], []
    checked = verify_report(
        _body_report(), _fragments(), None, _reviewer(calls, verdict),
        diagnostics=diagnostics,
        allowed_fragment_ids_by_section=(
            {SECTION: frozenset(("made", "sold"))} if grouped else None),
    )
    kept = _kept(checked)
    assert BAD_TEXT not in kept
    assert GOOD_TEXT in kept
    assert len(calls) == 1
    assert "추가 검증 필요: 관계" in calls[0]
    assert ROLE_BINDING_REVIEW_GUIDE in calls[0]
    reasons = {item["reason_code"] for item in final_review_outcomes(checked, diagnostics)}
    assert ROLE_BINDING_UNBOUND_IN_SOURCE in reasons


@pytest.mark.parametrize("grouped", (False, True))
def test_body_without_any_binding_drops_both_role_claims(grouped):
    calls, diagnostics = [], []
    checked = verify_report(
        _body_report(), _fragments(), None,
        _reviewer(calls, omit_binding=True), diagnostics=diagnostics,
        allowed_fragment_ids_by_section=(
            {SECTION: frozenset(("made", "sold"))} if grouped else None),
    )
    assert _kept(checked) == []
    reasons = {item["reason_code"] for item in final_review_outcomes(checked, diagnostics)}
    assert reasons == {ROLE_BINDING_MISSING}


def test_duplicate_numbers_invalidate_the_binding():
    """같은 번호가 두 번 오면 «마지막이 이기지» 않는다 — 근거 없음으로 막는다."""

    calls, diagnostics = [], []
    checked = verify_report(
        _body_report(), _fragments(), None, _reviewer(calls, duplicate=True),
        diagnostics=diagnostics,
    )
    assert _kept(checked) == []
    assert {item["reason_code"] for item in final_review_outcomes(checked, diagnostics)} == {
        ROLE_BINDING_MISSING}


def test_format_retry_does_not_skip_the_guard():
    calls, diagnostics = [], []
    checked = verify_report(
        _body_report(), _fragments(), None,
        _reviewer(calls, garbage_first=True), diagnostics=diagnostics,
    )
    assert len(calls) == 2
    assert BAD_TEXT not in _kept(checked)
    assert GOOD_TEXT in _kept(checked)


def test_rewrite_after_a_false_verdict_cannot_smuggle_the_role_back():
    """«거짓» 판정의 재작성 기회로 결속 없는 역할 주장을 되살리지 못한다."""

    rewritten = "나래가 음반을 제작하고 유통한다."
    calls = []

    def ask(prompt):
        calls.append(prompt)
        if REWRITE_PROMPT_HEADER in prompt:
            return rewritten
        rows = []
        for item in review_items(_ITEM_GRADE_RE.sub("", prompt)):
            # 첫 검수만 BAD 를 거짓으로 본다. 재검수 프롬프트에는 고친 문장이 실린다.
            verdict = "거짓" if item.text == BAD_TEXT else "참"
            entry = {"번호": item.number, "장": item.section,
                     "근거": list(item.citations), "결과": verdict}
            if verdict == "참" and item.citations:
                entry["검증근거"] = {"관계": [_binding(item.citations[0])]}
            rows.append(entry)
        return json.dumps({"판정": rows}, ensure_ascii=False)

    checked = verify_report(_body_report(), _fragments(), None, ask)
    kept = _kept(checked)
    assert GOOD_TEXT in kept
    assert BAD_TEXT not in kept and rewritten not in kept
    assert any(REWRITE_PROMPT_HEADER in prompt for prompt in calls)


def test_summary_path_applies_the_same_contract():
    calls = []
    kept = verify_sentences(
        (ComposedSentence(GOOD_TEXT, ("made",), "확인"),
         ComposedSentence(BAD_TEXT, ("sold",), "확인")),
        _fragments(), None, _reviewer(calls),
    )
    assert [sentence.text for sentence in kept] == [GOOD_TEXT]
    assert len(calls) == 1


# ── 도식: 묶음 packet 과 구형 단독 경로 ──────────────────────────────
def _flow_rows():
    # 두 줄의 칸이 같으면 진단 지문이 겹쳐 «살아남은 줄»로 상쇄된다. 도착 칸을 달리한다.
    return (
        FlowRow(("음반", "제작", "청취자"), ("made",)),
        FlowRow(("음반", "제작", "구매자"), ("sold",)),
    )


def test_grouped_packet_diagram_uses_the_row_cells():
    rows = _flow_rows()
    report = ComposedReport((ComposedSection(SECTION, (), flow_rows=rows),))
    calls, diagnostics = [], []
    checked = verify_report(
        report, _fragments(), None, _reviewer(calls), diagnostics=diagnostics,
        allowed_fragment_ids_by_section={SECTION: frozenset(("made", "sold"))},
    )
    assert checked.sections[0].flow_rows == (rows[0],)
    assert "추가 검증 필요: 관계" in calls[0]
    assert {item["reason_code"] for item in final_review_outcomes(checked, diagnostics)} == {
        ROLE_BINDING_UNBOUND_IN_SOURCE}


def test_legacy_standalone_diagram_keeps_the_guard():
    rows = _flow_rows()
    report = ComposedReport((ComposedSection(SECTION, (), flow_rows=rows),))
    calls, diagnostics = [], []

    def ask(prompt):
        calls.append(prompt)
        return json.dumps({"판정": [
            {"번호": 1, "결과": "참", "검증근거": {"관계": [_binding("made")]}},
            {"번호": 2, "결과": "참", "검증근거": {"관계": [_binding("sold")]}},
        ]}, ensure_ascii=False)

    checked, problems = check_diagrams(report, _fragments(), ask, diagnostics=diagnostics)
    assert checked.sections[0].flow_rows == (rows[0],)
    assert len(problems) == 1 and ROLE_BINDING_UNBOUND_IN_SOURCE in problems[0]
    assert ROLE_BINDING_REVIEW_GUIDE in calls[0]
    assert "추가 검증 필요: 관계" in calls[0]


# ── 기존 fixture 에서 «미증명이라 고친» 행을 원본 그대로 보존한다 ────────
#
# ★ 가드를 켠 뒤 골든·이음매·manifest fixture 의 도식 행 세 개가 떨어졌다. 원인은
#   전부 «그 행이 자기 인용 조각이 밝힌 적 없는 역할·반복을 적었다»는 한 가지였고,
#   각 fixture 는 그 시험의 목적(레이아웃·화면 사슬·공개 manifest 왕복)을 지키는 선에서
#   미증명 낱말만 뺐다. 원문은 손대지 않았다.
# 고치기 전 행에 결속 근거가 없는 응답을 주어, 근거 누락 시 제외되는지 확인한다.
# 이 시험은 원문의 의미 결속을 별도로 검증하거나 원본 fixture 파일의 변경을 감지하지 않는다.
_UNPROVEN_FIXTURE_ROWS = (
    # ① jyp 골든 7장 3행. 조각 7은 「공연 인프라는 Live Nation과 … 협력한다」만 밝힌다.
    (
        ("공연 기획", "Live Nation 공연 인프라", "공연 관람객"),
        "캐스팅·트레이닝과 콘텐츠 기획·핵심 제작은 내부에서 수행하고, 음반 유통은 "
        "Republic Records·Sony Music과, 공연 인프라는 Live Nation과, MD 해외 판매는 "
        "Merch Traffic과 협력한다. MD 자회사 Blue Garage는 100% 자회사다.",
    ),
    # ② 이음매 시험 2장 행. 조각 3은 제작 역할도 후속·반복 재발생도 밝히지 않는다.
    (
        ("음반·음원·공연·MD 등 아티스트 IP", "레이블 통합 제작·유통",
         "팬이 음원·공연·MD에 지불", "음원 스트리밍·재공연·후속 MD 반복 매출"),
        "회사는 음반·음원, 공연, MD·라이선싱, 광고·출연 부문에서 수익을 얻으며, "
        "2025년 연결 매출에서 음악·공연·MD 핵심 3축의 합계가 77.3%를 차지한다.",
    ),
    # ③ 공개 manifest 시험의 자리표시 행. 합성 조각에 「반복」이 없다.
    (
        ("핵심 자산", "핵심 제품", "기업 고객", "반복 수익"),
        "회사 사업 고객 제품 전략 운영 문화 경쟁 과제 대응 협력 실적을 공식 자료에서 확인했다.",
    ),
)


@pytest.mark.parametrize("cells,source", _UNPROVEN_FIXTURE_ROWS)
def test_unproven_fixture_rows_without_binding_are_excluded(cells, source):
    rows = (FlowRow(tuple(cells), ("only",)),)
    report = ComposedReport((ComposedSection(SECTION, (), flow_rows=rows),))
    fragments = (CollectedFragment("only", "사업내용", source),)

    def ask(_prompt):
        return json.dumps({"판정": [{"번호": 1, "결과": "참"}]}, ensure_ascii=False)

    checked, problems = check_diagrams(report, fragments, ask)
    assert checked.sections[0].flow_rows == ()
    assert len(problems) == 1 and ROLE_BINDING_MISSING in problems[0]


def test_legacy_standalone_diagram_without_binding_drops_every_role_row():
    rows = _flow_rows()
    report = ComposedReport((ComposedSection(SECTION, (), flow_rows=rows),))
    diagnostics = []

    def ask(_prompt):
        return json.dumps({"판정": [{"번호": 1, "결과": "참"},
                                  {"번호": 2, "결과": "참"}]}, ensure_ascii=False)

    checked, problems = check_diagrams(report, _fragments(), ask, diagnostics=diagnostics)
    assert checked.sections[0].flow_rows == ()
    assert len(problems) == 2
    assert all(ROLE_BINDING_MISSING in problem for problem in problems)
