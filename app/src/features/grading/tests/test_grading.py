"""등급 판정 시험.

정본: 확정/06_검증/1_흐름/02_성립판정과부분보고서.md
- 성립 3조건: ① 세는 칸 4개 이상 ② 1번 채움 ③ 4-1/4-2/4-3 중 최소 1개
- 셋을 다 넘기면 완성. 미달이어도 3칸 이상이면 부분 완성. 그 아래는 미완성.
- ★ 어느 등급이든 「폐기」는 없다. 보고서는 나간다.
"""

from __future__ import annotations

import re

import pytest

from src.core import paths
from src.core.constants import (
    COUNTED_CELLS,
    MIN_FILLED_CELLS,
    PARTIAL_MIN_CELLS,
    REQUIRED_CELL,
    SITUATION_CELLS,
)
from src.features.grading.constants import ACCOUNTING_POLICY_REASON
from src.features.grading.logic import (
    count_filled,
    count_table_headers,
    grade_message,
    grade_of,
    is_accounting_policy,
    is_table_dump,
)
from src.features.pipeline.port import Grade


def cells(*filled: str) -> dict[str, bool]:
    """지정한 칸만 채워진 상태를 만든다."""
    return {cell: cell in filled for cell in COUNTED_CELLS}


# ── 세기 ────────────────────────────────────────────────

def test_아무것도_없으면_0개():
    assert count_filled(cells()) == 0


def test_세는_칸만_센다():
    """공고 블록과 화면에서 숨긴 칸은 세지 않는다."""
    counted = cells("1", "2")
    counted.update({"5": True, "6": True, "7": True, "8": True, "9": True, "附": True})
    assert count_filled(counted) == 2


def test_없는_칸은_안_채워진_것으로_본다():
    assert count_filled({}) == 0


# ── 완성 ────────────────────────────────────────────────

def test_성립_3조건을_다_넘기면_완성():
    got, reasons = grade_of(cells("1", "2", "3", "4-1"))
    assert got is Grade.COMPLETE
    assert reasons == []


def test_전부_채우면_완성():
    got, reasons = grade_of(cells(*COUNTED_CELLS))
    assert got is Grade.COMPLETE
    assert reasons == []


# ── 조건별 미달 ─────────────────────────────────────────

def test_1번이_비면_개수가_충분해도_완성이_아니다():
    """1번은 지원동기의 뿌리라 대체할 수 없다."""
    got, reasons = grade_of(cells("2", "3", "4-1", "4-2"))
    assert got is Grade.PARTIAL
    assert any("뭘 팔아서 돈 버나" in r for r in reasons)


def test_회사_상황이_통째로_비면_완성이_아니다():
    got, reasons = grade_of(cells("1", "2", "3"))
    assert got is Grade.PARTIAL
    assert any("지금 이 회사 상황" in r for r in reasons)


def test_개수만_모자라면_그_사유만_나온다():
    got, reasons = grade_of(cells("1", "2", "4-1"))
    assert got is Grade.PARTIAL
    assert len(reasons) == 1
    assert f"{MIN_FILLED_CELLS}개 이상" in reasons[0]


@pytest.mark.parametrize("situation", SITUATION_CELLS)
def test_상황_칸은_셋_중_아무거나_하나면_된다(situation):
    got, _ = grade_of(cells("1", "2", "3", situation))
    assert got is Grade.COMPLETE


# ── 부분 완성 / 미완성 경계 ─────────────────────────────

def test_경계_3칸이면_부분_완성():
    got, _ = grade_of(cells("1", "2", "3"))
    assert got is Grade.PARTIAL


def test_경계_2칸이면_미완성():
    got, _ = grade_of(cells("1", "2"))
    assert got is Grade.INCOMPLETE


def test_부분_완성_문턱은_상수와_일치한다():
    """상수를 바꾸면 이 시험이 먼저 깨져야 한다."""
    assert grade_of(cells(*COUNTED_CELLS[:PARTIAL_MIN_CELLS]))[0] is Grade.PARTIAL
    assert grade_of(cells(*COUNTED_CELLS[: PARTIAL_MIN_CELLS - 1]))[0] is Grade.INCOMPLETE


def test_1번만_있으면_미완성이지만_사유는_개수뿐이_아니다():
    got, reasons = grade_of(cells(REQUIRED_CELL))
    assert got is Grade.INCOMPLETE
    assert len(reasons) == 2  # 개수 미달 + 상황 통째로 빔


# ── 안내 문구 ───────────────────────────────────────────

def test_완성이면_안내_문구가_없다():
    assert grade_message(Grade.COMPLETE, len(COUNTED_CELLS)) == ""


def test_부분_완성_문구에_채운_개수가_들어간다():
    msg = grade_message(Grade.PARTIAL, 3)
    assert "3" in msg and str(len(COUNTED_CELLS)) in msg


def test_미완성_문구는_직접_확인하라고_안내한다():
    msg = grade_message(Grade.INCOMPLETE, 1)
    assert "면접" in msg or "홈페이지" in msg


@pytest.mark.parametrize("grade", list(Grade))
def test_어느_등급이든_폐기라는_말은_쓰지_않는다(grade):
    """미달이어도 보고서는 나간다 — 결정기록 D1."""
    assert "폐기" not in grade_message(grade, 0)


# ── 표 덩어리 가려내기 (문제로그 P-29) ──────────────────

# 실제 파일럿 보고서에서 그대로 가져온 줄이다. 줄이면 시험의 뜻이 없어진다.
루트로닉_1번_표 = (
    "(단위: 원) 구 분 당기 전기 [재화 및 용역의 유형] 재화매출 상품매출 "
    "17,340,455,114 2,060,771,947 제품매출 194,604,332,628 157,192,573,284 "
    "원재료매출 66,653,048,278 23,608,720,966 소 계 278,597,836,020 182,862,066,197 "
    "기타매출 기타 및 용역매출 1,879,461,822 652,626,698 합 계 280,477,297,842 "
    "183,514,692,895 [지리적 시장] 대한민국 94,843,707,257 41,880,970,784"
)
엠투아이_3번_표 = (
    "매출실적 (단위: 천원) 매출유형 품 목 2025년(제27기) 2024년(제26기) 2023년(제25기) "
    "제품 스마트HMI 내수 26,224,337 28,495,929 28,531,879 수출 1,802,964 1,779,998 "
    "1,625,118 소계 28,027,301 30,275,927 30,156,988 스마트SCADA 내수 602,444 785,338 "
    "779,440 수출 8,318 2,575 15,793 소계 610,762 787,913 795,233"
)


# ★ 짧은 표 — 87자·숫자 2개라 「길이+숫자」 규칙을 그대로 통과했다 (문제로그 P-36).
#   진짜 조사 1건에서 9번 칸에 실린 실물이다.
로보스타_9번_짧은표 = (
    "(단위: 원) 구분 회사명 거래내역 당기 전기 유의적영향력을 행사하는 기업 "
    "LG전자㈜ 매출 23,718,551,992 8,074,079,087 지급임차료 등"
)
토스씨엑스_9번_짧은표 = (
    "구분 회사명 기타특수관계자(*) 토스인슈어런스(주) 토스증권(주) 토스페이먼츠(주) "
    "토스뱅크(주) 토스플레이스(주) 토스모바일(주)"
)


def test_공시_표가_통째로_들어온_줄은_표로_본다():
    assert is_table_dump(루트로닉_1번_표) is True
    assert is_table_dump(엠투아이_3번_표) is True


@pytest.mark.parametrize(
    "표", [로보스타_9번_짧은표, 토스씨엑스_9번_짧은표], ids=["로보스타9", "토스씨엑스9"]
)
def test_짧은_표도_머리말로_잡는다(표):
    """길이·숫자 기준을 둘 다 밑돌아도 머리말이 2개 이상이면 표다 (P-36)."""
    assert len(표) < 200, "이 시험의 뜻은 «짧은데도 잡힌다»이다"
    assert is_table_dump(표) is True


def test_머리말이_하나뿐이면_표로_보지_않는다():
    """★ 이게 이 규칙의 «가장 중요한 안전장치»다.

    「당기순이익」 안에 「당기」가 들어 있어서, 문턱을 1로 낮추면
    보고서에서 «가장 좋은 문장»이 통째로 버려진다.
    """
    좋은_문장 = (
        "2025년 연결기준 매출액 5,363억원, 영업이익 2,144억원, "
        "당기순이익 1,683억원의 실적을 달성하였습니다."
    )
    assert count_table_headers(좋은_문장) == 1
    assert is_table_dump(좋은_문장) is False


@pytest.mark.parametrize(
    "sentence",
    [
        # 금액이 여럿 있어도 사람이 읽을 수 있는 문장은 살린다
        "2025년 연결기준 매출액 5,363억원, 영업이익 2,144억원, 당기순이익 1,683억원의 "
        "실적을 달성하였습니다.",
        "이는 전년대비 매출액 53%, 영업이익 70% 성장 및 당기순이익 89% 증가한 수치로 "
        "국내에서는 외국인 의료관광 유입 확대에 따른 수요 증가가 있었으며, 수출 부문에서는 "
        "중국, 일본, 동남아, 북미 등 주요 해외시장에서의 유통망 확장과 브랜드 인지도 제고가 "
        "매출 성장의 주요 요인으로 작용하였습니다.",
        # 짧은 줄은 숫자가 많아도 읽을 수 있다
        "연구개발비 13,030,958,449 10,854,851,929",
        "당사는 재생의학 기술을 기반으로 자가 재생 촉진제인 PDRN 및 PN을 중심으로 하는 "
        "의약품·의료기기·화장품을 연구, 제조 및 판매합니다.",
        "",
    ],
)
def test_정상_문장은_버리지_않는다(sentence):
    assert is_table_dump(sentence) is False


def test_길이만_길고_숫자가_적으면_표가_아니다():
    """한쪽 신호만 보면 긴 설명문까지 버리게 된다."""
    assert is_table_dump("가나다라마바사" * 40) is False


def test_숫자만_많고_짧으면_표가_아니다():
    assert is_table_dump("1,111 2,222 3,333 4,444 5,555 6,666 7,777") is False


def test_연도는_숫자_덩어리로_세지_않는다():
    """「2025 2024 2023 2022 2021 2020」이 표 신호가 되면 안 된다."""
    years = " ".join(str(y) for y in range(2000, 2026))
    assert is_table_dump(years + "가나다" * 60) is False


# ── 회계 정형 문구 가려내기 (문제로그 P-40) ─────────────
#
# 1번 칸에 「회사 이름을 바꿔도 말이 되는」 회계기준 설명이 실렸다.
# 아래 문장들은 전부 **실측 원문 그대로**다. 줄이면 시험의 뜻이 없어진다.

수익인식모형_설명 = (
    "수익인식모형 연결회사의 고객과의 계약에서 생기는 수익은 제품매출, 기타매출로 "
    "구성되어 있습니다"
)
증권사_수수료수익_인식 = (
    "수익인식(1) 수수료수익당사는 유가증권 및 파생상품 위탁매매와 관련된 수수료 등을 "
    "매매계약체결일에 수익으로 인식하고 있습니다."
)


@pytest.mark.parametrize(
    "문장",
    [수익인식모형_설명, 증권사_수수료수익_인식],
    ids=["수익인식모형", "수수료수익인식"],
)
def test_회계기준_설명_문구는_거른다(문장):
    """P-40에 등재된 실물 두 건. 이 둘을 못 잡으면 이 규칙은 존재 이유가 없다."""
    assert is_accounting_policy(문장) is True


# ★★ 오거부 안전핀 — 여기가 이 규칙의 «가장 중요한 부분»이다 ★★
#    「매출·수익·자산」 같은 회계 낱말을 세는 순간 아래 문장들이 통째로 죽는다.
#    정본 §D12-b에 똑같은 사고(표 머리말 문턱을 1로 낮춘 건)가 기록돼 있다.
@pytest.mark.parametrize(
    "문장",
    [
        # 「수익을 «창출»」 — 「수익으로 «인식»」과 한 글자 차이다. 반드시 살아야 한다.
        "사업의 내용 국내 1위 메신저 카카오톡을 중심으로 커머스, 모빌리티, 페이, 게임, "
        "뮤직, 스토리를 비롯한 다양한 영역에서 수익을 창출하고 있습니다.",
        # 보고서에서 «가장 좋은 문장». 회계 낱말이 셋이나 들었다.
        "2025년 연결기준 매출액 5,363억원, 영업이익 2,144억원, 당기순이익 1,683억원의 "
        "실적을 달성하였습니다.",
        # 금융회사 문장이라도 «사업 설명»이면 살아야 한다.
        '회사의 개요 넥스트증권 주식회사(이하 "당사")는 1997년 1월 21일 선물거래의 수탁 및 '
        "매매의 중개업무 등을 사업목적으로 설립되었으며, 서울특별시 영등포구 국제금융로에 "
        "본점을 두고 있습니다.",
        # 「수익을 인식하는 톡비즈」 — 관형형이라 회계 설명이 아니라 «사업 설명»이다.
        "카카오톡 등의 다양한 카카오 서비스의 이용자들을 대상으로 광고를 전달하고, 노출과 "
        "전환에 따라 수익을 인식하는 톡비즈(광고)와 포털비즈는 광고주들의 입찰 강도 그리고 "
        "도달하고자 하는 이용자의 범위 및 타겟팅 정도와 같이 비즈니스 파트너별 요구사항에 "
        "따라 그 가격이 매우 유동적으로 결정됩니다.",
        # 수익인식 주석에서 왔지만 «뭘 파는지»를 말하는 문장이다.
        "(1) 용역의 제공 당사는 온라인 판매자들에게 IT 물류 플랫폼 및 풀필먼트 서비스를 "
        "제공하고 있습니다.",
        "수익인식 (1) 재화의 판매 연결회사는 국내외 유통사 및 자사의 플랫폼 등을 통해 "
        "재화를 판매합니다.",
        # 표 덩어리 시험에서도 지키고 있는 문장들 (두 규칙이 서로를 깨면 안 된다)
        "당사는 재생의학 기술을 기반으로 자가 재생 촉진제인 PDRN 및 PN을 중심으로 하는 "
        "의약품·의료기기·화장품을 연구, 제조 및 판매합니다.",
        "일반사항 주식회사 콜로세움코퍼레이션(이하 \"당사\"라 함)은 2019년 5월 24일에 "
        "설립되어 운수 및 창고업과 물류 솔루션 제공 등을 주된 사업으로 하고 있습니다.",
        "",
    ],
)
def test_사업_설명과_실적_문장은_버리지_않는다(문장):
    """★ 오거부가 최대 실패 유형이다 — 정본 06_검증/1_흐름/01_채점순서.md."""
    assert is_accounting_policy(문장) is False


def test_사유_문구는_왜_비었는지를_말한다():
    """조용히 지우지 않는다 — 사유가 없으면 사용자는 왜 비었는지 알 수 없다."""
    assert "보류" in ACCOUNTING_POLICY_REASON
    assert "폐기" not in ACCOUNTING_POLICY_REASON


# ── ★ 전수 회귀 — 상수를 늘릴 때마다 실측 전체에 다시 돌린다 ──
#
# 정본 §D12-b가 정한 절차다. 표 머리말 목록을 늘렸을 때
# 「당기순이익 1,683억원…」 같은 좋은 문장이 통째로 버려진 사고가 있었다.
# 그 뒤로 «목록을 늘리면 실측 문장 전체에 다시 돌려본다»가 규칙이 됐는데,
# 사람이 손으로 하면 결국 빠뜨린다. → 이 시험이 그 절차를 대신 돌린다.
#
# 걸린 문장의 «목록 자체»를 못 박는다. 상수를 늘려 새 문장이 걸리면
# 이 시험이 먼저 깨져서, 그게 진짜 회계 문구인지 사람이 눈으로 보게 만든다.

#: 파일럿 보고서의 인용 줄 — 「- 문장 〔출처〕」
_QUOTED_LINE = re.compile(r"^-\s*(?P<text>.+?)\s*〔(?P<cite>[^〕]+)〕\s*$")

#: 걸린 문장을 짧게 적어 두는 길이. 전문을 다 적으면 시험이 못 읽게 길어진다.
_SNAPSHOT_LEN = 40

#: 실측 보고서 21개에서 이 규칙에 걸리는 «전부». (파일명, 문장 앞 40자)
#: ⚠️ 여기에 줄이 늘면 그 문장이 «진짜 회계 문구인지» 먼저 눈으로 확인할 것.
회계문구_적중_실측: frozenset[tuple[str, str]] = frozenset(
    {
        (
            "재수집-p014.md",
            "수익인식(1) 수수료수익당사는 유가증권 및 파생상품 위탁매매와 관련된 수",
        ),
    }
)

#: 실측 보고서에서 뽑히는 인용 문장 개수의 바닥선.
#: 파일이 깨져 0문장이 되면 시험이 «조용히 통과»한다 — 그걸 막는다.
_MIN_PILOT_SENTENCES = 200


def _파일럿_인용_문장() -> list[tuple[str, str]]:
    """파일럿 보고서 전부에서 인용 문장을 뽑는다. (파일명, 문장)"""
    out: list[tuple[str, str]] = []
    for report in sorted(paths.PILOT_REPORTS_DIR.glob("*.md")):
        for raw in report.read_text(encoding="utf-8").splitlines():
            matched = _QUOTED_LINE.match(raw.strip())
            if matched is not None:
                out.append((report.name, matched.group("text")))
    return out


def test_전수회귀_실측_보고서에_오탐이_없다():
    """★ 실측 보고서 전부에 돌려 «걸린 것이 전부 진짜 회계 문구»임을 못 박는다."""
    if not paths.PILOT_REPORTS_DIR.is_dir():
        pytest.skip("파일럿 보고서 폴더가 없습니다 (prototype_v1 미배치)")

    문장들 = _파일럿_인용_문장()
    assert len(문장들) >= _MIN_PILOT_SENTENCES, (
        f"인용 문장이 {len(문장들)}개뿐입니다 — 보고서를 못 읽었을 가능성이 큽니다"
    )

    # 표 덩어리는 이 규칙이 오기 «전에» 이미 걸러진다 (파이프라인 순서: 재무표 → 표 → 회계).
    # 표까지 세면 「[수익인식 시기]」가 든 공시 표가 섞여 목록이 읽기 어려워진다.
    걸린것 = {
        (파일명, 문장[:_SNAPSHOT_LEN])
        for 파일명, 문장 in 문장들
        if not is_table_dump(문장) and is_accounting_policy(문장)
    }
    assert 걸린것 == 회계문구_적중_실측, (
        "실측 적중 목록이 달라졌습니다. 늘었다면 «그 문장이 진짜 회계 문구인지» "
        "눈으로 확인한 뒤에만 위 목록을 고치세요 (정본 §D12-b)."
    )


def test_전수회귀_실적_문장은_한_건도_안_걸린다():
    """★ 오거부 안전핀의 전수판 — 금액이 든 «실적 문장»은 하나도 걸리면 안 된다.

    회계 낱말을 세는 규칙으로 되돌아가면 여기서 무더기로 걸린다.
    """
    if not paths.PILOT_REPORTS_DIR.is_dir():
        pytest.skip("파일럿 보고서 폴더가 없습니다 (prototype_v1 미배치)")

    실적어 = ("달성", "성장", "증가", "기록하", "매출액")
    걸린_실적문장 = [
        문장
        for _, 문장 in _파일럿_인용_문장()
        if any(말 in 문장 for 말 in 실적어)
        and not is_table_dump(문장)
        and is_accounting_policy(문장)
    ]
    assert 걸린_실적문장 == [], f"실적 문장이 걸렸습니다: {걸린_실적문장[:2]}"
