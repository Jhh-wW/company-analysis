# -*- coding: utf-8 -*-
"""6장 장 배치 위반 문장을 «버리지 않고 5장으로 옮기는» 실제 검증 경로.

기존 계약(`test_future_section_contract.py`)은 «미래 표지가 없는 6장 문장은
6장에 남지 않는다»만 확인한다. 이 파일은 그 문장이 «어디로 가는지»를 확인한다.

다루는 것
  ① 인용이 5장에 허용된 조각이면 5장으로 옮긴다(평문·묶음 두 경로).
  ② 인용이 5장 허용 밖이면 예전대로 제외하고 사유를 남긴다.
  ③ 6장 근거 결속에도 걸린 문장은 옮기지 않는다 — 결속 요구를 장 바꾸기로
     피해 갈 수 없다.
  ④ 5장에 이미 같은 사실이 있으면 옮기지 않는다 — «같은 사실»은 장 간 삭제와 같은
     주장절 대응 증명으로 가린다. 어투만 다른 짝은 증명이 없어 옮긴다.
  ⑤ 미래 표지가 있는 문장은 그대로 6장에 남는다.
  ⑥ 부록의 «본문 사용 장»이 5장으로 기록된다.
"""

import json
import re

import pytest

from src.features.composer.constants import (
    CHALLENGE_FLOW_SECTION_ID, GRADE_CONFIRMED, SECTION_IDS,
    STRATEGY_TABLE_SECTION_ID,
)
from src.features.composer.logic import (
    _assert_composed_report_evidence_invariant,
)
from src.features.composer.future_plan_constants import (
    FUTURE_ACTIVITY_KEY,
    FUTURE_KEY,
    FUTURE_MODE_KEY,
    FUTURE_MODE_PLAN,
    FUTURE_QUOTE_KEY,
    FUTURE_SECTION_NO_FORWARD_STATEMENT,
    FUTURE_SOURCE_KEY,
    FUTURE_TARGET_KEY,
)
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
)
from src.features.composer.render import render_report
from src.features.composer.review_outcomes import final_review_outcomes
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import (
    _apply_grounding,
    _challenge_section_prose_problem,
    _pending_moves,
    _SectionMove,
    verify_report,
    DIAGNOSTIC_KIND_BODY,
    VERDICT_TRUE,
)
from src.shared.report_quality.composition_diagnostic_constants import (
    BODY_SECTION_MOVE_STEP,
    SECTION_MOVE_BLOCKED_DUPLICATE,
    SECTION_MOVE_BLOCKED_NO_TARGET,
    SECTION_MOVE_BLOCKED_OUT_OF_EVIDENCE,
    SECTION_MOVE_BLOCKED_SOURCE_BINDING,
    SECTION_MOVE_REASONS,
)
from src.shared.report_quality.composition_diagnostics import (
    observed_composition_steps,
)

_GRADE_RE = re.compile(r"(?m)^  등급: [^\n]+\n")
FRAGMENT = "1"
OTHER_FRAGMENT = "2"

#: 실측 6장 본문 문구 — 앞으로의 이야기가 없는 현재 서술이다.
PRESENT_TEXT = (
    "회사는 텐센트 뮤직 엔터테인먼트 그룹(TME)과 합작법인 STE를 베이징에 "
    "설립하여 중화권 엔터테인먼트 시장 내 영향력 확대에 박차를 가하고 있다."
)
PRESENT_SOURCE = (
    "당사는 텐센트 뮤직 엔터테인먼트 그룹과 합작법인 STE를 베이징에 설립하여 "
    "중화권 엔터테인먼트 시장 내 영향력 확대에 박차를 가하고 있습니다."
)

#: 같은 사실을 5장이 이미 싣고 있는 꼴 — 어투만 다르다.
CHALLENGE_TWIN = (
    "회사는 텐센트 뮤직 엔터테인먼트 그룹과 합작법인 STE를 베이징에 설립해 "
    "중화권 엔터테인먼트 시장 내 영향력 확대에 힘을 쏟고 있다."
)

#: 미래 표지가 있어 6장에 그대로 남아야 하는 문장과 그 자기 원문.
PLAN_SOURCE = (
    "당사는 팬 플랫폼 사업을 운영하고 있으며 팬 플랫폼 사업을 확대할 계획입니다."
)
PLAN_TEXT = (
    "회사는 팬 플랫폼 사업을 운영하고 있으며 팬 플랫폼 사업을 확대할 계획이다."
)
PLAN_EVIDENCE = {FUTURE_KEY: [{
    FUTURE_SOURCE_KEY: FRAGMENT,
    FUTURE_TARGET_KEY: "팬 플랫폼 사업",
    FUTURE_ACTIVITY_KEY: "확대",
    FUTURE_QUOTE_KEY: PLAN_SOURCE,
    FUTURE_MODE_KEY: FUTURE_MODE_PLAN,
}]}


def _reviewer(evidence=None):
    """실제 프롬프트를 읽고 «참»을 돌려주는 가짜 검수 AI."""

    def ask(prompt):
        rows = []
        for item in review_items(_GRADE_RE.sub("", prompt)):
            row = {
                "번호": item.number,
                "장": item.section,
                "근거": [str(c).split()[-1] for c in item.citations],
                "결과": "참",
            }
            if evidence is not None:
                row["검증근거"] = json.loads(
                    json.dumps(evidence, ensure_ascii=False)
                )
            rows.append(row)
        return json.dumps({"판정": rows}, ensure_ascii=False)

    return ask


def _run(
    strategy_texts,
    *,
    grouped,
    challenge_texts=(),
    challenge_allows_fragment=True,
    fragments=None,
    evidence=None,
):
    """5장과 6장을 함께 담은 보고서를 «실제 verify_report»로 돌린다."""

    report = ComposedReport((
        ComposedSection(CHALLENGE_FLOW_SECTION_ID, tuple(
            ComposedSentence(text, (FRAGMENT,), GRADE_CONFIRMED)
            for text in challenge_texts
        )),
        ComposedSection(STRATEGY_TABLE_SECTION_ID, tuple(
            ComposedSentence(text, (FRAGMENT,), GRADE_CONFIRMED)
            for text in strategy_texts
        )),
    ))
    allowed = None
    if grouped:
        allowed = {
            CHALLENGE_FLOW_SECTION_ID: (
                frozenset((FRAGMENT,)) if challenge_allows_fragment
                else frozenset((OTHER_FRAGMENT,))
            ),
            STRATEGY_TABLE_SECTION_ID: frozenset((FRAGMENT,)),
        }
    diagnostics: list[dict] = []
    protocol: list[dict] = []
    checked = verify_report(
        report,
        fragments if fragments is not None
        else (CollectedFragment(FRAGMENT, "사업내용", PRESENT_SOURCE),),
        None,
        _reviewer(evidence),
        allowed_fragment_ids_by_section=allowed,
        diagnostics=diagnostics,
        protocol_diagnostics=protocol,
    )
    return checked, diagnostics, protocol


def _by_section(checked):
    return {
        section.section_id: [sentence.text for sentence in section.sentences]
        for section in checked.sections
    }


def _move_step(protocol):
    steps = [
        step for step in observed_composition_steps(protocol)
        if step["step"] == BODY_SECTION_MOVE_STEP
    ]
    assert len(steps) == 1, ("장 이동 기록이 실행 기록 계약을 통과하지 못했다",
                             protocol)
    return steps[0]


# ══════════════════════════════════════════════════════════════════════
# ① 인용이 5장 허용 안이면 옮긴다
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("grouped", (False, True), ids=("평문", "묶음"))
def test_미래표지_없는_6장_문장은_5장으로_옮겨진다(grouped):
    checked, diagnostics, protocol = _run([PRESENT_TEXT], grouped=grouped)
    sections = _by_section(checked)
    assert sections[STRATEGY_TABLE_SECTION_ID] == [], "6장에 그대로 남았다"
    assert sections[CHALLENGE_FLOW_SECTION_ID] == [PRESENT_TEXT], (
        "5장으로 옮겨지지 않았다"
    )
    assert final_review_outcomes(checked, diagnostics) == (), (
        "옮긴 문장을 «제외»로 적으면 화면 안내문이 빠지지 않은 문장을 뺐다고 말한다"
    )
    assert _move_step(protocol) == {
        "step": BODY_SECTION_MOVE_STEP,
        "출발장": STRATEGY_TABLE_SECTION_ID,
        "도착장": CHALLENGE_FLOW_SECTION_ID,
        "사유코드": FUTURE_SECTION_NO_FORWARD_STATEMENT,
        "이동": 1,
        "이동불가": {},
    }


@pytest.mark.parametrize("grouped", (False, True), ids=("평문", "묶음"))
def test_옮긴_뒤_5장_기존_문장_순서는_그대로고_옮긴_문장이_뒤에_붙는다(grouped):
    keep = "회사는 국내 매니지먼트 사업을 운영하고 있다."
    fragments = (
        CollectedFragment(
            FRAGMENT, "사업내용", PRESENT_SOURCE + " 당사는 국내 매니지먼트 사업을 운영하고 있습니다.",
        ),
    )
    checked, _diagnostics, _protocol = _run(
        [PRESENT_TEXT], grouped=grouped, challenge_texts=(keep,),
        fragments=fragments,
    )
    assert _by_section(checked)[CHALLENGE_FLOW_SECTION_ID] == [
        keep, PRESENT_TEXT,
    ]


# ══════════════════════════════════════════════════════════════════════
# ② 인용이 5장 허용 밖이면 예전대로 제외한다
# ══════════════════════════════════════════════════════════════════════

def test_5장_허용_근거_밖이면_옮기지_않고_제외한다():
    checked, diagnostics, protocol = _run(
        [PRESENT_TEXT], grouped=True, challenge_allows_fragment=False,
    )
    sections = _by_section(checked)
    assert sections[STRATEGY_TABLE_SECTION_ID] == []
    assert sections[CHALLENGE_FLOW_SECTION_ID] == [], (
        "5장 근거 계약을 어기면서 옮겼다 — packet 불변식이 보고서를 죽인다"
    )
    assert {row["reason_code"] for row in
            final_review_outcomes(checked, diagnostics)} == {
        FUTURE_SECTION_NO_FORWARD_STATEMENT
    }
    step = _move_step(protocol)
    assert step["이동"] == 0
    assert step["이동불가"] == {SECTION_MOVE_BLOCKED_OUT_OF_EVIDENCE: 1}


# ══════════════════════════════════════════════════════════════════════
# ③ 6장 근거 결속에도 걸린 문장은 옮기지 않는다 (결속 완화 금지)
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("grouped", (False, True), ids=("평문", "묶음"))
def test_근거_결속에도_걸린_문장은_옮기지_않고_제외한다(grouped):
    # 「…할 계획이다」라는 계획 주장을 하면서 미래근거를 하나도 대지 않은 문장.
    # 표지 뒤에서 그 계획이 부정돼 `has_forward_marker` 는 거짓이지만
    # 계획 주장 자리는 그대로 남아 근거 결속 요구가 걸린다.
    text = "회사는 신규 사업을 확대할 계획은 없으며 현재 사업을 운영하고 있다."
    source = "당사는 신규 사업을 확대할 계획은 없으며 현재 사업을 운영하고 있습니다."
    fragments = (CollectedFragment(FRAGMENT, "사업내용", source),)
    checked, diagnostics, protocol = _run(
        [text], grouped=grouped, fragments=fragments,
    )
    sections = _by_section(checked)
    assert sections[CHALLENGE_FLOW_SECTION_ID] == [], (
        "근거 결속 요구를 «장 바꾸기»로 피해 갔다"
    )
    assert sections[STRATEGY_TABLE_SECTION_ID] == []
    assert {row["reason_code"] for row in
            final_review_outcomes(checked, diagnostics)} == {
        FUTURE_SECTION_NO_FORWARD_STATEMENT
    }
    step = _move_step(protocol)
    assert step["이동"] == 0
    assert step["이동불가"] == {SECTION_MOVE_BLOCKED_SOURCE_BINDING: 1}


# ══════════════════════════════════════════════════════════════════════
# ④ 5장에 이미 같은 사실이 있으면 옮기지 않는다
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("grouped", (False, True), ids=("평문", "묶음"))
def test_5장에_같은_사실이_이미_있으면_옮기지_않는다(grouped):
    # 5장에 이미 «같은 주장 문장»이 있다 — 삭제 증명이 되는 참 중복이다.
    checked, diagnostics, protocol = _run(
        [PRESENT_TEXT], grouped=grouped, challenge_texts=(PRESENT_TEXT,),
    )
    sections = _by_section(checked)
    assert sections[CHALLENGE_FLOW_SECTION_ID] == [PRESENT_TEXT], (
        "장 «간» 중복 제거는 한 장 안의 반복을 보지 않는다 — "
        "여기서 막지 않으면 5장에 같은 말이 두 번 실린다"
    )
    assert sections[STRATEGY_TABLE_SECTION_ID] == []
    step = _move_step(protocol)
    assert step["이동"] == 0
    assert step["이동불가"] == {SECTION_MOVE_BLOCKED_DUPLICATE: 1}


@pytest.mark.parametrize("grouped", (False, True), ids=("평문", "묶음"))
def test_5장의_어투만_다른_짝은_증명이_없어_옮겨_온_문장을_버리지_않는다(grouped):
    """기대 변경 (2026-09-23 dedupe 엄밀 증명, 총괄 확정) — 종전에는 이 원래 짝을
    중복으로 막아 6장 문장을 버렸다.

    「설립하여 … 박차를 가하고 있다」↔「설립해 … 힘을 쏟고 있다」는 짝(비교 후보)은
    되지만 주장절이 어절 그대로 대응하지 않는다. 재배치는 «중복»이면 옮겨 오는
    문장을 버리므로, 증명 없는 의역은 버리지 않고 옮긴다 — 5장에 두 번 보일 수 있다.
    """
    checked, _diagnostics, protocol = _run(
        [PRESENT_TEXT], grouped=grouped, challenge_texts=(CHALLENGE_TWIN,),
    )
    sections = _by_section(checked)
    assert sections[CHALLENGE_FLOW_SECTION_ID] == [CHALLENGE_TWIN, PRESENT_TEXT]
    assert sections[STRATEGY_TABLE_SECTION_ID] == []
    step = _move_step(protocol)
    assert step["이동"] == 1
    assert step["이동불가"] == {}


# ══════════════════════════════════════════════════════════════════════
# ⑤ 미래 표지가 있으면 그대로 6장
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("grouped", (False, True), ids=("평문", "묶음"))
def test_미래표지가_있는_문장은_옮기지_않고_6장에_남는다(grouped):
    fragments = (CollectedFragment(FRAGMENT, "사업내용", PLAN_SOURCE),)
    checked, _diagnostics, protocol = _run(
        [PLAN_TEXT], grouped=grouped, fragments=fragments,
        evidence=PLAN_EVIDENCE,
    )
    sections = _by_section(checked)
    assert sections[STRATEGY_TABLE_SECTION_ID] == [PLAN_TEXT]
    assert sections[CHALLENGE_FLOW_SECTION_ID] == []
    assert [step for step in protocol
            if step.get("step") == BODY_SECTION_MOVE_STEP] == [], (
        "이동 판정 자체가 없었어야 한다"
    )


# ══════════════════════════════════════════════════════════════════════
# ⑥ 부록의 «본문 사용 장»이 5장으로 기록된다 (렌더 배선)
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("grouped", (False, True), ids=("평문", "묶음"))
def test_옮긴_문장의_부록_사용장은_5장으로_적힌다(grouped):
    fragments = (CollectedFragment(FRAGMENT, "사업내용", PRESENT_SOURCE),)
    checked, _diagnostics, _protocol = _run(
        [PRESENT_TEXT], grouped=grouped, fragments=fragments,
    )
    rendered = render_report("회사", checked, fragments, None)
    used = {
        source.number: list(source.used_in) for source in rendered.citations
    }
    assert used == {1: [CHALLENGE_FLOW_SECTION_ID]}, (
        "부록이 옮기기 «전» 장을 그대로 적고 있다"
    )


# ══════════════════════════════════════════════════════════════════════
# ⑦ 도착 장이 없는 묶음은 이동을 시도조차 하지 않는다
# ══════════════════════════════════════════════════════════════════════

def test_도착_장이_없는_묶음에서는_예전처럼_제외된다():
    report = ComposedReport((
        ComposedSection(
            STRATEGY_TABLE_SECTION_ID,
            (ComposedSentence(PRESENT_TEXT, (FRAGMENT,), GRADE_CONFIRMED),),
        ),
    ))
    diagnostics: list[dict] = []
    protocol: list[dict] = []
    checked = verify_report(
        report,
        (CollectedFragment(FRAGMENT, "사업내용", PRESENT_SOURCE),),
        None,
        _reviewer(),
        diagnostics=diagnostics,
        protocol_diagnostics=protocol,
    )
    assert list(checked.sections[0].sentences) == []
    assert {row["reason_code"] for row in
            final_review_outcomes(checked, diagnostics)} == {
        FUTURE_SECTION_NO_FORWARD_STATEMENT
    }
    assert [step for step in protocol
            if step.get("step") == BODY_SECTION_MOVE_STEP] == []


def test_옮긴_보고서가_장별_근거_불변식을_통과한다():
    """packet 엄격 경로는 검수 «직후» 이 불변식을 다시 건다(stage=post-verify).

    5장 허용 밖 조각을 가진 문장을 옮기면 보고서 생성이 그 자리에서 죽는다.
    그래서 옮긴 보고서를 실제 불변식 함수에 그대로 통과시켜 본다.
    """

    sections = []
    for section_id in SECTION_IDS:
        texts = [PRESENT_TEXT] if section_id == STRATEGY_TABLE_SECTION_ID else []
        sections.append(ComposedSection(section_id, tuple(
            ComposedSentence(text, (FRAGMENT,), GRADE_CONFIRMED)
            for text in texts
        )))
    allowed = {
        section_id: (
            frozenset((FRAGMENT,))
            if section_id in (CHALLENGE_FLOW_SECTION_ID,
                              STRATEGY_TABLE_SECTION_ID)
            else frozenset()
        )
        for section_id in SECTION_IDS
    }
    checked = verify_report(
        ComposedReport(tuple(sections)),
        (CollectedFragment(FRAGMENT, "사업내용", PRESENT_SOURCE),),
        None,
        _reviewer(),
        allowed_fragment_ids_by_section=allowed,
        diagnostics=[],
    )
    moved = {
        section.section_id: [s.text for s in section.sentences]
        for section in checked.sections
    }
    assert moved[CHALLENGE_FLOW_SECTION_ID] == [PRESENT_TEXT]
    _assert_composed_report_evidence_invariant(
        checked, allowed, frozenset((FRAGMENT,)), stage="post-verify",
    )


def test_도착_장이_없으면_옮길_자리를_만들지_않는다():
    moves = (_SectionMove(
        number=1,
        source_section_id=STRATEGY_TABLE_SECTION_ID,
        target_section_id=CHALLENGE_FLOW_SECTION_ID,
        reason_code=FUTURE_SECTION_NO_FORWARD_STATEMENT,
        blocker="",
    ),)
    positions, blocked = _pending_moves(
        (STRATEGY_TABLE_SECTION_ID,), {(0, 0): 1}, moves,
    )
    assert positions == {}
    assert blocked == {SECTION_MOVE_BLOCKED_NO_TARGET: 1}


# ══════════════════════════════════════════════════════════════════════
# ⑧ 5장 규칙 도우미가 실제 판정 경로와 같은 답을 낸다 (쌍둥이 대조)
# ══════════════════════════════════════════════════════════════════════

_TARGET_RULE_SAMPLES = (
    PRESENT_TEXT,
    CHALLENGE_TWIN,
    PLAN_TEXT,
    "회사는 원가 상승 부담에 대응해 공급처를 다변화하고 있다.",
    "회사는 임직원 복리후생 제도를 운영하고 있다.",
)


#: 문화 슬롯이 붙은 후보 — 5장 본문이어도 문화 추론 검사를 더 받는다.
_CULTURE_SLOT_SAMPLE = (
    "이러한 사업 확장은 회사가 도전적인 조직문화를 가지고 있음을 보여 준다."
)


@pytest.mark.parametrize("text", (*_TARGET_RULE_SAMPLES, _CULTURE_SLOT_SAMPLE))
@pytest.mark.parametrize("culture_candidate", (False, True),
                         ids=("일반후보", "문화슬롯후보"))
def test_5장_규칙_도우미는_실제_판정_경로와_같은_답을_낸다(text, culture_candidate):
    """`_challenge_section_prose_problem` 을 실제 `_apply_grounding` 과 맞댄다.

    두 벌로 적어 두면 한쪽만 고쳐져 «옮긴 문장만 검사를 덜 받는» 구멍이 생긴다.
    그래서 목록을 다시 세지 않고, 5장 문맥으로 실제 판정 함수를 돌려 답을 비교한다.
    """

    sources = {FRAGMENT: PRESENT_SOURCE}
    raw = json.dumps({"판정": [{"번호": 1, "결과": VERDICT_TRUE}]},
                     ensure_ascii=False)
    diagnostics: list[dict] = []
    constrained = _apply_grounding(
        raw,
        {1: VERDICT_TRUE},
        {1: (text, sources)},
        diagnostics=diagnostics,
        diagnostic_contexts={
            1: (CHALLENGE_FLOW_SECTION_ID, DIAGNOSTIC_KIND_BODY, text)
        },
        culture_candidate_numbers=frozenset((1,)) if culture_candidate
        else frozenset(),
    )
    rejected = constrained.get(1) != VERDICT_TRUE
    assert rejected == bool(_challenge_section_prose_problem(
        text, sources, culture_candidate=culture_candidate,
    )), (text, culture_candidate, diagnostics)


def test_문화_슬롯_표본이_두_문맥에서_실제로_갈린다():
    """위 대조가 «둘 다 통과»만 보고 있지 않다는 음성 대조."""

    sources = {FRAGMENT: PRESENT_SOURCE}
    assert _challenge_section_prose_problem(
        _CULTURE_SLOT_SAMPLE, sources, culture_candidate=False,
    ) == ""
    assert _challenge_section_prose_problem(
        _CULTURE_SLOT_SAMPLE, sources, culture_candidate=True,
    ) != ""


# ══════════════════════════════════════════════════════════════════════
# ⑨ 전송 계약 — 사유 코드 글자가 composer 상수와 같아야 한다
# ══════════════════════════════════════════════════════════════════════

def test_이동_사유코드는_composer_상수와_같은_글자다():
    assert SECTION_MOVE_REASONS == frozenset(
        (FUTURE_SECTION_NO_FORWARD_STATEMENT,)
    )


@pytest.mark.parametrize("record", (
    {"step": BODY_SECTION_MOVE_STEP, "출발장": STRATEGY_TABLE_SECTION_ID,
     "도착장": STRATEGY_TABLE_SECTION_ID,
     "사유코드": FUTURE_SECTION_NO_FORWARD_STATEMENT, "이동": 1, "이동불가": {}},
    {"step": BODY_SECTION_MOVE_STEP, "출발장": STRATEGY_TABLE_SECTION_ID,
     "도착장": "summary",
     "사유코드": FUTURE_SECTION_NO_FORWARD_STATEMENT, "이동": 1, "이동불가": {}},
    {"step": BODY_SECTION_MOVE_STEP, "출발장": STRATEGY_TABLE_SECTION_ID,
     "도착장": CHALLENGE_FLOW_SECTION_ID,
     "사유코드": "semantic_grounding_missing", "이동": 1, "이동불가": {}},
    {"step": BODY_SECTION_MOVE_STEP, "출발장": STRATEGY_TABLE_SECTION_ID,
     "도착장": CHALLENGE_FLOW_SECTION_ID,
     "사유코드": FUTURE_SECTION_NO_FORWARD_STATEMENT, "이동": True,
     "이동불가": {}},
    {"step": BODY_SECTION_MOVE_STEP, "출발장": STRATEGY_TABLE_SECTION_ID,
     "도착장": CHALLENGE_FLOW_SECTION_ID,
     "사유코드": FUTURE_SECTION_NO_FORWARD_STATEMENT, "이동": 1,
     "이동불가": {"내부 오류문": 1}},
    {"step": BODY_SECTION_MOVE_STEP, "출발장": STRATEGY_TABLE_SECTION_ID,
     "도착장": CHALLENGE_FLOW_SECTION_ID,
     "사유코드": FUTURE_SECTION_NO_FORWARD_STATEMENT, "이동": 0,
     "이동불가": {}},
))
def test_오염된_이동_기록은_실행_기록에서_버려진다(record):
    assert observed_composition_steps([record]) == ()
