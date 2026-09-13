"""추가 매체의 후보 기회를 열되 기사 검증·접근 제한·출처 경계를 보존한다."""

import pytest

from src.features.news_intake import constants as c
from src.features.news_intake.collection import collect_from_snapshot
from src.features.news_intake.models import NewsBodyFetchResult, NewsCollectionPolicy
from src.features.news_intake.tests.test_collection import AS_OF, BODY, COMPANY, analyzer, item, snapshot


@pytest.mark.parametrize("domain", ["etoday.co.kr", "inews24.com", "nocutnews.co.kr"])
def test_verified_publisher_passes_body_validation_as_supplementary_news(domain):
    policy = NewsCollectionPolicy()
    snap, _ = snapshot([item(host=domain)], policy=policy)
    assert len(snap.candidates) == 1
    assert snap.candidates[0].source_category == "news_report"
    result = collect_from_snapshot(snap, company=COMPANY, as_of=AS_OF,
                                   fetch_text=lambda url: BODY, analyze_grounded=analyzer(), policy=policy)
    assert len(result.fragments) == 1
    assert result.fragments[0].source_kind == c.SOURCE_KIND_NEWS
    assert result.fragments[0].source_category == "news_report"


@pytest.mark.parametrize("host", [
    "etoday.co.kr.attacker.example", "fakeinews24.com", "nocutnews.co.kr.attacker.example", "unknown.example",
])
def test_similar_or_unregistered_domain_is_still_held_for_verification(host):
    snap, _ = snapshot([item(host=host)], policy=NewsCollectionPolicy())
    assert not snap.candidates
    assert snap.exclusion_counts["publisher_verification_required"] == 1


def test_verified_publisher_does_not_bypass_robots_or_company_validation():
    policy = NewsCollectionPolicy()
    snap, _ = snapshot([item(host="etoday.co.kr")], policy=policy)
    blocked = collect_from_snapshot(snap, company=COMPANY, as_of=AS_OF, policy=policy,
        fetch_text=lambda url: NewsBodyFetchResult(reason_code=c.EXCLUDED_FETCH_ROBOTS_BLOCKED),
        analyze_grounded=analyzer())
    assert not blocked.fragments
    assert blocked.diagnostics["분석AI호출"] == 0
    assert blocked.diagnostics["실패"] == c.EXCLUDED_FETCH_ROBOTS_BLOCKED
    rejected = collect_from_snapshot(snap, company=COMPANY, as_of=AS_OF, policy=policy,
        fetch_text=lambda url: BODY, analyze_grounded=analyzer(lambda rows, payload:
            [{**row, "same_company": False, "excerpts": []} for row in rows]))
    assert not rejected.fragments
    assert rejected.diagnostics["제외"]["grounded_wrong_company"] == 1


def test_policy_change_invalidates_previous_collection_snapshot(monkeypatch):
    policy = NewsCollectionPolicy()
    with monkeypatch.context() as prior:
        prior.setattr(c, "COLLECTION_POLICY_VERSION", "news-grounded-v6")
        old, _ = snapshot([item(host="yna.co.kr")], policy=policy)
    new, _ = snapshot([item(host="yna.co.kr")], policy=policy)
    assert old.policy_digest != new.policy_digest
    assert old.digest != new.digest
    with pytest.raises(ValueError, match="결속"):
        collect_from_snapshot(old, company=COMPANY, as_of=AS_OF, policy=policy,
                              fetch_text=lambda url: BODY, analyze_grounded=analyzer())
