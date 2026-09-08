"""검색 메타는 읽기 순위만 정하고 본문이 실제 법인·사실을 증명한다."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.features.news_intake import constants as c
from src.features.news_intake.collection import collect_from_snapshot, collect_search_snapshot
from src.features.news_intake.models import NewsBodyFetchResult, NewsCompanyContext
from src.features.news_intake.search_snapshot import diverse_candidates
from src.features.news_intake.tests.test_collection import (
    AS_OF, BODY, COMPANY, POLICY, analyzer, collect, item, snapshot,
)


def unnamed(number=0, **kwargs):
    return item(number, title="새로운 사업 계약을 체결했다", description="해외 고객과 공급 계약을 맺었다.", **kwargs)


@pytest.mark.parametrize("company,body", [
    (COMPANY, BODY),
    (NewsCompanyContext("가나다교육", identity_context="기업교육 직무훈련"),
     "가나다교육은 기업교육 직무훈련 사업에서 중소기업 임직원 대상 교육 과정 3개를 개설했다."),
    (NewsCompanyContext("디에이치물류", aliases=("DH Logistics Inc.",), identity_context="화물 운송"),
     "DH물류는 화물 운송 사업에서 중소기업 고객의 해외 배송 거점 3곳을 새로 운영했다."),
    (NewsCompanyContext("제이와이피엔터테인먼트", aliases=("JYP Ent.",), identity_context="음악 매니지먼트"),
     "JYP엔터테인먼트는 음악 매니지먼트 사업에서 소속 그룹의 해외 20개 도시 공연을 제작했다."),
])
def test_공식이름이_본문에만_있는_회사를_규모와업종없이_회수한다(company, body):
    snap, _ = snapshot([unnamed()], company=company)
    assert len(snap.candidates) == 1 and not snap.candidates[0].metadata_name_match
    calls = []
    result = collect_from_snapshot(snap, company=company, as_of=AS_OF,
        fetch_text=lambda url: body, analyze_grounded=analyzer(calls=calls), policy=POLICY)
    assert len(calls) == 1
    assert len(result.fragments) == 1 and result.fragments[0].text == body
    assert result.diagnostics["메타이름비일치후보"] == 1
    assert result.diagnostics["이름미확인후보"] == 0
    assert not result.diagnostics["검증미완료"]
    assert result.diagnostics["캐시재사용가능"]


def test_정상6기사에_타사검색오탐1개가_섞여도_충분성과_캐시를_유지한다():
    articles = [item(number) for number in range(6)] + [unnamed(99)]
    fetched = []
    def fetch(url):
        fetched.append(url)
        if url.endswith("99"):
            return "라마바이오는 신약 후보 물질의 해외 임상 시험을 승인받아 의료기관과 시험 계약을 체결했다."
        return BODY.replace("120대", str(120 + int(url.rsplit("/", 1)[-1])) + "대")
    def review(rows, payload):
        for row, article in zip(rows, payload["articles"]):
            number = int(article["url"].rsplit("/", 1)[-1])
            if number == 99:
                row.update(same_company=False, excerpts=[])
            else:
                row["excerpts"][0]["topic"] = ("products", "partnerships", "strategy")[number % 3]
        return rows
    # 기본 4개 배치로 마지막 정상 2건과 오탐 1건을 실제 함께 검수한다.
    result = collect(articles, fetch=fetch, analyze=analyzer(review))
    assert len(fetched) == 7 and fetched[-1].endswith("99")
    assert result.diagnostics["제외"]["grounded_wrong_company"] == 1
    assert result.diagnostics["독립기사"] == 6
    assert result.diagnostics["완전성"] == "sufficient"
    assert result.diagnostics["검색상태"] == "success"
    assert result.diagnostics["자료부족"] is False
    assert not result.diagnostics["검증미완료"]
    assert result.diagnostics["캐시재사용가능"]


@pytest.mark.parametrize("field,excluded", [("same_company", "grounded_wrong_company"), ("material", "grounded_non_material")])
def test_본문검수가_명시적으로_거절하면_장애나_미확인으로_바꾸지_않는다(field, excluded):
    result = collect([unnamed()], analyze=analyzer(lambda rows, payload:
        [{**row, field: False, "excerpts": []} for row in rows]))
    assert not result.fragments and result.diagnostics["제외"][excluded] == 1
    assert result.diagnostics["완전성"] == "insufficient" and result.diagnostics["자료부족"]
    assert not result.diagnostics["검증미완료"] and result.diagnostics["실패"] is None
    assert result.diagnostics["캐시재사용가능"]


@pytest.mark.parametrize("field,value", [("same_company", "false"), ("same_company", None), ("material", 0)])
def test_불리언아닌_검수결과는_정상제외로_숨기지_않는다(field, value):
    result = collect([unnamed()], analyze=analyzer(lambda rows, payload:
        [{**row, field: value} for row in rows]))
    assert not result.fragments and result.diagnostics["제외"]["grounded_invalid_item"] == 1
    assert result.diagnostics["실패"] == "grounded_response_incomplete"
    assert not result.diagnostics["자료부족"] and not result.diagnostics["캐시재사용가능"]


def test_메타비일치의_본문접속실패는_정상빈검색으로_바뀌지_않는다():
    def fail(url):
        raise RuntimeError("모의 접속 실패")
    result = collect([unnamed()], fetch=fail)
    assert result.diagnostics["실패"] == "fetch_failed"
    assert "article_body_unavailable" in result.diagnostics["검증미완료"]
    assert not result.diagnostics["자료부족"] and not result.diagnostics["캐시재사용가능"]


@pytest.mark.parametrize("company,body", [
    (NewsCompanyContext("하이브", aliases=("HYBE",), identity_context="음악 매니지먼트"),
     "HYBE미디어코프는 영화 제작과 배급 사업에서 새로운 장편 영화의 해외 상영 계약을 체결했다."),
    (NewsCompanyContext("멀티캠퍼스", identity_context="기업교육 임직원 직무훈련"),
     "멀티캠퍼스는 대학의 여러 캠퍼스를 연결하는 입시 제도로 신입생의 복수 전공 신청을 지원한다."),
    (NewsCompanyContext("제이와이피엔터테인먼트", aliases=("JYP Ent.",), identity_context="음악 매니지먼트"),
     "JYP엔터는 음악 매니지먼트 사업에서 소속 그룹의 해외 20개 도시 공연을 제작했다."),
])
def test_메타검사를_통과해도_타법인과_증명없는접두를_본문에서_차단한다(company, body):
    calls = []
    result = collect([unnamed()], company=company, fetch=lambda url: body, analyze=analyzer(calls=calls))
    assert len(calls) == 1 and not result.fragments and not result.articles
    assert "grounded_identity_unverified" in result.diagnostics["검증미완료"]
    assert result.diagnostics["이름미확인후보"] == 1
    assert not result.diagnostics["캐시재사용가능"]


def test_타법인브랜드는_대상회사가_같은본문에_있어도_관계원문없이_채택하지_않는다():
    identity = "가나다전자는 기업용 산업설비 제조 사업을 운영한다."
    other = "라마로봇은 다른 제조사의 새 제품으로 해외 고객 공장에 로봇 50대를 공급했다."
    def review(rows, payload):
        for row in rows:
            row["entity_evidence"] = identity
            row["excerpts"][0].update(text=other, subject="라마로봇", subject_evidence=other)
        return rows
    result = collect([unnamed()], fetch=lambda url: identity + " " + other, analyze=analyzer(review))
    assert not result.fragments and result.diagnostics["제외"]["grounded_subject_missing"] == 1


@pytest.mark.parametrize("article,reason", [
    (item(title="", description="이름 없이 보도했다"), "missing_title"),
    (unnamed(date="2026-09-09"), "outside_window"),
    (unnamed(date="2023-09-07"), "outside_window"),
    (unnamed(date="날짜 미상"), "invalid_date"),
    (unnamed(host="unknown.example"), "publisher_verification_required"),
    (unnamed(host="blog.naver.com"), "blog_or_community"),
])
def test_메타이름외의_제목_출처_기간_조건은_계속_후보단계에서_막는다(article, reason):
    snap, _ = snapshot([article])
    assert not snap.candidates and snap.exclusion_counts[reason] == 1


@pytest.mark.parametrize("transform,reason", [
    (lambda text: text.replace("120", "200"), "grounded_text_not_exact"),
    (lambda text: text + " 추가로 세계 최대 공급사다.", "grounded_text_not_exact"),
])
def test_본문으로_넘긴_후보도_숫자변조나_없는원문을_채택하지_않는다(transform, reason):
    result = collect([unnamed()], analyze=analyzer(lambda rows, payload:
        [{**row, "excerpts": [{**row["excerpts"][0], "text": transform(BODY)}]} for row in rows]))
    assert not result.fragments and result.diagnostics["제외"][reason] == 1
    assert not result.diagnostics["캐시재사용가능"]


def test_이름일치가_후보상한전에_우선하며_같은순위의_주제를_번갈아_남긴다():
    snap, _ = snapshot([item(number) for number in range(4)] + [unnamed(4, date="2026-09-08")])
    by_number = {int(row.source_url.rsplit("/", 1)[-1]): row for row in snap.candidates}
    rows = [replace(by_number[number], topics=("products" if number < 3 else "partnerships",)) for number in range(5)]
    ranked = diverse_candidates(rows, 3)
    assert all(row.metadata_name_match for row in ranked)
    assert {row.topics[0] for row in ranked[:2]} == {"products", "partnerships"}
    limited, _ = snapshot([unnamed(4, date="2026-09-08"), item(1), item(2)], policy=replace(POLICY, max_candidates=2))
    assert all(row.metadata_name_match for row in limited.candidates)


def test_같은URL의_다른메타에서_관측한이름도_순위와지문에_보존한다():
    snap, _ = snapshot([unnamed(0, date="2026-09-02"), item(0)])
    assert len(snap.candidates) == 1 and snap.candidates[0].metadata_name_match
    assert snap.candidates[0].published_on == "2026-09-02"
    assert snap.exclusion_counts["duplicate_url"] == 1
    with pytest.raises(ValueError, match="결속"):
        collect_from_snapshot(replace(snap, candidates=(replace(snap.candidates[0], metadata_name_match=False),)),
            company=COMPANY, as_of=AS_OF, fetch_text=lambda url: BODY, analyze_grounded=analyzer(), policy=POLICY)
    with pytest.raises(TypeError, match="불리언"):
        replace(snap.candidates[0], metadata_name_match=1)


def test_최근12개월은_이름있는_과거기사보다_먼저_본문을_검증한다():
    calls = []
    def fetch(url):
        calls.append(url)
        return BODY.replace("120대", "121대" if url.endswith("1") else "120대")
    collect([unnamed(0), item(1, date="2025-08-01")], fetch=fetch, policy=replace(POLICY, batch_size=1))
    assert [url.rsplit("/", 1)[-1] for url in calls] == ["0", "1"]


def test_날짜보정으로_이월된_본문도_같은기간의_이름일치_순위를_따른다():
    analyzed = []
    def fetch(url):
        return NewsBodyFetchResult(text=BODY.replace("120대", "121대" if url.endswith("1") else "120대"),
                                   stage=c.BODY_STAGE_ARTICLE_TAG,
                                   published_on="2025-08-02" if url.endswith("0") else "2025-08-01")
    result = collect([unnamed(0), item(1, date="2025-08-01")], fetch=fetch,
                     analyze=analyzer(calls=analyzed), policy=replace(POLICY, batch_size=1))
    assert [row["articles"][0]["url"].rsplit("/", 1)[-1] for row in analyzed] == ["1", "0"]
    assert result.diagnostics["본문호출"] == 2


def test_비일치후보가_많아도_본문24와_기간16_4_4와_전송AI상한은_늘지_않는다():
    articles = [unnamed(n, date="2026-09-01" if n < 20 else "2025-08-01" if n < 35 else "2024-08-01") for n in range(50)]
    searches = []
    def search(query, *, remaining_transport_budget, **options):
        start = len(searches) * POLICY.search_page_size
        searches.append(remaining_transport_budget)
        return SimpleNamespace(state="success", reason_code="news_search_ok", items=articles[start:start + POLICY.search_page_size],
            transport_attempts=1, retry_recovered=False, attempt_reason_codes=("news_search_ok",))
    snap = collect_search_snapshot(search_news=search, company=COMPANY, as_of=AS_OF, policy=POLICY)
    result = collect_from_snapshot(snap, company=COMPANY, as_of=AS_OF,
        fetch_text=lambda url: BODY + " 다른 기사 번호 " + url.rsplit("/", 1)[-1],
        analyze_grounded=analyzer(lambda rows, payload: [{**row, "same_company": False, "excerpts": []} for row in rows]), policy=POLICY)
    assert len(snap.candidates) == 50
    assert result.diagnostics["본문시도기사"] == result.diagnostics["본문호출"] == 24
    assert tuple(row["본문"] for row in result.diagnostics["기간별"].values()) == (16, 4, 4)
    assert result.diagnostics["분석AI호출"] <= c.GROUNDED_CALL_BUDGET
    assert len(searches) <= c.SEARCH_TRANSPORT_ATTEMPT_BUDGET
    assert "window_body_budget" in result.diagnostics["상한사유"]
    assert not result.diagnostics["자료부족"] and not result.diagnostics["캐시재사용가능"]
