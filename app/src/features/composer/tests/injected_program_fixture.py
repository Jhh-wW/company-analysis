"""FULL 시험의 실제 봉인 프로그램 근거를 만드는 composer 전용 합성 자료."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from src.features.composer.constants import DART_FINANCIAL_API_PREFIX, GRADE_CONFIRMED
from src.features.composer.port import (
    CollectedFragment,
    ComposedSentence,
    FilingMeta,
    PerformanceTable,
    VerifiedProgramEvidence,
)
from src.features.pipeline.port import FactRecord
from src.features.provenance.sources import (
    Source,
    SourceKind,
    evidence_text_hash,
    exact_evidence_text_hash,
    seal_collected_source,
)
from src.shared.report_quality.comparison_claims import (
    stated_differentiator_limitation_claim,
)
from src.shared.report_quality.constants import STATED_DIFFERENTIATOR_CLAIM_TYPE
from src.shared.report_quality.fact_binding import fact_evidence_binding
from src.shared.report_quality.source_identity import (
    bound_source_fragment_provenance,
    document_identity,
    document_identity_from_parts,
)


_SYNTHETIC_DATE = "2026-03-15"
_SYNTHETIC_COLLECTED_DATE = "2026-08-19"
_STATED_SLOT = "competitive_position:stated_differentiator"
_LIMITATION_SLOT = "competitive_position:limitation"
_SUPPORT_TERMS = ("독자", "기술")


def make_numeric_performance_evidence(
    *, fragment_number: int,
) -> tuple[PerformanceTable, CollectedFragment, FilingMeta]:
    """DART 원 payload와 3개년 원값이 결속된 숫자표·조각·공시 메타다."""
    if fragment_number < 1:
        raise ValueError("재무 조각 번호는 양수여야 합니다")
    payload = {
        "status": "000",
        "list": [
            {
                "fs_div": "CFS", "sj_div": "IS",
                "account_id": "ifrs-full_Revenue", "account_nm": "매출액",
                "bsns_year": "2025", "reprt_code": "11011", "currency": "KRW",
                "thstrm_dt": "2025.01.01 ~ 2025.12.31",
                "thstrm_amount": "1242800000000",
                "frmtrm_dt": "2024.01.01 ~ 2024.12.31",
                "frmtrm_amount": "1100000000000",
                "bfefrmtrm_dt": "2023.01.01 ~ 2023.12.31",
                "bfefrmtrm_amount": "1000000000000",
            },
            {
                "fs_div": "CFS", "sj_div": "IS",
                "account_id": "dart_OperatingIncomeLoss", "account_nm": "영업이익",
                "bsns_year": "2025", "reprt_code": "11011", "currency": "KRW",
                "thstrm_dt": "2025.01.01 ~ 2025.12.31",
                "thstrm_amount": "200000000000",
                "frmtrm_dt": "2024.01.01 ~ 2024.12.31",
                "frmtrm_amount": "150000000000",
                "bfefrmtrm_dt": "2023.01.01 ~ 2023.12.31",
                "bfefrmtrm_amount": "100000000000",
            },
        ],
    }
    evidence = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    table = PerformanceTable(
        caption="전자공시 최근 세 사업연도 연결 주요 실적",
        headers=("사업연도", "매출액", "영업이익"),
        rows=(("2025", "12,428", "2,000"), ("2024", "11,000", "1,500"),
              ("2023", "10,000", "1,000")),
        unit="억원",
        cite=f"조각 {fragment_number}·재무",
        raw_rows=(("2025", "1,242,800,000,000", "200,000,000,000"),
                  ("2024", "1,100,000,000,000", "150,000,000,000"),
                  ("2023", "1,000,000,000,000", "100,000,000,000")),
        scale_divisor="100000000", scale_places=0,
        evidence_rows=(evidence,) * 3,
        entity_scope="consolidated", raw_unit="원", unit_dimension="currency",
    )
    text = f"{DART_FINANCIAL_API_PREFIX} {evidence}"
    source_url = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
    fragment = CollectedFragment(
        fragment_id=str(fragment_number), kind="재무", text=text,
        source_url=source_url, document_title="합성 주요계정 API 원문",
        document_identity=document_identity_from_parts(
            host="opendart.fss.or.kr", document_id="fnlttsinglacnt.json",
            url=source_url,
        ),
        document_content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        supported_claim_slots=("past_changes:historical_performance",),
    )
    filing = FilingMeta(
        document_id="20260315000001", title="합성 사업보고서",
        disclosed_at=_SYNTHETIC_DATE,
    )
    return table, fragment, filing


def make_stated_limitation_program(
    *,
    fragment_number: int,
    company_name: str,
    statement: str,
) -> VerifiedProgramEvidence:
    """공식 자기 선언과 그 비교 한계를 Source·Fact·문장으로 함께 봉인한다."""
    if not 10 <= fragment_number <= 99:
        raise ValueError("합성 프로그램 조각 번호는 10~99여야 합니다")
    if any(term not in statement for term in _SUPPORT_TERMS):
        raise ValueError("합성 공식 문장에 근거어가 부족합니다")
    document_id = f"202603150000{fragment_number:02d}"
    source = seal_collected_source(Source(
        number=fragment_number,
        kind=SourceKind.FILING,
        label="합성 공식 공시",
        disclosed_at=_SYNTHETIC_DATE,
        collected_at=_SYNTHETIC_COLLECTED_DATE,
        source_id=f"source-synthetic-comparison-{fragment_number}",
        title="합성 공식 공시",
        publisher=company_name,
        host="dart.fss.or.kr",
        url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={document_id}",
        document_id=document_id,
        location="사업의 내용",
        source_type="공식 공시",
        fact_status="공시 실제값",
        used_in=["competitive_position"],
        evidence_hashes=[evidence_text_hash(statement)],
        exact_evidence_hashes=[exact_evidence_text_hash(statement)],
    ))
    fragment = CollectedFragment(
        fragment_id=str(fragment_number),
        kind="공식 공시",
        text=statement,
        supported_claim_slots=(_STATED_SLOT, _LIMITATION_SLOT),
        bound_source=source,
        **bound_source_fragment_provenance(source),
    )

    def make_fact(*, suffix: str, claim: str, slot: str) -> FactRecord:
        fact = FactRecord(
            fact_id=f"fact-synthetic-comparison-{fragment_number}-{suffix}",
            legal_entity=company_name,
            subject_scope="회사 자기 선언",
            relationship_or_action="회사 발표 표현의 한계" if slot == _LIMITATION_SLOT else "공식 차별점 선언",
            claim=claim,
            claim_type=STATED_DIFFERENTIATOR_CLAIM_TYPE,
            section_owner="competitive_position",
            time_state="current",
            as_of=_SYNTHETIC_DATE,
            source_id=source.source_id,
            source_type=source.source_type,
            source_title=source.title,
            source_publisher=source.publisher,
            source_host=source.host,
            source_url=source.url,
            source_document_id=source.document_id,
            location=source.location,
            status="verified",
            fact_status="actual",
            verification_status="verified",
            state_evidence=statement,
            source_date=source.disclosed_at,
            claim_slot=slot,
            supporting_source_ids=[source.source_id],
            supporting_source_identities=[document_identity(source)],
            supporting_evidence_hashes=[exact_evidence_text_hash(statement)],
            evidence_support_terms=list(_SUPPORT_TERMS),
        )
        return replace(fact, evidence_binding=fact_evidence_binding(fact))

    facts = (
        make_fact(suffix="statement", claim=statement, slot=_STATED_SLOT),
        make_fact(
            suffix="limitation",
            claim=stated_differentiator_limitation_claim(company_name, statement),
            slot=_LIMITATION_SLOT,
        ),
    )
    sentences = tuple(
        ComposedSentence(
            text=fact.claim,
            citations=(str(fragment_number),),
            grade=GRADE_CONFIRMED,
            planned_claim_slot=fact.claim_slot,
            verification_state="verified",
            verified_fact_id=fact.fact_id,
        )
        for fact in facts
    )
    return VerifiedProgramEvidence(
        section_id="competitive_position",
        source_fragments=(fragment,),
        registry_sources=(source,),
        facts=facts,
        sentences=sentences,
    )
