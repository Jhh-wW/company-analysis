# -*- coding: utf-8 -*-
"""실적표 예외는 «검증된 수치 결속»에만 준다 — 선언만으로는 주지 않는다.

전부 `verify_report`·`verify_sentences` 를 지난다. 가드 함수를 직접 부르지 않으므로
배선이 빠지거나 예외가 넓어지면 여기서 깨진다.

★ 왜 이 파일이 필요한가 (실측 반례) — 예외를 「검수 응답이 결속 항목 어딘가에
  근거로 실적표를 적었으면」으로 두었더니, 「미래근거: [{근거: 실적표}]」 한 줄만
  붙여도 예외가 열렸다. 그 배열은 `grounding_problem` 이 «모양만» 보고 넘기므로
  아무것도 증명하지 않는다. 실제 SM F1·F3·F4 가 평문·묶음·요약 × 표 유무
  18가지에서 전부 그대로 공개됐다.
★ 반대로 «수치» 배열은 `_numeric_valid` 가 원문에 실제로 결속한다. 그 결속이
  실적표 원문에 걸렸을 때만 이 검사가 물러난다 — 그때는 수치 결속 계약이 본다.
"""

import json
import re

import pytest

from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer.grounding_constants import TABLE_SOURCE_ID
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
    PerformanceTable,
)
from src.features.composer.prose_own_source_constants import (
    PROSE_OWN_SOURCE_REASON_CODES,
)
from src.features.composer.review_outcomes import final_review_outcomes
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report, verify_sentences

_GRADE_RE = re.compile(r"(?m)^  등급: [^\n]+\n")

SECTION = "business_model"
#: 실적표 결속 원문이 실제로 실리는 장. 묶음(packet) 경로는 이 장에만 표를 준다 —
#: 그래서 표 수치를 근거로 드는 후보는 이 장에 둔다(기존 동작이며 이번 변경 밖이다).
TABLE_SECTION = "past_changes"
FRAGMENT = "1"
#: 후보 문장과 낱말이 거의 겹치지 않는 자기 원문 — 근거어 계약 최소치에 못 미친다.
SOURCE = "당사는 서울특별시에 본점을 두고 있습니다."
#: 자기 원문이 뒷받침하지 않는 «확인» 산문. 수치가 없어 수치 결속 대상도 아니다.
UNBACKED = "회사는 글로벌 팬덤 커머스에서 압도적 경쟁우위를 보유한다."

#: 3개년 실적표. 억원 단위로 공개 표시값을 쓴다.
TABLE = PerformanceTable(
    caption="3개년 주요 실적",
    headers=("항목", "2022", "2023", "2024"),
    rows=(("매출액", "1,500", "1,600", "1,683"),),
    unit="억원",
    cite="조각 1·사업내용",
)
#: 실적표 셀에만 있는 값을 쓴 «수치» 후보. 자기 인용 조각에는 이 숫자가 없다.
NUMERIC_PROSE = "매출 규모는 1,683억원대다."


def _fragments():
    return (CollectedFragment(FRAGMENT, "사업내용", SOURCE),)


def _echoed(item):
    return [str(citation).split()[-1] for citation in item.citations]


def _reviewer(evidence_for):
    """실제 프롬프트를 읽고 «참»과 지정한 검증근거를 돌려주는 가짜 검수 AI."""

    def ask(prompt):
        rows = []
        for item in review_items(_GRADE_RE.sub("", prompt)):
            entry = {"번호": item.number, "장": item.section,
                     "근거": _echoed(item), "결과": "참"}
            evidence = evidence_for(item.text.strip())
            if evidence is not None:
                entry["검증근거"] = evidence
            rows.append(entry)
        return json.dumps({"판정": rows}, ensure_ascii=False)

    return ask


def _run(sentence, ask, *, grouped, table, diagnostics, section=SECTION):
    report = ComposedReport((ComposedSection(section, (sentence,)),))
    allowed = {section: frozenset((FRAGMENT,))} if grouped else None
    return verify_report(
        report, _fragments(), table, ask,
        allowed_fragment_ids_by_section=allowed, diagnostics=diagnostics,
    )


def _kept(checked):
    return [sentence.text for sentence in checked.sections[0].sentences]


def _table_declaration(key):
    """검증 없이 «표를 근거로 들었다»고 적기만 한 배열."""

    return lambda text: {key: [{"근거": TABLE_SOURCE_ID}]}


#: 검증기가 «모양만» 보고 넘기는 배열들. 여기 표를 적어도 예외가 되면 안 된다.
_SHAPE_ONLY_KEYS = ("미래근거", "관계")


# ── 선언만으로는 예외가 열리지 않는다 ──────────────────────────────────
@pytest.mark.parametrize("key", _SHAPE_ONLY_KEYS)
@pytest.mark.parametrize("grouped", (False, True))
@pytest.mark.parametrize("table", (None, TABLE))
def test_a_bare_table_declaration_does_not_switch_the_check_off(key, grouped, table):
    """「미래근거·관계 배열에 근거: 실적표」만 적은 응답으로는 통과하지 못한다."""

    diagnostics: list[dict] = []
    checked = _run(
        ComposedSentence(UNBACKED, (FRAGMENT,), GRADE_CONFIRMED),
        _reviewer(_table_declaration(key)),
        grouped=grouped, table=table, diagnostics=diagnostics,
    )
    assert UNBACKED not in _kept(checked), (
        f"검증하지 않는 «{key}» 배열의 표 선언만으로 자기 원문 근거 검사가 꺼졌다"
    )
    reasons = {row["reason_code"] for row in final_review_outcomes(checked, diagnostics)}
    assert reasons & set(PROSE_OWN_SOURCE_REASON_CODES), reasons


@pytest.mark.parametrize("key", _SHAPE_ONLY_KEYS)
def test_the_summary_path_also_ignores_a_bare_table_declaration(key):
    kept = verify_sentences(
        (ComposedSentence(UNBACKED, (FRAGMENT,), GRADE_CONFIRMED),),
        _fragments(), TABLE, _reviewer(_table_declaration(key)), diagnostics=[],
    )
    assert [sentence.text for sentence in kept] == []


@pytest.mark.parametrize("grouped", (False, True))
def test_without_a_table_the_check_still_applies(grouped):
    """표가 아예 없는 보고서에서도 판정이 같아야 한다."""

    diagnostics: list[dict] = []
    checked = _run(
        ComposedSentence(UNBACKED, (FRAGMENT,), GRADE_CONFIRMED),
        _reviewer(lambda text: None),
        grouped=grouped, table=None, diagnostics=diagnostics,
    )
    assert UNBACKED not in _kept(checked)


# ── 검증된 수치 결속이 표 원문에 걸리면 예외를 준다 ────────────────────
def _valid_numeric_evidence(text):
    """실적표 셀에 실제로 있는 값으로 결속한 «수치» 배열."""

    if text != NUMERIC_PROSE:
        return None
    return {"수치": [{
        "표현": "매출 규모는 1,683억원",
        "항목": "매출 규모",
        "근거": TABLE_SOURCE_ID,
        "원문": "매출액 | 2024년 | 1,683억원",
        "원문항목": "매출액",
        "원문값": "1,683억원",
    }]}


@pytest.mark.parametrize("grouped", (False, True))
def test_a_validated_numeric_binding_on_the_table_is_exempt(grouped):
    """표 수치가 근거인 후보는 낱말 겹침이 아니라 수치 결속 계약이 본다.

    이 문장의 자기 인용 원문에는 「1,683」이 없다 — 실적표 셀에만 있다.
    기존 계약(`test_verify::실적표_수치도_근거로_인정된다`)을 그대로 지킨다.
    """

    diagnostics: list[dict] = []
    checked = _run(
        ComposedSentence(NUMERIC_PROSE, (FRAGMENT,), GRADE_CONFIRMED),
        _reviewer(_valid_numeric_evidence),
        grouped=grouped, table=TABLE, diagnostics=diagnostics,
        section=TABLE_SECTION,
    )
    assert _kept(checked) == [NUMERIC_PROSE]
    assert checked.sections[0].sentences[0].grade == GRADE_CONFIRMED
    reasons = {row["reason_code"] for row in final_review_outcomes(checked, diagnostics)}
    assert not (reasons & set(PROSE_OWN_SOURCE_REASON_CODES)), reasons


def test_the_summary_path_keeps_a_validated_table_number():
    kept = verify_sentences(
        (ComposedSentence(NUMERIC_PROSE, (FRAGMENT,), GRADE_CONFIRMED),),
        _fragments(), TABLE, _reviewer(_valid_numeric_evidence), diagnostics=[],
    )
    assert [sentence.text for sentence in kept] == [NUMERIC_PROSE]


def test_a_numeric_binding_on_another_source_does_not_open_the_table_exemption():
    """수치 결속이라도 근거가 실적표가 아니면 이 예외를 주지 않는다."""

    source = "당사의 2024년 매출액은 1,683억원입니다."
    diagnostics: list[dict] = []
    report = ComposedReport((ComposedSection(
        SECTION, (ComposedSentence(UNBACKED, (FRAGMENT,), GRADE_CONFIRMED),)),))
    checked = verify_report(
        report, (CollectedFragment(FRAGMENT, "사업내용", source),), TABLE,
        _reviewer(lambda text: {"수치": [{
            "표현": "매출액은 1,683억원",
            "항목": "매출액",
            "근거": FRAGMENT,
            "원문": source,
            "원문항목": "매출액",
            "원문값": "1,683억원",
        }]}),
        diagnostics=diagnostics,
    )
    assert UNBACKED not in _kept(checked)
