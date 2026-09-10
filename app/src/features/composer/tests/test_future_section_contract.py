# -*- coding: utf-8 -*-
"""미래 장에서 현재 전용 본문을 제외하고 정상 계획을 보존하는 실제 검증 경로.

SM 후보 문구는 실측이며 인용 조각은 장 배치만 검사하기 위한 대역이다.
이 파일은 생성 당시 공식 원문을 복원하거나 사실성을 증명하는 시험이 아니다.
"""

import json
import re

import pytest

from src.features.composer.constants import (
    GRADE_CONFIRMED, STRATEGY_TABLE_SECTION_ID,
)
from src.features.composer.future_plan_constants import (
    FUTURE_ACTIVITY_KEY,
    FUTURE_KEY,
    FUTURE_MODE_KEY,
    FUTURE_MODE_OUTLOOK,
    FUTURE_MODE_PLAN,
    FUTURE_QUOTE_KEY,
    FUTURE_SECTION_NO_FORWARD_STATEMENT,
    FUTURE_SOURCE_KEY,
    FUTURE_TARGET_KEY,
)
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
)
from src.features.composer.review_outcomes import final_review_outcomes
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import NOTICE_ALL_SENTENCES_REJECTED, verify_report

_GRADE_RE = re.compile(r"(?m)^  등급: [^\n]+\n")
FRAGMENT = "1"


# ══════════════════════════════════════════════════════════════════════
# 사례 — (문장, 그 문장을 뒷받침하는 자기 원문, 미래근거 배열 또는 None)
# ══════════════════════════════════════════════════════════════════════

#: 새 SM 실행 6장 본문의 «실측 문구 그대로». 둘 다 앞으로의 이야기가 없다.
#: sha256(앞16): 334a7785aad722cb / a9a849a67668702d
SM_PRESENT = (
    (
        "회사는 텐센트 뮤직 엔터테인먼트 그룹(TME)과 합작법인 STE를 베이징에 "
        "설립하여 중화권 엔터테인먼트 시장 내 영향력 확대에 박차를 가하고 있다.",
        "당사는 텐센트 뮤직 엔터테인먼트 그룹과 합작법인 STE를 베이징에 설립하여 "
        "중화권 엔터테인먼트 시장 내 영향력 확대에 박차를 가하고 있습니다.",
    ),
    (
        "회사의 종속회사 에스엠컬처앤콘텐츠는 K-Pop 글로벌 팬들이 공연을 관람하고 "
        "한국 문화를 향유하는 'K컬처 여행 상품'을 런칭하여 여행 사업 매출 증대를 "
        "도모하고 있다.",
        "당사의 종속회사 에스엠컬처앤콘텐츠는 K-Pop 글로벌 팬들이 공연을 관람하고 "
        "한국 문화를 향유하는 K컬처 여행 상품을 런칭하여 여행 사업 매출 증대를 "
        "도모하고 있습니다.",
    ),
)

#: 「출시·추진·목표·계획」이 «이미 한 일»에 붙은 꼴 — 앞으로의 약속이 아니다.
COMPLETED_PRESENT = (
    (
        "회사는 지난해 신규 여행 상품을 출시하여 현재 판매하고 있다.",
        "당사는 지난해 신규 여행 상품을 출시하여 현재 판매하고 있습니다.",
    ),
    (
        "회사는 지난해 추진한 글로벌 유통 협업을 완료했다.",
        "당사는 지난해 추진한 글로벌 유통 협업을 완료했습니다.",
    ),
    (
        "회사는 연간 매출 목표를 달성했다.",
        "당사는 연간 매출 목표를 달성했습니다.",
    ),
    (
        "회사는 신규 라인업 확대 계획을 완료했다.",
        "당사는 신규 라인업 확대 계획을 완료했습니다.",
    ),
)


def _evidence(target, activity, quote, mode=FUTURE_MODE_PLAN):
    return {FUTURE_KEY: [{
        FUTURE_SOURCE_KEY: FRAGMENT,
        FUTURE_TARGET_KEY: target,
        FUTURE_ACTIVITY_KEY: activity,
        FUTURE_QUOTE_KEY: quote,
        FUTURE_MODE_KEY: mode,
    }]}


#: 살아야 하는 것 — (이름, 문장, 자기 원문, 미래근거).
#: 미래근거의 대상·활동·양태는 그 원문에 실제로 있는 말로 맞췄다.
_PLAN_SOURCE = "당사는 팬 플랫폼 사업을 운영하고 있으며 팬 플랫폼 사업을 확대할 계획입니다."
_SUBSIDIARY_SOURCE = "종속회사 에스엠컬처앤콘텐츠는 여행 상품을 확대할 계획입니다."
_OUTLOOK_SOURCE = "글로벌 팬덤 시장은 지속적으로 성장할 것으로 전망됩니다."

PRESERVED = (
    (
        "현재사업과 명시적 향후 단계",
        "회사는 팬 플랫폼 사업을 운영하고 있으며 팬 플랫폼 사업을 확대할 계획이다.",
        _PLAN_SOURCE,
        _evidence("팬 플랫폼 사업", "확대", _PLAN_SOURCE),
    ),
    (
        "종속회사 계획",
        "종속회사 에스엠컬처앤콘텐츠는 여행 상품을 확대할 계획이다.",
        _SUBSIDIARY_SOURCE,
        _evidence("여행 상품", "확대", _SUBSIDIARY_SOURCE),
    ),
    (
        "산업 전망 배경",
        "글로벌 팬덤 시장은 지속적으로 성장할 것으로 전망된다.",
        _OUTLOOK_SOURCE,
        _evidence("팬덤 시장", "성장", _OUTLOOK_SOURCE, mode=FUTURE_MODE_OUTLOOK),
    ),
)


# ══════════════════════════════════════════════════════════════════════
# 하네스 — 실제 verify_report 만 쓴다
# ══════════════════════════════════════════════════════════════════════

def _reviewer(evidence=None):
    """실제 프롬프트를 읽고 «참»과 지정한 검증근거를 돌려주는 가짜 검수 AI."""

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
                row["검증근거"] = json.loads(json.dumps(evidence, ensure_ascii=False))
            rows.append(row)
        return json.dumps({"판정": rows}, ensure_ascii=False)

    return ask


def _run(texts, source, *, section=STRATEGY_TABLE_SECTION_ID, grouped=False,
         evidence=None, diagnostics=None):
    sentences = tuple(
        ComposedSentence(text, (FRAGMENT,), GRADE_CONFIRMED) for text in texts
    )
    report = ComposedReport((ComposedSection(section, sentences),))
    allowed = {section: frozenset((FRAGMENT,))} if grouped else None
    return verify_report(
        report,
        (CollectedFragment(FRAGMENT, "사업내용", source),),
        None,
        _reviewer(evidence),
        allowed_fragment_ids_by_section=allowed,
        diagnostics=diagnostics if diagnostics is not None else [],
    )


def _kept(checked):
    return [sentence.text for sentence in checked.sections[0].sentences]


def _reasons(checked, diagnostics):
    return {row["reason_code"] for row in final_review_outcomes(checked, diagnostics)}


# ══════════════════════════════════════════════════════════════════════
# ① 실측 SM 두 문장 — 정확한 사유로 빠진다
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("text,source", SM_PRESENT, ids=("STE설립", "K컬처런칭"))
@pytest.mark.parametrize("grouped", (False, True), ids=("평문", "묶음"))
def test_the_measured_present_prose_is_dropped_with_the_section_reason(
    text, source, grouped
):
    diagnostics: list[dict] = []
    checked = _run([text], source, grouped=grouped, diagnostics=diagnostics)
    assert _kept(checked) == [], "앞으로의 이야기가 없는 현재 서술이 6장에 남았다"
    # 자기 원문을 함께 줬으므로 근거 부족이 아니라 «장 계약»이 사유여야 한다.
    assert _reasons(checked, diagnostics) == {FUTURE_SECTION_NO_FORWARD_STATEMENT}


# ══════════════════════════════════════════════════════════════════════
# ② 완료·출시·목표달성·계획완료 네 사례 — 낱말이 있어도 빠진다
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "text,source", COMPLETED_PRESENT,
    ids=("출시하여판매중", "추진한협업완료", "목표달성", "계획완료"),
)
@pytest.mark.parametrize("grouped", (False, True), ids=("평문", "묶음"))
def test_a_completed_statement_is_dropped_even_with_a_forward_looking_noun(
    text, source, grouped
):
    diagnostics: list[dict] = []
    checked = _run([text], source, grouped=grouped, diagnostics=diagnostics)
    assert _kept(checked) == [], text
    assert _reasons(checked, diagnostics) == {FUTURE_SECTION_NO_FORWARD_STATEMENT}


# ══════════════════════════════════════════════════════════════════════
# ③ 정상 미래 문장 — 실제 배선을 지나 «그대로» 남는다
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize(
    "name,text,source,evidence", PRESERVED,
    ids=("현재+향후단계", "종속회사계획", "산업전망"),
)
@pytest.mark.parametrize("grouped", (False, True), ids=("평문", "묶음"))
def test_a_forward_looking_sentence_survives_verify_report(
    name, text, source, evidence, grouped
):
    diagnostics: list[dict] = []
    checked = _run([text], source, grouped=grouped, evidence=evidence,
                   diagnostics=diagnostics)
    assert _kept(checked) == [text], (name, _reasons(checked, diagnostics))


# ══════════════════════════════════════════════════════════════════════
# ④ 한 장에 섞여 있으면 문장 단위로 갈린다
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("grouped", (False, True), ids=("평문", "묶음"))
def test_only_the_present_only_sentence_leaves_the_mixed_section(grouped):
    keep_text, keep_source, evidence = (
        PRESERVED[0][1], PRESERVED[0][2], PRESERVED[0][3]
    )
    drop_text = COMPLETED_PRESENT[0][0]
    source = keep_source + " " + COMPLETED_PRESENT[0][1]
    checked = _run([keep_text, drop_text], source, grouped=grouped,
                   evidence=evidence)
    assert _kept(checked) == [keep_text]


# ══════════════════════════════════════════════════════════════════════
# ⑤ 다른 장은 같은 문장을 그대로 싣는다
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("text,source", SM_PRESENT, ids=("STE설립", "K컬처런칭"))
@pytest.mark.parametrize(
    "section", ("business_model", "operations_partners"),
    ids=("사업구조", "운영파트너"),
)
def test_other_sections_keep_the_same_present_prose(text, source, section):
    checked = _run([text], source, section=section)
    assert _kept(checked) == [text]


# ══════════════════════════════════════════════════════════════════════
# ⑥ 6장이 통째로 비면 «자료 없음»이 아니라 검증 안내가 붙는다
# ══════════════════════════════════════════════════════════════════════

def test_the_section_notice_appears_when_every_sentence_is_dropped():
    texts = [text for text, _source in SM_PRESENT]
    source = " ".join(src for _text, src in SM_PRESENT)
    checked = _run(texts, source)
    section = checked.sections[0]
    assert list(section.sentences) == []
    assert section.notice == NOTICE_ALL_SENTENCES_REJECTED
