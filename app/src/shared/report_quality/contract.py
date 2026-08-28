"""새 생성 계약과 과거 조회 계약을 분리하는 버전 선택 API."""

from __future__ import annotations

from src.shared.report_quality.constants import (
    LEGACY_UNVERSIONED_CONTRACT,
    MAX_NOTICE_ONLY_SECTIONS,
    MIN_CLAIMS_PER_COVERED_SECTION,
    MIN_DOCUMENT_SOURCES,
    MIN_SUBSTANTIVE_CLAIMS,
    MIN_VERIFIED_RATIO,
    QUALITY_CONTRACT_VERSION,
    REQUIRED_QUALITY_SECTION_IDS,
)
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

_GENERATION_CONTRACTS: dict[str, QualityContract] = {
    CURRENT_CONTRACT.version: CURRENT_CONTRACT,
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
