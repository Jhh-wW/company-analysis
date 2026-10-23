"""영문 발언도 대상 회사 귀속과 정확 원문 선택을 유지한다. 모델 호출은 없다."""

from copy import deepcopy
from dataclasses import replace
import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from src.features.news_intake.attributed_statement import has_attributed_statement
from src.features.news_intake.grounded import validate_grounded_response
from src.features.news_intake.models import NewsCandidate, NewsCompanyContext
from src.features.news_intake.quote_selection import quote_candidates
from src.features.news_intake import quote_selection_constants as qc


FIXTURE = json.loads((Path(__file__).parent / "fixtures/english_company_statement.json").read_text(encoding="utf-8"))
COMPANY = NewsCompanyContext(**{
    **FIXTURE["company"], "aliases": tuple(FIXTURE["company"]["aliases"]),
})
ARTICLE = FIXTURE["article"]
BODY = ARTICLE["body"]
CANDIDATE = NewsCandidate(
    id=ARTICLE["id"], title=ARTICLE["title"], description="", originallink=ARTICLE["url"],
    link=ARTICLE["url"], published_on=ARTICLE["indexed_published_on"], publisher=ARTICLE["publisher"],
    priority=1, source_url=ARTICLE["url"], source_category=ARTICLE["source_category"],
)


def _validate(raw, body=BODY):
    return validate_grounded_response(
        raw, articles=[(CANDIDATE, body)], company=COMPANY,
        as_of=dt.date.fromisoformat(FIXTURE["as_of"]),
    )


def _fresh_selection():
    # 새 모델 판정이 아니라, 현재 후보 중 정확한 회사 발언 ID를 선택한 입력 시험이다.
    quote = next(q for q in quote_candidates(CANDIDATE, BODY, COMPANY)
                 if BODY[q["start"]:q["end"]].startswith("Toss Payments emphasized")
                 and "Hanwha stated" not in BODY[q["start"]:q["end"]])
    raw = deepcopy(FIXTURE["saved_response"])
    row = raw["items"][0]
    row["excerpts"] = row["excerpts"][:1]
    row["entity_evidence_quote_id"] = quote["id"]
    row["excerpts"][0]["text_quote_id"] = quote["id"]
    return raw, quote


@pytest.mark.parametrize("verb", ["said", "stated", "explained", "announced", "emphasized", "emphasised", "SAID"])
def test_명시_대상회사_발언동사만_지원한다(verb):
    assert has_attributed_statement(f'Toss Payments {verb}, “The payment system was not breached.”', COMPANY)


@pytest.mark.parametrize("speaker", [
    "Toss Payments CEO", "Toss Payments' representative", "Toss Payments’s spokesperson",
    "a spokesperson for Toss Payments", "the chief executive officer of Toss Payments",
])
def test_회사에_직접_소속된_대표발언만_지원한다(speaker):
    assert has_attributed_statement(f'{speaker} said, “The payment system was not breached.”', COMPANY)


@pytest.mark.parametrize("text", [
    'The FSS said that Toss Payments faced a regulatory inspection.',
    'A reporter who covers Toss Payments said the inspection had begun.',
    'Hanwha emphasized that Toss Payments faced a regulatory inspection.',
    'Toss Payments was described as a target, the reporter explained.',
    'Toss Payments will announce the inspection findings next week.',
    'Toss Payments would have said that the payment system was safe.',
    'Toss Payments left the cause unstated after the inspection.',
    'Toss Payments saidless material was removed from the document.',
    'OtherToss Payments said that the payment system was safe.',
    'Toss Payments subsidiary said that the payment system was safe.',
    'Toss Payments spokesperson for Hanwha said the inspection had begun.',
    'Toss Payments emphasized, “No breach was found.” Hanwha stated, “An inspection began.”',
    'Toss Payments emphasized that no breach was found, while Hanwha stated otherwise.',
    'Toss Payments said, “No breach was found.” The FSS began an inspection.',
])
def test_타사_기자_예정_동사부분일치_혼합범위는_새양성이_아니다(text):
    assert not has_attributed_statement(text, COMPANY)


def test_실제_회사발언_새선택은_검증전체를_통과하고_원응답은_그대로다():
    before = deepcopy(FIXTURE["saved_response"])
    raw, quote = _fresh_selection()
    result, rejected = _validate(raw)
    assert len(result) == 1 and not rejected
    assert result[0].text == BODY[quote["start"]:quote["end"]]
    assert (result[0].span_start, result[0].span_end) == (quote["start"], quote["end"])
    assert result[0].claim_kind == "company_statement"
    assert result[0].claim_slot == "current_challenges:issue"
    assert "Hanwha" not in result[0].text
    assert FIXTURE["saved_response"] == before


def test_실제_혼합원선택과_FSS기자서술은_계속거절된다():
    result, rejected = _validate(FIXTURE["saved_response"])
    assert not result
    assert rejected == {"grounded_attribution_required": 2}


@pytest.mark.parametrize("field,value,reason", [
    ("subject_is_target", False, "grounded_invalid_excerpt"),
    ("claim_kind", "reported_fact", "grounded_attribution_required"),
    ("claim_kind", "company_plan", "grounded_plan_mismatch"),
    ("temporal_status", "planned", "grounded_plan_mismatch"),
])
def test_영어지원이_모델귀속과_종류_시점계약을_완화하지_않는다(field, value, reason):
    raw, _ = _fresh_selection()
    raw["items"][0]["excerpts"][0][field] = value
    result, rejected = _validate(raw)
    assert not result
    assert reason in rejected


@pytest.mark.parametrize("first,second", [
    ('Toss Payments emphasized, “Payment version A.v2 remains available.”', 'Hanwha stated, “A check began.”'),
    ('토스페이먼츠는 “A.v2 결제 서비스가 정상입니다.”', '한빛은 “다른 사항입니다.”'),
    ('Toss Payments said, "Version A.v2 is available. No breach was found."', 'Hanwha stated, "A check began."'),
])
def test_닫는인용부호_경계와_내부마침표_및_선택SHA를_보존한다(first, second):
    body = first + " " + second
    units = [m.group().strip() for m in qc.QUOTE_SENTENCE_RE.finditer(body)]
    assert units == [first, second]
    quotes = quote_candidates(CANDIDATE, body, COMPANY)
    assert len(quotes) <= qc.QUOTE_MAX_CANDIDATES_PER_ARTICLE
    assert any(body[q["start"]:q["end"]] == first for q in quotes)
    body_sha = hashlib.sha256(body.encode()).hexdigest()
    for quote in quotes:
        start, end = quote["start"], quote["end"]
        assert 0 <= start < end <= len(body)
        assert end - start <= qc.QUOTE_MAX_CHARS
        digest = hashlib.sha256(f"{CANDIDATE.id}\n{body_sha}\n{start}:{end}".encode()).hexdigest()
        assert quote["id"] == qc.QUOTE_ID_PREFIX + digest[:qc.QUOTE_ID_HASH_CHARS]


def test_기사나_본문이_다르면_영문발언ID도_재사용할_수_없다():
    raw, _ = _fresh_selection()
    assert not _validate(raw, BODY.replace("was breached", "was compromised"))[0]
    other = replace(CANDIDATE, id="news-different-source")
    assert not validate_grounded_response(
        raw, articles=[(other, BODY)], company=COMPANY,
        as_of=dt.date.fromisoformat(FIXTURE["as_of"]),
    )[0]
