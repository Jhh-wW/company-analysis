"""산업 탐색 기회와 모델 빈 제안·검증 탈락을 다른 단위로 관측한다."""

from dataclasses import replace

import pytest

from src.features.news_intake.collection import collect_from_snapshot
from src.features.news_intake.models import NewsCollectionPolicy, NewsSearchSnapshot
from src.features.news_intake.search_snapshot import company_digest, policy_digest, search_plan, snapshot_digest
from src.features.news_intake.tests.test_industry_context import AS_OF, BODY, CANDIDATE, COMPANY, response


def _collect(raw, *, fetch=lambda _: BODY):
    policy = NewsCollectionPolicy(max_body_articles=1, max_analysis_calls=1,
                                  trusted_publisher_domains=("media.example",))
    snapshot = NewsSearchSnapshot(candidates=(CANDIDATE,), query_attempts=(),
                                  company_digest=company_digest(COMPANY), policy_digest=policy_digest(policy),
                                  as_of=AS_OF.isoformat(), digest="", status="success", reason_codes=(),
                                  cache_eligible=True)
    snapshot = replace(snapshot, digest=snapshot_digest(snapshot))
    calls = []

    def analyze(prompt, schema, tokens):
        calls.append(tokens)
        return raw

    result = collect_from_snapshot(snapshot, company=COMPANY, as_of=AS_OF, fetch_text=fetch,
                                   analyze_grounded=analyze, policy=policy)
    return result, calls


def test_단일사업_국내세계검색을_반복하지_않고_기존총량을_유지한다():
    plan = search_plan(COMPANY, AS_OF)
    queries = [row for row in plan if row[2].startswith("industry_")]
    assert len(queries) == 2
    assert len(set(queries)) == len(queries)
    assert {row[2].split(":", 1)[0] for row in queries} == {"industry_domestic", "industry_global"}
    plain = search_plan(replace(COMPANY, business_anchors=()), AS_OF)
    assert len(plan) == len(plain)
    assert plan[:2] == plain[:2]
    assert len([row for row in plan if not row[2].startswith("industry_")]) == len(plain) - len(queries)


@pytest.mark.parametrize("mode,proposals,survivors,rejected,empty", (
    ("empty", 0, 0, 0, 1),
    ("rejected", 1, 0, 1, 0),
    ("accepted", 1, 1, 0, 0),
))
def test_모델빈제안과_제안의_검증탈락은_분리된다(mode, proposals, survivors, rejected, empty):
    raw = response(geography="global") if mode == "rejected" else response()
    if mode == "empty":
        raw["items"][0]["industry_problems"] = []
    result, calls = _collect(raw)
    assert len(calls) == 1
    assert result.diagnostics["산업탐색"] == {
        "검색호출": 0, "검색반환행": 0, "신뢰후보기사": 1, "본문시도기사": 1,
        "본문미시도기사": 0, "본문읽기사": 1, "분석입력기사": 1,
    }
    assert result.diagnostics["산업검수"] == {
        "검수입력기사": 1, "응답기사": 1, "빈제안기사": empty,
        "제안근거": proposals, "검증생존": survivors, "검증탈락": rejected,
    }
    assert len(result.industry_problems) == survivors
    assert result.fragments == result.articles == ()


def test_산업후보_본문실패는_모델빈제안으로_집계하지_않는다():
    result, calls = _collect(response(), fetch=lambda _: None)
    assert not calls
    assert result.diagnostics["산업탐색"]["본문시도기사"] == 1
    assert result.diagnostics["산업탐색"]["본문읽기사"] == 0
    assert result.diagnostics["산업탐색"]["분석입력기사"] == 0
    assert not any(result.diagnostics["산업검수"].values())


def test_검수응답누락은_빈제안과_구별한다():
    result, calls = _collect({"items": []})
    assert len(calls) == 1
    assert result.diagnostics["산업검수"]["검수입력기사"] == 1
    assert result.diagnostics["산업검수"]["응답기사"] == 0
    assert result.diagnostics["산업검수"]["빈제안기사"] == 0
    assert result.diagnostics["제외"]["industry_invalid_missing_result"] == 1
