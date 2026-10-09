"""사업명 exact 신호가 없는 예비 문제의 조사 순위와 승인 경계를 구분한다."""

from dataclasses import replace

from src.features.news_intake.search_snapshot import _industry_candidates, body_ranked_candidates
from src.features.news_intake.tests.test_industry_context import ANCHOR, BODY, COMPANY, response, split
from src.features.news_intake.tests.test_industry_minimum_opportunity import candidate, candidates


def problem(identifier, region="domestic", *, title="새 기술의 다음 숙제는 권리와 보상"):
    return replace(candidate(identifier, region), title=title, description="새 기준을 논의한다",
                   published_on="2026-08-01")


def test_오래된문제제목을_최신행사와프로필보다_먼저읽는다():
    issue = problem("issue")
    event = replace(candidate("event", "domestic"), title="국제 포럼 개최", published_on="2026-09-01")
    profile = replace(event, id="profile", title="대표이사 인물 소개", description="비전과 과제")
    assert _industry_candidates([event, profile, issue], company=COMPANY)[0] == issue


def test_제목의문제신호는_요약의문제신호보다_우선한다():
    issue = problem("title-issue", title="새 기술의 규제 쟁점")
    summary = replace(candidate("summary", "domestic"), title="대표 경력 소개",
                      description="공급 부족으로 인한 비용 상승", published_on="2026-09-01")
    assert _industry_candidates([summary, issue], company=COMPANY)[0] == issue


def test_정확사업문제연결은_약한문제신호보다_먼저다():
    weak = problem("weak", title="다른 산업의 규제 문제")
    strong = replace(candidate("strong", "domestic"), title=f"{ANCHOR.business_item} 공급 지연",
                     published_on="2026-07-01")
    assert _industry_candidates([weak, strong], company=COMPANY)[0] == strong


def test_약한문제우선도_양지역과회사후보몫과원문을보존한다():
    original = [problem("domestic-issue"), problem("global-issue", "global"),
                *(item for item in candidates() if item.metadata_name_match)]
    before = tuple(original)
    ranked = body_ranked_candidates(original, attempt_budget=22, probe_budget=3, company=COMPANY)
    assert {item.id for item in ranked[:2]} == {"domestic-issue", "global-issue"}
    assert sum(item.metadata_name_match for item in ranked[:22]) == 20
    assert len(ranked) == len(original) and set(ranked) == set(original)
    assert tuple(original) == before


def test_타산업의강한문제제목도_본문사업판정을대신하지않는다():
    other = problem("other", title="다른 산업의 공급 부족과 규제 문제")
    assert _industry_candidates([candidate("event", "domestic"), other], company=COMPANY)[0] == other
    payload = response(same_business=False)
    payload["items"][0]["id"] = other.id
    assert not split(payload, candidate=other, body=BODY)[1]


def test_문제신호가강해도_없는앵커와지역근거는보충하지않는다():
    issue = problem("issue")
    missing = replace(issue, topics=("industry_domestic:unknown",))
    assert _industry_candidates([missing], company=COMPANY) == []
    assert _industry_candidates([issue], company=replace(COMPANY, business_anchors=())) == []
    payload = response(geography="global")
    payload["items"][0]["id"] = issue.id
    assert not split(payload, candidate=issue, body=BODY)[1]
