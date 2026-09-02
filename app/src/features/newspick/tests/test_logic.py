"""뉴스 AI 선별을 못 박는다 (문제로그 P-108).

★ 여기서 지키는 것은 둘이다.
  ① **AI가 준 글자는 하나도 안 쓴다** — 번호만 받고 원문은 프로그램이 복사한다.
  ② **AI가 이상한 답을 줘도 안 깨진다** — 없는 번호·중복·엉뚱한 딱지는 버린다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Optional

import pytest

from src.features.newspick.constants import FRAGMENT_KIND, USE_KINDS
from src.features.newspick.logic import (
    Candidate,
    looks_like_other_company,
    apply_picks,
    build_prompt,
    ceo_name,
    count_other_corps,
    interleave,
    pick_with_ai,
    prefilter,
    press_of,
    profile_lines,
    search_terms,
    to_fragments,
)

오늘 = dt.date(2026, 8, 16)


@dataclass(frozen=True)
class 가짜기사:
    """네이버 `NewsItem`과 «같은 이름의 속성»만 갖는다."""

    title: str = "제목"
    description: str = "본문 요약"
    pub_date: Optional[dt.date] = 오늘
    link: str = "https://n.news.naver.com/1"
    originallink: str = "https://www.hankyung.com/article/1"


# ══════════════════════════════════════════════════════════
# ⓪ 여러 검색 섞기
# ══════════════════════════════════════════════════════════


def test_검색_결과를_번갈아_섞는다():
    """★★ 앞에서부터 이어 붙이면 **첫 검색어가 자리를 다 차지한다.**

    실측 — 회사 이름만 최신순으로 찾으면 20건이 전부 그날 기사라
    「실적」·「신사업」 검색 결과가 후보에 **한 건도 못 들어간다.**
    """
    최근 = [가짜기사(title=f"오늘기사{i}") for i in range(5)]
    실적 = [가짜기사(title=f"실적기사{i}") for i in range(5)]

    섞은것 = interleave([최근, 실적], limit=4)

    assert [i.title for i in 섞은것] == ["오늘기사0", "실적기사0", "오늘기사1", "실적기사1"]


def test_같은_기사는_한_번만_넣는다():
    """★ 같은 기사가 매체마다 다른 주소로 올라온다 — **제목**으로 가른다.

    실측 — 캣츠아이 스포티파이 기사가 4개 매체에 거의 같은 제목으로 올라왔다.
    """
    가 = [가짜기사(title="캣츠아이, 스포티파이 6위", link="https://a.com/1")]
    나 = [가짜기사(title="캣츠아이 스포티파이 6위", link="https://b.com/2")]

    assert len(interleave([가, 나], limit=10)) == 1


def test_짧은_묶음이_먼저_떨어져도_계속_섞는다():
    긴것 = [가짜기사(title=f"가{i}") for i in range(4)]
    짧은것 = [가짜기사(title="나0")]

    섞은것 = interleave([긴것, 짧은것], limit=10)

    assert [i.title for i in 섞은것] == ["가0", "나0", "가1", "가2", "가3"]


def test_검색이_전부_비면_빈_목록():
    assert interleave([], limit=10) == []
    assert interleave([[], []], limit=10) == []


# ══════════════════════════════════════════════════════════
# ① 사전 걸러내기 — 코드가 하는 부분
# ══════════════════════════════════════════════════════════


def test_회사_이름이_없어도_후보로_올린다():
    """★ 이번 변경의 «핵심».

    「BTS 신곡 발표」는 하이브 이름이 한 글자도 없지만 하이브 기사다.
    글자 맞추기로는 알 수 없으므로 **AI 앞까지는 보낸다.**
    """
    items = [가짜기사(title="BTS 정국, 새 앨범 발표")]

    후보, _ = prefilter(items, company="하이브", today=오늘)

    assert len(후보) == 1
    assert 후보[0].title == "BTS 정국, 새 앨범 발표"


@pytest.mark.parametrize(
    "기사, 사유",
    [
        (가짜기사(pub_date=None), "날짜없음"),
        (가짜기사(pub_date=dt.date(2020, 1, 1)), "오래됨"),
        (
            가짜기사(title="㈜에이전시 ㈜비전테크 ㈜씨엔에스 실적 발표"),
            "나열기사",
        ),
    ],
)
def test_날짜와_나열기사는_코드가_먼저_버린다(기사: 가짜기사, 사유: str):
    """★ 싸고 확실한 거절은 AI에게 안 맡긴다 — 값이 싸진다."""
    후보, 버림 = prefilter([기사], company="하이브", today=오늘)

    assert 후보 == []
    assert 버림[사유] == 1


def test_후보_번호는_1부터_차례로_붙는다():
    """★ 버려진 기사 때문에 번호가 건너뛰면 AI가 헷갈린다."""
    items = [가짜기사(pub_date=None), 가짜기사(title="가"), 가짜기사(title="나")]

    후보, _ = prefilter(items, company="하이브", today=오늘)

    assert [c.number for c in 후보] == [1, 2]


def test_후보_수에_상한이_있다():
    후보, _ = prefilter(
        [가짜기사(title=f"기사{i}") for i in range(50)],
        company="하이브", today=오늘, limit=5,
    )

    assert len(후보) == 5


@pytest.mark.parametrize(
    "본문, 기대",
    [
        ("㈜에이전시와 ㈜비전테크가 합작", 2),
        ("하이브가 발표했다", 0),              # ㈜ 표기가 없으면 안 센다
        ("㈜하이브가 발표했다", 0),            # ★ 자기 회사는 안 센다
        ("㈜하이브와 ㈜에이전시가 합작", 1),   # ★ 자기 회사를 뺀 나머지만
    ],
)
def test_다른_회사_세기(본문: str, 기대: int):
    """★ 자기 회사를 「다른 회사」로 세면 나열 기사 문턱에 빨리 닿아
    **멀쩡한 기사를 버린다.** 조사를 떼고 비교해야 한다.
    """
    assert count_other_corps(본문, "하이브") == 기대


def test_매체는_원_기사_주소에서_알아낸다():
    """★ 네이버 API가 매체 이름을 안 준다 — 1판이 쓰던 방법 그대로다."""
    assert press_of("https://www.hankyung.com/a/1", "https://n.news.naver.com/1") \
        == "www.hankyung.com"
    assert press_of("", "https://n.news.naver.com/1") == "n.news.naver.com"
    assert press_of("", "") == "출처미상"


# ══════════════════════════════════════════════════════════
# ② 지시문
# ══════════════════════════════════════════════════════════


def test_지시문에_회사_단서가_들어간다():
    """★ 이게 없으면 «이름만 같은 다른 회사»를 가를 수 없다."""
    프롬프트 = build_prompt(
        "하이브",
        {"induty_code_nm": "음반 및 기타 오디오물 출판업", "ceo_nm": "김대표"},
        [Candidate(1, "제목", "본문", 오늘, "a.com")],
    )

    assert "음반 및 기타 오디오물 출판업" in 프롬프트
    assert "김대표" in 프롬프트


def test_지시문에_기사가_번호와_함께_들어간다():
    프롬프트 = build_prompt(
        "하이브", {}, [Candidate(1, "BTS 신곡", "요약입니다", 오늘, "a.com")]
    )

    assert "[1]" in 프롬프트
    assert "BTS 신곡" in 프롬프트
    assert "2026-08-16" in 프롬프트


def test_지시문이_요약을_금지한다():
    """★ AI는 «고르기»만 한다. 글을 쓰면 정본 규칙 2 위반이다."""
    프롬프트 = build_prompt("하이브", {}, [Candidate(1, "가", "나", 오늘, "a.com")])

    assert "요약하거나 새로 쓰지 마세요" in 프롬프트
    assert "억지로 채우지 마세요" in 프롬프트


def test_비어있는_기업개황은_건너뛴다():
    assert profile_lines({}) == []
    assert profile_lines({"ceo_nm": "  "}) == []


# ══════════════════════════════════════════════════════════
# ③ 답 받기 — ★ 여기가 가장 중요하다
# ══════════════════════════════════════════════════════════


def _후보들() -> list[Candidate]:
    return [
        Candidate(1, "BTS 신곡 발표", "하이브 소속…", oy := oy_date(), "a.com"),
        Candidate(2, "주가 급등", "…", oy, "b.com"),
    ]


def oy_date() -> dt.date:
    return 오늘


def test_고른_번호의_원문을_그대로_가져온다():
    """★★ **AI가 준 글자는 하나도 안 쓴다.**

    AI가 답에 엉뚱한 제목을 적어 보내도, 프로그램은 «번호»로 원문을 찾는다.
    """
    골랐다, 버림 = apply_picks(
        _후보들(),
        {"고른기사": [{"번호": 1, "쓰임새": "성과", "제목": "AI가 지어낸 제목"}]},
        "하이브",
    )

    assert len(골랐다) == 1
    assert 골랐다[0].candidate.title == "BTS 신곡 발표"
    assert sum(버림.values()) == 0


@pytest.mark.parametrize(
    "답",
    [
        {"고른기사": [{"번호": 99, "쓰임새": "성과"}]},          # 없는 번호
        {"고른기사": [{"번호": 1, "쓰임새": "아무거나"}]},        # 모르는 딱지
        {"고른기사": [{"쓰임새": "성과"}]},                       # 번호가 없다
    ],
)
def test_이상한_답은_버린다(답: dict[str, Any]):
    """★ 정본 W2와 같은 방식 — 없는 번호를 고르면 그 항목을 버린다."""
    골랐다, 버림 = apply_picks(_후보들(), 답, "하이브")

    assert 골랐다 == []
    assert 버림["모르는번호"] == 1


def test_같은_번호를_두_번_고르면_한_번만_쓴다():
    골랐다, 버림 = apply_picks(
        _후보들(),
        {"고른기사": [{"번호": 1, "쓰임새": "성과"}, {"번호": 1, "쓰임새": "전략"}]},
        "하이브",
    )

    assert len(골랐다) == 1
    assert 버림["중복"] == 1


def test_AI_호출이_실패해도_안_깨진다():
    """★ 뉴스가 없다고 보고서 전체가 멈추면 안 된다."""
    골랐다, 버림 = apply_picks(_후보들(), None, "하이브")

    assert 골랐다 == []
    assert sum(버림.values()) == 0


def test_채택_상한을_넘지_않는다():
    후보 = [Candidate(i, f"제목{i}", "본문", 오늘, "a.com") for i in range(1, 11)]

    골랐다, _ = apply_picks(
        후보, {"고른기사": [{"번호": i, "쓰임새": "성과"} for i in range(1, 11)]},
        "하이브", limit=3
    )

    assert len(골랐다) == 3


# ── ★ 이름 비슷한 다른 회사 거부 ─────────────────────────


@pytest.mark.parametrize(
    "글, 다른회사인가",
    [
        # ★ 실측으로 잡힌 것 — 「하이브미디어코프」는 영화 제작사로 하이브와 무관하다.
        ("'암살자(들)', 제작사 하이브미디어코프의 현대사 유니버스", True),
        ("하이브미디어코프가 신작을 내놓는다", True),
        # 제대로 가리키는 자리가 하나라도 있으면 통과한다
        ("하이브는 소속 아티스트의 공연을 제작한다", False),
        ("하이브측은 입장을 밝혔다", False),
        ("하이브(HYBE) 글로벌 걸그룹", False),
        ("하이브, 2분기 최대 실적", False),
        ("하이브미디어코프는 하이브와 무관하다", False),
        # ★★ 이름이 아예 안 나오면 **막지 않는다** — 이번 변경의 핵심이다
        ("BTS 정국, 새 앨범 발표", False),
        ("캣츠아이 '후티 프루티' 스포티파이 6위", False),
    ],
)
def test_이름이_더_긴_다른_회사면_거부한다(글: str, 다른회사인가: bool):
    """★ AI가 지시문으로 막아도 계속 골랐다 — **확인은 코드가 한다.**

    ⚠️ 마지막 두 줄이 중요하다. 브랜드로만 난 기사를 여기서 되레 막으면
    이번 변경 자체가 무의미해진다.
    """
    assert looks_like_other_company(글, "하이브") is 다른회사인가


def test_이름_비슷한_다른_회사는_AI가_골라도_버린다():
    후보 = [Candidate(1, "하이브미디어코프 신작 공개", "제작사 하이브미디어코프가", 오늘, "a.com")]

    골랐다, 버림 = apply_picks(후보, {"고른기사": [{"번호": 1, "쓰임새": "전략"}]}, "하이브")

    assert 골랐다 == []
    assert 버림["이름다른회사"] == 1


# ══════════════════════════════════════════════════════════
# ④ 조각 만들기
# ══════════════════════════════════════════════════════════


def test_조각_모양이_1판과_같다():
    """★ 모양이 다르면 뒤쪽(문장 고르기·출처 각주)이 뉴스를 못 알아본다."""
    골랐다, _ = apply_picks(_후보들(), {"고른기사": [{"번호": 1, "쓰임새": "성과"}]}, "하이브")

    조각 = to_fragments(골랐다)

    assert 조각[0]["종류"] == FRAGMENT_KIND
    assert 조각[0]["원문"].startswith("(2026-08-16 보도 · a.com) ")
    assert "BTS 신곡 발표" in 조각[0]["원문"]


# ══════════════════════════════════════════════════════════
# ⑤ 통째로
# ══════════════════════════════════════════════════════════


def test_후보가_없으면_AI를_안_부른다():
    """★ 돈이 나가는 일이다. 부를 이유가 없으면 안 부른다."""
    불렀나 = []

    def ask(prompt, schema):
        불렀나.append(1)
        return {"고른기사": []}, {}

    골랐다, 기록 = pick_with_ai(ask, company="하이브", profile={}, candidates=[])

    assert 골랐다 == []
    assert 불렀나 == []
    assert 기록["후보"] == 0


def test_통째로_돌린다():
    def ask(prompt, schema):
        assert "BTS 신곡 발표" in prompt
        return {"고른기사": [{"번호": 1, "쓰임새": "성과"}]}, {"in": 100, "out": 20}

    골랐다, 기록 = pick_with_ai(
        ask, company="하이브", profile={"ceo_nm": "김대표"}, candidates=_후보들()
    )

    assert len(골랐다) == 1
    assert 골랐다[0].use_kind == "성과"
    assert 기록["채택"] == 1
    assert 기록["사용량"]["in"] == 100


def test_AI가_실패하면_사유가_기록에_남는다():
    """★ 조용한 실패 금지 — 왜 뉴스가 없는지 사용자가 알아야 한다."""
    골랐다, 기록 = pick_with_ai(
        lambda p, s: (None, {"error": "APIError"}),
        company="하이브", profile={}, candidates=_후보들(),
    )

    assert 골랐다 == []            # 후보 제목에 「하이브」가 없다
    assert 기록["오류"] == "APIError"
    assert "되돌아감" in 기록["비고"]


def test_AI가_실패하면_1판_방식으로_되돌아간다():
    """★ AI 한 번 삐끗했다고 뉴스를 통째로 버리지 않는다.

    덜 잡지만(제목에 이름이 있어야 한다) **틀리지는 않는다.**
    """
    후보 = [
        Candidate(1, "하이브, 신사옥 착공", "…", 오늘, "a.com"),
        Candidate(2, "BTS 정국 신곡", "…", 오늘, "b.com"),
    ]

    골랐다, 기록 = pick_with_ai(
        lambda p, s: (None, {"error": "APIError"}),
        company="하이브", profile={}, candidates=후보,
    )

    assert [p.candidate.number for p in 골랐다] == [1]
    assert 기록["채택"] == 1


def test_되돌아갈_때도_AI를_다시_안_부른다():
    """★ 돈이 두 번 나가고, 같은 이유로 또 실패할 수 있다."""
    부른횟수 = []

    def ask(prompt, schema):
        부른횟수.append(1)
        return None, {"error": "APIError"}

    pick_with_ai(ask, company="하이브", profile={}, candidates=_후보들())

    assert len(부른횟수) == 1


def test_쓰임새는_쓸_자리가_있는_것만_둔다():
    """★ 여기서 새 갈래를 만들면 뒤에서 쓸 자리가 없다.

    과제·성과·전략은 정본 4번 세 칸에 그대로 대응하고,
    경영진발언은 2026-08-16 사용자 지시로 추가한 갈래다.
    """
    assert USE_KINDS == ("과제", "성과", "전략", "경영진발언")


# ── ★ 경영진 발언 — 가장 최신 것 하나만 ──────────────────


def test_경영진_발언은_가장_최신_하나만_남긴다():
    """★ 2026-08-16 사용자 지시: 「여러 개면 가장 최신 꺼로」.

    ⚠️ 왜 하나만 — 같은 대표가 여러 자리에서 비슷한 말을 한다.
    다 실으면 «같은 이야기 반복»이 되고 정작 다른 재료가 밀려난다.
    """
    후보 = [
        Candidate(1, "대표 인터뷰 작년", "…", dt.date(2025, 3, 1), "a.com"),
        Candidate(2, "대표 인터뷰 올해", "…", dt.date(2026, 8, 1), "b.com"),
        Candidate(3, "대표 인터뷰 중간", "…", dt.date(2026, 2, 1), "c.com"),
    ]

    골랐다, _ = apply_picks(
        후보,
        {"고른기사": [{"번호": i, "쓰임새": "경영진발언"} for i in (1, 2, 3)]},
        "하이브",
    )

    assert len(골랐다) == 1
    assert 골랐다[0].candidate.number == 2      # 2026-08-01 이 가장 최신


def test_다른_딱지는_여러_개_남는다():
    """★ 하나만 남기는 것은 «경영진 발언»뿐이다. 나머지를 줄이면 안 된다."""
    후보 = [
        Candidate(1, "가", "…", 오늘, "a.com"),
        Candidate(2, "나", "…", 오늘, "b.com"),
    ]

    골랐다, _ = apply_picks(
        후보,
        {"고른기사": [{"번호": 1, "쓰임새": "성과"}, {"번호": 2, "쓰임새": "전략"}]},
        "하이브",
    )

    assert len(골랐다) == 2


def test_경영진_발언이_하나뿐이면_그대로_둔다():
    후보 = [Candidate(1, "대표 인터뷰", "…", 오늘, "a.com")]

    골랐다, _ = apply_picks(
        후보, {"고른기사": [{"번호": 1, "쓰임새": "경영진발언"}]}, "하이브"
    )

    assert len(골랐다) == 1


def test_경영진_발언_설명이_발언_인용을_요구한다():
    """★ 이 조건이 없으면 임원 이름만 스친 인사·수상 기사가 딸려 온다."""
    from src.features.newspick.constants import USE_KIND_DESC

    설명 = USE_KIND_DESC["경영진발언"]

    assert "직접 말한" in 설명
    assert "발언이 인용된 기사만" in 설명


# ══════════════════════════════════════════════════════════
# 대표자 이름 — 검색어를 망가뜨리지 않는가
#
# ★ 왜 이 시험이 생겼나 (2026-08-27)
#   검색어 6개 중 2개가 「{ceo} 인터뷰」·「{ceo} 전략」이다. 「대표가 직접 한 말」은
#   **공시에 없는 말**이라 자소서에 쓰면 진짜 찾아봤다는 증거가 되는 재료다.
#   그런데 `ceo_name` 을 지키는 시험이 **0건**이었다 — 규칙을 지워도 아무 데도 빨간불이
#   안 떴다. 실제로 전자공시 30곳을 물어 보니 1곳(삼성바이오로직스)에서 깨지고 있었다.
#
#   아래 값들은 «지어낸 것이 아니라» 2026-08-27 에 전자공시 기업개황(company.json)에서
#   실제로 받은 문자열이다.
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("공시값", "기대"),
    [
        # ── 대표자 표본 시작 ── (test_sample_names.py가 이 사이만 검사한다)
        # ★ 이름은 전부 «가명»이다. 전자공시에서 실제로 받아온 값을 그대로 두면
        #   실존 임원 이름이 공개 저장소에 남는다. 이 시험이 지키는 것은 이름이
        #   아니라 «값의 꼴»이므로, 실측에서 본 꼴만 그대로 옮겨 적었다.
        # 쉼표로 갈리는 공동대표 — 첫 사람만 쓴다
        ("김대표, 이대표", "김대표"),                              # 쉼표+공백, 2명
        ("박대표, 최대표", "박대표"),                              # 쉼표+공백, 2명
        ("정대표,강대표(각자 대표이사)", "정대표"),                  # 쉼표 뒤 공백이 없는 꼴
        ("조대표, 윤대표(각자대표이사)", "조대표"),                  # 「각자대표이사」 띄어쓰기가 다른 꼴
        (
            "한대표, 가나 다라 마바 사아, 오대표(각자 대표이사)",
            "한대표",
        ),                                                        # 3명 + 여러 토막 이름
        # ★ 쉼표가 «없는데» 괄호가 붙는 경우 — 고치기 전에는 여기서 깨졌다
        ("서대표 (Seo Daepyo Sample)", "서대표"),                  # 쉼표 없이 괄호 영문명
        # 첫 사람에 직함 괄호가 붙어도 뗀다
        ("오대표(각자 대표이사), 홍길동", "오대표"),
        # 홑이름·외국식 이름은 그대로 둔다
        ("김대표", "김대표"),                                      # 홑이름
        ("가나 다라 마바", "가나 다라 마바"),                        # 외국식 여러 토막 이름
        ("이대표", "이대표"),                                      # 홑이름
        # 값이 없으면 빈 문자열
        ("", ""),
        # ── 대표자 표본 끝 ──
    ],
)
def test_대표자_이름에서_괄호와_공동대표를_걷어낸다(공시값: str, 기대: str):
    assert ceo_name({"ceo_nm": 공시값}) == 기대


def test_대표자_칸이_아예_없어도_안_깨진다():
    assert ceo_name({}) == ""
    assert ceo_name({"ceo_nm": None}) == ""


def test_뽑은_이름에는_괄호가_남지_않는다():
    """★ 괄호가 남으면 검색어가 통째로 망가진다 — 기사가 0건이 된다."""
    공시값들 = (
        "서대표 (Seo Daepyo Sample)",
        "오대표(각자 대표이사)",
        "홍길동（전각괄호）",
    )

    for 값 in 공시값들:
        뽑힘 = ceo_name({"ceo_nm": 값})
        assert not any(글자 in 뽑힘 for 글자 in "()（）"), f"{값!r} → {뽑힘!r}"


def test_대표자_이름이_없으면_그_검색어를_통째로_뺀다():
    """빈칸으로 검색하면 「하이브 인터뷰」가 되어 엉뚱한 회사 기사가 들어온다(실측)."""
    질의 = (("", "date", 20), ("{ceo} 인터뷰", "sim", 10), ("실적", "sim", 10))

    있을때 = search_terms("하이브", {"ceo_nm": "김대표"}, 질의)
    없을때 = search_terms("하이브", {"ceo_nm": ""}, 질의)

    assert [t[0] for t in 있을때] == ["하이브", "하이브 김대표 인터뷰", "하이브 실적"]
    assert [t[0] for t in 없을때] == ["하이브", "하이브 실적"]


def test_괄호가_붙은_대표자도_검색어가_깨끗하다():
    """★ 이 시험이 고친 결함을 직접 겨눈다 (삼성바이오로직스 실측)."""
    질의 = (("{ceo} 인터뷰", "sim", 10),)

    만들어진 = search_terms(
        "삼성바이오로직스", {"ceo_nm": "서대표 (Seo Daepyo Sample)"}, 질의
    )

    assert [t[0] for t in 만들어진] == ["삼성바이오로직스 서대표 인터뷰"]
