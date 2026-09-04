"""새 생성 계약과 과거 조회 계약을 분리하는 버전 선택 API."""

from __future__ import annotations

from src.shared.report_quality.constants import (
    LEGACY_STRICT_QUALITY_CONTRACT_VERSION,
    LEGACY_UNVERSIONED_CONTRACT,
    MAX_INTERPRETED_CLAIMS,
    MAX_INTERPRETED_CLAIMS_PER_SECTION,
    MAX_INTERPRETED_RATIO,
    MIN_FULL_PUBLIC_SENTENCES_PER_SECTION,
    MAX_NOTICE_ONLY_SECTIONS,
    MIN_CLAIMS_PER_COVERED_SECTION,
    MIN_DOCUMENT_SOURCES,
    MIN_SUBSTANTIVE_CLAIMS,
    MIN_VERIFIED_RATIO,
    QUALITY_CONTRACT_VERSION,
    REQUIRED_QUALITY_SECTION_IDS,
    STRICT_MAX_NOTICE_ONLY_SECTIONS,
    STRICT_QUALITY_CONTRACT_VERSION,
    STRICT_REQUIRED_QUALITY_SECTION_IDS,
)
from src.shared.report_evidence.policy import EVIDENCE_SLOT_POLICY_VERSION
from src.shared.report_quality.models import (
    ContractResolution,
    ContractUse,
    HistoricalReadPolicy,
    QualityContract,
)


CURRENT_CONTRACT = QualityContract(
    version=QUALITY_CONTRACT_VERSION,
    required_section_ids=REQUIRED_QUALITY_SECTION_IDS,
    min_claims_per_covered_section=MIN_CLAIMS_PER_COVERED_SECTION,
    min_substantive_claims=MIN_SUBSTANTIVE_CLAIMS,
    min_verified_ratio=MIN_VERIFIED_RATIO,
    min_document_sources=MIN_DOCUMENT_SOURCES,
    max_notice_only_sections=MAX_NOTICE_ONLY_SECTIONS,
    historical_read_policy=HistoricalReadPolicy.PRESERVE_ISSUED,
)

# v2는 ENFORCE_NO_PARTIAL의 현행 엄격 계약이자 과거 FULL 영수증의 계약이다.
# 숫자와 뜻을 그대로 보존하고, 어느 공개 모드가 이를 발급할지는 composer의
# 단일 release-mode 라우터가 결정한다(FULL 신규 발급에는 v3만 허용).
LEGACY_STRICT_CONTRACT = QualityContract(
    version=LEGACY_STRICT_QUALITY_CONTRACT_VERSION,
    required_section_ids=STRICT_REQUIRED_QUALITY_SECTION_IDS,
    min_claims_per_covered_section=MIN_CLAIMS_PER_COVERED_SECTION,
    min_substantive_claims=MIN_SUBSTANTIVE_CLAIMS,
    min_verified_ratio=MIN_VERIFIED_RATIO,
    min_document_sources=MIN_DOCUMENT_SOURCES,
    max_notice_only_sections=STRICT_MAX_NOTICE_ONLY_SECTIONS,
    historical_read_policy=HistoricalReadPolicy.PRESERVE_ISSUED,
)

# v1 하한을 새 출력에 맞춰 낮추지 않는다. 새 FULL 계약은 그 하한을 그대로
# 유지하면서 9장·안내문 없음·해석 문장 천장을 함께 요구한다.
STRICT_CONTRACT = QualityContract(
    version=STRICT_QUALITY_CONTRACT_VERSION,
    required_section_ids=STRICT_REQUIRED_QUALITY_SECTION_IDS,
    # 새 FULL은 임의 claim 범주 2종으로 의미 충족을 대신하지 않는다.
    # 아래 versioned 필수 의미칸 정책을 정확히 대조한다.
    min_claims_per_covered_section=0,
    min_substantive_claims=MIN_SUBSTANTIVE_CLAIMS,
    min_verified_ratio=MIN_VERIFIED_RATIO,
    min_document_sources=MIN_DOCUMENT_SOURCES,
    max_notice_only_sections=STRICT_MAX_NOTICE_ONLY_SECTIONS,
    historical_read_policy=HistoricalReadPolicy.PRESERVE_ISSUED,
    max_interpreted_claims_per_section=MAX_INTERPRETED_CLAIMS_PER_SECTION,
    max_interpreted_claims=MAX_INTERPRETED_CLAIMS,
    max_interpreted_ratio=MAX_INTERPRETED_RATIO,
    min_public_sentences_per_section=MIN_FULL_PUBLIC_SENTENCES_PER_SECTION,
    required_public_claim_slot_policy_version=EVIDENCE_SLOT_POLICY_VERSION,
)

_GENERATION_CONTRACTS: dict[str, QualityContract] = {
    CURRENT_CONTRACT.version: CURRENT_CONTRACT,
    LEGACY_STRICT_CONTRACT.version: LEGACY_STRICT_CONTRACT,
    STRICT_CONTRACT.version: STRICT_CONTRACT,
}
_STORED_ASSESSMENT_CONTRACTS: dict[str, QualityContract] = {
    **_GENERATION_CONTRACTS,
}


def contract_for_generation(version: str = "") -> QualityContract:
    """새 보고서에 적용할 명시적 계약을 돌려준다.

    빈 값은 현재 버전을 고르지만, 알 수 없는 버전을 최신 규칙으로 몰래 바꾸지
    않는다. 배포 중 버전 오타가 품질 기준을 우회하지 않게 ``ValueError``로 막는다.
    """

    selected = str(version or QUALITY_CONTRACT_VERSION).strip()
    try:
        return _GENERATION_CONTRACTS[selected]
    except KeyError as error:
        raise ValueError(f"알 수 없는 보고서 품질 계약 버전입니다: {selected}") from error


def contract_for_stored_assessment(version: str) -> QualityContract:
    """발급 당시 평가 영수증을 그 당시 계약으로만 재검산한다.

    생성 시점 평가 API와 조회 시점 재검산 API를 분리한다. v2는 ENFORCE에서
    계속 생성하지만 FULL 신규 발급은 composer 라우터가 v3로 고정한다. 과거
    v2 FULL 영수증도 뜻을 바꾸지 않고 읽으며, 알 수 없는 미래·오타 버전은
    현재 규칙으로 몰래 승격하지 않는다.
    """

    selected = str(version or "").strip()
    try:
        return _STORED_ASSESSMENT_CONTRACTS[selected]
    except KeyError as error:
        raise ValueError(f"알 수 없는 저장 품질 계약 버전입니다: {selected}") from error


def resolve_contract(
    stored_contract_version: str = "",
    *,
    use: ContractUse,
) -> ContractResolution:
    """생성/조회 목적별로 계약을 선택한다.

    과거 조회에서는 현재 assessor를 절대로 켜지 않는다. 옛 링크의 정정·교체·
    만료는 별도 제품 정책이며, 새 코드가 GET 시점에 소급 차단하면 안 된다.
    """

    requested = str(stored_contract_version or "").strip()
    if use is ContractUse.GENERATION:
        contract = contract_for_generation(requested)
        return ContractResolution(
            requested_version=requested,
            resolved_version=contract.version,
            use=use,
            assess_now=True,
            preserve_issued=False,
            reason="새 보고서는 선택된 생성 시점 계약으로 평가합니다.",
        )

    resolved = requested or LEGACY_UNVERSIONED_CONTRACT
    return ContractResolution(
        requested_version=requested,
        resolved_version=resolved,
        use=use,
        assess_now=False,
        preserve_issued=True,
        reason=(
            "과거 보고서는 발급 당시 결과를 유지하며 현재 품질 계약을 소급 적용하지 않습니다."
        ),
    )
