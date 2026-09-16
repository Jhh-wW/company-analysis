"""뉴스 신원 문맥과 현재성 캐시 결속의 회사 공통 회귀 시험."""

from types import SimpleNamespace

from src.features.pipeline.constants import NEWS_IDENTITY_CONTEXT_CHARS
from src.features.pipeline.news_research_context import (
    news_generation_digest,
    official_news_context,
)


from src.shared.report_evidence.constants import OFFICIAL_WEB_SOURCE_KINDS


def _candidate(section_id, *, text, kind="dart_business_report", url="https://dart.fss.or.kr/", bound=True):
    document = SimpleNamespace(
        document_id="document", source_kind=kind, canonical_url=url,
        identity_binding="verified-company" if bound else "",
    )
    fragment = SimpleNamespace(document_id="document", text=text)
    return SimpleNamespace(section_id=section_id, documents=(document,), fragments=(fragment,))


def test_profile_homepage_alone_does_not_attest_its_current_owner():
    domain, context = official_news_context(
        {"corp_name": "가나다교육", "hm_url": "https://reassigned.example"}, None
    )
    assert domain == ""
    assert context == ""


def test_공식_사업원문이_없으면_개황_필드명을_본문_일치조건으로_쓰지_않는다():
    _, context = official_news_context(
        {"corp_name": "가나다전자", "corp_name_eng": "Ganada", "induty_code": "26299", "ceo_nm": "홍길동"},
        None,
    )
    assert context == ""


def test_only_bound_official_document_can_supply_company_context():
    evidence = SimpleNamespace(candidates=(
        _candidate("identity", text="검증된 교육 회사입니다."),
        _candidate("portfolio", text="다른 회사의 제품입니다.", bound=False),
    ))
    _, context = official_news_context({}, evidence)
    assert "검증된 교육 회사" in context
    assert "다른 회사" not in context


def test_verified_exact_homepage_is_usable_but_unrelated_host_is_not():
    evidence = SimpleNamespace(candidates=(
        _candidate("identity", text="기업 교육을 제공합니다.",
                   kind=next(iter(OFFICIAL_WEB_SOURCE_KINDS)), url="https://training.example/about"),
    ))
    domain, _ = official_news_context({"hm_url": "https://training.example"}, evidence)
    assert domain == "https://training.example"
    domain, _ = official_news_context({"hm_url": "training.example"}, evidence)
    assert domain == "https://training.example"
    domain, _ = official_news_context({"hm_url": "https://other.example"}, evidence)
    assert domain == ""
    domain, _ = official_news_context({"hm_url": "https://[잘못된주소"}, evidence)
    assert domain == ""


def test_long_identity_section_does_not_displace_other_company_context():
    identity = _candidate("identity", text="회사 역사 " * NEWS_IDENTITY_CONTEXT_CHARS)
    identity.fragments = tuple(SimpleNamespace(document_id="document", text=f"{n} {identity.fragments[0].text}") for n in range(20))
    evidence = SimpleNamespace(candidates=(identity, _candidate("portfolio", text="회사 소속 아티스트 라인업")))
    _, context = official_news_context({}, evidence)
    assert len(context) <= NEWS_IDENTITY_CONTEXT_CHARS
    assert "회사 소속 아티스트 라인업" in context


def test_news_change_and_date_change_invalidate_unchanged_dart_input():
    first = news_generation_digest("same-dart", news_snapshot_digest="news-a", as_of="2026-09-08")
    assert first == news_generation_digest("same-dart", news_snapshot_digest="news-a", as_of="2026-09-08")
    assert first != news_generation_digest("same-dart", news_snapshot_digest="news-b", as_of="2026-09-08")
    assert first != news_generation_digest("same-dart", news_snapshot_digest="news-a", as_of="2026-09-09")
    assert first != news_generation_digest("changed-dart", news_snapshot_digest="news-a", as_of="2026-09-08")
