"""빈 장 복구에서 같은 종류의 근거가 다른 필수 질문을 밀어내지 않는다."""

from dataclasses import replace

import pytest

from src.features.composer.empty_section_recovery import recovery_evidence
from src.features.composer.empty_section_recovery_constants import (
    MAX_EMPTY_RECOVERY_EVIDENCE_CHARS,
    MAX_EMPTY_RECOVERY_FRAGMENTS,
)
from src.features.composer.tests.test_empty_section_recovery import _fragment


@pytest.mark.parametrize(
    "section_id,first_slot,last_slot,first_text,last_text",
    [
        (
            "portfolio", "product_role", "revenue_link",
            "회사는 가맹점의 결제 승인과 정산을 처리하는 서비스를 제공한다.",
            "회사는 해당 결제 서비스의 거래 처리에 대한 수수료를 받는다.",
        ),
        (
            "business_model", "revenue_model", "customer_type",
            "회사는 결제 처리 서비스의 이용 수수료로 수익을 얻는다.",
            "이 서비스의 고객은 온라인 쇼핑몰을 운영하는 가맹점이다.",
        ),
        (
            "current_challenges", "issue", "response",
            "회사의 생산설비는 부품 공급 지연으로 가동이 제한되고 있다.",
            "회사는 부품 공급 지연에 대응해 대체 공급처와 계약을 체결했다.",
        ),
    ],
)
def test_late_complementary_evidence_survives_fragment_limit(
    section_id, first_slot, last_slot, first_text, last_text,
):
    repeated = tuple(
        replace(
            _fragment(section_id, first_text, str(index + 1)),
            supported_claim_slots=(f"{section_id}:{first_slot}",),
        )
        for index in range(MAX_EMPTY_RECOVERY_FRAGMENTS)
    )
    complementary = replace(
        _fragment(section_id, last_text, str(MAX_EMPTY_RECOVERY_FRAGMENTS + 1)),
        supported_claim_slots=(f"{section_id}:{last_slot}",),
    )

    selected = recovery_evidence((*repeated, complementary))[section_id]

    assert complementary in selected
    assert selected[0] is repeated[0]
    assert len(selected) <= MAX_EMPTY_RECOVERY_FRAGMENTS
    assert all(item is complementary or item in repeated for item in selected)


def test_complementary_evidence_survives_character_limit_without_truncation():
    long_text = "회사는 가맹점에 결제 처리 서비스를 제공한다. " * 140
    repeated = tuple(
        replace(
            _fragment("portfolio", long_text, str(index + 1)),
            supported_claim_slots=("portfolio:product_role",),
        )
        for index in range(MAX_EMPTY_RECOVERY_FRAGMENTS)
    )
    complementary = replace(
        _fragment("portfolio", "회사는 거래 처리에 대한 수수료를 받는다. " * 100, "7"),
        supported_claim_slots=("portfolio:revenue_link",),
    )

    selected = recovery_evidence((*repeated, complementary))["portfolio"]

    assert complementary in selected
    assert sum(len(item.text) for item in selected) <= MAX_EMPTY_RECOVERY_EVIDENCE_CHARS
    assert selected[-1] is complementary


def test_unknown_slot_cannot_crowd_out_usable_evidence():
    unknown = tuple(
        replace(
            _fragment("portfolio", "회사는 서비스를 제공한다.", str(index + 1)),
            supported_claim_slots=("portfolio:unknown",),
        )
        for index in range(MAX_EMPTY_RECOVERY_FRAGMENTS)
    )
    valid = replace(
        _fragment("portfolio", "회사는 가맹점의 결제 승인을 처리한다.", "7"),
        supported_claim_slots=("portfolio:product_role",),
    )

    assert recovery_evidence((*unknown, valid))["portfolio"] == (valid,)


def test_long_first_fragment_does_not_hide_short_complementary_sources():
    long_role = replace(
        _fragment("portfolio", "가" * (MAX_EMPTY_RECOVERY_EVIDENCE_CHARS - 10), "1"),
        supported_claim_slots=("portfolio:product_role",),
    )
    short_role = replace(
        _fragment("portfolio", "회사는 가맹점의 결제 승인과 정산을 처리한다.", "2"),
        supported_claim_slots=("portfolio:product_role",),
    )
    revenue = replace(
        _fragment("portfolio", "회사는 결제 서비스의 거래 처리 수수료를 받는다.", "3"),
        supported_claim_slots=("portfolio:revenue_link",),
    )

    assert recovery_evidence((long_role, short_role, revenue))["portfolio"] == (short_role, revenue)


def test_diversity_does_not_promote_unbound_or_external_evidence():
    valid = replace(
        _fragment("portfolio", "회사는 결제 서비스를 제공한다.", "1"),
        supported_claim_slots=("portfolio:product_role",),
    )
    revenue = replace(
        _fragment("portfolio", "회사는 거래 수수료를 받는다.", "2"),
        supported_claim_slots=("portfolio:revenue_link",),
    )

    assert recovery_evidence((
        valid,
        replace(revenue, identity_binding=""),
        replace(revenue, fragment_id="3", formal_source_kind="news"),
    ))["portfolio"] == (valid,)


def test_두_필수칸을_지지하는_조각은_문자예산을_차지한_중복대표를_교체한다():
    short_text = "가" * (MAX_EMPTY_RECOVERY_EVIDENCE_CHARS // 3)
    single_role = replace(
        _fragment("portfolio", short_text, "1"),
        supported_claim_slots=("portfolio:product_role",),
    )
    combined = replace(
        _fragment("portfolio", "나" * (MAX_EMPTY_RECOVERY_EVIDENCE_CHARS - len(short_text) + 1), "2"),
        supported_claim_slots=("portfolio:product_role", "portfolio:revenue_link"),
    )

    selected = recovery_evidence((single_role, combined))["portfolio"]

    assert selected == (combined,)
    assert selected[0] is combined
    assert len(selected[0].text) <= MAX_EMPTY_RECOVERY_EVIDENCE_CHARS


def test_새_조각이_지원하지_않는_기존_필수칸은_예산확보를_위해_버리지_않는다():
    short_text = "가" * (MAX_EMPTY_RECOVERY_EVIDENCE_CHARS // 3)
    revenue = replace(
        _fragment("business_model", short_text, "1"),
        supported_claim_slots=("business_model:revenue_model",),
    )
    customer = replace(
        _fragment("business_model", short_text, "2"),
        supported_claim_slots=("business_model:customer_type",),
    )
    combined = replace(
        _fragment("business_model", "나" * (MAX_EMPTY_RECOVERY_EVIDENCE_CHARS - len(short_text) + 1), "3"),
        supported_claim_slots=("business_model:customer_type", "business_model:value_exchange"),
    )

    assert recovery_evidence((revenue, customer, combined))["business_model"] == (revenue, customer)
