"""약한 검색 메타의 국내·세계 읽기 기회와 실제 근거 승인을 분리한다."""

from dataclasses import replace
import json

from src.features.news_intake.collection import collect_from_snapshot
from src.features.news_intake.models import NewsCollectionPolicy, NewsSearchSnapshot
from src.features.news_intake.search_snapshot import (
    _industry_candidates, body_ranked_candidates, company_digest, policy_digest,
    search_plan, snapshot_digest,
)
from src.features.news_intake.tests.test_industry_context import (
    ANCHOR, AS_OF, BODY, CANDIDATE, COMPANY, response, split,
)


def candidate(identifier, region=None):
    return replace(CANDIDATE, id=identifier, title="시장 변화 분석", description="새 동향",
                   source_url=f"https://media.example/{identifier}",
                   metadata_name_match=region is None,
                   topics=(f"industry_{region}:{ANCHOR.anchor_id}",) if region else ("business",))


def candidates():
    return [candidate("domestic", "domestic"), candidate("global", "global"),
            *(candidate(f"company-{index}") for index in range(20))]


def test_사업명통단어없는_양지역도_고정예약몫안에_읽을기회를받는다():
    original = candidates()
    before = tuple(original)
    ranked = body_ranked_candidates(original, attempt_budget=22, probe_budget=3, company=COMPANY)
    assert {item.id for item in ranked[:2]} == {"domestic", "global"}
    assert len(ranked) == len(original) == 22
    assert tuple(original) == before
    assert set(ranked) == set(original)


def test_정확사업문제후보를_약한동일지역후보보다_우선한다():
    strong = replace(candidate("strong", "domestic"), title=f"{ANCHOR.business_item} 공급 지연")
    ranked = body_ranked_candidates([*candidates(), strong], attempt_budget=22,
                                  probe_budget=3, company=COMPANY)
    assert ranked[0] == strong
    assert ranked[1].id == "global"
    assert len({item.id for item in ranked}) == 23


def test_없는앵커나_다른지역라벨은_예약자격을만들지않는다():
    invalid = replace(candidate("invalid", "domestic"), topics=("industry_domestic:unknown",))
    invalid_region = replace(invalid, id="invalid-region", topics=(f"industry_mars:{ANCHOR.anchor_id}",))
    assert _industry_candidates([invalid, invalid_region], company=COMPANY) == []
    assert _industry_candidates(candidates(), company=replace(COMPANY, business_anchors=())) == []


def test_지역탐색기회는_한본문의_지리근거나_문제승인이아니다():
    labelled_global = candidate("global", "global")
    payload = response(geography="global")
    payload["items"][0]["id"] = labelled_global.id
    _, problems, rejected = split(payload, candidate=labelled_global, body=BODY)
    assert not problems and rejected
    payload["items"][0]["industry_problems"][0].update(geography="domestic", problem_present=False)
    assert not split(payload, candidate=labelled_global, body=BODY)[1]


def test_양지역약한후보가_실제묶음에들어가도_세호출상한과빈제안을보존한다():
    policy = NewsCollectionPolicy(max_body_articles=22, max_analysis_calls=3,
                                  trusted_publisher_domains=("media.example",))
    snapshot = NewsSearchSnapshot(candidates=tuple(candidates()), query_attempts=(),
                                  company_digest=company_digest(COMPANY), policy_digest=policy_digest(policy),
                                  as_of=AS_OF.isoformat(), digest="", status="success", reason_codes=(),
                                  cache_eligible=True)
    snapshot = replace(snapshot, digest=snapshot_digest(snapshot))
    calls = []

    def analyze(prompt, schema, tokens):
        articles = json.loads(prompt.split("자료 시작:\n", 1)[1])["articles"]
        calls.append([article["id"] for article in articles])
        return {"items": [{"id": article["id"], "same_company": False, "material": False,
                            "entity_evidence": "", "source_type": "news_report", "excerpts": [],
                            "industry_problems": []} for article in articles]}

    result = collect_from_snapshot(snapshot, company=COMPANY, as_of=AS_OF,
                                   fetch_text=lambda url: f"{COMPANY.company_name}의 새 사업 소식과 시장 변화에 관한 충분히 긴 본문이며 기사 식별자는 {url}이다.",
                                   analyze_grounded=analyze, policy=policy)
    assert len(calls) == 3 and sum(map(len, calls)) == 12
    assert {"domestic", "global"} <= set(calls[0])
    assert result.diagnostics["본문시도기사"] <= 22
    assert result.diagnostics["산업탐색"]["분석입력기사"] == 2
    assert result.diagnostics["산업검수"]["제안근거"] == 0
    assert not result.fragments and not result.industry_problems


def test_질의는_사업과양지역을유지하되_특정문제를미리단정하지않는다():
    active = search_plan(COMPANY, AS_OF)
    plain = search_plan(replace(COMPANY, business_anchors=()), AS_OF)
    industrial = [row for row in active if row[2].startswith("industry_")]
    assert len(active) == len(plain) and len(industrial) == 4
    # 질의 표현 변경만 반영한다. 양지역·질의 수·전체 조사 예산은 그대로다.
    assert {row[0] for row in industrial} == {
        f"{ANCHOR.business_item} 국내 문제", f"{ANCHOR.business_item} 세계 문제",
        f"{ANCHOR.business_item} 한국 시장 변화", f"{ANCHOR.business_item} 글로벌 시장 변화"}
