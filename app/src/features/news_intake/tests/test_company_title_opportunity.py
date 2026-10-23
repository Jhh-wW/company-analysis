"""제목의 전체 법인명은 제한된 기사 탐색 순위만 높인다."""

from dataclasses import replace

from src.features.news_intake.models import NewsCandidate, NewsCompanyContext
from src.features.news_intake.search_snapshot import body_ranked_candidates, diverse_candidates


COMPANY = NewsCompanyContext("가나다전자 주식회사", aliases=("ABC Electronics",))


def candidate(identifier, title, *, date="2026-10-06", topic="archive", matched=True):
    return NewsCandidate(
        id=identifier, title=title, description="가나다전자 관련 보도",
        originallink=f"https://media.example/{identifier}", link="",
        published_on=date, publisher="media.example", priority=1,
        source_url=f"https://media.example/{identifier}", source_category="news_report",
        topics=(topic,), metadata_name_match=matched,
    )


def test_최신종합기사에밀린_회사제품도_같은탐색예산으로읽는다():
    general = [candidate(f"general-{i}", f"여러 기업의 보안 현안 종합 {i}") for i in range(20)]
    product = candidate("product", "가나다전자, 센서 신제품 공급", date="2026-04-03")
    rows = [*general, product]
    ranked = body_ranked_candidates(rows, attempt_budget=4, probe_budget=0, company=COMPANY)
    assert ranked[0] is product
    assert len(ranked) == len(rows)
    assert {id(row) for row in ranked} == {id(row) for row in rows}
    assert sum(row is product for row in ranked[:4]) == 1


def test_주제순환과_같은등급의최신순서를유지한다():
    recent = candidate("recent", "여러 기업의 투자 현안", topic="recent")
    general = candidate("general", "전자 업계 신제품 종합")
    old = candidate("old", "가나다전자 센서 출시", date="2026-04-03")
    newer = candidate("new", "가나다전자 센서 공급", date="2026-09-03")
    # 최근 주제의 첫 자리는 제목에 회사가 없어도 보존한다.
    recent = replace(recent, published_on="2026-10-07")
    assert diverse_candidates([recent, general, old, newer], 4, company=COMPANY) == (
        recent, newer, old, general,
    )


def test_법인접두나그룹이름을_제목일치로오인하지않는다():
    unrelated = candidate("other", "가나다전자홀딩스의 신제품")
    alias = candidate("alias", "ABC Electronics launches sensors", date="2026-04-03")
    assert diverse_candidates([unrelated, alias], 2, company=COMPANY) == (alias, unrelated)


def test_회사문맥이없으면_기존최신순서를유지한다():
    general = candidate("general", "전자 업계 신제품 종합")
    old = candidate("old", "가나다전자 센서 출시", date="2026-04-03")
    assert diverse_candidates([old, general], 2) == (general, old)


def test_메타이름비일치_탐색몫과_후보내용은바꾸지않는다():
    direct = candidate("direct", "가나다전자 센서 출시")
    general = [candidate(f"general-{i}", f"여러 기업 현안 {i}") for i in range(4)]
    probe = candidate("probe", "신규 센서 공급 계약", matched=False)
    rows = [direct, *general, probe]
    ranked = body_ranked_candidates(rows, attempt_budget=4, probe_budget=1, company=COMPANY)
    assert ranked[0] is direct
    assert ranked[2] is probe
    assert {id(row) for row in ranked} == {id(row) for row in rows}
