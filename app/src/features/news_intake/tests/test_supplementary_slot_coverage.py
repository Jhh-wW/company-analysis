"""뉴스 매핑이 붙이는 의미 칸은 보조 종류(news)가 주장할 수 있는 칸 안에만 있어야 한다.

transport 는 보조 목록 밖 칸을 하나라도 실은 조각을 만나면 packet 전체를 거절하므로,
매핑 단계에서 미리 걸러 「탈락」으로 만드는지, 그리고 계약서가 허용한 5·6장(귀속 인용문만)이
실제로 칸을 얻는지 잠근다.
"""

from __future__ import annotations

from src.features.news_intake import constants as c
from src.features.news_intake import mapping
from src.shared.report_evidence.policy import collector_slots_for
from src.shared.report_evidence.source_kind_policy import (
    supplementary_slots_for_source_kind,
)

_NEWS_SECTIONS = tuple(
    sorted(
        c.JOURNALIST_NARRATION_SECTIONS
        | c.ATTRIBUTED_QUOTE_ONLY_SECTIONS
        | c.DATED_QUOTE_ONLY_SECTIONS
    )
)


def test_뉴스가_갈_수_있는_모든_장의_칸은_보조_허용_목록_안에_있다() -> None:
    allowed = supplementary_slots_for_source_kind(c.SOURCE_KIND_NEWS)
    for section_id in _NEWS_SECTIONS:
        slots = mapping._supported_slots((section_id,))
        assert set(slots) <= allowed, (section_id, slots)


def test_5장과_6장은_보조_칸을_얻는다() -> None:
    for section_id in ("current_challenges", "future_strategy"):
        slots = mapping._supported_slots((section_id,))
        assert slots, section_id
        assert all(slot.startswith(f"{section_id}:") for slot in slots)


def test_보조_목록_밖_칸은_붙이지_않고_빈_장은_뺀다() -> None:
    # identity 의 회사 공식 자기 정의 칸은 뉴스가 주장할 수 없다.
    identity_slots = mapping._supported_slots(("identity",))
    assert "identity:corporate_identity" in collector_slots_for("identity")
    assert "identity:corporate_identity" not in identity_slots
    assert identity_slots  # 다른 identity 칸은 남는다

    # 칸이 하나도 남지 않는 장은 조각의 장 목록에서 빠진다.
    remaining = mapping._sections_with_slots(
        ("identity", "current_challenges"),
        mapping._supported_slots(("identity",)),
    )
    assert remaining == ("identity",)


def test_구장은_보조_칸을_하나도_얻지_못한다() -> None:
    """9장은 회사가 밝힌 차별점만 싣는 장이라 산문 칸까지 통째로 닫혀 있다."""

    section_id = "competitive_position"

    assert collector_slots_for(section_id)  # 장 자체에는 칸이 있다
    assert mapping._supported_slots((section_id,)) == ()
    assert mapping._sections_with_slots(
        (section_id,), mapping._supported_slots((section_id,))
    ) == ()


def test_구장은_분류_대상_장에서도_빠진다() -> None:
    assert "competitive_position" in c.NEWS_EXCLUDED_SECTIONS
    # 5·6장을 빼는 규칙과 뜻이 달라 목록을 합치지 않는다.
    assert not (c.NEWS_EXCLUDED_SECTIONS & c.NON_EXTENDABLE_SECTIONS)


def test_탈락_사유_상수가_있다() -> None:
    assert c.EXCLUDED_NO_SUPPLEMENTARY_SLOT == "no_supplementary_slot"
    assert c.EXCLUDED_ARTICLE_FRAGMENT_LIMIT == "article_fragment_limit"
