"""협력 상대만 나온 인용의 생략 주어를 원문 문맥으로 결속하는 합성 반례."""
from copy import deepcopy
import hashlib

import pytest

from src.features.news_intake.grounded import _bound_subject, validate_grounded_response
from src.features.news_intake.models import NewsCompanyContext
from src.features.news_intake.quote_selection import _selection_subject_supported, quote_candidates
from src.features.news_intake.relation_subject_scope import bind_relation_subject
from src.features.news_intake.tests.test_collection import AS_OF, accepted, item, snapshot
from src.features.news_intake.tests.test_quote_selection import selected

COMPANY = NewsCompanyContext("가상설비")
ACTOR = "새빛전자는 모듈러 주택을 단독주택 시장에 한정하지 않고 범위를 넓히고 있다."
RELATION = "가상설비와 모듈러 건축시장 확대를 위한 협력을 진행하고 있다."
OTHER = "별빛제작의 공장은 연간 1200채의 주택을 생산할 수 있다."


def bind(body, text=RELATION, limit=1000):
    return bind_relation_subject(text, body, COMPANY, body.index(text), max_chars=limit)


def test_contiguous_actor_context_keeps_smallest_relation_and_original():
    text = RELATION + " " + OTHER
    body = ACTOR + " " + text
    before = body
    expected = ACTOR + " " + RELATION
    assert bind(body, text) == (expected, 0)
    raw = {"text": text, "subject": "", "subject_evidence": ""}
    assert _bound_subject(raw, body, COMPANY, body.index(text)) == (expected, 0)
    assert _selection_subject_supported(text, COMPANY, body)
    assert body == before and raw["text"] == text


@pytest.mark.parametrize("prefix", [
    "", "회사는 모듈러 주택 범위를 넓히고 있다. ",
    "새빛전자는 배터리 포장 범위를 넓히고 있다. ",
    ACTOR + "\n\n", ACTOR + "\n", ACTOR + " 중간 설명 ",
    '“' + ACTOR + '” ', "반면 " + ACTOR + " ",
])
def test_missing_ambiguous_or_different_context_rejects_both_paths(prefix):
    body = prefix + RELATION
    assert bind(body) is None
    assert _bound_subject({"text": RELATION, "subject": "", "subject_evidence": ""},
                          body, COMPANY, body.index(RELATION)) is None
    assert not _selection_subject_supported(RELATION, COMPANY, body)


@pytest.mark.parametrize("text", [
    "가상설비는 모듈러 건축물을 제작해 공급했다.",
    "새빛전자는 가상설비와 모듈러 협력을 진행하고 있다.",
    "가상설비와 새빛전자는 모듈러 건축 협력을 진행하고 있다.",
    "가상설비, 모듈러 건축 협력 확대 행사 개최.",
])
def test_complete_company_or_counterparty_relation_remains_unchanged(text):
    assert bind(text, text) == (text, 0)
    assert _bound_subject({"text": text, "subject": "", "subject_evidence": ""},
                          text, COMPANY, 0) == (text, 0)


def test_quote_contrast_duplicate_and_limit_cannot_borrow_actor():
    quoted = '가상설비와 “모듈러 건축” 협력을 진행하고 있다.'
    assert bind(ACTOR + " " + quoted, quoted) is None
    contrast = "반면 " + RELATION
    assert bind(ACTOR + " " + contrast, contrast) is None
    assert bind(ACTOR + " " + RELATION + " " + RELATION) is None
    assert bind(ACTOR + " " + RELATION, limit=len(RELATION)) is None


@pytest.mark.parametrize("designator", ["(주)", "㈜", "주식회사 "])
def test_legal_designator_preserves_complete_context_and_rejects_missing_actor(designator):
    relation = designator + RELATION
    body = designator + ACTOR + " " + relation
    assert bind(body, relation) == (body, 0)
    assert bind(relation, relation) is None


def test_intervening_named_actor_cannot_borrow_preceding_subject():
    changed = "가상설비와 모듈러 협력을 진행하며 별빛전자는 시장 범위를 넓히고 있다."
    assert bind(ACTOR + " " + changed, changed) is None
    previous = "새빛전자는 모듈러 공급을 검토했고 별빛전자는 제품 범위를 넓히고 있다."
    assert bind(previous + " " + RELATION) is None


def test_short_named_actor_cannot_borrow_earlier_actor():
    previous = "새빛전자는 모듈러 공급을 검토했고 다온은 모듈러 공급을 확대하고 있다."
    body = previous + " " + RELATION
    assert bind(body) is None
    assert _bound_subject({"text": RELATION, "subject": "", "subject_evidence": ""},
                          body, COMPANY, body.index(RELATION)) is None
    assert not _selection_subject_supported(RELATION, COMPANY, body)


@pytest.mark.parametrize("particle", ["과는", "와는", "과도", "와도"])
def test_counterparty_particle_keeps_exact_context_in_both_paths(particle):
    relation = RELATION.replace("가상설비와", "가상설비" + particle)
    body = ACTOR + " " + relation
    assert bind(body, relation) == (body, 0)
    assert _bound_subject({"text": relation, "subject": "", "subject_evidence": ""},
                          body, COMPANY, body.index(relation)) == (body, 0)
    assert _selection_subject_supported(relation, COMPANY, body)


def test_selection_id_and_legacy_share_binding_and_keep_raw_metadata():
    snap, _ = snapshot([item()])
    candidate = snap.candidates[0]
    body = ACTOR + " " + RELATION + " " + OTHER
    chosen = RELATION + " " + OTHER
    raw = selected(candidate, body, company=COMPANY)
    quote = next(q for q in quote_candidates(candidate, body, COMPANY)
                 if body[q["start"]:q["end"]] == chosen)
    raw["items"][0]["excerpts"][0]["text_quote_id"] = quote["id"]
    before = deepcopy(raw)
    observations = []
    result, rejected = validate_grounded_response(raw, articles=[(candidate, body)], company=COMPANY,
                                                  as_of=AS_OF, binding_observations=observations)
    assert len(result) == 1 and not rejected
    assert result[0].text == ACTOR + " " + RELATION
    assert (result[0].span_start, result[0].span_end) == (0, len(ACTOR + " " + RELATION))
    assert raw == before and observations[0]["선택ID"] == quote["id"]
    assert observations[0]["원선택범위"] == [body.index(chosen), len(body)]
    assert observations[0]["최종인용범위"] == [0, len(ACTOR + " " + RELATION)]
    assert observations[0]["원선택SHA256"] == hashlib.sha256(chosen.encode()).hexdigest()
    assert observations[0]["최종인용SHA256"] == hashlib.sha256(result[0].text.encode()).hexdigest()
    legacy = {"items": [accepted({"id": candidate.id, "body": body})]}
    legacy["items"][0]["excerpts"][0]["text"] = chosen
    legacy_observations = []
    assert validate_grounded_response(legacy, articles=[(candidate, body)], company=COMPANY,
                                     as_of=AS_OF, binding_observations=legacy_observations)[0][0].text == result[0].text
    assert legacy_observations[0]["선택ID"] == ""
