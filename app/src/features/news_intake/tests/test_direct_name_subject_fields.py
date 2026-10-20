"""동일한 회사명·선택 원문의 중복만 허용하고 주어와 인용 경계를 유지한다."""

from collections import Counter
from copy import deepcopy

import pytest

from src.features.news_intake import constants as c
from src.features.news_intake.grounded import (
    build_grounded_prompt,
    validate_grounded_response,
)
from src.features.news_intake.quote_selection import quote_candidates
from src.features.news_intake.tests.test_collection import (
    AS_OF, BODY, COMPANY, accepted, collect, item, snapshot,
)
from src.features.news_intake.tests.test_quote_selection import selected


def response(candidate, body=BODY, *, selection=True, subject="", evidence=""):
    if selection:
        raw = selected(candidate, body)
        excerpt = raw["items"][0]["excerpts"][0]
        excerpt["subject_evidence_quote_id"] = (
            excerpt["text_quote_id"] if evidence else ""
        )
    else:
        raw = {"items": [accepted({"id": candidate.id, "body": body})]}
        excerpt = raw["items"][0]["excerpts"][0]
        excerpt["subject_evidence"] = evidence
    excerpt["subject"] = subject
    return raw


def validate(raw, candidate, body=BODY):
    details = Counter()
    result, rejected = validate_grounded_response(
        raw, articles=[(candidate, body)], company=COMPANY, as_of=AS_OF,
        subject_diagnostics=details,
    )
    return result, rejected, details


@pytest.mark.parametrize("selection", [False, True])
@pytest.mark.parametrize("subject,evidence", [
    ("가나다전자", ""),
    ("(주) 가나다전자", BODY),
    ("Ganada Electronics", BODY),
    ("", BODY),
])
def test_전체회사명과_동일원문_중복은_내용과_범위를_그대로_보존한다(
    selection, subject, evidence,
):
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    raw = response(candidate, selection=selection, subject=subject, evidence=evidence)
    original = deepcopy(raw)
    result, rejected, details = validate(raw, candidate)
    assert len(result) == 1
    assert not rejected and not details
    assert (result[0].text, result[0].span_start, result[0].span_end) == (BODY, 0, len(BODY))
    assert raw == original


@pytest.mark.parametrize("selection", [False, True])
@pytest.mark.parametrize("subject", [
    "가나다", "가나다전자의 자회사", "다른기술", "푸른칩", "이 회사", "가나다전자 대표",
])
def test_중복으로_볼_수_없는_주어는_여전히_거절한다(selection, subject):
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    raw = response(candidate, selection=selection, subject=subject, evidence=BODY)
    result, rejected, details = validate(raw, candidate)
    assert not result
    assert rejected == {"grounded_subject_missing": 1}
    assert details == {c.SUBJECT_DIRECT_NAME_EXTRA_FIELDS: 1}


@pytest.mark.parametrize("evidence", [
    BODY.replace("120", "900"),
    "가나다전자는 기업용 산업설비 제조 사업을 운영한다.",
])
def test_다른_주어근거는_같은회사명이라도_묵살하지_않는다(evidence):
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    raw = response(candidate, selection=False, subject="가나다전자", evidence=evidence)
    result, rejected, details = validate(raw, candidate, BODY + " " + evidence)
    assert not result
    assert rejected == {"grounded_subject_missing": 1}
    assert details == {c.SUBJECT_DIRECT_NAME_EXTRA_FIELDS: 1}


def test_같은기사의_다른선택ID를_주어근거로_넣어도_중복으로_보지_않는다():
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    other = "가나다전자는 기업용 산업설비 제조 사업을 운영한다."
    body = BODY + " " + other
    raw = response(candidate, body, subject="가나다전자", evidence=body)
    ids = {
        body[row["start"]:row["end"]]: row["id"]
        for row in quote_candidates(candidate, body, COMPANY)
    }
    excerpt = raw["items"][0]["excerpts"][0]
    excerpt["text_quote_id"] = ids[BODY]
    excerpt["subject_evidence_quote_id"] = ids[other]
    result, rejected, details = validate(raw, candidate, body)
    assert not result
    assert rejected == {"grounded_subject_missing": 1}
    assert details == {c.SUBJECT_DIRECT_NAME_EXTRA_FIELDS: 1}


@pytest.mark.parametrize("field", ["subject_is_target", "same_company"])
def test_강한모델의_대상법인_거절을_중복필드로_뒤집지_않는다(field):
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    raw = response(candidate, subject="가나다전자", evidence=BODY)
    row = raw["items"][0]
    (row["excerpts"][0] if field == "subject_is_target" else row)[field] = False
    assert not validate(raw, candidate)[0]


@pytest.mark.parametrize("body", [
    "가나다전자의 자회사인 별도법인 푸른기술은 산업설비 신제품을 개발해 공급했다.",
    "행사 참석 기업은 가나다전자와 다른기술이다. 다른기술은 산업설비 신제품을 개발해 공급했다.",
    "기사 제공: 가나다전자. 다른기술은 산업설비 신제품을 개발해 공급했다.",
])
def test_회사명_중복이_타법인_행동을_대상회사에_귀속시키지_않는다(body):
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    raw = response(candidate, body, subject="가나다전자", evidence=body)
    assert not validate(raw, candidate, body)[0]


def test_원문선택_수집은_재호출없이_중복필드를_검증한다():
    snap, _ = snapshot([item()])
    calls = []

    def analyze(prompt, schema, max_tokens):
        calls.append(schema)
        return response(snap.candidates[0], subject="가나다전자", evidence=BODY)

    result = collect(analyze=analyze)
    assert len(result.fragments) == len(calls) == 1
    assert result.fragments[0].text == BODY
    assert result.diagnostics.get("주어결속상세", {}) == {}


def test_선택프롬프트가_실제필드명의_빈값과_별도주어판정을_구분한다():
    snap, _ = snapshot([item()])
    prompt = build_grounded_prompt(COMPANY, [(snap.candidates[0], BODY)], AS_OF, selection=True)
    assert "subject와 subject_evidence_quote_id는 모두 빈 문자열" in prompt
    assert "subject_is_target 판정은 생략하지 마세요" in prompt
