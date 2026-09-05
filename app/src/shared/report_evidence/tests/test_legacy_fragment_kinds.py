"""실제 legacy 생산자와 장 소유권 정본의 완전성을 지킨다."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.features.filingclean.extra import EXTRA_SECTION_HEADS
from src.features.homepage.constants import FRAGMENT_KIND as HOMEPAGE_FRAGMENT_KIND
from src.features.homepage.ir_pdf import OFFICIAL_IR_FRAGMENT_KIND
from src.features.newspick.constants import FRAGMENT_KIND as NEWS_FRAGMENT_KIND
from src.shared.report_evidence.legacy_fragment_kinds import (
    LEGACY_FRAGMENT_KINDS,
    LEGACY_FRAGMENT_KINDS_BY_SECTION,
    LEGACY_SEMANTIC_SECTIONS_BY_ENGINE_CELL,
    LEGACY_KIND_AUDITOR_FINDING,
    LEGACY_KIND_BUSINESS_CONTENT,
    LEGACY_KIND_FINANCIAL,
    LEGACY_KIND_HOMEPAGE,
    LEGACY_KIND_INTELLECTUAL_PROPERTY,
    LEGACY_KIND_LITIGATION,
    LEGACY_KIND_MARKET_SHARE,
    LEGACY_KIND_MDA,
    LEGACY_KIND_NEW_BUSINESS_OUTLOOK,
    LEGACY_KIND_NEWS,
    LEGACY_KIND_OFFICIAL_IR,
    LEGACY_KIND_RELATED_PARTY,
    LEGACY_KIND_RESEARCH_AND_DEVELOPMENT,
    LEGACY_KIND_REVENUE_AND_ORDERS,
    LEGACY_KIND_REVENUE_RECOGNITION,
    LEGACY_KIND_RISK_FACTOR,
    LEGACY_KIND_SG_AND_A,
    LEGACY_SECTIONS_BY_FRAGMENT_KIND,
    LegacyFragmentKindContractError,
    legacy_fragment_kind_is_owned_by,
    legacy_fragment_kinds_for_section,
    sections_for_legacy_fragment_kind,
    validate_legacy_fragment_kind_ownership,
)
from src.shared.revenue_table_provenance import (
    REVENUE_AXIS_PRODUCT,
    REVENUE_AXIS_REGION,
    revenue_table_section_id,
)


_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_RUN_PILOT = _PROJECT_ROOT / "analysis_engine" / "tools" / "run_pilot.py"

_ENGINE_SECTION_KINDS = frozenset(
    {
        LEGACY_KIND_BUSINESS_CONTENT,
        LEGACY_KIND_REVENUE_RECOGNITION,
        LEGACY_KIND_FINANCIAL,
        LEGACY_KIND_MDA,
        LEGACY_KIND_RESEARCH_AND_DEVELOPMENT,
        LEGACY_KIND_RELATED_PARTY,
        LEGACY_KIND_SG_AND_A,
        LEGACY_KIND_REVENUE_AND_ORDERS,
    }
)
_EXTRA_SECTION_KINDS = frozenset(
    {
        LEGACY_KIND_NEW_BUSINESS_OUTLOOK,
        LEGACY_KIND_MARKET_SHARE,
        LEGACY_KIND_LITIGATION,
        LEGACY_KIND_AUDITOR_FINDING,
        LEGACY_KIND_INTELLECTUAL_PROPERTY,
        LEGACY_KIND_RISK_FACTOR,
    }
)


def _literal_assignment(path: Path, name: str) -> object:
    """무거운 1판 엔진을 import하지 않고 실제 대입값을 AST로 읽는다."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                return ast.literal_eval(node.value)
    raise AssertionError(f"{path}에서 {name} 대입을 찾지 못했습니다")


def test_실제_생산자가_만드는_모든_종류가_정본에_정확히_등록된다() -> None:
    """생산자 추가·이름 변경 시 정본과 함께 바꾸지 않으면 실패한다."""

    section_heads = _literal_assignment(_RUN_PILOT, "SECTION_HEADS")
    assert isinstance(section_heads, dict)
    assert frozenset(section_heads) == _ENGINE_SECTION_KINDS
    assert frozenset(EXTRA_SECTION_HEADS) == _EXTRA_SECTION_KINDS
    assert HOMEPAGE_FRAGMENT_KIND == LEGACY_KIND_HOMEPAGE
    assert OFFICIAL_IR_FRAGMENT_KIND == LEGACY_KIND_OFFICIAL_IR
    assert NEWS_FRAGMENT_KIND == LEGACY_KIND_NEWS
    produced = (
        frozenset(section_heads)
        | frozenset(EXTRA_SECTION_HEADS)
        | frozenset(
            {
                HOMEPAGE_FRAGMENT_KIND,
                OFFICIAL_IR_FRAGMENT_KIND,
                NEWS_FRAGMENT_KIND,
            }
        )
    )

    assert produced == LEGACY_FRAGMENT_KINDS
    assert len(produced) == 17


def test_실제_CELL_SOURCES가_보내는_DART종류의_semantic장을_정본이_누락하지않는다() -> None:
    """생산자 배선을 AST로 읽어 숫자 칸→v2 장 보존을 전 종류에 강제한다.

    ``뉴스``는 별도 newspick 생산자와 신뢰 정책이 소유하므로 여기서는
    ``SECTION_HEADS``가 실제로 만드는 DART legacy 종류만 대조한다. 이 시험은
    허용 장을 전부로 넓히는 기준값이 아니라, 생산자가 이미 명시한 소비 칸을
    v2 이행 중 잃지 않는 최소 완전성 계약이다.
    """

    section_heads = _literal_assignment(_RUN_PILOT, "SECTION_HEADS")
    cell_sources = _literal_assignment(_RUN_PILOT, "CELL_SOURCES")
    assert isinstance(section_heads, dict)
    assert isinstance(cell_sources, dict)
    assert frozenset(cell_sources) == frozenset(
        LEGACY_SEMANTIC_SECTIONS_BY_ENGINE_CELL
    )

    producer_sections_by_kind: dict[str, set[str]] = {
        kind: set() for kind in section_heads
    }
    for cell, kinds in cell_sources.items():
        semantic_sections = LEGACY_SEMANTIC_SECTIONS_BY_ENGINE_CELL[cell]
        for kind in kinds:
            if kind in producer_sections_by_kind:
                producer_sections_by_kind[kind].update(semantic_sections)

    missing = {
        kind: required - set(LEGACY_SECTIONS_BY_FRAGMENT_KIND[kind])
        for kind, required in producer_sections_by_kind.items()
        if required - set(LEGACY_SECTIONS_BY_FRAGMENT_KIND[kind])
    }
    assert missing == {}
    assert producer_sections_by_kind[LEGACY_KIND_REVENUE_AND_ORDERS] == {
        "operations_partners"
    }
    assert producer_sections_by_kind[LEGACY_KIND_RESEARCH_AND_DEVELOPMENT] == {
        "portfolio",
        "current_challenges",
        "operations_partners",
    }


def test_정확한_종류별_장_소유행렬을_고정한다() -> None:
    revenue_sections = {
        revenue_table_section_id(REVENUE_AXIS_PRODUCT),
        revenue_table_section_id(REVENUE_AXIS_REGION),
        "operations_partners",
    }
    expected = {
        "사업내용": {
            "identity",
            "business_model",
            "portfolio",
            "past_changes",
            "operations_partners",
            "culture",
            "competitive_position",
        },
        "수익인식": {"business_model"},
        "재무": {"business_model", "past_changes", "competitive_position"},
        "MD&A": {"past_changes", "current_challenges", "future_strategy"},
        "연구개발": {
            "portfolio",
            "current_challenges",
            "future_strategy",
            "operations_partners",
        },
        "특수관계자": {"operations_partners"},
        "판관비": {"past_changes"},
        "매출수주": revenue_sections,
        "신규사업전망": {"future_strategy"},
        "시장점유율": {"competitive_position"},
        "소송·분쟁": {"current_challenges"},
        "감사인지적": {"current_challenges"},
        "지적재산권": {"portfolio"},
        "위험요인": {"current_challenges"},
        "홈페이지": {
            "identity",
            "business_model",
            "portfolio",
            "current_challenges",
            "future_strategy",
            "operations_partners",
            "culture",
        },
        "공식 IR": {
            "identity",
            "business_model",
            "portfolio",
            "past_changes",
            "current_challenges",
            "future_strategy",
            "operations_partners",
            "culture",
            "competitive_position",
        },
        "뉴스": {"current_challenges", "competitive_position"},
    }

    assert {
        kind: set(sections)
        for kind, sections in LEGACY_SECTIONS_BY_FRAGMENT_KIND.items()
    } == expected
    for section_id, kinds in LEGACY_FRAGMENT_KINDS_BY_SECTION.items():
        assert kinds == frozenset(
            kind for kind, sections in expected.items() if section_id in sections
        )


def test_홈페이지는_실제_수집범위의_직접관련_장에도_간다() -> None:
    assert sections_for_legacy_fragment_kind(LEGACY_KIND_HOMEPAGE) == frozenset(
        {
            "identity",
            "business_model",
            "portfolio",
            "current_challenges",
            "future_strategy",
            "operations_partners",
            "culture",
        }
    )


def test_신규사업전망은_부분문자열_사업때문에_다른_장으로_새지_않는다() -> None:
    assert sections_for_legacy_fragment_kind(
        LEGACY_KIND_NEW_BUSINESS_OUTLOOK
    ) == frozenset({"future_strategy"})
    assert not legacy_fragment_kind_is_owned_by(
        LEGACY_KIND_NEW_BUSINESS_OUTLOOK, "identity"
    )
    assert not legacy_fragment_kind_is_owned_by(
        LEGACY_KIND_NEW_BUSINESS_OUTLOOK, "past_changes"
    )


@pytest.mark.parametrize(
    "kind", ["사업", "신규사업", "공식IR", " 홈페이지", "홈페이지 "]
)
def test_부분문자열과_비슷한_이름은_등록종류로_받지_않는다(kind: str) -> None:
    with pytest.raises(LegacyFragmentKindContractError, match="등록되지 않은"):
        sections_for_legacy_fragment_kind(kind)


def test_미등록종류와_알수없는_장은_순수_api에서_예외가_난다() -> None:
    with pytest.raises(LegacyFragmentKindContractError, match="등록되지 않은"):
        legacy_fragment_kind_is_owned_by("처음보는종류", "identity")
    with pytest.raises(LegacyFragmentKindContractError, match="알 수 없는"):
        legacy_fragment_kinds_for_section("처음보는장")


def test_무소유와_알수없는_장_계약은_검증에서_예외가_난다() -> None:
    with pytest.raises(LegacyFragmentKindContractError, match="소유 장이 없는"):
        validate_legacy_fragment_kind_ownership({"새종류": ()})
    with pytest.raises(LegacyFragmentKindContractError, match="알 수 없는 소유 장"):
        validate_legacy_fragment_kind_ownership({"새종류": ("없는장",)})
