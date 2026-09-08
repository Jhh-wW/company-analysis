"""공식 이름 표기 복구와 타법인·짧은 접두 오탐 방어의 회귀 시험."""

from dataclasses import replace

import pytest

from src.features.news_intake import constants as c
from src.features.news_intake.collection import collect_from_snapshot
from src.features.news_intake.identity_names import company_query_names, derived_company_names, mentions_target
from src.features.news_intake.models import NewsCompanyContext
from src.features.news_intake.tests.test_collection import (
    AS_OF, POLICY, analyzer, collect, item, snapshot,
)


@pytest.mark.parametrize("legal_name,aliases,public_name,context,body", [
    ("제이와이피엔터테인먼트", ("JYP Ent.", "JYP Entertainment Corporation"), "JYP엔터테인먼트",
     "음악 매니지먼트", "JYP엔터테인먼트는 음악 매니지먼트 사업에서 소속 그룹의 해외 20개 도시 공연을 제작했다."),
    ("와이지엔터테인먼트", ("YG ENTERTAINMENT Inc.",), "YG엔터테인먼트",
     "음악 매니지먼트", "YG엔터테인먼트는 음악 매니지먼트 사업에서 소속 그룹의 해외 공연 계약 3건을 체결했다."),
    ("에이비씨정밀", ("ABC Precision Co., Ltd.",), "ABC정밀",
     "산업설비 제조", "ABC정밀은 산업설비 제조 사업에서 제어 장치 120대를 고객 공장에 공급했다."),
    ("디에이치물류", ("DH Logistics Inc.",), "DH물류",
     "화물 운송", "DH물류는 화물 운송 사업에서 중소기업 고객의 해외 배송 거점 3곳을 새로 운영했다."),
])
def test_공식한영이름이_일치하는_전체통용표기를_검색과본문에서_회수한다(legal_name, aliases, public_name, context, body):
    company = NewsCompanyContext(legal_name, aliases=aliases, identity_context=context)
    article = item(title=public_name + " 사업 확대", description=public_name + " 신규 사업 소식")
    snap, searches = snapshot([article], company=company)
    assert derived_company_names(company) == (public_name,)
    assert any(query == public_name for query, _ in searches)
    assert len(searches) <= c.SEARCH_CALL_BUDGET
    assert len(snap.candidates) == 1 and snap.cache_eligible
    payloads = []
    result = collect_from_snapshot(snap, company=company, as_of=AS_OF, fetch_text=lambda url: body,
                                   analyze_grounded=analyzer(calls=payloads), policy=POLICY)
    assert len(result.fragments) == 1 and result.fragments[0].text == body
    assert public_name in payloads[0]["verified_company_names"]
    assert result.diagnostics["검증된이름변형"] == (public_name,)
    assert c.NAME_RESOLUTION_PENDING not in result.diagnostics["검증미완료"]


@pytest.mark.parametrize("name,aliases,unproved", [
    ("제이와이피엔터테인먼트", (), "JYP엔터테인먼트"),
    ("제이와이피엔터테인먼트", ("JPY Entertainment",), "JYP엔터테인먼트"),
    ("가나다전자", ("GN Electronics",), "GN전자"),
    ("가나다전자", (), "GN전자"),
])
def test_공식한영대응이_없으면_본문을_읽어도_추측약칭을_증명으로_쓰지_않는다(name, aliases, unproved):
    company = NewsCompanyContext(name, aliases=aliases)
    article = item(title=unproved + " 사업 확대", description=unproved + " 신규 제품 출시")
    snap, _ = snapshot([article], company=company)
    assert not derived_company_names(company)
    assert len(snap.candidates) == 1 and not snap.candidates[0].metadata_name_match
    assert snap.status == "success" and snap.cache_eligible and not snap.reason_codes
    calls = []
    def fetch(url):
        calls.append(url)
        return unproved + "는 해외 고객에게 새로운 산업설비 제품을 공급하는 계약을 체결했다."
    result = collect_from_snapshot(snap, company=company, as_of=AS_OF, fetch_text=fetch,
                                   analyze_grounded=analyzer(), policy=POLICY)
    assert len(calls) == 1 and not result.fragments
    assert result.diagnostics["완전성"] == "partial"
    assert result.diagnostics["이름미확인후보"] == 1
    assert result.diagnostics["실패"] is None
    assert "grounded_identity_unverified" in result.diagnostics["검증미완료"]
    assert not result.diagnostics["캐시재사용가능"]


def test_부분후보가_검증돼도_해결못한이름은_미완료와_캐시불가로_남긴다():
    company = NewsCompanyContext("가나다전자")
    articles = [item(0), item(1, title="GN전자 새로운 제품", description="GN전자 사업 확대")]
    result = collect(articles, company=company, fetch=lambda url:
        "GN전자는 해외 고객에게 새로운 산업설비 제품을 공급하는 계약을 체결했다." if url.endswith("1")
        else "가나다전자는 산업설비 제조 사업에서 제어 장치 120대를 고객 공장에 공급했다.")
    assert len(result.fragments) == 1
    assert result.diagnostics["검색상태"] == "success"
    assert "grounded_identity_unverified" in result.diagnostics["검증미완료"]
    assert not result.diagnostics["캐시재사용가능"]


def test_이름후보가_실제로_없었던_정상빈검색은_미확인을_만들지_않는다():
    company = NewsCompanyContext("가나다전자")
    snap, _ = snapshot([], company=company)
    assert snap.status == "empty" and snap.cache_eligible
    assert not snap.exclusion_counts


@pytest.mark.parametrize("text", [
    "JYP는 새 공연 사업을 확대했다.",
    "JYP엔터는 새 공연 사업을 확대했다.",
    "JYP엔터테인먼트미디어는 별도 영화 배급 법인이다.",
    "JYP엔터테인먼트와이즈는 다른 회사이다.",
    "다른JYP엔터테인먼트는 별도 회사이다.",
])
def test_파생표기의_접두만이나_더긴_타법인은_대상으로_인정하지_않는다(text):
    company = NewsCompanyContext("제이와이피엔터테인먼트", aliases=("JYP Ent.",))
    assert not mentions_target(text, company)
    assert "JYP" not in company_query_names(company)


@pytest.mark.parametrize("text", [
    "하이브미디어코프는 영화 제작과 배급 사업을 운영한다.",
    "HYBE미디어코프는 영화 제작과 배급 사업을 운영한다.",
    "HYBEMedia는 영화 제작과 배급 사업을 운영한다.",
    "하이브와이엔터는 별도 매니지먼트 법인이다.",
])
def test_하이브의_한글과영문_접두를_공유하는_타법인을_차단한다(text):
    company = NewsCompanyContext("하이브", aliases=("HYBE",), identity_context="음악 매니지먼트")
    assert not mentions_target(text, company)
    assert not derived_company_names(company)


def test_알파벳독음만으로_짧은영문접두를_신뢰별칭으로_승격하지_않는다():
    company = NewsCompanyContext("에이비씨", aliases=("ABC Company",))
    assert derived_company_names(company) == ()
    assert "ABC" not in company_query_names(company)
    assert not mentions_target("ABC는 공급 계약을 체결했다.", company)


def test_회사문맥이나_기사속_이름은_파생공식명_입력이_아니다():
    company = NewsCompanyContext("제이와이피엔터테인먼트", identity_context="사용자가 JYP Ent.라는 이름을 입력했다.")
    assert derived_company_names(company) == ()
    assert not mentions_target("JYP엔터테인먼트는 공연을 제작했다.", company)


@pytest.mark.parametrize("text", ["YG ENTERTAINMENT는 공연을 제작했다.", "YG Entertainment Inc.는 공연을 제작했다."])
def test_공식영문이름의_법인꼬리표와_대소문자는_공통정규화한다(text):
    company = NewsCompanyContext("와이지엔터테인먼트", aliases=("YG ENTERTAINMENT Inc.",))
    assert mentions_target(text, company)


def test_외국어이름의_한자나_다른문자를_삭제해_타사와_같게_만들지_않는다():
    company = NewsCompanyContext("가나다東京")
    assert mentions_target("가나다東京는 신규 제품을 공급했다.", company)
    assert not mentions_target("가나다北京는 신규 제품을 공급했다.", company)


def test_파생표기를_쓴_기사도_실제법인검수를_통과해야_한다():
    company = NewsCompanyContext("제이와이피엔터테인먼트", aliases=("JYP Ent.",), identity_context="음악 매니지먼트")
    body = "JYP엔터테인먼트는 음악 매니지먼트 계약을 검토했지만 다른 법인이 계약 당사자로 확인됐다."
    result = collect([item(title="JYP엔터테인먼트 계약 검토", description="JYP엔터테인먼트 사업 소식")],
        company=company, fetch=lambda url: body,
        analyze=analyzer(lambda rows, payload: [{**row, "same_company": False} for row in rows]))
    assert not result.fragments and result.diagnostics["제외"]["grounded_wrong_company"] == 1


def test_공식이름증명이_바뀌면_같은기사라도_snapshot이_다르다():
    company = NewsCompanyContext("제이와이피엔터테인먼트", aliases=("JYP Ent.",))
    article = item(title="JYP엔터테인먼트 공연 제작", description="JYP엔터테인먼트 사업 소식")
    proved, _ = snapshot([article], company=company)
    unproved, _ = snapshot([article], company=replace(company, aliases=()))
    assert proved.digest != unproved.digest and proved.company_digest != unproved.company_digest
    with pytest.raises(ValueError, match="결속"):
        collect_from_snapshot(proved, company=replace(company, aliases=()), as_of=AS_OF,
            fetch_text=lambda url: "", analyze_grounded=analyzer(), policy=POLICY)
