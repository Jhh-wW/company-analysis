from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from src.features.news_intake import constants as c
from src.features.news_intake.select import needs_extended_window, select_candidates
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS


@dataclass(frozen=True)
class FakeNewsItem:
    title: str
    originallink: str
    link: str
    description: str
    pubDate: str


AS_OF = dt.date(2026, 9, 6)


def _item(
    title: str,
    *,
    description: str = "인이지가 새로운 소식을 전했다.",
    date: str = "2026-08-01",
    url: str = "https://media.example/article",
) -> FakeNewsItem:
    return FakeNewsItem(title, url, "https://n.news.naver.com/x", description, date)


def test_시뮬레이션_기사_열건과_뺀기사_다섯건을_기계필터한다() -> None:
    valid = [
        _item("인이지 뉴스룸 소식", url="https://in-easy.example/newsroom/a"),
        _item("인이지, 제조 AI를 출시했다", url="https://press.example/2"),
        _item("인이지, 연구 협약을 체결했다", url="https://press.example/3"),
        _item("[인터뷰] 인이지 대표가 말한 현장", url="https://press.example/4"),
        _item("인이지 최적화 기술의 적용", url="https://press.example/5"),
        _item("인이지 기술상 수상", url="https://press.example/6"),
        _item("인이지와 산업 현장", url="https://press.example/7"),
        _item("인이지 제품 이야기", url="https://press.example/8"),
        _item("인이지 고객 사례", url="https://press.example/9"),
        _item("인이지 기술 철학", url="https://press.example/10"),
    ]
    removed = [
        _item("일반 AI 산업 이야기", description="제조 현장을 다룬 기사다.", url="https://drop.example/1"),
        _item("인이지 신규 사업", description="업계에 따르면 인이지가 사업을 검토한다.", url="https://drop.example/2"),
        _item("인이지 주가 장중 상승", url="https://drop.example/3"),
        _item("제조 AI 정책 토론", description="일반 정책 발언만 다뤘다.", url="https://drop.example/4"),
        _item("인이지 과거 기사", date="2024-01-01", url="https://drop.example/5"),
    ]

    result = select_candidates(
        "주식회사 인이지",
        ("인이지",),
        [*valid, *removed],
        AS_OF,
        company_domain="in-easy.example",
        executive_names=("최대표",),
    )

    assert result.searched_count == 15
    assert result.selected_count == 10
    assert result.exclusion_counts[c.EXCLUDED_COMPANY_NOT_MENTIONED] == 2
    assert result.exclusion_counts[c.EXCLUDED_RUMOR_ONLY] == 1
    assert result.exclusion_counts[c.EXCLUDED_STOCK_ARTICLE] == 1
    assert result.exclusion_counts[c.EXCLUDED_OUTSIDE_WINDOW] == 1


def test_같은_보도자료는_뉴스룸을_남기고_하나로_줄인다() -> None:
    result = select_candidates(
        "인이지",
        (),
        (
            _item(
                "인이지 클라우드 AI 서비스 출시",
                date="2026-08-30",
                url="https://media.example/cloud",
            ),
            _item(
                "인이지, 클라우드 AI 서비스를 출시했다",
                date="2026-08-20",
                url="https://in-easy.example/newsroom/cloud",
            ),
            _item(
                "인이지 클라우드 AI 서비스 출시 소식",
                date="2026-08-25",
                url="https://in-easy.example/about/cloud",
            ),
        ),
        AS_OF,
        company_domain="in-easy.example",
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].source_url.endswith("/newsroom/cloud")
    assert result.exclusion_counts[c.EXCLUDED_DUPLICATE_RELEASE] == 2


def test_우선순위_네단계와_각단계_최신순을_지킨다() -> None:
    result = select_candidates(
        "인이지",
        (),
        (
            _item("인이지 일반 소식", date="2026-09-01", url="https://m.example/o"),
            _item("인이지, 사업 계약을 체결했다", date="2026-08-01", url="https://m.example/p1"),
            _item("인이지, 연구 성과를 공개했다", date="2026-08-20", url="https://m.example/p2"),
            _item("[인터뷰] 인이지의 미래", date="2026-08-30", url="https://m.example/i"),
            _item("인이지 뉴스룸", date="2026-07-01", url="https://in-easy.example/news/a"),
        ),
        AS_OF,
        company_domain="in-easy.example",
    )

    assert [item.priority for item in result.candidates] == [1, 2, 2, 3, 4]
    assert [item.title for item in result.candidates[1:3]] == [
        "인이지, 연구 성과를 공개했다",
        "인이지, 사업 계약을 체결했다",
    ]


def test_기사_후보_상한은_스무건이다() -> None:
    result = select_candidates(
        "인이지",
        (),
        tuple(
            _item(f"인이지 독립 기사 {index}", url=f"https://m.example/{index}")
            for index in range(25)
        ),
        AS_OF,
    )

    assert len(result.candidates) == c.MAX_CANDIDATES
    assert result.exclusion_counts[c.EXCLUDED_CANDIDATE_LIMIT] == 5


def test_확장창은_오장과_육장을_제외한_READY_미달만_본다() -> None:
    ready = {section_id: True for section_id in REQUIRED_EVIDENCE_SECTION_IDS}
    ready["current_challenges"] = False
    ready["future_strategy"] = False
    assert needs_extended_window(ready) is False

    ready["portfolio"] = False
    assert needs_extended_window(ready) is True


def test_확장호출은_삼년안_기사를_후보로_받는다() -> None:
    old = _item("인이지 과거 실행", date="2024-04-29")

    assert select_candidates("인이지", (), (old,), AS_OF).selected_count == 0
    assert (
        select_candidates(
            "인이지", (), (old,), AS_OF, extended_window=True
        ).selected_count
        == 1
    )


def test_혼합요약은_업계표지가_있어도_통째로_버리지_않는다() -> None:
    result = select_candidates(
        "인이지",
        (),
        (
            _item(
                "인이지 신제품",
                description="업계에 따르면 인이지가 검토했다. 회사는 출시 사실을 밝혔다.",
            ),
        ),
        AS_OF,
    )

    assert result.selected_count == 1
