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
