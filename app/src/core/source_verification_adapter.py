"""실행 조립 계층에서 보완조사 출처 검증 구현을 연결한다."""

from src.shared.report_evidence.source_verification import SourceVerifier


def supplementary_research_source_verifier() -> SourceVerifier:
    """키를 복제하지 않고 이미 로드한 provenance 정본을 사용한다."""

    from src.features.provenance.supplementary_research_adapter import (
        verify_supplementary_research_source,
    )

    return verify_supplementary_research_source
