"""보도 근거는 공개 본문에 사용해도 공식 독립 문서 하한을 늘리지 않는다."""

from dataclasses import replace
import hashlib

import pytest

from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_quality.assessment import assess_quality, assess_safety
from src.shared.report_quality.contract import contract_for_generation
from src.shared.report_quality.constants import STRICT_QUALITY_CONTRACT_VERSIONS, VERIFIED_PROSE_CLAIM_TYPE
from src.shared.report_quality.dto import ClaimFact, ReportCandidate, ReportSectionCandidate, SourceDocument


def _candidate():
    sources, facts, sections = [], [], []
    for index, (section_id, slots) in enumerate(CLAIM_SLOTS_BY_SECTION.items()):
        source_id, fact_id = f"source-{index}", f"fact-{index}"
        identity = f"https://example.com/{index}"
        digest = hashlib.sha256(source_id.encode()).hexdigest()
        news = index >= 7
        sources.append(SourceDocument(source_id, identity, (digest,), digest,
            counts_toward_document_floor=not news, source_kind="news" if news else "filing"))
        facts.append(ClaimFact(fact_id, section_id, source_id, identity, "verified",
            claim_slot=slots[0], evidence_binding_valid=True, claim="서로 다른 사업 사실 " + source_id,
            claim_type=VERIFIED_PROSE_CLAIM_TYPE))
        sections.append(ReportSectionCandidate(section_id, (fact_id,), public_sentence_count=1))
    return ReportCandidate(tuple(sections), tuple(facts), tuple(sources))


@pytest.mark.parametrize("version", tuple(STRICT_QUALITY_CONTRACT_VERSIONS))
def test_공식문서7개와_뉴스2개는_공식문서_하한8을_채우지_못한다(version):
    quality = assess_quality(_candidate(), contract_for_generation(version))
    assert quality.document_sources == 7
    assert "too_few_document_sources" in {code.value for code in quality.problem_codes}


def test_뉴스계수표식을_공식으로_변조하면_안전검사가_거절한다():
    candidate = _candidate()
    forged = replace(candidate, sources=tuple(replace(source, counts_toward_document_floor=True)
        if source.source_kind == "news" else source for source in candidate.sources))
    result = assess_safety(forged, contract_for_generation())
    assert any("뉴스 출처가 공식 문서 수에 포함" in problem for problem in result.problems)
