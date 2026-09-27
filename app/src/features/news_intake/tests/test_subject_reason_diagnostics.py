"""주어 결속 거절의 닫힌 집계가 판정·비용·캐시 계약을 바꾸지 않는지 확인한다."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

import pytest

from src.features.news_intake import analysis_result_cache as cache
from src.features.news_intake import constants as c
from src.features.news_intake.body_prefetch import BodyFetchConcurrency
from src.features.news_intake.collection import (
    _closed_subject_diagnostics,
    collect_from_snapshot,
)
from src.features.news_intake.grounded import _bound_subject
from src.features.news_intake.models import NewsCompanyContext
from src.features.news_intake.tests.test_analysis_result_cache import (
    NAMESPACE,
    execute,
    payload,
    request,
)
from src.features.news_intake.tests.test_collection import (
    AS_OF,
    BODY,
    COMPANY,
    POLICY,
    analyzer,
    collect,
    item,
    snapshot,
)


COMPANY_SYNTHETIC = NewsCompanyContext("가상기술")
RELATION = "가상기술은 푸른칩을 개발해 판매한다."
CLAIM = "푸른칩은 신규 공장에 센서 100개를 공급했다."
PRONOUN = "이 회사는 신규 공장에 산업설비 자동화 장치 120대를 공급했다."


@pytest.mark.parametrize(
    "code,body,text,subject,evidence",
    (
        (c.SUBJECT_DIRECT_NAME_EXTRA_FIELDS, RELATION, RELATION, "푸른칩", RELATION),
        (c.SUBJECT_MISSING_OR_GENERIC, RELATION + " " + PRONOUN, PRONOUN, "", ""),
        (c.SUBJECT_NOT_IN_QUOTE_OR_RELATION, RELATION + " 센서를 공급했다.",
         "센서를 공급했다.", "푸른칩", RELATION),
        (c.SUBJECT_RELATION_NOT_EXACT_OR_AMBIGUOUS, RELATION + " " + CLAIM,
         CLAIM, "푸른칩", "가상기술은 푸른칩을 직접 개발했다."),
        (c.SUBJECT_RELATION_TARGET_OR_MARKER_MISSING,
         RELATION + " 다른기술은 푸른칩을 개발했다. " + CLAIM,
         CLAIM, "푸른칩", "다른기술은 푸른칩을 개발했다."),
        (c.SUBJECT_SPAN_LONG_OR_AMBIGUOUS, RELATION + " " * 1001 + CLAIM,
         CLAIM, "푸른칩", RELATION),
    ),
)
def test_첫_실패_여섯_분기만_세고_기존_판정은_같다(code, body, text, subject, evidence):
    raw = {"text": text, "subject": subject, "subject_evidence": evidence}
    start = body.index(text)
    before = _bound_subject(raw, body, COMPANY_SYNTHETIC, start)
    details: Counter[str] = Counter()
    after = _bound_subject(raw, body, COMPANY_SYNTHETIC, start, diagnostics=details)
    assert before is after is None
    assert _closed_subject_diagnostics(details) == {code: 1}
    assert sum(details.values()) == 1


def test_정상_직접인용과_명시관계는_거절집계를_늘리지_않는다():
    details: Counter[str] = Counter()
    assert _bound_subject(
        {"text": RELATION, "subject": "", "subject_evidence": ""},
        RELATION, COMPANY_SYNTHETIC, 0, diagnostics=details,
    ) == (RELATION, 0)
    body = RELATION + " " + CLAIM
    assert _bound_subject(
        {"text": CLAIM, "subject": "푸른칩", "subject_evidence": RELATION},
        body, COMPANY_SYNTHETIC, body.index(CLAIM), diagnostics=details,
    ) == (body, 0)
    assert _closed_subject_diagnostics(details) == {}


def test_악성_동적_열쇠와_비정수_개수는_영속_진단에서_제외한다():
    raw = Counter({c.SUBJECT_MISSING_OR_GENERIC: 2, "https://secret.example/body": 9})
    raw[c.SUBJECT_DIRECT_NAME_EXTRA_FIELDS] = True
    raw[c.SUBJECT_RELATION_NOT_EXACT_OR_AMBIGUOUS] = -1
    assert _closed_subject_diagnostics(raw) == {c.SUBJECT_MISSING_OR_GENERIC: 2}


def _pronoun_analysis(prompt, schema, max_tokens):
    def change(rows, _payload):
        for row in rows:
            row["excerpts"][0]["text"] = PRONOUN
        return rows
    return analyzer(change)(prompt, schema, max_tokens)


def test_실제수집_합계와_신원복구실패_폐기경계():
    body = BODY + " " + PRONOUN
    result = collect(fetch=lambda _url: body, analyze=_pronoun_analysis)
    assert not result.fragments
    assert result.diagnostics["분석AI호출"] == 1
    assert result.diagnostics["제외"]["grounded_subject_missing"] == 1
    assert result.diagnostics["주어결속상세"] == {c.SUBJECT_MISSING_OR_GENERIC: 1}
    assert sum(result.diagnostics["주어결속상세"].values()) == 1

    def invalid_identity(prompt, schema, max_tokens):
        response = _pronoun_analysis(prompt, schema, max_tokens)
        response["items"][0]["entity_evidence"] = "본문에 없는 신원 근거"
        return response
    rejected = collect(fetch=lambda _url: body, analyze=invalid_identity)
    assert rejected.diagnostics["제외"] == {"grounded_identity_unverified": 1}
    assert rejected.diagnostics["주어결속상세"] == {}


def test_순차와_병렬의_주어_집계와_기사_결과가_같다():
    policy = replace(POLICY, batch_size=2)
    snap, _ = snapshot([item(0), item(1, host="specialist.example")], policy=policy)

    def fetch(url):
        return BODY + (" 첫째." if url.endswith("/0") else " 둘째.") + " " + PRONOUN

    def run(body_fetch=None):
        return collect_from_snapshot(
            snap, company=COMPANY, as_of=AS_OF, fetch_text=fetch,
            analyze_grounded=_pronoun_analysis, policy=policy, body_fetch=body_fetch,
        )

    sequential = run()
    concurrent = run(BodyFetchConcurrency(max_in_flight=2, max_per_host=1))
    for result in (sequential, concurrent):
        assert not result.fragments
        assert result.diagnostics["분석AI호출"] == 1
        assert result.diagnostics["제외"]["grounded_subject_missing"] == 2
        assert result.diagnostics["주어결속상세"] == {c.SUBJECT_MISSING_OR_GENERIC: 2}
    assert sequential.articles == concurrent.articles


def test_주어_거절은_기존처럼_분석_캐시에_남지_않는다():
    store = cache.AnalysisResultCache()
    req = request(body_suffix=" " + PRONOUN)
    output = payload(req)
    output["items"][0]["excerpts"][0]["text"] = PRONOUN
    calls: list[int] = []
    assert execute(store, req, namespace=NAMESPACE, output=output, calls=calls) == output
    assert execute(store, req, namespace=NAMESPACE, output=output, calls=calls) == output
    assert calls == [1, 1]
    assert not store._entries
