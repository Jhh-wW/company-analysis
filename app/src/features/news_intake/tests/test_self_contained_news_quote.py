"""가상 기사 인용문의 법인 주어와 출처 혼동을 막는 회귀 시험."""

from __future__ import annotations

import datetime as dt
import json

from src.features.news_intake import constants as c
from src.features.news_intake.grounded import (
    build_grounded_prompt,
    validate_grounded_response,
)
from src.features.news_intake.models import (
    NewsCandidate,
    NewsCollectionPolicy,
    NewsCompanyContext,
)
from src.features.news_intake.search_snapshot import policy_digest


AS_OF = dt.date(2026, 9, 27)
COMPANY = NewsCompanyContext(
    "가상기술", aliases=("Virtual Sensor Co",), identity_context="산업용 센서 제조"
)
CANDIDATE = NewsCandidate(
    id="synthetic-1",
    title="가상기술의 센서 공급",
    description="가상기술 사업 기사",
    originallink="https://media.example/article/1",
    link="",
    published_on="2026-09-20",
    publisher="media.example",
    priority=1,
    source_url="https://media.example/article/1",
    source_category="news_report",
)
IDENTITY = "가상기술은 산업용 센서를 제조하고 신제품을 공급한다."
PRONOUN_CLAIM = "이 회사는 신규 공장에 산업용 센서를 공급했다."


def _response(
    body: str,
    text: str,
    *,
    company: NewsCompanyContext = COMPANY,
    subject: str = "",
    subject_evidence: str = "",
    identity_evidence: str = IDENTITY,
) -> tuple[tuple, dict[str, int]]:
    return validate_grounded_response(
        {
            "items": [
                {
                    "id": CANDIDATE.id,
                    "same_company": True,
                    "material": True,
                    "entity_evidence": identity_evidence,
                    "source_type": "news_report",
                    "excerpts": [
                        {
                            "text": text,
                            "section_id": "portfolio",
                            "claim_slot": "portfolio:product_role",
                            "claim_kind": "reported_fact",
                            "temporal_status": "completed",
                            "topic": "products",
                            "event_key": "센서 공급",
                            "event_on": "",
                            "time_evidence": "",
                            "subject": subject,
                            "subject_evidence": subject_evidence,
                        }
                    ],
                }
            ]
        },
        articles=[(CANDIDATE, body)],
        company=company,
        as_of=AS_OF,
    )


def test_prompt_prioritizes_self_contained_legal_actor_and_keeps_strict_exceptions() -> None:
    prompt = build_grounded_prompt(COMPANY, [(CANDIDATE, IDENTITY)], AS_OF)
    assert "각 text 인용 범위 자체에 검증된 대상 법인명과 그 법인의 실제 사업 행동" in prompt
    assert "'이 회사' 같은 대명사만 남긴 문장은 고르지 마세요" in prompt
    assert "그런 인용이 불가능하고 제품·브랜드·소속 인물이 실제 주어인 경우에만" in prompt
    assert "발행처·기자·저작권 표기의 회사명" in prompt
    assert "그 언론사가 보도한 타사의 사건은 same_company=false" in prompt
    assert "두 범위를 포함하는 연속 본문" in prompt
    assert "떨어진 문장을 합치지 마세요" in prompt
    payload = json.loads(prompt.split("자료 시작:\n", 1)[1])
    assert payload["verified_company_names"]
    assert len(prompt) < c.GROUNDED_PROMPT_CHARS_BUDGET


def test_anaphoric_quote_is_rejected_even_when_full_body_names_company() -> None:
    body = IDENTITY + " " + PRONOUN_CLAIM
    excerpts, excluded = _response(body, PRONOUN_CLAIM)
    assert excerpts == ()
    assert excluded == {"grounded_subject_missing": 1}


def test_self_contained_exact_quote_is_accepted_without_subject_exception() -> None:
    body = IDENTITY + " " + PRONOUN_CLAIM
    excerpts, excluded = _response(body, body)
    assert len(excerpts) == 1
    assert excerpts[0].text == body
    assert excluded == {}


def test_verified_alias_is_allowed_but_cannot_carry_unneeded_subject_fields() -> None:
    company = NewsCompanyContext("가상기술", aliases=("Virtual Sensor Co",))
    body = "Virtual Sensor Co는 산업용 센서 신제품을 개발해 공급했다."
    excerpts, excluded = _response(
        body, body, company=company, identity_evidence=body
    )
    assert len(excerpts) == 1
    assert excluded == {}

    excerpts, excluded = _response(
        body,
        body,
        company=company,
        identity_evidence=body,
        subject="신제품",
        subject_evidence=body,
    )
    assert excerpts == ()
    assert excluded == {"grounded_subject_missing": 1}


def test_adjacent_product_relation_remains_the_only_named_subject_exception() -> None:
    relation = "가상기술은 산업용 센서 푸른칩을 자체 개발해 판매한다."
    claim = "푸른칩은 신규 공장에 산업용 센서 100개를 공급했다."
    excerpts, excluded = _response(
        relation + " " + claim,
        claim,
        subject="푸른칩",
        subject_evidence=relation,
        identity_evidence=relation,
    )
    assert len(excerpts) == 1
    assert excerpts[0].text == relation + " " + claim
    assert excluded == {}


def test_other_company_relation_cannot_rescue_brand_quote() -> None:
    relation = "가상기술은 산업용 센서 푸른칩을 자체 개발해 판매한다."
    other_relation = "다른기술은 푸른칩을 제조해 판매한다."
    claim = "푸른칩은 신규 공장에 산업용 센서 100개를 공급했다."
    excerpts, excluded = _response(
        relation + " " + other_relation + " " + claim,
        claim,
        subject="푸른칩",
        subject_evidence=other_relation,
        identity_evidence=relation,
    )
    assert excerpts == ()
    assert excluded == {"grounded_subject_missing": 1}


def test_publisher_name_does_not_bind_another_company_action() -> None:
    publisher = NewsCompanyContext("가상언론")
    body = "발행처: 가상언론. 다른기술은 산업용 센서를 신규 공장에 공급했다."
    claim = "다른기술은 산업용 센서를 신규 공장에 공급했다."
    excerpts, excluded = _response(
        body,
        claim,
        company=publisher,
        identity_evidence="발행처: 가상언론.",
    )
    assert excerpts == ()
    assert excluded == {"grounded_subject_missing": 1}


def test_prompt_change_invalidates_old_policy_cache_namespace(monkeypatch) -> None:
    policy = NewsCollectionPolicy()
    current = policy_digest(policy)
    # 메타 목록 인용 차단과 산업 주과제·상태 계약의 캐시를 분리한다.
    # 산업 우선 요청의 단일 판정 계약으로 이전 분석 캐시를 분리한다.
    # 산업 본문 실패의 제한 보충으로 수집 정책 캐시를 분리한다. 인용 계약은 같다.
    assert c.COLLECTION_POLICY_VERSION == "news-grounded-v29"
    monkeypatch.setattr(c, "COLLECTION_POLICY_VERSION", "news-grounded-v11")
    assert policy_digest(policy) != current
