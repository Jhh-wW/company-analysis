"""6·7·8번 생성 시험.

정본: 확정/05_생성/2_규칙/01_출력틀.md · 확정/90_운영기록/01_문제로그.md P-31

★ 1판은 이 세 칸을 만들지 않았다(P-31). 여기서 반드시 확인해야 하는 것 넷:
  ① 재료가 없으면 빈 칸 + 사유가 나오는가
  ② 8번이 문장을 만들지 않는가 (교차표만)
  ③ 교차표의 열 개수가 항상 맞는가 (`ReportTable.is_valid`)
  ④ 공고 원문이 안 바뀌는가 (규칙⑤)

★ 2026-08-15 추가 (P-41 2차) — 잡음 줄이기의 «안전핀»:
  ⑤ 제외 목록이 조사에 안 뚫리는가 (「업무를」)
  ⑥ 실측으로 확인된 «좋은 행» 5개가 안 죽는가  ← 가장 중요
  ⑦ 행 상한이 걸렸을 때 «잘랐다고 말하는가»
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from src.core import paths
from src.features.blocks678.constants import (
    BLOCK6_EMPTY_NO_CELL1,
    BLOCK6_EMPTY_NO_JOB,
    BLOCK7_EMPTY_REASON,
    BLOCK7_MAX_ITEMS,
    BLOCK8_EMPTY_NO_OVERLAP,
    BLOCK8_EMPTY_NO_REQUIREMENTS,
    BLOCK8_EMPTY_NO_SITUATION,
    BLOCK8_MAX_ROWS,
    BLOCK8_STOPWORDS,
    BLOCK8_TABLE_CAPTION,
)
from src.features.blocks678.logic import (
    _is_stopword,
    build_block6,
    build_block7,
    build_block8,
)

# ══════════════════════════════════════════════════════════
# 6. 내 자리가 회사 어디에 붙나
# ══════════════════════════════════════════════════════════

셀1_문장 = [("반도체 장비로 매출 70%를 올린다", "조각 2·사업내용")]


def test_6번_재료가_있으면_직무명과_1번_문장을_함께_준다():
    section = build_block6(셀1_문장, "생산기술 엔지니어")
    assert section.is_filled is True
    assert section.lines[0] == ("지원 직무: 생산기술 엔지니어", "공고")
    assert section.lines[1:] == 셀1_문장


def test_6번은_1번_문장을_고치지_않는다():
    """규칙⑤ — 6번에도 원문 그대로 적용된다."""
    section = build_block6(셀1_문장, "생산기술 엔지니어")
    for (text, cite), (원문, 원출처) in zip(section.lines[1:], 셀1_문장):
        assert text == 원문
        assert cite == 원출처


def test_6번_직무명이_없으면_빈_칸에_사유가_붙는다():
    section = build_block6(셀1_문장, "")
    assert section.is_filled is False
    assert section.empty_reason == BLOCK6_EMPTY_NO_JOB


def test_6번_직무명이_공백뿐이면_빈_칸이다():
    section = build_block6(셀1_문장, "   ")
    assert section.is_filled is False
    assert section.empty_reason == BLOCK6_EMPTY_NO_JOB


def test_6번_1번이_비어있으면_빈_칸에_사유가_붙는다():
    section = build_block6([], "생산기술 엔지니어")
    assert section.is_filled is False
    assert section.empty_reason == BLOCK6_EMPTY_NO_CELL1


def test_6번_사유는_AI가_아니라_프로그램이_붙인_고정_문구다():
    """사유가 매번 바뀌면 W5·S6 검사가 값을 못 맞춘다 — 상수와 동일해야 한다."""
    section = build_block6([], "")
    assert section.empty_reason in (BLOCK6_EMPTY_NO_JOB, BLOCK6_EMPTY_NO_CELL1)


# ══════════════════════════════════════════════════════════
# 7. 이 일 하면 뭐가 힘든가
# ══════════════════════════════════════════════════════════


def test_7번_영문_이름이_든_요구역량만_고른다():
    requirements = [
        "Order Management System 운영 경험",
        "책임감 있는 자세",
        "협업 능력",
    ]
    section = build_block7(requirements)
    assert section.is_filled is True
    assert section.lines == [("Order Management System 운영 경험", "공고")]


def test_7번은_요구역량_원문을_고치지_않는다():
    """규칙⑤ 예시 그대로 — "Order Management System 운영"을 다듬지 않는다."""
    원문 = "Order Management System 운영 및 SAP ERP 데이터 정합성 관리"
    section = build_block7([원문])
    assert section.lines[0][0] == 원문


def test_7번_최대_개수를_넘기지_않는다():
    requirements = [f"System{i} 운영 경험" for i in range(1, 6)]
    section = build_block7(requirements)
    assert len(section.lines) == BLOCK7_MAX_ITEMS
    # 앞에서부터 순서대로 고른다 — 임의로 섞지 않는다
    assert section.lines[0][0] == "System1 운영 경험"
    assert section.lines[1][0] == "System2 운영 경험"


def test_7번_영문_이름이_없으면_빈_칸에_사유가_붙는다():
    requirements = ["책임감 있는 자세", "원활한 협업 능력"]
    section = build_block7(requirements)
    assert section.is_filled is False
    assert section.empty_reason == BLOCK7_EMPTY_REASON


def test_7번_요구역량이_아예_없으면_빈_칸이다():
    section = build_block7([])
    assert section.is_filled is False
    assert section.empty_reason == BLOCK7_EMPTY_REASON


# ══════════════════════════════════════════════════════════
# 8. 그래서 뭘 어필하나 — 4번×5번 교차표
# ══════════════════════════════════════════════════════════

셀4_상황 = {
    "4-1": [("반도체 후공정 수율 저하가 지속되고 있다", "조각 5·MD&A")],
    "4-2": [("스마트팩토리 전환 프로젝트를 진행하고 있다", "조각 6·연구개발")],
    "4-3": [("2027년까지 해외 생산기지를 확대할 계획이다", "조각 7·MD&A")],
}
요구역량_겹침 = [
    "반도체 공정 수율 개선 경험자 우대",
    "스마트팩토리 구축 프로젝트 참여 경험",
]


def test_8번은_문장을_만들지_않고_표만_준다():
    """규칙⑥ — 8번은 문장을 쓰지 않는다."""
    section = build_block8(셀4_상황, 요구역량_겹침)
    assert section.is_filled is True
    assert section.lines == []
    assert len(section.tables) == 1


def test_8번_표는_열_개수가_항상_맞는다():
    section = build_block8(셀4_상황, 요구역량_겹침)
    table = section.tables[0]
    assert table.is_valid is True
    assert all(len(row) == len(table.headers) for row in table.rows)


def test_8번_표는_겹치는_짝만_행으로_낸다():
    section = build_block8(셀4_상황, 요구역량_겹침)
    table = section.tables[0]
    row_texts = [row[0] for row in table.rows]
    assert any("수율" in r for r in row_texts)
    assert any("스마트팩토리" in r for r in row_texts)
    # 겹치지 않는 4-3(해외 생산기지 확대)은 행이 없어야 한다
    assert not any("해외 생산기지" in r for r in row_texts)


def test_8번_표_안의_문장은_원문_그대로다():
    """규칙⑤ — 6·7·8번에도 원문 그대로 적용된다.

    한 상황에 여러 요구역량이 걸리면 한 칸에 «줄바꿈으로 묶여» 들어간다.
    묶이더라도 각 문장은 **한 글자도 다듬지 않는다** — 그걸 여기서 못 박는다.
    """
    section = build_block8(셀4_상황, 요구역량_겹침)
    table = section.tables[0]
    상황_원문 = 셀4_상황["4-1"][0][0]
    요구_원문 = 요구역량_겹침[0]
    matched = [row for row in table.rows if 상황_원문 in row[0]]
    assert matched, "4-1 원문이 표에 그대로 들어가야 한다"

    # 묶인 칸을 다시 줄 단위로 풀면 «원문 그대로»가 나와야 한다
    담긴_요구 = [
        line.lstrip("· ").strip()
        for row in matched
        for line in row[1].splitlines()
        if line.strip()
    ]
    assert 요구_원문 in 담긴_요구


def test_8번_한_상황에_여러_요구역량이_걸리면_한_행으로_묶인다():
    """같은 긴 문장이 여러 행에 반복되면 표를 읽을 수 없다 (실측 — 넥스트증권 3행 중복)."""
    section = build_block8(셀4_상황, 요구역량_겹침)
    table = section.tables[0]
    상황_칸 = [row[0] for row in table.rows]
    assert len(상황_칸) == len(set(상황_칸)), "같은 상황 문장이 두 행에 나오면 안 된다"


def test_8번_회사_상황이_비어있으면_빈_칸에_사유가_붙는다():
    section = build_block8({}, 요구역량_겹침)
    assert section.is_filled is False
    assert section.empty_reason == BLOCK8_EMPTY_NO_SITUATION


def test_8번_요구역량이_없으면_빈_칸에_사유가_붙는다():
    section = build_block8(셀4_상황, [])
    assert section.is_filled is False
    assert section.empty_reason == BLOCK8_EMPTY_NO_REQUIREMENTS


def test_8번_겹치는_게_없으면_빈_칸에_사유가_붙는다():
    무관한_요구역량 = ["엑셀 활용 능력 우대", "영어 회화 가능자 우대"]
    section = build_block8(셀4_상황, 무관한_요구역량)
    assert section.is_filled is False
    assert section.empty_reason == BLOCK8_EMPTY_NO_OVERLAP


def test_8번_흔한_업무_낱말만_겹치면_행으로_치지_않는다():
    """「고객」·「관리」처럼 아무 데나 겹치는 낱말은 근거가 아니다 (규칙② 취지).

    두 문장은 "고객"·"관리"라는 낱말 자체는 그대로 겹치지만, 둘 다 스톱워드라
    걸러진다. 걸러지지 않았다면 겹침으로 잡혀 행이 생겼을 조합이다.
    """
    상황 = {"4-1": [("고객 관리 역량이 중요하다고 밝혔다", "조각 1")]}
    요구역량 = ["고객 관리 우대"]
    section = build_block8(상황, 요구역량)
    assert section.is_filled is False
    assert section.empty_reason == BLOCK8_EMPTY_NO_OVERLAP


# ══════════════════════════════════════════════════════════
# 8-①. 제외 목록이 조사에 안 뚫리게 (P-41 2차 · 미결 P-03)
# ══════════════════════════════════════════════════════════
#
# 1차 완화 때 형식어 18개를 넣었는데 «절반만» 들었다. 낱말을 자르는 정규식이
# 「업무를」을 통째로 한 낱말로 잡아서 목록의 「업무」와 안 맞았기 때문이다.

#: 제외 목록에 이미 있는 「업무」에 조사가 붙은 꼴. 전부 걸러져야 한다.
조사가_붙은_제외어 = [
    "업무를", "업무가", "업무는", "업무의", "업무에", "업무도", "업무와", "업무로",
    "업무에서", "업무들을", "가능한", "필요한", "사용된", "운영에", "제품이나",
]

#: 겹침의 «진짜 근거»가 되는 낱말. 조사를 떼는 처리에 절대 깨지면 안 된다.
#: ★ 「인증」이 「인」으로 잘리는 사고를 막는 안전핀이다.
깨지면_안_되는_낱말 = [
    "인증", "의료기기", "전력전자", "동물병원", "해외송금", "결제", "장비",
    "강화학습", "강화", "분석의견", "이하선",
]


@pytest.mark.parametrize("token", 조사가_붙은_제외어)
def test_8번_제외_목록은_조사가_붙어도_듣는다(token: str):
    """「업무」가 목록에 있으면 「업무를」도 걸려야 한다 (P-41 2차)."""
    assert _is_stopword(token) is True, f"「{token}」이(가) 제외 목록에 안 걸립니다"


@pytest.mark.parametrize("token", 깨지면_안_되는_낱말)
def test_8번_조사를_떼는_처리가_멀쩡한_낱말을_죽이지_않는다(token: str):
    """★ 안전핀 — 「인증」→「인」처럼 잘려 근거가 통째로 사라지면 안 된다.

    「강화」는 일부러 넣었다. 「경쟁력 강화」(형식어)와 「강화학습」(진짜 기술 역량)이
    글자가 같아서, 낱말 자체를 막으면 진짜 근거까지 죽는다 — 그래서 안 막았다.
    """
    assert _is_stopword(token) is False, f"「{token}」이(가) 잘못 걸러집니다"


def test_8번_조사_처리는_겹침을_늘리지_않는다():
    """★ 어간 통일이 아니라 «빼기»만 한다 — 그래서 행이 늘어날 수 없다.

    조사를 떼어 낱말 자체를 어간으로 «고치면» 겹침이 폭증한다고 별도 실측에서
    보고됐다 (루트로닉·파마리서치에서 행이 2~3배). 여기서는 낱말을 고치지 않고
    「제외 대상인가」만 물으므로, 조사가 다르면 여전히 안 겹친다.
    """
    상황 = {"4-1": [("전력전자를 개발한다", "조각 1")]}
    요구역량 = ["전력전자가 필요하다"]
    section = build_block8(상황, 요구역량)
    # 「전력전자를」과 「전력전자가」는 글자가 다르므로 겹치지 않는다 (미달 방향 = 안전)
    assert section.is_filled is False
    assert section.empty_reason == BLOCK8_EMPTY_NO_OVERLAP


def test_8번_공고_형식_문구는_근거로_안_쓴다():
    """「경력 3년 이하」의 「이하」는 공고 양식 문구지 회사 이야기가 아니다."""
    assert "이하" in BLOCK8_STOPWORDS
    assert "이상" in BLOCK8_STOPWORDS, "「이상」이 빠지면 「이하」만 막는 게 앞뒤가 안 맞는다"


def test_8번_뜻이_갈리는_낱말은_막지_않는다():
    """「강화」를 막으면 「강화학습」이라는 진짜 기술 역량까지 죽는다."""
    assert "강화" not in BLOCK8_STOPWORDS


# ══════════════════════════════════════════════════════════
# 8-②. ★ 안전핀 — 실측으로 확인된 「좋은 행」은 절대 안 죽는다
# ══════════════════════════════════════════════════════════
#
# 파일럿 21곳 실측에서 «진짜 근거»로 확인된 짝이다. 제외 목록을 늘리거나
# 상한을 조이다가 이게 죽으면 여기서 먼저 깨진다.
# ⚠️ 5개 중 3개가 «겹침 1낱말짜리»다 — 겹침 문턱을 올리면 먼저 죽는 쪽이다.
#
# (회사, 4번 상황 문장 발췌, 5번 요구역량 원문, 살아 있어야 할 겹친 말)

좋은_행_실측: list[tuple[str, str, str, frozenset[str]]] = [
    (
        "글로벌머니익스프레스",
        "(2026-07-22 보도 · sateconomy.co.kr) 글로벌머니익스프레스(GMEBiz), 인도네시아 대상 "
        "한국 유학비·병원비 결.... 현지 가족이 루피아로 결제하면 한국 학교·병원에 원화 지급…"
        "청구서 금액 기준 한도 제한 없어 ▲ 글로벌머니익스프레스(Global Money Express·GME) "
        "글로벌머니익스프레스(Global Money Express·GME)의 기업 해외송금·결제...",
        "결제(Payments), 해외송금(Cross-border Remittance), 정산(Settlement) 관련 업무 이해",
        frozenset({"결제", "해외송금"}),
    ),
    (
        "우리엔",
        "(2026-07-28 보도 · www.dailyvet.co.kr) 대만 수의사 연수교육에서 2년 연속 우리엔CT "
        "활용 방법 소개. 동물병원 전용 이미징 장비 및 전자차트 기업 우리엔(대표 고석빈)이 "
        "3~4일(금~토) 양일간 대만 가오슝과... 이번 세미나는 지난해 대만에서 처음 개최된 "
        "우리엔 CT 세미나에 대한 높은 만족도와 현지 대리점 및... ",
        "병원 또는 동물병원 대상 영업 경험 (EMR, PACS, 의료 AI 솔루션, 장비 등)",
        frozenset({"동물병원", "장비"}),
    ),
    (
        "인텍에프에이",
        "(2026-02-24 보도 · www.industrynews.co.kr) 인텍에프에이, 한국에너지공과대와 발전기금 "
        "기부 약정 체결. 인더스트리뉴스 정한교 기자 전력전자 및 에너지 솔루션 기업 "
        "인텍에프에이(대표 최기수)는 지난 23일 한국에너지공과대학교(총장직무대행 박진호)와 "
        "발전기금 기부 약정을 체결하고, 대학 발전과 미래 에너지 인재 양성을... ",
        "MATLAB/Simulink, PSIM 등 전력전자 시뮬레이션 툴 활용이 가능한 자",
        frozenset({"전력전자"}),
    ),
    (
        "루트로닉",
        "(2026-07-15 보도 · www.mdtoday.co.kr) 사이노슈어 루트로닉, 유럽 CE MDR 인증 획득. "
        "미국에 본사를 둔 글로벌 메디컬 에스테틱 기업 사이노슈어 루트로닉은 자사의 "
        "모노폴라 고주파(RF)... 사이노슈어 루트로닉은 이번 인증을 바탕으로 독일, 오스트리아, "
        "스위스 등 직영 판매망을 갖춘 국가와... ",
        "의료기기 인증 절차에 대한 이해도가 있거나, 혹은 다른 산업군이더라도 "
        "규제 환경 속에서 인증 절차에 대한 이해도가 있으신 분",
        frozenset({"인증"}),
    ),
    (
        "파마리서치",
        "(2026-08-15 보도 · www.newsis.com) 호주 간 파마리서치…리쥬란 임상 사례 공유. "
        "재생의학 전문기업 파마리서치가 국제 심포지엄에 참가해 의료기기 ‘리쥬란’ 임상 사례를 "
        "공유했다. 파마리서치는 최근 호주 시드니에서 열린 국제 미용의학 심포지엄 "
        "‘에스테틱스(Aesthetics) 2026’에 참가해... ",
        "학사 이상 (이공계 또는 제약·의료기기 관련 전공)",
        frozenset({"의료기기"}),
    ),
]


@pytest.mark.parametrize(
    "회사, 상황, 요구역량, 겹친말",
    좋은_행_실측,
    ids=[회사 for 회사, _s, _r, _w in 좋은_행_실측],
)
def test_8번_실측된_좋은_행은_살아남는다(
    회사: str, 상황: str, 요구역량: str, 겹친말: frozenset[str]
):
    """★ 잡음을 줄이다가 진짜 근거를 죽이지 않았는지 못 박는다."""
    section = build_block8({"4-1": [(상황, "조각 1·뉴스")]}, [요구역량])
    assert section.is_filled is True, f"{회사} — 「{'·'.join(sorted(겹친말))}」 행이 사라졌습니다"
    words = set(section.tables[0].rows[0][2].split(", "))
    assert 겹친말 <= words, f"{회사} — 겹친 말 {sorted(겹친말 - words)}이(가) 죽었습니다"


# ══════════════════════════════════════════════════════════
# 8-③. 행 상한 + 정렬 — 자르되 «잘랐다고 말한다»
# ══════════════════════════════════════════════════════════


def _상황_여러개(n: int) -> dict[str, list[tuple[str, str]]]:
    """겹침이 1낱말씩인 상황 문장을 n개 만든다."""
    return {"4-1": [(f"회사가 특수장비{i}를 새로 들였다", f"조각 {i}") for i in range(n)]}


def test_8번_행은_상한을_넘지_않는다():
    n = BLOCK8_MAX_ROWS + 3
    요구역량 = [f"특수장비{i}를 다뤄 본 분" for i in range(n)]
    section = build_block8(_상황_여러개(n), 요구역량)
    assert len(section.tables[0].rows) == BLOCK8_MAX_ROWS


def test_8번_행을_자르면_몇_개를_감췄는지_말한다():
    """★ 조용히 자르지 않는다 — 「없는 것을 지어내지 않는다 · 숨기지 않는다」."""
    n = BLOCK8_MAX_ROWS + 3
    요구역량 = [f"특수장비{i}를 다뤄 본 분" for i in range(n)]
    caption = build_block8(_상황_여러개(n), 요구역량).tables[0].caption
    assert caption != BLOCK8_TABLE_CAPTION, "잘랐는데 표 설명이 그대로입니다"
    assert str(n) in caption and str(n - BLOCK8_MAX_ROWS) in caption, (
        f"몇 개 중 몇 개를 감췄는지가 안 적혔습니다: {caption}"
    )


def test_8번_안_잘리면_표_설명에_군더더기가_안_붙는다():
    section = build_block8(셀4_상황, 요구역량_겹침)
    assert section.tables[0].caption == BLOCK8_TABLE_CAPTION


def test_8번_상한에_걸리면_겹침이_많은_행부터_남는다():
    """앞에서부터 자르면 1낱말짜리가 남고 2낱말짜리가 잘린다 — 그 반대여야 한다."""
    상황 = {
        "4-1": [
            (f"회사가 특수장비{i}를 새로 들였다", f"조각 {i}") for i in range(BLOCK8_MAX_ROWS)
        ],
        "4-3": [("회사가 정밀검사장비 도입과 자동화라인 증설을 함께 한다", "조각 9")],
    }
    요구역량 = [f"특수장비{i}를 다뤄 본 분" for i in range(BLOCK8_MAX_ROWS)]
    요구역량 += ["정밀검사장비 운용 경험", "자동화라인 설비 경험"]
    section = build_block8(상황, 요구역량)
    남은_상황 = [row[0] for row in section.tables[0].rows]
    assert any("정밀검사장비" in s for s in 남은_상황), (
        "겹침 2낱말짜리(4-3)가 1낱말짜리에 밀려 잘렸습니다"
    )
    assert section.tables[0].rows[0][2] == "자동화라인, 정밀검사장비"


def test_8번_같은_재료면_항상_같은_표가_나온다():
    """정렬이 흔들리면 같은 회사를 두 번 돌릴 때 다른 표가 나온다."""
    첫번째 = build_block8(셀4_상황, 요구역량_겹침).tables[0].rows
    두번째 = build_block8(셀4_상황, 요구역량_겹침).tables[0].rows
    assert 첫번째 == 두번째


# ══════════════════════════════════════════════════════════
# 8-④. ★ 전수 회귀 — 제외 목록을 늘리면 실측 전체에 다시 돌린다 (D12-b 절차)
# ══════════════════════════════════════════════════════════
#
# P-41 조치문이 못 박은 함정이다 — 「스톱워드 목록은 지금까지 본 것만 막는다.
# 늘릴 때마다 실측 문장 전체에 다시 돌려야 한다」. 사람이 손으로 하면 빠뜨린다.
#
# 재료 만드는 법 (AI 0회 · 파일럿 저장분만 씀):
#   4번 = 파일럿 조각의 「뉴스」 원문 (조각 1개 = 상황 문장 1줄)
#   5번 = 파일럿 보고서 md의 「## 5. 요구역량(공고)」 절
# ※ 파일럿 보고서는 4번이 거의 다 비어 있다(P-43). 그래서 4번이 채워진 뒤를
#   내다보려면 이렇게 «뉴스를 4번 자리에 놓고» 돌려 보는 수밖에 없다.

#: 파일럿 조각 폴더. `paths`에 이름이 없어 여기서 만든다 (시험 전용).
_PILOT_FRAGMENTS_DIR: Path = paths.PILOT_DIR / "fragments"

#: 4번 자리에 놓아 볼 조각 종류.
_SITUATION_FRAGMENT_KIND = "뉴스"

#: 파일럿 보고서의 인용 줄 — 「- 문장 〔출처〕」
_QUOTED_LINE = re.compile(r"^-\s*(?P<text>.+?)\s*〔(?P<cite>[^〕]+)〕\s*$")

#: 파일럿 21곳 전수 실측 — 8번이 «나오는» 회사와 그 행 수. (파일 이름, 행 수)
#: 2026-08-15 P-41 2차 조치 직후 값. 조치 전에는 9곳 17행이었고, 그중 4곳이
#: 잡음이었다 (로보스타 「분석」·토스씨엑스 「업무를」·엠투아이 「이하」·
#: 카카오페이 「가능한」). 그 4곳이 통째로 사라졌다.
#: ⚠️ 이 표가 «줄면» 진짜 근거를 죽였는지 눈으로 확인할 것. «늘면» 새 잡음인지 볼 것.
파일럿_8번_실측: frozenset[tuple[str, int]] = frozenset(
    {
        ("재수집-p003", 3),  # 우리엔 — 동물병원·장비
        ("재수집-p010", 3),  # 루트로닉 — 의료기기·인증 (겹친 상황 6개 중 3개는 상한으로 생략)
        ("재수집-p021", 1),  # 글로벌머니익스프레스 — 결제·해외송금
        ("재수집-p022", 2),  # 파마리서치 — 의료기기
        ("재수집-p036", 1),  # 인텍에프에이 — 전력전자
    }
)

#: 파일럿 조각 파일이 이보다 적으면 폴더를 못 읽은 것이다 — 조용한 통과를 막는다.
_MIN_PILOT_FRAGMENT_FILES = 20


def _파일럿_요구역량(stem: str) -> list[str]:
    """파일럿 보고서에서 5번 절의 요구역량 원문을 뽑는다."""
    path = paths.PILOT_REPORTS_DIR / f"{stem}.md"
    if not path.is_file():
        return []
    out: list[str] = []
    안에_있나 = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            안에_있나 = line.startswith("## 5.")
            continue
        if 안에_있나:
            matched = _QUOTED_LINE.match(line)
            if matched is not None:
                out.append(matched.group("text"))
    return out


def _파일럿_상황(path: Path) -> dict[str, list[tuple[str, str]]]:
    """파일럿 조각의 뉴스 원문을 4-1 자리에 놓는다."""
    frags = json.loads(path.read_text(encoding="utf-8"))
    lines = [
        (v["원문"], f"조각 {k}·{v['종류']}")
        for k, v in frags.items()
        if v.get("종류") == _SITUATION_FRAGMENT_KIND
    ]
    return {"4-1": lines}


def test_전수회귀_파일럿_전체에서_8번_행_수가_실측과_같다():
    """★ 제외 목록·상한을 건드리면 여기가 먼저 깨진다 (D12-b 절차의 자동판)."""
    if not _PILOT_FRAGMENTS_DIR.is_dir() or not paths.PILOT_REPORTS_DIR.is_dir():
        pytest.skip("파일럿 자료 폴더가 없습니다 (prototype_v1 미배치)")

    조각파일 = sorted(_PILOT_FRAGMENTS_DIR.glob("*.json"))
    assert len(조각파일) >= _MIN_PILOT_FRAGMENT_FILES, (
        f"조각 파일이 {len(조각파일)}개뿐입니다 — 폴더를 못 읽었을 가능성이 큽니다"
    )

    나온것 = set()
    for path in 조각파일:
        요구역량 = _파일럿_요구역량(path.stem)
        상황 = _파일럿_상황(path)
        if not 요구역량 or not 상황["4-1"]:
            continue
        section = build_block8(상황, 요구역량)
        if section.is_filled:
            나온것.add((path.stem, len(section.tables[0].rows)))

    assert 나온것 == 파일럿_8번_실측, (
        "파일럿 실측 결과가 달라졌습니다. 줄었다면 «진짜 근거를 죽인 것은 아닌지», "
        "늘었다면 «새 잡음은 아닌지» 눈으로 확인한 뒤에만 위 표를 고치세요 (정본 §D12-b)."
    )


def test_전수회귀_좋은_행_다섯_개가_파일럿에서도_살아_있다():
    """★ 안전핀의 전수판 — 실측 자료 그대로 돌려 좋은 겹침이 남아 있는지 본다."""
    if not _PILOT_FRAGMENTS_DIR.is_dir() or not paths.PILOT_REPORTS_DIR.is_dir():
        pytest.skip("파일럿 자료 폴더가 없습니다 (prototype_v1 미배치)")

    기대 = {
        "재수집-p021": {"결제", "해외송금"},
        "재수집-p003": {"동물병원", "장비"},
        "재수집-p036": {"전력전자"},
        "재수집-p010": {"인증"},
        "재수집-p022": {"의료기기"},
    }
    for stem, 있어야_할_말 in 기대.items():
        path = _PILOT_FRAGMENTS_DIR / f"{stem}.json"
        section = build_block8(_파일럿_상황(path), _파일럿_요구역량(stem))
        assert section.is_filled is True, f"{stem} — 8번이 통째로 비었습니다"
        나온_말 = {
            word
            for row in section.tables[0].rows
            for word in row[2].split(", ")
        }
        assert 있어야_할_말 <= 나온_말, (
            f"{stem} — 있어야 할 겹친 말 {sorted(있어야_할_말 - 나온_말)}이(가) 죽었습니다"
        )
