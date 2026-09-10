"""장별 «소유 경계»가 작가 프롬프트에 실리는지 못 박는다.

★ 왜 이 시험이 있나 (실측 결함) — 정본 `docs/출력물 기준/90_공통_규칙/
  사실_소유권과_중복_검사.md` §4에 장별 소유권 표가 있는데, 작가 프롬프트에
  «0%» 반영돼 있었다. 그래서 어느 장이 무엇을 소유하는지 작가가 알 수 없었고
  같은 사실이 여러 장에 흩어졌다.
★ 순서대로 쓰기(test_single_ownership.py)만으로는 부족했다 — 실측에서 앞 장이
  4장 소유인 3개년 실적을 먼저 집어 가는 바람에 4장이 1문장으로 쪼그라들었다.
  그래서 «소유하지 않는 것»과 «이 장이 소유한다»를 함께 알려 준다.
"""

from __future__ import annotations

import pytest

import src.features.composer.constants as composer_constants
from src.features.composer.constants import (
    CITATION_RULES_GUIDE,
    SECTION_GUIDES,
    SECTION_IDS,
)
from src.features.composer.logic import build_section_prompt
from src.features.composer.port import CollectedFragment

#: 「이 장이 소유하지 않는 것」을 여는 말 — 9개 장 전부에 있어야 한다.
_경계_표지 = "이 장이 소유하지 않는 것"


def _fragments() -> tuple[CollectedFragment, ...]:
    return (
        CollectedFragment(
            fragment_id="1", kind="사업내용", text="가나다전자는 검사 장비를 만든다."
        ),
    )


def test_아홉_장_모두_소유하지_않는_것을_밝힌다():
    빠진_장 = [
        section_id
        for section_id in SECTION_IDS
        if _경계_표지 not in SECTION_GUIDES[section_id]
    ]

    assert not 빠진_장, f"소유 경계가 없는 장: {빠진_장}"


def test_실적을_소유하는_장은_반드시_쓰라고_지시한다():
    """앞 장이 먼저 집어 가서 4장이 비는 실측 결함을 막는 지시다."""
    지침 = SECTION_GUIDES["past_changes"]

    assert "이 장이 소유한다" in 지침
    assert "반드시 여기서는 쓴다" in 지침 or "여기서는 반드시 쓴다" in 지침


def test_파트너를_소유하는_장도_반드시_쓰라고_지시한다():
    지침 = SECTION_GUIDES["operations_partners"]

    assert "이 장이 소유한다" in 지침
    assert "파트너" in 지침


def test_비교_장은_자사_내용_재출력을_막는다():
    """9장이 비교 근거 없이 자사 이야기로 빈자리를 채우던 실측 결함."""
    지침 = SECTION_GUIDES["competitive_position"]

    assert "재출력" in 지침
    assert "장 참조" in 지침


def test_인용_규칙에_장_참조_지침이_있다():
    """값을 복사하지 말고 «그 장을 가리키라»는 규칙."""
    assert "다른 장이 소유한" in CITATION_RULES_GUIDE
    assert "장 참조" in CITATION_RULES_GUIDE


@pytest.mark.parametrize("revenue_table_v2", [False, True])
def test_원단위_금액_금지_규칙은_수익표_스위치와_무관하게_프롬프트에_실린다(
    monkeypatch: pytest.MonkeyPatch,
    revenue_table_v2: bool,
):
    monkeypatch.setattr(
        composer_constants,
        "revenue_table_v2_enabled",
        lambda: revenue_table_v2,
    )

    for section_id in SECTION_IDS:
        prompt = build_section_prompt(
            "가나다전자(주)", section_id, _fragments(), None
        )

        assert "금액은 억원(또는 조원) 단위 표시값으로만 쓴다" in prompt
        assert "원 단위 전체 자릿수" in prompt
        assert "원문이 원 단위면 억원으로 직접 환산하지 말고" in prompt


def test_소유_경계가_실제_프롬프트에_실린다():
    """상수에만 있고 프롬프트에 안 실리면 아무 효과가 없다."""
    for section_id in SECTION_IDS:
        prompt = build_section_prompt(
            "가나다전자(주)", section_id, _fragments(), None
        )
        assert _경계_표지 in prompt, f"{section_id} 프롬프트에 소유 경계가 없습니다"
        assert "다른 장이 소유한" in prompt, f"{section_id} 프롬프트에 인용 규칙 6이 없습니다"


# ══════════════════════════════════════════════════════════
# 6장 «성장 전략» 배치 — 관문이 이미 보는 규칙을 작가에게도 알려 준다
# ══════════════════════════════════════════════════════════
#
# ★ 왜 필요한가 (2026-09-10~11 멀티캠퍼스 실측) — `future_section_prose_problem`
#   은 미래 표지가 없는 6장 본문을 제외한다. 그 규칙이 작가 지침에 한 줄도
#   없어서 작가가 원문 「…확보해 나가겠습니다」를 「…확보하려 하고 있다」로
#   옮겼고, 그 문장이 6장에서 빠졌다(실측 3건). 같은 실행에서 계획 문장은
#   1장·5장에 실렸다 — 1장 지침에 「미래 계획(6장)」 제외가 없었기 때문이다.


def test_미래_계획을_소유하지_않는_장은_6장으로_보낸다():
    빠진_장 = [
        section_id
        for section_id in SECTION_IDS
        if section_id != "future_strategy"
        and "(6장)" not in SECTION_GUIDES[section_id].partition(_경계_표지)[2]
    ]

    assert not 빠진_장, f"미래 계획을 6장으로 보내지 않는 장: {빠진_장}"


def test_성장_전략_지침이_미래_표지를_남기라고_말한다():
    """생산 정규식을 import하지 않는다 — 같은 값끼리 맞추면 순환 검증이 된다."""
    지침 = SECTION_GUIDES["future_strategy"]

    for 표지 in ("계획", "예정", "방침", "목표", "하겠다", "전망"):
        assert 표지 in 지침, f"6장 지침에 미래 표지 «{표지}»가 없습니다"
    assert "하고 있다" in 지침, "진행형으로 옮기면 빠진다는 경고가 없습니다"


def test_지침이_말한_어투가_실제로_6장_배치검사를_통과한다():
    """지침과 관문이 따로 놀지 않게 «행동»으로 묶는다."""
    from src.features.composer.future_plan_guard import future_section_prose_problem

    통과해야_함 = (
        "회사는 2026년에 AI 교육 체계를 고도화할 계획이다.",
        "회사는 새로운 시장으로 사업 영역을 확장하겠다고 밝혔다.",
        "회사는 진단 기반 리더십 교육을 강화하는 것을 목표로 한다.",
        "회사는 외국어평가 시장이 지속적으로 성장할 것으로 전망한다.",
    )
    막혀야_함 = (
        "회사는 AI 교육 체계를 고도화하고 리더십 교육을 강화하고 있다.",
        "회사는 합숙형 어학 교육 모델을 글로벌 기업 대상으로 확대하였다.",
    )
    for 문장 in 통과해야_함:
        assert future_section_prose_problem(문장) == "", 문장
    for 문장 in 막혀야_함:
        assert future_section_prose_problem(문장) == (
            "future_section_no_forward_statement"
        ), 문장


# ══════════════════════════════════════════════════════════
# 미래 표지 «있다/없다»는 배치 판정의 빈 문자열과 다르다
# ══════════════════════════════════════════════════════════
#
# ★ 왜 갈라 두나 — `future_section_prose_problem`의 빈 문자열은 «반례를 못
#   찾았다»는 뜻이지 «미래 표지가 있다»가 아니다. 양태 표지가 아예 없는 문장도
#   빈 문자열을 받는다. 장 간 중복의 소유권을 시제로 가르는 자리(dedupe)는
#   그 차이를 구별해야 한다 — 못 하면 시제가 없는 문장까지 6장으로 넘어간다.


def test_양태가_없는_문장은_배치는_통과해도_미래표지는_없다():
    from src.features.composer.future_plan_guard import (
        future_section_prose_problem,
        has_forward_marker,
    )

    양태_없는_문장 = "회사의 교육서비스 부문은 온라인과 집합교육 서비스로 구성된다."

    assert future_section_prose_problem(양태_없는_문장) == ""
    assert has_forward_marker(양태_없는_문장) is False


def test_지침이_말한_어투는_미래표지로도_읽힌다():
    """지침·배치 관문·소유권 가르기가 «같은 목록»을 쓰는지 행동으로 묶는다."""
    from src.features.composer.future_plan_guard import has_forward_marker

    for 문장 in (
        "회사는 2026년에 AI 교육 체계를 고도화할 계획이다.",
        "회사는 새로운 시장으로 사업 영역을 확장하겠다고 밝혔다.",
        "회사는 진단 기반 리더십 교육을 강화하는 것을 목표로 한다.",
        "회사는 외국어평가 시장이 지속적으로 성장할 것으로 전망한다.",
    ):
        assert has_forward_marker(문장) is True, 문장
    for 문장 in (
        "회사는 AI 교육 체계를 고도화하고 리더십 교육을 강화하고 있다.",
        "회사는 합숙형 어학 교육 모델을 글로벌 기업 대상으로 확대하였다.",
    ):
        assert has_forward_marker(문장) is False, 문장
