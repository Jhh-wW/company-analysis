"""합성 사례로 복구 안전성을 검증하며, 보존되지 않은 실제 5건의 오탐 여부는 단정하지 않는다."""

from collections import Counter
from copy import deepcopy
from dataclasses import replace

import pytest

from src.features.news_intake import constants as c
from src.features.news_intake.grounded import validate_grounded_response
from src.features.news_intake.models import NewsCompanyContext
from src.features.news_intake.tests.test_collection import (
    AS_OF, BODY, COMPANY, accepted, analyzer, collect, item, snapshot,
)


def review_identity(identity, **changes):
    def review(rows, payload):
        for row in rows:
            row.update(entity_evidence=identity, **changes)
        return rows
    return analyzer(review)


@pytest.mark.parametrize("identity", [
    "", None, "원문에 없는 회사 설명을 모델이 작성했다.",
    "가나다전자 관계자는 계약을 체결했다고 밝혔다.",
])
def test_verified_excerpt_recovers_identity_without_another_model_call(identity):
    body = BODY + " 가나다전자 관계자는 계약을 체결했다고 밝혔다."

    def review(rows, payload):
        for row in rows:
            row["entity_evidence"] = identity
            row["excerpts"][0]["text"] = BODY
        return rows

    result = collect(fetch=lambda url: body, analyze=analyzer(review))
    assert len(result.fragments) == 1 and result.fragments[0].text == BODY
    assert result.diagnostics["분석AI호출"] == 1
    assert result.diagnostics["법인검증상세"] == {"identity_recovered_from_excerpt": 1}
    assert "grounded_identity_unverified" not in result.diagnostics["제외"]
    assert not result.diagnostics["검증미완료"]
    assert result.diagnostics["캐시재사용가능"]


@pytest.mark.parametrize("identity,reason", [
    (None, "identity_evidence_invalid_type"),
    ("", "identity_evidence_empty"),
    (" \t\n ", "identity_evidence_empty"),
    ("가" * (c.GROUNDED_MAX_EXCERPT_CHARS + 1), "identity_evidence_too_long"),
    ("가나다전자는 원문에 없는 새로운 장비를 공급했다.", "identity_evidence_not_exact"),
    ("다른 회사는 산업설비를 제조한다.", "identity_name_missing"),
    ("가나다전자 관계자는 계약을 체결했다고 밝혔다.", "identity_context_mismatch"),
], ids=["invalid_type", "empty", "whitespace", "too_long", "not_exact", "name_missing", "context_mismatch"])
def test_identity_failure_reasons_remain_distinct_without_logging_article_text(identity, reason):
    body = "다른 회사는 산업설비를 제조한다. 가나다전자 관계자는 계약을 체결했다고 밝혔다."
    result = collect(fetch=lambda url: body, analyze=review_identity(identity, excerpts=[]))
    assert not result.fragments
    assert result.diagnostics["제외"]["grounded_identity_unverified"] == 1
    assert result.diagnostics["법인검증상세"] == {reason: 1}
    assert not result.diagnostics["자료부족"]
    assert not result.diagnostics["캐시재사용가능"]
    assert body not in str(result.diagnostics)


@pytest.mark.parametrize("change", [
    {"text": BODY.replace("120", "999")},
    {"text": "가나다전자 관계자는 계약을 체결했다고 밝혔다."},
    {"text": "다른 회사는 기업용 산업설비를 제조해 해외 공장에 새로운 제어 장치를 공급했다."},
    {"event_on": "2026-09-02", "time_evidence": "2026년 9월 1일"},
    {"claim_kind": "company_plan", "temporal_status": "completed"},
    {"section_id": "identity", "claim_slot": "identity:business_scope"},
])
def test_invalid_or_unrelated_excerpt_cannot_recover_identity(change):
    body = BODY + " 가나다전자 관계자는 계약을 체결했다고 밝혔다. " + (
        "다른 회사는 기업용 산업설비를 제조해 해외 공장에 새로운 제어 장치를 공급했다."
    )

    def review(rows, payload):
        for row in rows:
            row["entity_evidence"] = ""
            row["excerpts"][0].update(text=BODY)
            row["excerpts"][0].update(change)
        return rows

    result = collect(fetch=lambda url: body, analyze=analyzer(review))
    assert not result.fragments
    assert result.diagnostics["법인검증상세"] == {"identity_evidence_empty": 1}
    assert result.diagnostics["제외"]["grounded_identity_unverified"] == 1


@pytest.mark.parametrize("rejection", ["same_company", "material"])
def test_explicit_model_rejection_is_not_overridden(rejection):
    result = collect(analyze=review_identity("", **{rejection: False}))
    assert not result.fragments
    assert result.diagnostics["법인검증상세"] == {}


def test_same_name_in_unrelated_context_stays_unverified():
    company = NewsCompanyContext("멀티캠퍼스", identity_context="기업교육 임직원 직무훈련")
    body = "멀티캠퍼스는 대학의 여러 캠퍼스를 연결하는 입시 제도로 신입생의 복수 전공 신청을 지원한다."
    result = collect(company=company, fetch=lambda url: body, analyze=review_identity(""))
    assert not result.fragments
    assert result.diagnostics["법인검증상세"] == {"identity_evidence_empty": 1}


def test_verified_alias_and_business_context_must_belong_to_the_same_excerpt():
    company = replace(COMPANY, company_name="주식회사 하이브", aliases=("HYBE",), identity_context="음악 매니지먼트")
    body = "HYBE미디어코프는 음악 매니지먼트 사업에서 새로운 계약을 체결했다고 밝혔다."
    result = collect(company=company, fetch=lambda url: body, analyze=review_identity(""))
    assert not result.fragments
    assert result.diagnostics["법인검증상세"] == {"identity_evidence_empty": 1}


@pytest.mark.parametrize("source_type", [None, {}, "advertisement", "official_release"])
def test_untrusted_or_malformed_source_cannot_recover_identity(source_type):
    result = collect(analyze=review_identity("", source_type=source_type))
    assert not result.fragments
    assert result.diagnostics["법인검증상세"] == {"identity_evidence_empty": 1}
    assert result.diagnostics["제외"] == {"grounded_identity_unverified": 1}
    assert not result.diagnostics["캐시재사용가능"]


@pytest.mark.parametrize("excerpts", [None, {}, BODY, [{}], [{"text": BODY}]])
def test_malformed_excerpt_structure_cannot_recover_identity(excerpts):
    result = collect(analyze=review_identity("", excerpts=excerpts))
    assert not result.fragments
    assert result.diagnostics["법인검증상세"] == {"identity_evidence_empty": 1}
    assert result.diagnostics["제외"] == {"grounded_identity_unverified": 1}


def test_excess_excerpt_count_cannot_recover_identity():
    def review(rows, payload):
        for row in rows:
            row["entity_evidence"] = ""
            row["excerpts"] *= c.GROUNDED_EXCERPTS_PER_ARTICLE + 1
        return rows

    result = collect(analyze=analyzer(review))
    assert not result.fragments
    assert result.diagnostics["법인검증상세"] == {"identity_evidence_empty": 1}


@pytest.mark.parametrize("change", [
    {"event_key": None},
    {"subject": "가나다전자"},
    {"claim_kind": "unknown"},
    {"section_id": "future_strategy", "claim_slot": "future_strategy:stated_plan"},
])
def test_excerpt_type_subject_kind_and_attribution_checks_apply_during_recovery(change):
    def review(rows, payload):
        for row in rows:
            row["entity_evidence"] = ""
            row["excerpts"][0].update(change)
        return rows

    result = collect(analyze=analyzer(review))
    assert not result.fragments
    assert result.diagnostics["법인검증상세"] == {"identity_evidence_empty": 1}


@pytest.mark.parametrize("body", [
    "가나다전자는 기업용 산업설비 제조 기업으로 투자심리 개선에 따른 주가 상승이 기대되는 종목이다.",
    "가나다전자는 기업용 산업설비 제조 사업에서 자동화 장치 120대를 해외 고객에게 공급할 계획이다.",
    BODY + " " + BODY,
])
def test_market_commentary_unmarked_plan_and_ambiguous_span_cannot_recover_identity(body):
    def review(rows, payload):
        for row in rows:
            row["entity_evidence"] = ""
            if body == BODY + " " + BODY:
                row["excerpts"][0]["text"] = BODY
        return rows

    result = collect(fetch=lambda url: body, analyze=analyzer(review))
    assert not result.fragments
    assert result.diagnostics["법인검증상세"] == {"identity_evidence_empty": 1}


def test_recovery_keeps_invalid_sibling_excerpt_incomplete_and_counts_article_once():
    def review(rows, payload):
        for row in rows:
            row["entity_evidence"] = ""
            invalid = {**row["excerpts"][0], "text": BODY.replace("120", "999")}
            row["excerpts"].insert(0, invalid)
        return rows

    result = collect(analyze=analyzer(review))
    assert [fragment.text for fragment in result.fragments] == [BODY]
    assert result.diagnostics["법인검증상세"] == {"identity_recovered_from_excerpt": 1}
    assert result.diagnostics["제외"] == {"grounded_text_not_exact": 1}
    assert "grounded_text_not_exact" in result.diagnostics["검증미완료"]
    assert not result.diagnostics["캐시재사용가능"]


def test_identity_diagnostics_are_optional_accumulate_and_do_not_mutate_model_payload():
    snap, _ = snapshot([item()])
    articles = [(snap.candidates[0], BODY)]
    row = accepted({"id": snap.candidates[0].id, "body": BODY})
    row["entity_evidence"] = ""
    payload = {"items": [row]}
    original = deepcopy(payload)
    baseline = validate_grounded_response(payload, articles=articles, company=COMPANY, as_of=AS_OF)
    diagnostics = Counter({"identity_recovered_from_excerpt": 2})
    result = validate_grounded_response(payload, articles=articles, company=COMPANY, as_of=AS_OF,
                                        identity_diagnostics=diagnostics)
    assert result == baseline
    assert isinstance(result[0], tuple) and result[0][0].text == BODY and result[1] == {}
    assert diagnostics == {"identity_recovered_from_excerpt": 3}
    assert payload == original


def test_recovery_evidence_cannot_cross_article_ids_and_failure_diagnostics_remain_separate():
    snap, _ = snapshot([item(0), item(1)])
    other = "다른 회사는 산업설비 제조 사업에서 해외 고객에게 제어 장치를 공급했다."
    articles = [(snap.candidates[0], other), (snap.candidates[1], BODY)]
    rows = [accepted({"id": candidate.id, "body": BODY}) for candidate, _ in articles]
    for row in rows:
        row["entity_evidence"] = ""
    diagnostics = Counter()
    excerpts, excluded = validate_grounded_response({"items": rows}, articles=articles, company=COMPANY,
        as_of=AS_OF, identity_diagnostics=diagnostics)
    assert len(excerpts) == 1 and excerpts[0].candidate.id == snap.candidates[1].id
    assert diagnostics == {"identity_evidence_empty": 1, "identity_recovered_from_excerpt": 1}
    assert excluded == {"grounded_identity_unverified": 1}
