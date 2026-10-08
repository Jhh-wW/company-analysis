"""공식 산업 기능을 조사 조립부에 연결하는 얇은 실행 경계."""

from collections.abc import Callable

from src.shared.business_challenge_context import BusinessActivityAnchor, IndustryProblemEvidence
from src.shared.report_evidence.models import EvidenceFragment, CollectedEvidenceDocument


def collect_official_industry_context(*, candidates: tuple[tuple[EvidenceFragment, CollectedEvidenceDocument], ...], anchors: tuple[BusinessActivityAnchor, ...], company_id: str, reference_date: str, analyze: Callable[[str, dict, int], object], diagnostics: dict) -> tuple[IndustryProblemEvidence, ...]:
    """모델·원장 콜백은 호출자가 소유하고 검증은 공식 산업 기능에 맡긴다."""
    from src.features.official_industry_context.logic import collect_official_industry_context as collect

    return collect(candidates=candidates, anchors=anchors, company_id=company_id, reference_date=reference_date, analyze=analyze, diagnostics=diagnostics)
