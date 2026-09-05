"""legacy 수집 조각 이름과 장 소유권의 단일 정본.

운영 수집기의 오래된 조각 모양은 ``{"종류": ..., "원문": ...}``이다.
``종류``는 출처·절 분류일 뿐 의미 슬롯이 아니므로 새 typed 수집기는 이 표로
라우팅하지 않는다. 다만 legacy 조각을 장별 근거 계약으로 옮기는 동안에는
생산자와 소비자가 같은 정확한 이름·허용 장을 보도록 이 모듈만 사용한다.

부분 문자열 비교는 금지한다. 예를 들어 ``"사업" in "신규사업전망"``으로
판정하면 미래 계획이 정체성·과거 실적 근거로 섞인다. 공개 함수는 등록된 이름의
정확 일치만 받고, 모르는 이름이나 소유 장이 없는 계약은 예외로 드러낸다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Final

from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.revenue_table_provenance import (
    REVENUE_AXIS_PRODUCT,
    REVENUE_AXIS_REGION,
    revenue_table_section_id,
)


LEGACY_KIND_BUSINESS_CONTENT: Final[str] = "사업내용"
LEGACY_KIND_REVENUE_RECOGNITION: Final[str] = "수익인식"
LEGACY_KIND_FINANCIAL: Final[str] = "재무"
LEGACY_KIND_MDA: Final[str] = "MD&A"
LEGACY_KIND_RESEARCH_AND_DEVELOPMENT: Final[str] = "연구개발"
LEGACY_KIND_RELATED_PARTY: Final[str] = "특수관계자"
LEGACY_KIND_SG_AND_A: Final[str] = "판관비"
LEGACY_KIND_REVENUE_AND_ORDERS: Final[str] = "매출수주"
LEGACY_KIND_NEW_BUSINESS_OUTLOOK: Final[str] = "신규사업전망"
LEGACY_KIND_MARKET_SHARE: Final[str] = "시장점유율"
LEGACY_KIND_LITIGATION: Final[str] = "소송·분쟁"
LEGACY_KIND_AUDITOR_FINDING: Final[str] = "감사인지적"
LEGACY_KIND_INTELLECTUAL_PROPERTY: Final[str] = "지적재산권"
LEGACY_KIND_RISK_FACTOR: Final[str] = "위험요인"
LEGACY_KIND_HOMEPAGE: Final[str] = "홈페이지"
LEGACY_KIND_OFFICIAL_IR: Final[str] = "공식 IR"
LEGACY_KIND_NEWS: Final[str] = "뉴스"


class LegacyFragmentKindContractError(ValueError):
    """legacy 종류 이름이나 소유권 표가 정본 계약을 어겼다."""


# 1판 ``run_pilot.CELL_SOURCES``의 숫자 칸을 v2 의미 장으로 옮길 때 쓰는
# 최소 보존 계약이다. 이것은 fragment 종류의 최종 허용 범위를 넓히는 표가
# 아니다. 실제 생산자가 특정 종류를 어느 구형 칸에 넣었다면, 그 칸의 의미를
# 이어받은 v2 장에서 그 조각을 후보로 볼 기회만은 잃지 않아야 한다.
_LEGACY_SEMANTIC_SECTIONS_BY_ENGINE_CELL = {
    "1": frozenset({"business_model"}),
    "2": frozenset({"portfolio"}),
    "3": frozenset({"past_changes"}),
    "4-1": frozenset({"current_challenges"}),
    # 4-2는 「지금 문제에 회사가 실제로 하고 있는 일」이다. 완료된 과거
    # 성과나 미실행 계획으로 넓히지 않고 현재 과제의 대응에만 연결한다.
    "4-2": frozenset({"current_challenges"}),
    "4-3": frozenset({"future_strategy"}),
    "9": frozenset({"operations_partners"}),
}

LEGACY_SEMANTIC_SECTIONS_BY_ENGINE_CELL: Final[
    Mapping[str, frozenset[str]]
] = MappingProxyType(_LEGACY_SEMANTIC_SECTIONS_BY_ENGINE_CELL)


def validate_legacy_fragment_kind_ownership(
    ownership_by_kind: Mapping[str, Iterable[str]],
) -> None:
    """종류→장 표가 정확한 이름과 하나 이상의 유효 장을 갖는지 검사한다.

    이 함수는 입력을 바꾸지 않는 순수 검증 API다. 정본 import 시에도 실행하며,
    adapter가 별도 표를 만들 필요가 있는 시험·이행 코드에서도 같은 실패 규칙을
    재사용할 수 있다.
    """

    if not ownership_by_kind:
        raise LegacyFragmentKindContractError("legacy 조각 종류 소유권 표가 비었습니다")
    valid_sections = frozenset(REQUIRED_EVIDENCE_SECTION_IDS)
    for kind, raw_sections in ownership_by_kind.items():
        if type(kind) is not str or not kind or kind != kind.strip():
            raise LegacyFragmentKindContractError(
                f"legacy 조각 종류 이름이 정확하지 않습니다: {kind!r}"
            )
        sections = tuple(raw_sections)
        if not sections:
            raise LegacyFragmentKindContractError(
                f"소유 장이 없는 legacy 조각 종류입니다: {kind!r}"
            )
        if any(type(section_id) is not str for section_id in sections):
            raise LegacyFragmentKindContractError(
                f"legacy 조각 {kind!r}의 장 식별자는 문자열이어야 합니다"
            )
        duplicates = sorted(
            section_id for section_id in set(sections) if sections.count(section_id) > 1
        )
        if duplicates:
            raise LegacyFragmentKindContractError(
                f"legacy 조각 {kind!r}의 소유 장이 중복됐습니다: {duplicates}"
            )
        unknown_sections = sorted(set(sections) - valid_sections)
        if unknown_sections:
            raise LegacyFragmentKindContractError(
                f"legacy 조각 {kind!r}의 알 수 없는 소유 장: {unknown_sections}"
            )


validate_legacy_fragment_kind_ownership(
    {
        f"engine-cell:{cell}": sections
        for cell, sections in _LEGACY_SEMANTIC_SECTIONS_BY_ENGINE_CELL.items()
    }
)


# 이 표는 legacy 종류가 직접 뒷받침할 수 있는 장의 상한이다. 조각 하나가 실제로
# 그 장에 적합한지는 typed ``section_id``·``covered_slot_ids``가 판단한다. 특히
# 홈페이지는 회사소개뿐 아니라 제품·수익구조·공식 과제·계획·운영 관계 페이지도
# 수집하므로 해당 장에 전달한다. 과거 실적과 독립 비교는 자사 홈페이지만으로
# 확정하지 않는다.
_LEGACY_SECTIONS_BY_FRAGMENT_KIND = {
    LEGACY_KIND_BUSINESS_CONTENT: frozenset(
        {
            "identity",
            "business_model",
            "portfolio",
            "past_changes",
            "operations_partners",
            "culture",
            "competitive_position",
        }
    ),
    LEGACY_KIND_REVENUE_RECOGNITION: frozenset({"business_model"}),
    LEGACY_KIND_FINANCIAL: frozenset(
        {"business_model", "past_changes", "competitive_position"}
    ),
    LEGACY_KIND_MDA: frozenset(
        {"past_changes", "current_challenges", "future_strategy"}
    ),
    LEGACY_KIND_RESEARCH_AND_DEVELOPMENT: frozenset(
        {
            "portfolio",
            "current_challenges",
            "future_strategy",
            "operations_partners",
        }
    ),
    LEGACY_KIND_RELATED_PARTY: frozenset({"operations_partners"}),
    LEGACY_KIND_SG_AND_A: frozenset({"past_changes"}),
    LEGACY_KIND_REVENUE_AND_ORDERS: frozenset(
        {
            revenue_table_section_id(REVENUE_AXIS_PRODUCT),
            revenue_table_section_id(REVENUE_AXIS_REGION),
            "operations_partners",
        }
    ),
    LEGACY_KIND_NEW_BUSINESS_OUTLOOK: frozenset({"future_strategy"}),
    LEGACY_KIND_MARKET_SHARE: frozenset({"competitive_position"}),
    LEGACY_KIND_LITIGATION: frozenset({"current_challenges"}),
    LEGACY_KIND_AUDITOR_FINDING: frozenset({"current_challenges"}),
    LEGACY_KIND_INTELLECTUAL_PROPERTY: frozenset({"portfolio"}),
    LEGACY_KIND_RISK_FACTOR: frozenset({"current_challenges"}),
    LEGACY_KIND_HOMEPAGE: frozenset(
        {
            "identity",
            "business_model",
            "portfolio",
            "current_challenges",
            "future_strategy",
            "operations_partners",
            "culture",
        }
    ),
    LEGACY_KIND_OFFICIAL_IR: frozenset(REQUIRED_EVIDENCE_SECTION_IDS),
    LEGACY_KIND_NEWS: frozenset(
        {"current_challenges", "competitive_position"}
    ),
}

validate_legacy_fragment_kind_ownership(_LEGACY_SECTIONS_BY_FRAGMENT_KIND)

LEGACY_SECTIONS_BY_FRAGMENT_KIND: Final[Mapping[str, frozenset[str]]] = (
    MappingProxyType(_LEGACY_SECTIONS_BY_FRAGMENT_KIND)
)

LEGACY_FRAGMENT_KINDS: Final[frozenset[str]] = frozenset(
    LEGACY_SECTIONS_BY_FRAGMENT_KIND
)

LEGACY_FRAGMENT_KINDS_BY_SECTION: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        section_id: frozenset(
            kind
            for kind, owned_sections in LEGACY_SECTIONS_BY_FRAGMENT_KIND.items()
            if section_id in owned_sections
        )
        for section_id in REQUIRED_EVIDENCE_SECTION_IDS
    }
)


def sections_for_legacy_fragment_kind(kind: str) -> frozenset[str]:
    """정확히 등록된 종류의 허용 장을 돌려주고 모르는 이름은 거절한다."""

    if type(kind) is not str or kind not in LEGACY_SECTIONS_BY_FRAGMENT_KIND:
        raise LegacyFragmentKindContractError(
            f"등록되지 않은 legacy 조각 종류입니다: {kind!r}"
        )
    sections = LEGACY_SECTIONS_BY_FRAGMENT_KIND[kind]
    if not sections:
        # 정본은 import 때 이미 검사하지만 런타임 변조도 조용히 통과시키지 않는다.
        raise LegacyFragmentKindContractError(
            f"소유 장이 없는 legacy 조각 종류입니다: {kind!r}"
        )
    return sections


def legacy_fragment_kind_is_owned_by(kind: str, section_id: str) -> bool:
    """종류가 장을 소유하는지 정확 일치로만 판정한다."""

    if type(section_id) is not str or section_id not in REQUIRED_EVIDENCE_SECTION_IDS:
        raise LegacyFragmentKindContractError(
            f"알 수 없는 근거 장 식별자입니다: {section_id!r}"
        )
    return section_id in sections_for_legacy_fragment_kind(kind)


def legacy_fragment_kinds_for_section(section_id: str) -> frozenset[str]:
    """장에 허용된 등록 종류를 돌려주고 알 수 없는 장은 거절한다."""

    if type(section_id) is not str or section_id not in LEGACY_FRAGMENT_KINDS_BY_SECTION:
        raise LegacyFragmentKindContractError(
            f"알 수 없는 근거 장 식별자입니다: {section_id!r}"
        )
    kinds = LEGACY_FRAGMENT_KINDS_BY_SECTION[section_id]
    if not kinds:
        raise LegacyFragmentKindContractError(
            f"허용된 legacy 조각 종류가 없는 장입니다: {section_id!r}"
        )
    return kinds
