from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final

import pytest

from src.features.news_intake import constants as c
from src.features.news_intake.select import (
    needs_extended_window,
    news_eligible_sections,
    news_trigger,
    select_candidates,
)
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


@pytest.mark.parametrize(
    "title",
    [
        "해외 플랫폼 확대 기대…인이지 수혜주는",
        "인이지, AI 관련주로 묶여 거론",
        "인이지 테마주 열풍",
        "증권가가 본 인이지",
        "애널리스트가 짚은 인이지",
        "인이지 투자의견 상향",
        "인이지 목표가 제시",
        "인이지 시총 순위 변동",
        "인이지 시가총액 1조 돌파",
    ],
)
def test_증권_투자_칼럼_어휘가_있으면_기사를_뺀다(title: str) -> None:
    result = select_candidates("인이지", (), (_item(title),), AS_OF)

    assert result.selected_count == 0
    assert result.exclusion_counts[c.EXCLUDED_STOCK_ARTICLE] == 1


@pytest.mark.parametrize(
    "title",
    [
        "인이지 주가 급등 마감",
        "인이지 주가 급락 마감",
        "증권가, 인이지 매수 추천",
        "증권사, 인이지 매도 추천",
        "인이지 매수 의견 유지",
        "인이지 매도 의견 제시",
        "인이지 주주 가치 제고 요구",
        "소액주주, 인이지에 지분 공개 요구",
        # 띄어쓰기를 붙여 써도 같은 결합형으로 본다.
        "인이지 매수추천 리포트",
    ],
)
def test_증권_문맥_결합형이_있으면_기사를_뺀다(title: str) -> None:
    """낱말 하나로는 못 가르지만 붙어 나오면 증권 기사가 확실한 말."""

    result = select_candidates("인이지", (), (_item(title),), AS_OF)

    assert result.selected_count == 0
    assert result.exclusion_counts[c.EXCLUDED_STOCK_ARTICLE] == 1


@pytest.mark.parametrize(
    "title",
    [
        "인이지, 협력사 지분 매수를 마쳤다",
        "인이지 매출 급등",
        "인이지 수주 급락에도 생산 유지",
        "인이지 최대주주 변경",
        "인이지 주주환원 정책을 밝혔다",
        "인이지, 비핵심 자산 매도를 완료했다",
    ],
)
def test_사업_사실_기사는_증권_낱말이_있어도_남긴다(title: str) -> None:
    """「급등·급락·매수·매도·주주」는 회사가 한 일에도 쓰는 말이다.

    이 낱말들을 통째로 막으면 지분 취득·자산 처분·최대주주 변경 같은 공시
    사실 기사까지 사라진다. 그래서 결합형으로만 잡는다.
    """

    result = select_candidates("인이지", (), (_item(title),), AS_OF)

    assert result.selected_count == 1
    assert c.EXCLUDED_STOCK_ARTICLE not in result.exclusion_counts


@pytest.mark.parametrize(
    "title",
    [
        "[머니플러스] 인이지가 만든 공정",
        "[증시일보] 인이지 현장 취재",
        "［마켓워치］ 인이지 이야기",
        "【투자백서】 인이지 탐방",
        "[오늘의 종목] 인이지",
    ],
)
def test_대괄호_칼럼_꼬리표가_있으면_기사를_뺀다(title: str) -> None:
    result = select_candidates("인이지", (), (_item(title),), AS_OF)

    assert result.selected_count == 0
    assert result.exclusion_counts[c.EXCLUDED_STOCK_ARTICLE] == 1


@pytest.mark.parametrize(
    "title",
    [
        "인이지, 신제품을 출시했다",
        "인이지 제2공장 준공",
        "인이지 채용 확대",
        "[인터뷰] 인이지 대표가 말한 현장",
        "[단독] 인이지, 연구 협약을 체결했다",
        "인이지 주주총회에서 신사업 계획을 밝혔다",
    ],
)
def test_정상_기사는_칼럼_규칙에_걸리지_않는다(title: str) -> None:
    result = select_candidates("인이지", (), (_item(title),), AS_OF)

    assert result.selected_count == 1
    assert c.EXCLUDED_STOCK_ARTICLE not in result.exclusion_counts


#: 업종을 가리지 않는지 확인하는 표본. 회사명은 가공값이고, 업종 이름은
#: 시험이 무엇을 덮는지 읽는 사람에게 알려 주는 꼬리표일 뿐 규칙에 없다.
_INDUSTRY_NORMAL_TITLES: Final[tuple[tuple[str, str], ...]] = (
    ("제조", "인이지, 제3공장을 준공하고 양산을 시작했다"),
    ("금융", "인이지, 간편결제 신제품을 출시했다"),
    ("플랫폼", "인이지, 이용자 화면을 개편한 앱을 공개했다"),
    ("바이오", "인이지, 신약 후보물질 임상에 진입했다"),
)
_INDUSTRY_COLUMN_TITLES: Final[tuple[tuple[str, str], ...]] = (
    ("제조", "로봇 설비 확대…인이지 수혜주는"),
    ("금융", "금리 인하기 인이지 목표가 상향"),
    ("플랫폼", "[마켓인] 인이지 관련주 강세"),
    ("바이오", "[증시] 임상 발표에 인이지 매수 추천"),
)


@pytest.mark.parametrize(("industry", "title"), _INDUSTRY_NORMAL_TITLES)
def test_업종을_가리지_않고_정상_기사는_통과한다(industry: str, title: str) -> None:
    result = select_candidates("인이지", (), (_item(title),), AS_OF)

    assert result.selected_count == 1, industry
    assert c.EXCLUDED_STOCK_ARTICLE not in result.exclusion_counts


@pytest.mark.parametrize(("industry", "title"), _INDUSTRY_COLUMN_TITLES)
def test_업종을_가리지_않고_증권_칼럼은_걸린다(industry: str, title: str) -> None:
    result = select_candidates("인이지", (), (_item(title),), AS_OF)

    assert result.selected_count == 0, industry
    assert result.exclusion_counts[c.EXCLUDED_STOCK_ARTICLE] == 1


def test_결합형은_제목과_요약의_경계를_넘어_붙지_않는다() -> None:
    """제목 끝의 「지분 매수」와 요약 첫머리의 「추천」이 이어지면 안 된다.

    띄어쓰기를 지워 견주므로 두 칸을 이어 붙이면 없는 「매수 추천」이 생긴다.
    그래서 줄마다 따로 견준다.
    """

    result = select_candidates(
        "인이지",
        (),
        (
            _item(
                "인이지, 협력사 지분 매수",
                description="추천 상품군을 함께 넓힌다.",
            ),
        ),
        AS_OF,
    )

    assert result.selected_count == 1
    assert c.EXCLUDED_STOCK_ARTICLE not in result.exclusion_counts


def test_주주총회_기사라도_증권_어휘가_있으면_뺀다() -> None:
    """회사 공식 행사를 다뤄도 시세 어휘가 함께 있으면 증권 기사다.

    면제 상수는 없다. 어느 목록에도 낱말 「주주」가 단독으로 없어서, 예외를
    두지 않아도 주주총회 기사가 그대로 남는다(위 정상 기사 시험이 잠근다).
    """

    result = select_candidates(
        "인이지",
        (),
        (_item("인이지 주주총회 뒤 주가 급등"),),
        AS_OF,
    )

    assert result.selected_count == 0
    assert result.exclusion_counts[c.EXCLUDED_STOCK_ARTICLE] == 1


def test_낱말_주주는_어느_목록에도_단독으로_없다() -> None:
    """단독 「주주」가 들어오면 주주총회·주주환원 기사가 통째로 걸린다.

    면제 상수를 없앤 근거가 이 불변식이므로 시험으로 잠근다.
    """

    assert "주주" not in c.STOCK_KEYWORDS
    assert "주주" not in c.STOCK_PHRASES
    assert not hasattr(c, "STOCK_KEYWORD_EXEMPTIONS")


def test_칼럼_꼬리표_어휘는_괄호_밖에서는_걸리지_않는다() -> None:
    """「투자」·「종목」은 흔한 말이라 꼬리표 안에서만 칼럼 표식으로 본다."""

    result = select_candidates(
        "인이지",
        (),
        (_item("인이지, 연구 설비에 투자한다", description="설비 투자를 늘린다."),),
        AS_OF,
    )

    assert result.selected_count == 1
    assert c.EXCLUDED_STOCK_ARTICLE not in result.exclusion_counts


def _all_ready() -> dict[str, bool]:
    return {section_id: True for section_id in REQUIRED_EVIDENCE_SECTION_IDS}


def test_전장_READY이고_웹문서_0건이면_오육구장을_뺀_전부가_대상이다() -> None:
    eligible = news_eligible_sections(_all_ready(), official_web_documents=0)

    assert eligible == (
        frozenset(REQUIRED_EVIDENCE_SECTION_IDS)
        - c.NON_EXTENDABLE_SECTIONS
        - c.NEWS_EXCLUDED_SECTIONS
    )
    assert "current_challenges" not in eligible
    assert "future_strategy" not in eligible
    assert "competitive_position" not in eligible
    assert eligible  # 남는 장이 있어야 이 시험이 무엇을 지키는지 뜻이 있다


def test_구장은_미달이어도_뉴스_대상이_되지_않는다() -> None:
    """9장은 회사가 밝힌 것만 싣는 장이라 빈 장이어도 기자 서술을 받지 않는다."""

    ready = _all_ready()
    ready["competitive_position"] = False

    assert news_eligible_sections(ready, official_web_documents=0) == frozenset()
    assert news_eligible_sections(ready, official_web_documents=3) == frozenset()


def test_구장과_다른장이_함께_미달이면_다른장만_대상이다() -> None:
    ready = _all_ready()
    ready["competitive_position"] = False
    ready["portfolio"] = False

    assert news_eligible_sections(ready, official_web_documents=3) == frozenset(
        {"portfolio"}
    )


def test_전장_READY이고_웹문서가_있으면_대상이_없다() -> None:
    assert news_eligible_sections(_all_ready(), official_web_documents=1) == frozenset()


def test_빈_장이_있으면_웹문서_0건이어도_빈_장만_대상이다() -> None:
    ready = _all_ready()
    ready["portfolio"] = False

    assert news_eligible_sections(ready, official_web_documents=0) == frozenset(
        {"portfolio"}
    )


def test_오육장만_미달이면_그_둘만_대상이다() -> None:
    ready = _all_ready()
    ready["current_challenges"] = False
    ready["future_strategy"] = False
    expected = frozenset({"current_challenges", "future_strategy"})

    # 확장 창 판정은 5·6장을 보지 않지만, 대상 장 판정은 본다.
    assert needs_extended_window(ready) is False
    assert news_eligible_sections(ready, official_web_documents=0) == expected
    assert news_eligible_sections(ready, official_web_documents=3) == expected


def test_발동사유는_대상장과_같은_자리에서_나온다() -> None:
    """호출부가 「웹 문서가 0건인가」를 다시 계산하지 않게 사유를 함께 준다."""

    ready = _all_ready()
    assert news_trigger(ready, official_web_documents=3) == (frozenset(), c.NEWS_TRIGGER_NONE)

    eligible, reason = news_trigger(ready, official_web_documents=0)
    assert reason == c.NEWS_TRIGGER_WEB_ZERO
    assert eligible == news_eligible_sections(ready, official_web_documents=0)


def test_오육장만_미달이고_웹문서_0건이면_사유는_미달이다() -> None:
    """웹 0건이라는 사실만으로 「웹0건보강」이 되지 않는다.

    이 경우 대상은 5·6장뿐이고 기간도 1년이다. 사유를 웹 문서 수로 다시 세면
    창 이름이 「웹0건보강」으로 어긋난다.
    """

    ready = _all_ready()
    ready["current_challenges"] = False
    ready["future_strategy"] = False

    eligible, reason = news_trigger(ready, official_web_documents=0)

    assert eligible == frozenset({"current_challenges", "future_strategy"})
    assert reason == c.NEWS_TRIGGER_UNREADY
    assert needs_extended_window(ready) is False


def test_구장만_미달이면_사유가_없다() -> None:
    """대상이 비면 사유는 「없음」이고 호출부는 검색조차 하지 않는다."""

    ready = _all_ready()
    ready["competitive_position"] = False

    assert news_trigger(ready, official_web_documents=0) == (
        frozenset(),
        c.NEWS_TRIGGER_NONE,
    )


def test_문서_수가_정수가_아니면_거절한다() -> None:
    with pytest.raises(TypeError):
        news_eligible_sections(_all_ready(), official_web_documents=True)
    with pytest.raises(TypeError):
        news_eligible_sections(_all_ready(), official_web_documents="0")
    with pytest.raises(TypeError):
        news_eligible_sections((), official_web_documents=0)
