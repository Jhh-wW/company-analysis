"""닫힌 산업 상태는 별도 진단이며 사실·인용 검증을 대신하지 않는다."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace

import pytest

from src.features.news_intake import industry_constants as ic
from src.features.news_intake.grounded import build_grounded_prompt, build_grounded_schema, validate_grounded_response
from src.features.news_intake.industry_assessment import priority_enabled
from src.features.news_intake.industry_context import extend_prompt, extend_schema, split_response
from src.features.news_intake.tests.test_industry_context import (
    AS_OF, ANCHOR, BODY, CANDIDATE, COMPANY, response,
)
from src.features.news_intake.tests.test_collection import accepted
from src.shared.report_generation.models import exact_text_sha256


def assessed(raw=None, status="proposed"):
    raw = deepcopy(response() if raw is None else raw)
    raw["items"][0][ic.INDUSTRY_ASSESSMENT_FIELD] = [{"anchor_id": ANCHOR.anchor_id, "status": status}]
    return raw


def split(raw, *, body=BODY, articles=None, company=COMPANY):
    records = []
    articles = articles or [(CANDIDATE, body)]
    direct, problems, rejected = split_response(
        raw, articles=articles, company=company, as_of=AS_OF,
        full_body_hashes={candidate.source_url: exact_text_sha256(text) for candidate, text in articles},
        assessment_required=True, assessment_records=records,
    )
    return direct, problems, rejected, records


def test_산업토픽있는_동일묶음만_주과제와_닫힌상태를_요청한다():
    articles = [(CANDIDATE, BODY)]
    assert priority_enabled(COMPANY, articles)
    assert not priority_enabled(replace(COMPANY, business_anchors=()), articles)
    assert not priority_enabled(COMPANY, [(replace(CANDIDATE, topics=("products",)), BODY)])
    schema = build_grounded_schema(articles)
    original = deepcopy(schema)
    primary = extend_schema(schema, COMPANY, priority=True)
    status = primary["properties"]["items"]["items"]["properties"][ic.INDUSTRY_ASSESSMENT_FIELD]
    assert status["maxItems"] == status["minItems"] == 1
    assert schema == original
    assert ic.INDUSTRY_ASSESSMENT_FIELD not in extend_schema(schema, COMPANY)["properties"]["items"]["items"]["required"]
    prompt = build_grounded_prompt(COMPANY, articles, AS_OF)
    assert extend_prompt(prompt, COMPANY, priority=True) == ic.INDUSTRY_PRIORITY_GUIDE + extend_prompt(prompt, COMPANY)


def test_산업주과제는_최대기존묶음과_공식앵커상한만_요청한다():
    three = replace(COMPANY, business_anchors=tuple(replace(ANCHOR, anchor_id=f"anchor-{i}") for i in range(3)))
    four = [(replace(CANDIDATE, id=f"article-{i}"), BODY) for i in range(4)]
    assert priority_enabled(three, four)
    assert not priority_enabled(three, four + [(CANDIDATE, BODY)])
    assert not priority_enabled(replace(three, business_anchors=three.business_anchors + (ANCHOR,)), four)


def test_정상회사_false도_산업을_독립검증하고_상태를_사실에_넣지않는다():
    direct, problems, rejected, records = split(assessed())
    assert len(problems) == 1 and not rejected
    assert records == [{"article_id": CANDIDATE.id, "anchor_id": ANCHOR.anchor_id, "status": "proposed"}]
    assert ic.INDUSTRY_ASSESSMENT_FIELD not in direct["items"][0]
    assert not validate_grounded_response(direct, articles=[(CANDIDATE, BODY)], company=COMPANY, as_of=AS_OF)[0]


@pytest.mark.parametrize("change", (
    {"same_business": False}, {"problem_present": False}, {"geography_supported": False},
    {"geography": "global"}, {"problem": "원문에 없는 문제"}, {"anchor_id": "unknown"},
))
def test_proposed상태도_사업문제지역원문_불일치를_살리지_않는다(change):
    _, problems, rejected, records = split(assessed(response(**change)))
    assert not problems and rejected["industry_unbound_problem"] == 1
    assert records[0]["status"] == "proposed"


@pytest.mark.parametrize("status", ic.INDUSTRY_ASSESSMENT_STATUSES[1:])
def test_현재문제없음과_불확실은_제안0을_정직하게_기록한다(status):
    raw = response()
    raw["items"][0]["industry_problems"] = []
    _, problems, rejected, records = split(assessed(raw, status))
    assert not problems and not rejected and records[0]["status"] == status


@pytest.mark.parametrize("entries", (
    None, [], [{"anchor_id": "unknown", "status": "proposed"}],
    [{"anchor_id": ANCHOR.anchor_id, "status": "invalid"}],
    [{"anchor_id": ANCHOR.anchor_id, "status": "uncertain"}],
    [{"anchor_id": ANCHOR.anchor_id, "status": "proposed"}] * 2,
))
def test_상태결함은_유효회사인용을_삭제하지않는다(entries):
    direct_text = "예제법인은 산업설비 제조 사업에서 자동화 설비를 고객에게 공급했다."
    body = direct_text + "\n" + BODY
    raw = assessed()
    raw["items"][0].update(accepted({"id": CANDIDATE.id, "body": body}, text=direct_text))
    if entries is None:
        raw["items"][0].pop(ic.INDUSTRY_ASSESSMENT_FIELD)
    else:
        raw["items"][0][ic.INDUSTRY_ASSESSMENT_FIELD] = entries
    direct, problems, rejected, _ = split(raw, body=body)
    assert rejected and len(problems) == 1
    excerpts, company_rejected = validate_grounded_response(direct, articles=[(CANDIDATE, body)], company=COMPANY, as_of=AS_OF)
    assert len(excerpts) == 1 and not company_rejected


def test_누락중복미지기사는_상태진단에남으며_다른기사_결과를_빌리지않는다():
    other = replace(CANDIDATE, id="other", source_url="https://media.example/other")
    _, _, rejected, _ = split(assessed(), articles=[(CANDIDATE, BODY), (other, BODY)])
    assert rejected["industry_assessment_missing_article"] == 1
    raw = assessed()
    raw["items"] *= 2
    assert split(raw)[2]["industry_assessment_invalid_article"] == 2
    raw = assessed()
    raw["items"][0]["id"] = "unknown"
    assert split(raw)[2]["industry_assessment_invalid_article"] == 1


def test_상태required형식캐시는_결함을_저장하지않고_정상coldwarm을_보존한다():
    from src.features.news_intake import analysis_result_cache as cache
    from src.features.news_intake.models import NewsCollectionPolicy
    from src.features.news_intake.tests.test_analysis_result_cache import NAMESPACE
    articles = [(CANDIDATE, BODY)]
    policy = NewsCollectionPolicy()
    req = cache.AnalysisRequest(
        COMPANY, AS_OF, policy, articles, {CANDIDATE.source_url: exact_text_sha256(BODY)},
        extend_prompt(build_grounded_prompt(COMPANY, articles, AS_OF), COMPANY, priority=True),
        extend_schema(build_grounded_schema(articles), COMPANY, priority=True), policy.analysis_max_tokens,
    )
    assert not req.valid(response())
    assert req.valid(assessed())
    assert not req.valid(assessed(status="uncertain"))
    store, calls, hits = cache.AnalysisResultCache(), [], []
    def provider():
        calls.append(1)
        return cache.ProviderAnalysis(assessed(), True)
    assert store.run(req, NAMESPACE, provider, lambda: hits.append(1)) == assessed()
    assert store.run(req, NAMESPACE, provider, lambda: hits.append(1)) == assessed()
    assert calls == [1] and hits == [1]


def test_기존네호출과본문예산을_늘리지않고_상태를_관측한다():
    from src.features.news_intake.collection import collect_from_snapshot
    from src.features.news_intake.models import NewsCollectionPolicy, NewsSearchSnapshot
    from src.features.news_intake.search_snapshot import company_digest, policy_digest, snapshot_digest
    policy = NewsCollectionPolicy(max_body_articles=16, max_analysis_calls=4, trusted_publisher_domains=("media.example",))
    candidates = tuple(replace(CANDIDATE, id=f"article-{i}", source_url=f"https://media.example/{i}",
                               originallink=f"https://media.example/{i}") for i in range(16))
    snapshot = NewsSearchSnapshot(candidates=candidates, query_attempts=(), company_digest=company_digest(COMPANY),
                                  policy_digest=policy_digest(policy), as_of=AS_OF.isoformat(), digest="",
                                  status="success", reason_codes=(), cache_eligible=True)
    snapshot = replace(snapshot, digest=snapshot_digest(snapshot))
    calls = []
    def analyze(prompt, schema, tokens):
        import json
        payload = json.loads(prompt.split("자료 시작:\n", 1)[1])
        calls.append((prompt, schema, tokens))
        items = []
        for article in payload["articles"]:
            row = assessed()["items"][0]
            row["id"] = article["id"]
            # 이 시험은 구형 정확인용 응답의 예산 배선만 검사한다.
            items.append(row)
        return {"items": items}
    result = collect_from_snapshot(snapshot, company=COMPANY, as_of=AS_OF,
                                   fetch_text=lambda url: BODY + "\n" + url.rsplit("/", 1)[-1],
                                   analyze_grounded=analyze, policy=policy)
    assert len(calls) == 4
    assert all(tokens == policy.analysis_max_tokens and len(prompt) <= policy.max_prompt_chars for prompt, _, tokens in calls)
    assert len(result.diagnostics["산업판정상태"]) == 16
    assert result.diagnostics["본문글자"] <= policy.max_total_body_chars
    assert len(result.industry_problems) == 16 and not result.fragments
