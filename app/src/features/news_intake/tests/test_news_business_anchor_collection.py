"""기존 배치의 뉴스 사업 연결과 최종 조각 예산 탈락을 검증한다."""

from dataclasses import replace
from datetime import date
import json

import pytest

from src.features.news_intake.collection import collect_from_snapshot
from src.features.news_intake.models import NewsCandidate, NewsCompanyContext, NewsCollectionPolicy, NewsSearchSnapshot
from src.features.news_intake.search_snapshot import company_digest, policy_digest, snapshot_digest
from src.features.news_intake.tests.test_collection import accepted


@pytest.mark.parametrize("drop_anchor", [False, True])
def test_existing_following_batch_uses_verified_business_and_counts_selection_loss(drop_anchor):
    company = NewsCompanyContext("가람회사", company_id="12345678")
    business = "가람회사는 계측 장비를 생산한다. 계측 장비는 검사 현장에 쓰인다."
    direct = "가람회사는 기술 설명회를 열었다."
    policy = NewsCollectionPolicy(batch_size=1, max_body_articles=2, max_analysis_calls=2,
                                  max_fragment_chars=len(direct) if drop_anchor else 1000,
                                  trusted_publisher_domains=("media.example",))
    problem = "한국의 계측 장비 산업은 부품 공급 지연 문제를 겪고 있다."
    candidates = tuple(NewsCandidate(
        id=f"article-{index}", title="가람회사 사업 소식", description="가람회사 소식",
        originallink="", link="", published_on=f"2026-10-0{8 - index}",
        publisher="media.example", priority=4, source_url=f"https://media.example/{index}",
        source_category="news_report", metadata_name_match=True,
    ) for index in range(2))
    snapshot = NewsSearchSnapshot(candidates=candidates, query_attempts=(),
                                 company_digest=company_digest(company), policy_digest=policy_digest(policy),
                                 as_of="2026-10-09", digest="", status="success", reason_codes=(),
                                 cache_eligible=True)
    snapshot = replace(snapshot, digest=snapshot_digest(snapshot))
    calls = []

    def analyze(prompt, schema, max_tokens):
        payload = json.loads(prompt.split("자료 시작:\n", 1)[1])
        article, = payload["articles"]
        calls.append((article["id"], prompt, schema))
        row = accepted(article, text=business if article["id"] == "article-0" else direct,
                       event_key=article["id"])
        if article["id"] == "article-1":
            properties = schema["properties"]["items"]["items"]["properties"]
            anchor_id, = properties["industry_problems"]["items"]["properties"]["anchor_id"]["enum"]
            assert "검증된 사업 앵커" in prompt and "뉴스 사업앵커" in prompt
            row["industry_problems"] = [{
                "anchor_id": anchor_id, "text": problem, "industry": "계측 장비",
                "problem": "부품 공급 지연", "geography": "domestic", "geography_detail": "한국",
                "geography_evidence": "한국의", "applicability_quote": "계측 장비 산업",
                "problem_present": True, "same_business": True, "geography_supported": True,
            }]
        return {"items": [row]}

    result = collect_from_snapshot(
        snapshot, company=company, as_of=date(2026, 10, 9), policy=policy,
        fetch_text=lambda url: business if url.endswith("/0") else direct + "\n" + problem,
        analyze_grounded=analyze,
    )
    assert [entry[0] for entry in calls] == ["article-0", "article-1"], result.diagnostics
    assert result.diagnostics["분석AI호출"] == 2
    assert result.diagnostics["본문시도기사"] == 2
    assert len(result.business_anchors) == len(result.industry_problems) == (0 if drop_anchor else 1)
    assert result.diagnostics["뉴스사업앵커선택"] == {
        "단위": "사업앵커·산업문제", "검증뉴스앵커": 1, "추가뉴스앵커": 1,
        "최종보존앵커": 0 if drop_anchor else 1,
        "조각선택제외앵커": 1 if drop_anchor else 0,
        "연결산업문제선택제외": 1 if drop_anchor else 0,
    }
