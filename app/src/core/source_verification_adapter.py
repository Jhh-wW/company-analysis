"""실행 조립 계층에서 보완조사 출처 검증 구현을 연결한다."""

from src.shared.report_evidence.source_verification import SourceVerifier
from src.shared.business_challenge_context import IndustryProblemEvidence


def supplementary_research_source_verifier() -> SourceVerifier:
    """키를 복제하지 않고 이미 로드한 provenance 정본을 사용한다."""

    from src.features.provenance.supplementary_research_adapter import (
        verify_supplementary_research_source,
    )

    return verify_supplementary_research_source


def register_industry_problem_source(problem: IndustryProblemEvidence, *, number: int, section_id: str) -> object:
    """검수 원문의 실제 메타데이터로 회사 사실과 구분한 뉴스 출처를 봉인한다."""
    from urllib.parse import urlsplit
    from src.features.provenance.sources import Source, SourceKind, evidence_text_hash, seal_collected_source

    host = str(urlsplit(problem.source_url).hostname or "")
    return seal_collected_source(Source(
        number=number, kind=SourceKind.NEWS, label=problem.title,
        published_at=problem.published_on, domain=host,
        source_id=f"industry-{problem.evidence_id}", title=problem.title,
        publisher=problem.publisher, host=host, url=problem.source_url,
        document_id=problem.document_id, location=problem.location,
        source_type="산업 자료", fact_status="산업 자료·회사 적용은 해석",
        used_in=[section_id], evidence_hashes=[evidence_text_hash(problem.exact_text)],
        exact_evidence_hashes=[problem.text_sha256],
        document_content_sha256=problem.document_content_sha256,
    ))


def bind_collected_source_section(source: object, section_id: str) -> object:
    """기존 출처의 수집 증거는 유지하고 실제 사용 장을 추가해 다시 봉인한다."""
    from dataclasses import replace
    from src.features.provenance.sources import Source, seal_collected_source

    if not isinstance(source, Source):
        raise ValueError("공개 출처 등록부의 정식 출처 타입이 필요합니다")
    return seal_collected_source(replace(source, used_in=list(dict.fromkeys((*source.used_in, section_id)))))
