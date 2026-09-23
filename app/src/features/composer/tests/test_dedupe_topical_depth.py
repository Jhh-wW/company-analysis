"""장 간 중복 소유를 «넓은 조각의 인용 빈도»가 아니라 같은 주장 칸의 깊이로 정한다.

★ 4차 후속 재현 — 7장이 같은 공식 조각을 공장·제휴·조달 같은 «다른» 사실에 여러 번
  인용했다는 이유만으로 2장의 매출 구성 정답을 가져갔다. 또 같은 조각을 인용한 두
  장의 문장이 3-그램으로 비슷하면 서로 다른 기간·금액 사실이 하나로 합쳐졌다.
★ 지키는 것:
  ① 명시적으로 다른 주장 칸의 문장은 소유 깊이에 세지 않는다.
  ② 같은 주제를 여러 문장으로 다룬 장은 여전히 소유한다(원래 깊이 규칙의 목적).
  ③ 두 문장이 서로 상대에 없는 수치를 각각 가지면(기간·금액이 다름) 합치지 않는다.
     지울 문장에만 있는 수치는 남긴다. 쉼표 표기 차이는 같은 수치다.
  ④ (2026-09-23 총괄 확정) 소유 규칙이 정한 뒤 실제 삭제는 주장절이 어절 그대로
     대응할 때만 한다 — 의역·절 안 여분 어절은 증명이 없어 두 장에 남는다.
"""

from __future__ import annotations

import pytest

from src.features.composer.constants import SECTION_IDS
from src.features.composer.dedupe import drop_cross_section_duplicates, duplicates_kept_sentence
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)

_DOCUMENT = "document:filing.example:1"
_REVENUE_MIX = "가나다전자의 주된 영업수익은 설비 판매 매출과 유지보수 용역 매출로 구성된다."
_REVENUE_MIX_7 = "회사의 주된 영업수익은 설비 판매 매출과 유지보수 용역 매출로 구성된다."
_PARTNER = "가나다전자는 해외 유통사 세 곳과 판매 제휴를 맺어 북미 판로를 넓혔다."
_PARTNER_1 = "가나다전자는 해외 유통사 세 곳과 판매 제휴를 맺어 북미 판로를 확대했다."


def _fragment(fragment_id: str, slots: tuple[str, ...] = ()) -> CollectedFragment:
    return CollectedFragment(
        fragment_id=fragment_id, kind="typed", text=f"공식 조각 {fragment_id}",
        document_identity=_DOCUMENT, supported_claim_slots=slots,
        formal_source_kind="dart_audit_report",
    )


def _sentence(text: str, citations: tuple[str, ...], slot: str = "") -> ComposedSentence:
    return ComposedSentence(text, citations, "확인", planned_claim_slot=slot)


def _report(**by_section: tuple[ComposedSentence, ...]) -> ComposedReport:
    return ComposedReport(tuple(
        ComposedSection(section_id, by_section.get(section_id, ())) for section_id in SECTION_IDS
    ))


def _texts(report: ComposedReport, section_id: str) -> list[str]:
    return [sentence.text for sentence in report.sections[SECTION_IDS.index(section_id)].sentences]


_BROAD = _fragment("1", (
    "operations_partners:value_chain", "operations_partners:partnership",
    "operations_partners:operating_role",
))
_SPECIFIC = _fragment("18", ("business_model:revenue_model",))
_OTHER_FACTS_7 = (
    _sentence("가나다전자는 부산 공장에서 설비를 조립한다.", ("1",), "operations_partners:operating_role"),
    _sentence("가나다전자는 해외 유통사와 판매 제휴를 맺었다.", ("1",), "operations_partners:partnership"),
    _sentence("가나다전자는 부품을 국내 협력사에서 조달한다.", ("1",), "operations_partners:operating_role"),
)


#: 원래 사례(「가나다전자의」↔「회사의」, 「넓혔다」↔「확대했다」)는 주장절이 어절
#: 그대로 대응하지 않아 삭제 증명이 없다(2026-09-23 총괄 확정). 소유 규칙은 증명되는
#: 같은 문장으로 재고, 원래 사례는 보존 기대 음성으로 함께 잰다.
_ORIGINAL_AND_PROVEN = ("원래_의역_짝_보존", "증명된_같은_문장")


@pytest.mark.parametrize("seven_text", (_REVENUE_MIX_7, _REVENUE_MIX), ids=_ORIGINAL_AND_PROVEN)
def test_넓은_조각의_다른_사실_인용_빈도가_2장_정답을_빼앗지_않는다(seven_text: str) -> None:
    report = _report(
        business_model=(_sentence(_REVENUE_MIX, ("18",), "business_model:revenue_model"),),
        operations_partners=(_sentence(seven_text, ("1",), "operations_partners:value_chain"),
                             *_OTHER_FACTS_7),
    )
    result, dropped = drop_cross_section_duplicates(report, fragments=(_BROAD, _SPECIFIC))
    proven = seven_text == _REVENUE_MIX
    assert dropped == (1 if proven else 0)
    # 2장 정답은 어느 경우에도 빼앗기지 않는다.
    assert _texts(result, "business_model") == [_REVENUE_MIX]
    # 7장의 다른 사실은 하나도 지우지 않는다.
    assert _texts(result, "operations_partners") == (
        [] if proven else [seven_text]
    ) + [sentence.text for sentence in _OTHER_FACTS_7]


@pytest.mark.parametrize("identity_text", (_PARTNER_1, _PARTNER), ids=_ORIGINAL_AND_PROVEN)
def test_같은_주제를_여러_문장으로_다룬_장은_여전히_소유한다(identity_text: str) -> None:
    partner_slot = "operations_partners:partnership"
    report = _report(
        identity=(_sentence(identity_text, ("1",), "identity:business_definition"),),
        operations_partners=(
            _sentence(_PARTNER, ("1",), partner_slot),
            _sentence("가나다전자는 유통사와 공동 판촉 계약도 체결했다.", ("1",), partner_slot),
            _sentence("가나다전자는 제휴 유통사에 설비 교육을 제공한다.", ("1",), partner_slot),
        ),
    )
    result, dropped = drop_cross_section_duplicates(report, fragments=(_BROAD,))
    proven = identity_text == _PARTNER
    assert dropped == (1 if proven else 0)
    assert _texts(result, "identity") == ([] if proven else [identity_text])
    assert _PARTNER in _texts(result, "operations_partners")


@pytest.mark.parametrize("seven_text", (_REVENUE_MIX_7, _REVENUE_MIX), ids=_ORIGINAL_AND_PROVEN)
def test_주장_칸이_없는_옛_입력은_종전_깊이_규칙을_쓴다(seven_text: str) -> None:
    report = _report(
        business_model=(_sentence(_REVENUE_MIX, ("1",)),),
        operations_partners=(_sentence(seven_text, ("1",)),
                             *(_sentence(sentence.text, sentence.citations) for sentence in _OTHER_FACTS_7)),
    )
    result, dropped = drop_cross_section_duplicates(report, fragments=(_fragment("1"),))
    proven = seven_text == _REVENUE_MIX
    assert dropped == (1 if proven else 0)
    assert _texts(result, "business_model") == ([] if proven else [_REVENUE_MIX])


def test_같은_조각이어도_기간과_금액이_다르면_합치지_않는다() -> None:
    earlier = "가나다전자의 설비 판매 매출은 2024년 100억원이었고 유지보수 매출이 뒤를 이었다."
    later = "가나다전자의 설비 판매 매출은 2025년 120억원이었고 유지보수 매출이 뒤를 이었다."
    report = _report(
        business_model=(_sentence(earlier, ("5",), "business_model:revenue_model"),),
        past_changes=(_sentence(later, ("5",), "past_changes:change_context"),),
    )
    result, dropped = drop_cross_section_duplicates(report, fragments=(_fragment("5"),))
    assert dropped == 0
    assert _texts(result, "business_model") == [earlier]
    assert _texts(result, "past_changes") == [later]


def test_한쪽에만_있는_부가_수치도_합치지_않는다() -> None:
    base = "가나다전자는 2025년 설비 수주 20건을 기록하며 공공 부문 고객을 넓혔다."
    extra = "가나다전자는 2025년 설비 수주 20건과 수주액 300억원을 기록하며 공공 부문 고객을 넓혔다."
    report = _report(
        portfolio=(_sentence(base, ("5",), "portfolio:revenue_link"),),
        past_changes=(_sentence(extra, ("5",), "past_changes:completed_execution"),),
    )
    _result, dropped = drop_cross_section_duplicates(report, fragments=(_fragment("5"),))
    assert dropped == 0


def test_소유_문장이_절_안에_어절을_더_가지면_증명이_없어_뒤_장_문장도_남긴다() -> None:
    """위 시험의 거울 — 기대 변경 (2026-09-23 총괄 확정, 종전에는 뒤 장 문장을 지웠다).

    소유 문장(3장)은 같은 절 «안»에 「과 수주액 300억원을」을 더 가졌다. 소유 절 내부의
    여분 어절을 건너뛰는 대응은 삭제 증거로 쓰지 않는다 — 건너뛴 어절이 조건·부정·
    상대일 때와 표면으로 구별할 수 없다. 이 쌍은 정보 손실이 없는 쪽이지만 일반 규칙으로
    증명하지 못해 두 장에 남는다(중복 노출).
    """
    base = "가나다전자는 2025년 설비 수주 20건을 기록하며 공공 부문 고객을 넓혔다."
    extra = "가나다전자는 2025년 설비 수주 20건과 수주액 300억원을 기록하며 공공 부문 고객을 넓혔다."
    report = _report(
        portfolio=(_sentence(extra, ("5",), "portfolio:revenue_link"),),
        past_changes=(_sentence(base, ("5",), "past_changes:completed_execution"),),
    )
    result, dropped = drop_cross_section_duplicates(report, fragments=(_fragment("5"),))
    assert dropped == 0
    assert _texts(result, "portfolio") == [extra]
    assert _texts(result, "past_changes") == [base]


def test_장_안_중복도_옮겨_오는_문장에만_있는_수치를_지킨다() -> None:
    """verify 재배치는 «중복»이면 옮겨 오는 문장을 버린다 — 그 문장만의 수치를 잃으면 안 된다."""
    base = _sentence("가나다전자는 2025년 설비 수주 20건을 기록하며 공공 부문 고객을 넓혔다.", ("5",))
    extra = _sentence(
        "가나다전자는 2025년 설비 수주 20건과 수주액 300억원을 기록하며 공공 부문 고객을 넓혔다.", ("5",),
    )
    later = _sentence("가나다전자는 2026년 설비 수주 25건을 기록하며 공공 부문 고객을 넓혔다.", ("5",))
    assert duplicates_kept_sentence(extra, (base,)) is False
    # 기대 변경 (2026-09-23) — 기존 문장이 절 안에 어절을 더 가져 증명이 없다(종전 True).
    assert duplicates_kept_sentence(base, (extra,)) is False
    assert duplicates_kept_sentence(base, (base,)) is True
    assert duplicates_kept_sentence(later, (base,)) is False


def test_쉼표만_다른_같은_수치는_여전히_같은_사실이다() -> None:
    left = "가나다전자의 2025년 설비 매출은 1,200억원으로 집계돼 주력 사업 비중이 커졌다."
    right = "가나다전자의 2025년 설비 매출은 1200억원으로 집계돼 주력 사업 비중이 커졌다."
    report = _report(
        business_model=(_sentence(left, ("5",), "business_model:revenue_model"),),
        past_changes=(_sentence(right, ("5",), "past_changes:change_context"),),
    )
    _result, dropped = drop_cross_section_duplicates(report, fragments=(_fragment("5"),))
    assert dropped == 1
