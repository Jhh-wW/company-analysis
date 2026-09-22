"""공개 기사 원문의 전체 법인명 뒤 보조사로 생기는 오거부를 검증한다."""

from collections import Counter
from copy import deepcopy

import pytest

from src.features.news_intake import name_boundary_constants as nc
from src.features.news_intake.grounded import validate_grounded_response
from src.features.news_intake.identity_names import company_query_names, mentions_target
from src.features.news_intake.models import NewsCompanyContext
from src.features.news_intake.tests.test_collection import (
    AS_OF, accepted, analyzer, collect, item, snapshot,
)
from src.shared.report_generation.models import exact_text_sha256


# 이데일리 2026-05-10 기사에서 확인한 연속 원문 1문장이다.
# https://www.edaily.co.kr/News/Read?mediaCodeNo=257&newsId=02007366645447608
# 운영 실행이 이 문장을 분석했다는 의미는 아니며, 분석 응답은 무료 대역이다.
ARTICLE_TEXT = (
    "생성형 AI 플랫폼을 운영하는 뤼튼테크놀로지스도 일본 법인의 매출 상승률이 "
    "뤼튼 서비스가 한국 시장에 출시됐을 때와 비슷한 그래프를 그리고 있다고 밝혔다."
)
COMPANY = NewsCompanyContext(
    "주식회사 뤼튼테크놀로지스", identity_context="생성형 AI 플랫폼 운영",
)


def test_public_article_full_legal_name_with_additive_particle_is_grounded():
    calls = []
    result = collect(company=COMPANY, fetch=lambda url: ARTICLE_TEXT,
                     analyze=analyzer(calls=calls))
    assert len(result.fragments) == 1, result.diagnostics
    assert mentions_target(ARTICLE_TEXT, COMPANY)
    fragment = result.fragments[0]
    assert fragment.text == ARTICLE_TEXT
    assert ARTICLE_TEXT[fragment.span_start:fragment.span_end] == ARTICLE_TEXT
    assert fragment.text_sha256 == exact_text_sha256(ARTICLE_TEXT)
    assert len(calls) == result.diagnostics["분석AI호출"] == 1
    assert not result.diagnostics["법인검증상세"]
    assert not result.diagnostics["제외"]
    assert company_query_names(COMPANY) == ("뤼튼테크놀로지스",)


@pytest.mark.parametrize("name", [
    "뤼튼", "뤼튼도시개발", "뤼튼테크놀로지스도시개발", "서울뤼튼테크놀로지스",
    "뤼튼테크놀로지스재팬",
])
def test_unverified_short_name_and_longer_other_company_stay_rejected(name):
    body = ARTICLE_TEXT.replace("뤼튼테크놀로지스도", name + "도")
    assert not mentions_target(body, COMPANY)
    result = collect(company=COMPANY, fetch=lambda url: body)
    assert not result.fragments
    assert result.diagnostics["법인검증상세"] == {"identity_name_missing": 1}


def test_same_name_in_unrelated_business_stays_rejected():
    body = "뤼튼테크놀로지스도 대학 캠퍼스에서 신입생 입시와 복수 전공 신청에 관한 상담을 진행했다."
    result = collect(company=COMPANY, fetch=lambda url: body)
    assert not result.fragments
    assert result.diagnostics["법인검증상세"] == {"identity_context_mismatch": 1}


@pytest.mark.parametrize("replacement", ["반대", "거짓"])
def test_absent_article_excerpt_cannot_pass_after_name_boundary_fix(replacement):
    def change(rows, payload):
        rows[0]["excerpts"][0]["text"] = ARTICLE_TEXT.replace("비슷한", replacement)
        return rows

    result = collect(company=COMPANY, fetch=lambda url: ARTICLE_TEXT,
                     analyze=analyzer(change))
    assert not result.fragments
    assert result.diagnostics["제외"] == {"grounded_text_not_exact": 1}


def test_wrong_entity_evidence_is_not_repaired_without_valid_article_excerpt():
    snap, _ = snapshot([item()], company=COMPANY)
    candidate = snap.candidates[0]
    row = accepted({"id": candidate.id, "body": ARTICLE_TEXT})
    row.update(entity_evidence=ARTICLE_TEXT.replace("일본", "미국"), excerpts=[])
    payload = {"items": [row]}
    before = deepcopy(payload)
    diagnostics = Counter()
    excerpts, excluded = validate_grounded_response(
        payload, articles=[(candidate, ARTICLE_TEXT)], company=COMPANY,
        as_of=AS_OF, identity_diagnostics=diagnostics,
    )
    assert not excerpts
    assert excluded == {"grounded_identity_unverified": 1}
    assert diagnostics == {"identity_evidence_not_exact": 1}
    assert payload == before


@pytest.mark.parametrize("direct_identity", [False, True])
def test_fix_preserves_analysis_request_and_recovers_both_name_checks(monkeypatch, direct_identity):
    # 별도 신원 문장이 있는 경우에는 인용의 주체 경계에서만 탈락하던 경로도 확인한다.
    identity = "뤼튼테크놀로지스는 생성형 AI 플랫폼을 운영한다."
    body = identity + " " + ARTICLE_TEXT if direct_identity else ARTICLE_TEXT
    requests = []

    def change(rows, payload):
        rows[0]["entity_evidence"] = identity if direct_identity else ARTICLE_TEXT
        rows[0]["excerpts"][0]["text"] = ARTICLE_TEXT
        rows[0]["excerpts"][0]["claim_kind"] = "company_statement"
        return rows

    free_analyzer = analyzer(change)

    def analyze(prompt, schema, max_tokens):
        requests.append((prompt, deepcopy(schema), max_tokens))
        return free_analyzer(prompt, schema, max_tokens)

    with monkeypatch.context() as before_fix:
        # 새 조사 대안만 불가능한 패턴으로 바꾸어 기존 이름 경계를 재생한다.
        before_fix.setattr(nc, "ADDITIONAL_NAME_PARTICLE_PATTERN", r"(?!)")
        before = collect(company=COMPANY, fetch=lambda url: body, analyze=analyze)
    after = collect(company=COMPANY, fetch=lambda url: body, analyze=analyze)

    assert not before.fragments
    expected = "grounded_subject_missing" if direct_identity else "grounded_identity_unverified"
    assert before.diagnostics["제외"] == {expected: 1}
    assert len(after.fragments) == 1
    fragment = after.fragments[0]
    assert body[fragment.span_start:fragment.span_end] == fragment.text == ARTICLE_TEXT
    assert before.diagnostics["분석AI호출"] == after.diagnostics["분석AI호출"] == 1
    assert before.diagnostics["본문호출"] == after.diagnostics["본문호출"] == 1
    assert len(requests) == 2 and requests[0] == requests[1]


@pytest.mark.parametrize("rejection", ["same_company", "material"])
def test_model_rejection_of_subsidiary_or_non_material_story_still_wins(rejection):
    def change(rows, payload):
        rows[0][rejection] = False
        return rows

    result = collect(company=COMPANY, fetch=lambda url: ARTICLE_TEXT,
                     analyze=analyzer(change))
    assert not result.fragments
    reason = "grounded_wrong_company" if rejection == "same_company" else "grounded_non_material"
    assert result.diagnostics["제외"] == {reason: 1}
