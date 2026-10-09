"""산업 본문 실패의 제한 보충과 기존 회사·출처·호출 상한을 검증한다."""

from dataclasses import replace
import json

import pytest

from src.features.news_intake.body_prefetch import BodyFetchConcurrency
from src.features.news_intake import constants as c
from src.features.news_intake.collection import collect_from_snapshot
from src.features.news_intake.industry_body_fallback import IndustryBodyFallback
from src.features.news_intake.models import NewsBodyFetchResult, NewsCollectionPolicy, NewsSearchSnapshot
from src.features.news_intake.search_snapshot import (
    body_ranked_candidates, company_digest, industry_body_reservations, policy_digest, snapshot_digest,
)
from src.features.news_intake.tests.test_industry_context import AS_OF
from src.features.news_intake.tests.test_industry_query_opportunity_balance import candidate, company, four_groups


def items():
    values = [*four_groups(), *(replace(candidate(f"company-{index}", "business"),
                                      metadata_name_match=True) for index in range(20))]
    return [replace(item, originallink=item.source_url, link="") for item in values]


def fallback(candidates, allowance=4):
    context = company()
    reservations = industry_body_reservations(candidates, 24, company=context)
    return IndustryBodyFallback(reservations, company=context, remaining_allowance=allowance)


def run(*, fail_ids=(), parallel=False, old_date_id="", values=None, failed_urls=()):
    context = company()
    policy = NewsCollectionPolicy(max_analysis_calls=3, trusted_publisher_domains=("media.example",))
    candidates = items() if values is None else values
    snap = NewsSearchSnapshot(candidates=tuple(candidates), query_attempts=(),
                             company_digest=company_digest(context), policy_digest=policy_digest(policy),
                             as_of=AS_OF.isoformat(), digest="", status="success", reason_codes=(), cache_eligible=True)
    snap = replace(snap, digest=snapshot_digest(snap))
    fetches, batches, observations = [], [], []

    def fetch(url):
        identifier = url.rsplit("/", 1)[-1]
        fetches.append(identifier)
        if identifier in fail_ids or url in failed_urls:
            return NewsBodyFetchResult(reason_code="fetch_robots_blocked")
        text = f"{context.company_name}는 새로운 사업 운영 내용을 발표했으며 자기 기사 식별자는 {identifier}이다."
        return NewsBodyFetchResult(text=text, stage=c.BODY_STAGE_PROVIDED,
                                   published_on="2025-01-01" if identifier == old_date_id else "")

    def analyze(prompt, schema, tokens):
        articles = json.loads(prompt.split("자료 시작:\n", 1)[1])["articles"]
        batches.append([article["id"] for article in articles])
        rows = [{"id": article["id"], "same_company": False, "material": False,
                 "entity_evidence_quote_id": "", "source_type": "news_report", "excerpts": []}
                for article in articles]
        if "industry_assessments" in schema["properties"]["items"]["items"]["properties"]:
            for row in rows:
                row["industry_assessments"] = [{"anchor_id": anchor.anchor_id, "status": "different_business"}
                                               for anchor in context.business_anchors]
        return {"items": rows}

    result = collect_from_snapshot(snap, company=context, as_of=AS_OF, fetch_text=fetch,
                                   analyze_grounded=analyze, policy=policy,
                                   body_fetch=BodyFetchConcurrency(max_in_flight=3, max_per_host=1) if parallel else None,
                                   observer=lambda event, payload: observations.append((event, payload)))
    return result, fetches, batches, observations


def test_failed_reservations_reach_same_groups_before_company_batches_end():
    reserved = industry_body_reservations(items(), 24, company=company())
    result, fetches, batches, _ = run(fail_ids=list(reserved)[:2])
    model_ids = [identifier for batch in batches for identifier in batch]
    industrial = [identifier for identifier in model_ids if not identifier.startswith("company-")]
    assert len(batches) == 3 and len(model_ids) == 12
    assert len(industrial) == 4 and len(model_ids) - len(industrial) == 8
    assert {next(item.topics[0] for item in items() if item.id == identifier) for identifier in industrial} == set(reserved.values())
    assert result.diagnostics["산업본문보충"]["시도"] == 2
    assert result.diagnostics["본문시도기사"] <= 24
    assert result.diagnostics["본문호출"] <= c.BODY_CALL_BUDGET
    assert len(fetches) == len(set(fetches))
    assert not result.fragments and not result.industry_problems


def test_no_failure_keeps_original_order_and_eight_company_inputs():
    _, fetches, batches, _ = run()
    original = body_ranked_candidates(items(), attempt_budget=24, probe_budget=4, company=company())
    assert fetches == [item.id for item in original[:12]]
    assert [identifier for batch in batches for identifier in batch] == fetches
    assert sum(identifier.startswith("company-") for identifier in fetches) == 8


@pytest.mark.parametrize("parallel", [False, True])
def test_repeated_failures_are_bounded_and_never_repeat_urls(parallel):
    fail_ids = [item.id for item in four_groups()]
    result, fetches, batches, _ = run(fail_ids=fail_ids, parallel=parallel)
    diagnostics = result.diagnostics["산업본문보충"]
    assert diagnostics["선정"] == diagnostics["시도"] == 4
    assert len(fetches) == len(set(fetches))
    assert result.diagnostics["본문시도기사"] <= 24
    assert result.diagnostics["본문호출"] <= c.BODY_CALL_BUDGET
    assert len(batches) == 3


def test_other_group_and_already_launched_url_are_ineligible():
    data = items()
    state = fallback(data)
    failed, topic = next(iter(state.groups.items()))
    same = [item for item in data if topic in item.topics and item.id not in state.groups]
    other = [item for item in data if topic not in item.topics]
    assert state.select(failed, other, excluded_ids=set(), excluded_urls=set()) is None
    assert state.select(failed, same, excluded_ids={item.id for item in same}, excluded_urls=set()) is None
    assert state.select(failed, same, excluded_ids=set(), excluded_urls={item.source_url for item in same}) is None


def test_failed_original_link_cannot_reenter_through_another_source_url():
    state = fallback(items())
    failed, topic = next(iter(state.groups.items()))
    old = next(item for item in items() if item.id == failed)
    alias = replace(candidate("alias", topic), originallink=old.source_url, link="")
    assert state.select(failed, [alias], excluded_ids=set(), excluded_urls={old.source_url}) is None


def test_previous_window_failed_alias_cannot_reenter_later_basic_ranking():
    topic = "industry_domestic:content"
    portal = "https://media.example/previous-portal"
    recent = [candidate("recent-head", topic), candidate("recent-global", "industry_global:content"),
              *(replace(candidate(f"recent-company-{index}", "business"), metadata_name_match=True)
                for index in range(5))]
    recent = [replace(item, originallink=item.source_url, link=portal if item.id == "recent-head" else "")
              for item in recent]
    old_head = replace(candidate("old-head", topic, "뉴스콘텐츠 공급 지연", date="2025-01-01"),
                       originallink="https://media.example/old-head", link="")
    old_alias = replace(candidate("old-alias", topic, date="2025-01-01"), originallink=portal, link="")
    old_company = [replace(candidate(f"old-company-{index}", "business", date="2025-01-01"),
                           originallink=f"https://media.example/old-company-{index}", link="", metadata_name_match=True)
                   for index in range(8)]
    failed = {item.source_url for item in recent} | {portal, old_head.source_url, old_alias.source_url}
    result, fetches, _, _ = run(values=[*recent, old_head, old_alias, *old_company], failed_urls=failed)
    assert fetches.count("previous-portal") == 1
    assert result.diagnostics["제외"][c.EXCLUDED_DUPLICATE_BODY_CHAIN_URL] == 1
    assert result.diagnostics["본문시도기사"] == len(recent) + len(old_company) + 1


def test_multi_topic_candidate_receives_one_failed_group_and_one_attempt():
    data = items()
    state = fallback(data)
    failed, topic = next(iter(state.groups.items()))
    shared = replace(candidate("shared", topic), topics=tuple(state.groups.values()))
    assert state.select(failed, [shared], excluded_ids=set(), excluded_urls=set()) == shared
    assert state.groups["shared"] == topic
    other = next(identifier for identifier in state.groups if identifier not in (failed, "shared"))
    assert state.select(other, [shared], excluded_ids=set(), excluded_urls=set()) is None


def test_empty_group_does_not_borrow_another_anchor_or_region():
    state = fallback(items())
    failed, topic = next(iter(state.groups.items()))
    wrong = candidate("wrong", "industry_global:unknown")
    assert state.select(failed, [wrong], excluded_ids=set(), excluded_urls=set()) is None
    assert not state.records


def test_allowance_and_initial_count_are_both_upper_bounds():
    data = items()
    state = fallback(data, allowance=1)
    failed = next(iter(state.groups))
    chosen = state.select(failed, data, excluded_ids=set(), excluded_urls=set())
    assert chosen is not None
    assert state.select(chosen.id, data, excluded_ids=set(), excluded_urls=set()) is None


def test_corrected_older_body_is_deferred_without_fabricating_recent_success():
    reserved = industry_body_reservations(items(), 24, company=company())
    old_id = next(iter(reserved))
    result, fetches, batches, _ = run(old_date_id=old_id)
    assert old_id not in batches[0]
    assert result.diagnostics["기간별"]["12"]["이월"] == 1
    assert result.diagnostics["산업본문보충"]["선정"] == 0
    assert fetches.count(old_id) == 1
