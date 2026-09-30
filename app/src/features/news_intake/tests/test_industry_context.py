"""산업 보조 근거의 예산·원문·사업·지역 분리 회귀."""

from dataclasses import replace
import datetime as dt

import pytest

from src.features.news_intake.collection import collect_from_snapshot
from src.features.news_intake.grounded import build_grounded_schema, validate_grounded_response
from src.features.news_intake.industry_context import extend_schema, split_response
from src.features.news_intake.models import NewsCandidate, NewsCompanyContext, NewsCollectionPolicy, NewsSearchSnapshot
from src.features.news_intake.search_snapshot import company_digest, policy_digest, search_plan, snapshot_digest
from src.shared.business_challenge_context import BusinessActivityAnchor
from src.shared.report_generation.models import exact_text_sha256
from src.features.news_intake.tests.test_collection import accepted

AS_OF = dt.date(2026, 9, 30)
ANCHOR_TEXT = "예제법인은 산업설비 제조 사업을 운영한다."
ANCHOR = BusinessActivityAnchor(
    anchor_id="anchor-one", company_id="synthetic-company", source_id="formal-one", document_id="formal-document",
    source_kind="dart_business_report", source_url="https://official.example/report", location="chars:0-24",
    exact_text=ANCHOR_TEXT, text_sha256=exact_text_sha256(ANCHOR_TEXT), business_item="산업설비 제조",
)
COMPANY = NewsCompanyContext("예제법인", business_anchors=(ANCHOR,))
BODY = "한국의 산업설비 제조 산업은 핵심 부품의 공급 지연으로 생산 일정이 늦어지는 문제를 겪고 있다."
CANDIDATE = NewsCandidate(
    id="synthetic-article", title="산업설비 공급 지연", description="산업 공급 문제",
    originallink="https://media.example/industry", link="", published_on="2026-09-01",
    publisher="media.example", priority=4, source_url="https://media.example/industry",
    topics=("industry_domestic",), source_category="news_report",
)


def response(**changes):
    problem = {"anchor_id": ANCHOR.anchor_id, "text": BODY, "industry": "산업설비 제조", "problem": "공급 지연",
               "geography": "domestic", "geography_detail": "한국", "geography_evidence": "한국의",
               "applicability_quote": "산업설비 제조 산업", "problem_present": True, "same_business": True,
               "geography_supported": True}
    problem.update(changes)
    return {"items": [{"id": CANDIDATE.id, "same_company": False, "material": False,
                       "entity_evidence": "", "source_type": "news_report", "excerpts": [], "industry_problems": [problem]}]}


def split(raw=None, candidate=CANDIDATE, body=BODY, company=COMPANY):
    return split_response(raw or response(), articles=[(candidate, body)], company=company, as_of=AS_OF,
                          full_body_hashes={candidate.source_url: exact_text_sha256(body)})


def test_산업문제는_회사_직접사실을_통과시키지_않는다():
    direct, problems, rejected = split()
    assert not rejected and len(problems) == 1
    assert problems[0].exact_text == BODY
    assert problems[0].document_content_sha256 == exact_text_sha256(BODY)
    assert problems[0].applicability_quote in BODY
    excerpts, rejected = validate_grounded_response(direct, articles=[(CANDIDATE, BODY)], company=COMPANY, as_of=AS_OF)
    assert not excerpts and rejected == {"grounded_wrong_company": 1}


@pytest.mark.parametrize("changes", [
    {"same_business": False}, {"problem_present": False}, {"geography_supported": False},
    {"anchor_id": "unknown-anchor"}, {"text": "원문에 없는 산업 문제"},
    {"problem": "공급 지연 90%"}, {"problem": "모든 회사가 파산했다"},
    {"geography": "global"}, {"geography_detail": "미국"}, {"applicability_quote": "반도체 제조"},
])
def test_사업지역숫자과장_원문불일치는_보조근거에서도_차단(changes):
    _, problems, rejected = split(response(**changes))
    assert not problems and rejected


def test_미래기사와_검색요약은_근거가_될_수_없다():
    assert not split(candidate=replace(CANDIDATE, published_on="2026-10-01"))[1]
    assert not split(body="다른 본문이며 검색요약에만 산업설비 제조가 있다.")[1]


@pytest.mark.parametrize("body,changes", (
    (BODY, {"geography": "foreign"}),
    ("한국의 산업설비 제조 산업은 공급 지연 문제를 겪을 수 있다.", {}),
    ("글로벌 산업설비 제조 기업은 한국 공장에서 공급 지연 문제를 겪고 있다.",
     {"geography": "global", "geography_detail": "글로벌", "geography_evidence": "글로벌",
      "applicability_quote": "산업설비 제조 기업"}),
    ("한국의 산업설비 제조 산업은 유동성 위험을 예측하고 관리합니다.",
     {"problem": "유동성 위험"}),
    ("한국의 산업설비 제조 산업은 회계정책에 따라 수익 인식 문제를 설명합니다.",
     {"problem": "수익 인식"}),
))
def test_모델이_긍정해도_가능성_지역모순_회계상용구는_제외한다(body, changes):
    changes = {"text": body, **changes}
    if "problem" not in changes:
        changes["problem"] = "공급 지연"
    _, problems, rejected = split(response(**changes), body=body)
    assert not problems and rejected


@pytest.mark.parametrize("body,changes", (
    ("전 세계의 산업설비 제조 산업은 공급 지연 문제를 겪고 있다.",
     {"geography": "global", "geography_detail": "전 세계", "geography_evidence": "전 세계의"}),
    ("베트남의 산업설비 제조 산업은 공급 지연 문제를 겪고 있다.",
     {"geography": "foreign", "geography_detail": "베트남", "geography_evidence": "베트남의"}),
    ("한국 기업이 운영하는 베트남 현지의 산업설비 제조 산업은 공급 지연 문제를 겪고 있다.",
     {"geography": "foreign", "geography_detail": "베트남", "geography_evidence": "베트남 현지의"}),
))
def test_실제_국제지역_범위는_명시원문으로_보존한다(body, changes):
    _, problems, rejected = split(response(text=body, **changes), body=body)
    assert len(problems) == 1 and not rejected


def test_현재문제와_별도미래전망이_혼합되어도_원문을_보존한다():
    body = BODY + " 향후 개선될 수 있다는 전망도 있다."
    _, problems, rejected = split(response(text=body), body=body)
    assert len(problems) == 1 and not rejected
    assert problems[0].exact_text == body


@pytest.mark.parametrize("body,industry,problem", (
    ("한국의 회계감사 서비스 업계는 감사인의 부족으로 감사 일정 지연을 겪고 있다.",
     "회계감사 서비스", "감사 일정 지연"),
    ("한국의 평가 서비스 산업은 공정가치 평가 모형 오류 때문에 고객 서비스 지연을 겪고 있다.",
     "평가 서비스", "서비스 지연"),
))
def test_회계감사와_평가_서비스의_실제_산업문제는_회계상용구로_제외하지_않는다(body, industry, problem):
    anchor = replace(ANCHOR, business_item=industry, exact_text=industry + "를 제공한다.",
                     text_sha256=exact_text_sha256(industry + "를 제공한다."))
    _, problems, rejected = split(response(text=body, industry=industry, problem=problem,
                                          applicability_quote=industry), body=body,
                                  company=replace(COMPANY, business_anchors=(anchor,)))
    assert len(problems) == 1 and not rejected
    assert problems[0].exact_text == body


def test_현재사업문제와_별도회계정책이_혼합돼도_현재문제_절을_보존한다():
    body = BODY + " 회계정책에 따라 수익 인식 문제를 설명합니다."
    _, problems, rejected = split(response(text=body), body=body)
    assert len(problems) == 1 and not rejected
    assert problems[0].exact_text == body


@pytest.mark.parametrize("region", ("South Korea", "Republic of Korea"))
def test_국제기사가_명시한_대한민국_적용은_국내로_보존한다(region):
    body = region + "의 산업설비 제조 산업은 공급 지연 문제를 겪고 있다."
    _, problems, rejected = split(response(text=body, geography_detail=region,
                                            geography_evidence=region), body=body)
    assert len(problems) == 1 and not rejected


def test_산업응답의_누락과_중복_알수없는_ID는_검수미완료로_남긴다():
    direct, problems, rejected = split_response({"items": []}, articles=[(CANDIDATE, BODY)], company=COMPANY,
                                               as_of=AS_OF, full_body_hashes={CANDIDATE.source_url: exact_text_sha256(BODY)})
    assert not problems and rejected == {"industry_invalid_missing_result": 1}
    raw = response()
    raw["items"] *= 2
    assert not split(raw)[1]
    raw = response()
    raw["items"][0]["id"] = []
    assert not split(raw)[1]


def test_앵커없는_경로와_검색상한은_보존한다():
    plain = NewsCompanyContext("예제법인")
    ordinary = search_plan(plain, AS_OF)
    active = search_plan(COMPANY, AS_OF)
    assert len(active) == len(ordinary)
    assert active[:2] == ordinary[:2]
    assert [row[2] for row in active[2:4]] == ["industry_domestic:anchor-one", "industry_global:anchor-one"]
    assert company_digest(plain) != company_digest(COMPANY)
    schema = build_grounded_schema([(CANDIDATE, BODY)])
    assert extend_schema(schema, plain) == schema
    assert "industry_problems" in extend_schema(schema, COMPANY)["properties"]["items"]["items"]["required"]


def test_동일분석예산으로_산업만_수집하며_READY기사조각은_늘리지_않는다():
    policy = NewsCollectionPolicy(max_body_articles=1, max_analysis_calls=1, trusted_publisher_domains=("media.example",))
    snapshot = NewsSearchSnapshot(candidates=(CANDIDATE,), query_attempts=(), company_digest=company_digest(COMPANY),
                                  policy_digest=policy_digest(policy), as_of=AS_OF.isoformat(), digest="",
                                  status="success", reason_codes=(), cache_eligible=True)
    snapshot = replace(snapshot, digest=snapshot_digest(snapshot))
    calls = []
    def analyze(prompt, schema, tokens):
        calls.append((prompt, schema, tokens))
        return response()
    result = collect_from_snapshot(snapshot, company=COMPANY, as_of=AS_OF, fetch_text=lambda url: BODY,
                                   analyze_grounded=analyze, policy=policy)
    assert len(calls) == 1
    assert len(result.industry_problems) == 1
    assert result.fragments == result.articles == ()
    assert dict(result.document_hashes) == {}
    assert result.diagnostics["관련성통과"] == result.diagnostics["독립기사"] == 0
    assert result.diagnostics["본문시도기사"] <= policy.max_body_articles


def test_한기사의_회사사실과_산업문제는_각각_기존관문을_통과한다():
    direct_text = "예제법인은 기업용 산업설비 제조 사업을 운영하며 자동화 설비를 고객에게 공급했다."
    body = direct_text + "\n" + BODY
    raw = response()
    raw["items"][0].update(accepted({"id": CANDIDATE.id, "body": body}, text=direct_text))
    direct, problems, rejected = split(raw, body=body)
    assert not rejected and len(problems) == 1
    excerpts, rejected = validate_grounded_response(direct, articles=[(CANDIDATE, body)], company=COMPANY, as_of=AS_OF)
    assert len(excerpts) == 1 and not rejected
    del raw["items"][0]["industry_problems"]
    direct, problems, rejected = split(raw, body=body)
    assert not problems and rejected == {"industry_invalid_item": 1}
    assert len(validate_grounded_response(direct, articles=[(CANDIDATE, body)], company=COMPANY, as_of=AS_OF)[0]) == 1


def test_산업검수캐시는_원문대신_범위를_저장하고_앵커변경시_재사용하지_않는다():
    from src.features.news_intake import analysis_result_cache as cache
    from src.features.news_intake.tests.test_analysis_result_cache import NAMESPACE
    from src.features.news_intake.industry_context import extend_prompt
    from src.features.news_intake.grounded import build_grounded_prompt
    policy = NewsCollectionPolicy()
    articles = [(CANDIDATE, BODY)]
    req = cache.AnalysisRequest(
        COMPANY, AS_OF, policy, articles, {CANDIDATE.source_url: exact_text_sha256(BODY)},
        extend_prompt(build_grounded_prompt(COMPANY, articles, AS_OF), COMPANY),
        extend_schema(build_grounded_schema(articles), COMPANY), policy.analysis_max_tokens,
    )
    assert req.valid(response())
    store, calls, hits = cache.AnalysisResultCache(), [], []
    def provider():
        calls.append(1)
        return cache.ProviderAnalysis(response(), True)
    first = store.run(req, NAMESPACE, provider, lambda: hits.append(1))
    second = store.run(req, NAMESPACE, provider, lambda: hits.append(1))
    assert first == second == response() and calls == [1] and hits == [1]
    assert BODY not in repr(store._entries)
    changed = replace(req, company=replace(COMPANY, business_anchors=(replace(ANCHOR, anchor_id="new-anchor"),)))
    assert changed.key(NAMESPACE) != req.key(NAMESPACE)
    damaged = response(geography="global")
    assert not req.valid(damaged)


def test_우선핵심사업은_검색총량안에서_국내세계_기회를_받는다():
    anchors = tuple(replace(ANCHOR, anchor_id=f"anchor-{number}") for number in range(3))
    plan = search_plan(replace(COMPANY, business_anchors=anchors), AS_OF)
    queries = [row for row in plan if row[2].startswith("industry_")]
    assert {row[2].split(":", 1)[1] for row in queries} == {anchor.anchor_id for anchor in anchors[:2]}
    for anchor in anchors[:2]:
        assert {row[2].split(":", 1)[0] for row in queries if row[2].endswith(":" + anchor.anchor_id)} == {
            "industry_domestic", "industry_global",
        }
    assert len(plan) == len(search_plan(NewsCompanyContext("예제법인"), AS_OF))
