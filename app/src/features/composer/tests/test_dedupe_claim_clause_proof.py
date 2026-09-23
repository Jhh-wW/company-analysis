"""중복 삭제의 «완전 주장절 대응» 안전 증명을 실제 삭제 경로로 못 박는다.

★ 왜 이 시험이 있나 (2026-09-23 독립 반례 — tmp/fourth-fable-dedupe-feedback.md) —
  같은 근거를 인용하고 글자 3-그램이 60% 넘게 겹친다는 이유로, 짧은 조건 절·상대
  교체·방향 교환·금액 재귀속·시제 쌍의 추가 조건이 붙은 정상 문장이 지워졌다.
  겹침은 «같은 조건·관계·사실이 남았다»는 증거가 아니다.
★ 확정 설계 — 3-그램 겹침·깊이·typed 소유는 «비교 후보와 소유 장»만 고른다. 실제
  삭제는 소유 문장 «전체»가 후보 문장 전체(또는 명시 주체를 반복한 후보의 한 절)와
  어절 그대로 대응할 때만 한다(절 끝의 닫힌 종결형↔연결형 변환만 허용). 소유 문장의
  앞 절만 같은 경우는 지우지 않는다. 삭제 표면은 호환 정규화를 하지 않고 부호·
  대소문자·인용부호 안 글자를 지우지 않는다.
★ 모든 음성은 «짝 판정(비교 후보)을 통과한다»는 전제를 먼저 단정한다 — 짝이 안 돼서
  남는 것이면 증명 경계를 시험하지 못한다.
"""

from __future__ import annotations

import pytest

from src.features.composer.constants import SECTION_IDS
from src.features.composer.dedupe import (
    _same_fact,
    _signature,
    drop_cross_section_duplicates,
    duplicates_kept_sentence,
)
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence

_CITATION = ("8",)
#: 소유 장 깊이를 확보하는 같은 인용의 별도 정상 문장(독립 검증 원래 입력과 같은 방식).
_DEPTH_FILLER = "가나다전자는 협력사와 분기마다 품질 점검 회의를 열어 개선 과제를 공유한다."


def _sentence(text: str) -> ComposedSentence:
    return ComposedSentence(text, _CITATION, "확인")


def _report(**by_section: tuple[str, ...]) -> ComposedReport:
    return ComposedReport(tuple(
        ComposedSection(
            section_id,
            tuple(_sentence(text) for text in by_section.get(section_id, ())),
        )
        for section_id in SECTION_IDS
    ))


def _texts(report: ComposedReport, section_id: str) -> list[str]:
    return [sentence.text for sentence in report.sections[SECTION_IDS.index(section_id)].sentences]


def _paired(left: str, right: str) -> bool:
    """두 문장이 짝 판정(비교 후보)을 통과하는가 — 삭제 경로와 같은 함수."""

    citations = frozenset(_CITATION)
    return _same_fact(
        _signature(left), _signature(right), citations, citations,
        frozenset(), frozenset(), documents_known=False,
        left_text=left, right_text=right,
    )


# ══════════════════════════════════════════════════════════
# ① 음성 — 짝은 되지만 삭제 증명이 없어 남는다 (장 간 · 장 안 모두)
# ══════════════════════════════════════════════════════════

#: (후보 = 지워질 뻔한 쪽, 소유 = 더 깊은 7장 문장)
_NEGATIVES = {
    "상대_교체": (
        "가나다전자는 2025년 공급사 가에 설비 대금 100억원을 지급하고 공동 개발 계약을 체결했다.",
        "가나다전자는 2025년 공급사 나에 설비 대금 100억원을 지급하고 공동 개발 계약을 체결했다.",
    ),
    "방향_교환": (
        "가나다전자는 공급사 가에 부품 100개를 공급하고 공동 개발 계약을 체결했다.",
        "공급사 가는 가나다전자에 부품 100개를 공급하고 공동 개발 계약을 체결했다.",
    ),
    "금액_재귀속": (
        "가나다전자는 거래처 가에 100억원과 거래처 나에 200억원을 각각 정산했다.",
        "가나다전자는 거래처 가에 200억원과 거래처 나에 100억원을 각각 정산했다.",
    ),
    "음수_부호": (
        "가나다전자의 2025년 영업이익은 -100억원으로 집계됐다.",
        "가나다전자의 2025년 영업이익은 100억원으로 집계됐다.",
    ),
    "영문_대소문자": (
        "가나다전자는 AB와 부품 공급 계약을 맺고 공동 개발을 진행한다.",
        "가나다전자는 Ab와 부품 공급 계약을 맺고 공동 개발을 진행한다.",
    ),
    "인용_안_어미": (
        "가나다전자의 공식 표어는 '고객과 함께 성장하며'이다.",
        "가나다전자의 공식 표어는 '고객과 함께 성장한다'이다.",
    ),
    "소유에만_조건": (
        "가나다전자는 해외 설비 사업을 확대하고 신규 공장을 가동한다.",
        "가나다전자는 해외 설비 사업을 승인 시에만 확대하고 신규 공장을 가동한다.",
    ),
    "소유에만_부정": (
        "가나다전자는 해외 설비 사업을 확대하고 신규 공장을 가동한다.",
        "가나다전자는 해외 설비 사업을 확대하고 신규 공장을 가동하지 않는다.",
    ),
    "수동_교체": (
        "가나다전자는 공급사 가로부터 핵심 부품을 공급하고 있다.",
        "가나다전자는 공급사 가로부터 핵심 부품을 공급받고 있다.",
    ),
    "조건_조사_교체": (
        "가나다전자의 행동 원칙은 정규직에만 적용되며 매년 갱신된다.",
        "가나다전자의 행동 원칙은 정규직에게도 적용되며 매년 갱신된다.",
    ),
    # 독립 검증 최종 3건 — 인용 안 표기, 소유 문장 뒤 절의 목표·조건.
    "인용_안_천단위_쉼표": (
        "가나다전자는 'Model 1,000'을 공식 제품 이름으로 사용하며 해외 시장에 공급한다.",
        "가나다전자는 'Model 1000'을 공식 제품 이름으로 사용하며 해외 시장에 공급한다.",
    ),
    "인용_안_원문자": (
        "가나다전자는 '제품 ①'을 공식 제품 이름으로 정하고 해외 시장에 공급한다.",
        "가나다전자는 '제품 1'을 공식 제품 이름으로 정하고 해외 시장에 공급한다.",
    ),
    "소유_뒤_절_목표": (
        "가나다전자는 공급사 가에 핵심 부품 100개를 공급한다.",
        "가나다전자는 공급사 가에 핵심 부품 100개를 공급하며 신규 공장을 운영하는 것을 목표로 한다.",
    ),
    "소유_뒤_절_조건": (
        "가나다전자는 공급사 가에 핵심 부품 100개를 공급한다.",
        "가나다전자는 공급사 가에 핵심 부품 100개를 공급하며 신규 공장을 운영하는 경우에만 계약을 유지한다.",
    ),
    # 기대 변경 — 종전 설계 초안은 「제시한다」가 소유 문장의 앞 절 「제시하며」와
    # 같다는 이유로 지웠다. 뒤 절이 앞 절을 한정하는지 증명할 수 없어 이제 남긴다.
    "소유_뒤_절_여분": (
        "가나다전자는 행동 원칙으로 정직·책임·존중을 제시한다.",
        "가나다전자는 행동 원칙으로 정직·책임·존중을 제시하며 매년 준수 교육을 실시한다.",
    ),
}


@pytest.mark.parametrize("case", sorted(_NEGATIVES))
def test_짝이어도_주장절이_통째로_같지_않으면_장_간_삭제를_하지_않는다(case: str) -> None:
    candidate, owner = _NEGATIVES[case]
    assert _paired(candidate, owner), "전제: 짝 판정은 통과해야 증명 경계를 시험한다"
    result, dropped = drop_cross_section_duplicates(_report(
        identity=(candidate,), operations_partners=(owner, _DEPTH_FILLER),
    ))
    assert dropped == 0
    assert _texts(result, "identity") == [candidate]


@pytest.mark.parametrize("case", sorted(_NEGATIVES))
def test_짝이어도_주장절이_통째로_같지_않으면_장_안_재배치도_버리지_않는다(case: str) -> None:
    candidate, owner = _NEGATIVES[case]
    assert duplicates_kept_sentence(_sentence(candidate), (_sentence(owner),)) is False


def test_다른_소유_문장_조각으로_한_주장을_조립하지_않는다() -> None:
    """독립 추적 — 앞 어절은 소유1, 뒤 어절은 소유2에서 빌리면 「가에 100개 납품」이 조립된다."""
    candidate = "회사는 공급사 가에 부품 100개를 납품했다."
    lend = "회사는 공급사 가에 부품 100개를 보관할 설비를 대여했다."
    other = "회사는 공급사 나에 부품 100개를 납품했다."
    assert _paired(candidate, lend) and _paired(candidate, other)
    result, dropped = drop_cross_section_duplicates(_report(
        identity=(candidate,), operations_partners=(lend, other, _DEPTH_FILLER),
    ))
    assert dropped == 0
    assert _texts(result, "identity") == [candidate]
    assert duplicates_kept_sentence(
        _sentence(candidate), (_sentence(lend), _sentence(other)),
    ) is False


def test_시제_쌍이라도_조건이_붙은_5장_문장은_지우지_않는다() -> None:
    """5↔6장 시제 소유는 «어느 장이 남길지»만 정한다 — 삭제 증명을 우회하지 않는다."""
    challenge = "가나다전자는 해외 설비 사업을 확대하고 신규 공장을 가동하며, 승인 시에만 수출한다."
    strategy = "가나다전자는 해외 설비 사업을 확대하고 신규 공장을 가동할 계획이다."
    assert _paired(challenge, strategy)
    result, dropped = drop_cross_section_duplicates(_report(
        current_challenges=(challenge,), future_strategy=(strategy,),
    ))
    assert dropped == 0
    assert _texts(result, "current_challenges") == [challenge]
    assert _texts(result, "future_strategy") == [strategy]


# ══════════════════════════════════════════════════════════
# ② 양성 — 증명되는 참 중복은 계속 지운다
# ══════════════════════════════════════════════════════════


def test_같은_주장_문장은_장이_달라도_한_장만_남긴다() -> None:
    text = "가나다전자는 공급사 가에 부품 100개를 공급하고 공동 개발 계약을 체결했다."
    result, dropped = drop_cross_section_duplicates(_report(
        identity=(text,), operations_partners=(text, _DEPTH_FILLER),
    ))
    assert dropped == 1
    assert _texts(result, "identity") == []
    assert _texts(result, "operations_partners") == [text, _DEPTH_FILLER]


def test_서술격_조사_축약만_다른_같은_문장은_지운다() -> None:
    """문장 끝 「'…'다」↔「'…'이다」는 닫힌 어미 묶음의 변환이다(인용 밖 어미만)."""
    candidate = "가나다전자의 공식 표어는 '빠르게 시험하고 함께 고친다'다."
    owner = "가나다전자의 공식 표어는 '빠르게 시험하고 함께 고친다'이다."
    result, dropped = drop_cross_section_duplicates(_report(
        identity=(candidate,), culture=(owner, _DEPTH_FILLER),
    ))
    assert dropped == 1
    assert _texts(result, "identity") == []
    assert duplicates_kept_sentence(_sentence(candidate), (_sentence(owner),)) is True


def test_시제_쌍이라도_소유_문장의_앞_절만_같은_5장_문장은_지우지_않는다() -> None:
    """기대 변경 — 종전 설계 초안은 6장이 소유하면 앞 절이 같은 5장 문장을 지웠다.

    6장 뒤 절(「보호 체계를 강화할 계획이다」)이 앞 절을 한정하는지 증명할 수 없다.
    """
    challenge = "가나다전자는 청소년 이용 제한 정책 도입을 추진 중이다."
    strategy = "가나다전자는 청소년 이용 제한 정책 도입을 추진 중이며 보호 체계를 강화할 계획이다."
    assert _paired(challenge, strategy)
    result, dropped = drop_cross_section_duplicates(_report(
        current_challenges=(challenge,), future_strategy=(strategy,),
    ))
    assert dropped == 0
    assert _texts(result, "current_challenges") == [challenge]
    assert _texts(result, "future_strategy") == [strategy]


def test_끝맺음_부호와_천_단위_쉼표만_다른_문장은_같은_주장이다() -> None:
    candidate = "가나다전자의 2025년 설비 매출은 1,200억원이다"
    owner = "가나다전자의 2025년 설비 매출은 1200억원이다."
    assert duplicates_kept_sentence(_sentence(candidate), (_sentence(owner),)) is True
