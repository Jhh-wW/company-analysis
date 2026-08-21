"""무료 데모가 재생하는 canonical(v3) 기준 보고서.

구형 파일럿 결과는 현재 목차·사실 원장·출고 게이트를 거치지 않았다. 기본
실행 경로에서 그 결과를 다시 보여 주면 서비스가 새 원칙을 지킨다는 보장이
깨진다. 이 모듈은 독립 사실 검수를 마친 (주)진영 표본을 같은 출고 게이트로
매번 검증한 뒤 반환한다.
"""

from __future__ import annotations

import json
from dataclasses import replace

from src.features.pipeline.port import (
    CompanyCard,
    FactRecord,
    Grade,
    Report,
    ReportSection,
    ReportTable,
    SummaryItem,
)
from src.features.provenance.sources import (
    Source,
    SourceKind,
    evidence_text_hash,
    seal_collected_source,
)
from src.features.report_standard.constants import CANONICAL_SCHEMA_VERSION
from src.features.report_standard.publish import (
    build_published_report,
    fact_evidence_binding,
    summary_evidence_text,
    summary_verification_binding,
)


DEMO_REF = "canonical-demo-jinyoung-2026-08-19"
DEMO_COMPANY = "(주)진영"
DEMO_ALIASES = frozenset({"진영", "주진영", "jinyoung"})


def _demo_comparison_payload(
    *,
    company: str,
    detail: str,
    definition: str,
) -> str:
    """비교 범위와 실제 공식 문장을 분리해 보존한 데모 원 payload."""

    return json.dumps(
        {
            "official_text": (
                f"{company} 사업보고서. 가구·건축 고객. "
                f"표면소재·제품군 제품. 인테리어·표면재 시장. {detail}"
            ),
            "comparison_period": "2025 회계연도",
            "comparison_definition": definition,
            "accounting_scope": "연결 사업 범위",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


_JY_COMPARISON_PRODUCTS = _demo_comparison_payload(
    company="진영",
    detail="ASA·ABS·PP·PMMA 기반 주문맞춤 생산.",
    definition="각사 사업보고서에 열거된 소재와 생산거점",
)
_LX_COMPARISON_PRODUCTS = _demo_comparison_payload(
    company="LX하우시스",
    detail="PVC·PET 소재군과 미국·중국 생산거점.",
    definition="각사 사업보고서에 열거된 소재와 생산거점",
)
# 데모의 수집 단계에서 이미 보존돼 있었다고 간주하는 닫힌 원문 payload 장부다.
# 아래 FactRecord를 만든 뒤 역으로 해시를 합치는 방식은 사실이 스스로 근거를
# 등록하게 하므로 금지한다. 표 행도 이 목록의 실제 원문만 참조한다.
_DEMO_EVIDENCE_BY_SOURCE: dict[str, tuple[str, ...]] = {
    "JY-S1": (
        "반기보고서 회사 개요: 홈페이지 http://www.jyp21.co.kr",
        "반기보고서의 주문생산 납품과 열분해유 매출",
        "반기보고서의 가구 고객과 국내·중국·인도 매출",
        *(f"반기보고서 주요 제품 매출 비중: {name} {raw}" for name, raw in (
            ("가구용 시트·엣지", "69.98%"),
            ("산업용 시트", "9.13%"),
            ("열분해유", "6.25%"),
            ("기타 제품·상품·매출", "14.64%"),
        )),
        "반기보고서 제품 현황: 중국 시장 진출을 위한 프리미엄 아크릴 시트 출시",
        "반기보고서 제품 현황: 리얼 알루미늄 합지 필름 생산량 증가와 국내 고객사 공급",
        "반기보고서 종속회사 사업 현황: 한국에코에너지 열분해 설비 투자와 폐플라스틱 열분해유 생산",
        "반기보고서 해외 영업 현황: 협의·시험 단계, 본계약과 매출 미확인",
        "반기보고서 해외 영업 현황: 중국 대리점 8곳 MOU 체결",
        "반기보고서 해외 영업 현황: 러시아·폴란드 제품 품질시험 완료",
        "반기보고서 원재료·연구개발: LG화학 ASA 원료 조달과 공동 연구개발",
        "반기보고서 판매경로: 주문 사양 생산품의 가구 제조·유통 고객사 납품",
        *(f"반기보고서 종속회사 사업 현황: {company} {role} {state}" for company, role, state in (
            ("한국에코에너지", "폐플라스틱 열분해유 생산", "운영 중"),
            ("네체로", "폐산·폐알칼리 지정폐기물 처리", "운영 중"),
            ("진영에코에너지", "논산 열분해 설비 구축", "가동 준비"),
        )),
    ),
    "JY-S2": (
        "2025 사업보고서의 사업 내용과 연결대상 종속회사",
        "사업보고서 연결 범위: 2025년 한국에코에너지·네체로·진영에코에너지 포함 완료",
        "사업보고서 연결 범위: 종속회사 편입, 열분해유 생산과 지정폐기물 처리 사업 포함",
        *(f"사업보고서 요약 연결재무정보: {year} 매출 원값 {revenue} 영업손익 원값 {operating}" for year, revenue, operating in (
            ("2023", "30903000000", "-2361000000"),
            ("2024", "34221000000", "-2939000000"),
            ("2025", "32423000000", "-2656000000"),
        )),
        _JY_COMPARISON_PRODUCTS,
    ),
    "JY-S3": (
        "기업가치 제고 계획: 열분해 설비 14기, 2026년 하반기 가동 계획",
        "기업가치 제고 계획: 2026~2028년 해외 납품과 특수 필름 확대 계획",
        "기업가치 제고 계획: 2026~2028년 업무 자동화와 AX 솔루션 구축 계획",
    ),
    "JY-S4": (
        "공식 홈페이지 Company Overview: 고객 최우선·인간 존중·기술 혁신",
    ),
    "JY-S5": (
        "공식 홈페이지 Business Ethics: 공정성·주인의식·책임감·열정·안전·투명성과 신뢰",
    ),
    "JY-S6": (_LX_COMPARISON_PRODUCTS,),
}


def _demo_hashes(source_id: str) -> list[str]:
    return sorted(evidence_text_hash(text) for text in _DEMO_EVIDENCE_BY_SOURCE[source_id])


def demo_card(typed_name: str = DEMO_COMPANY) -> CompanyCard:
    """공식 공시와 회사 홈페이지에 맞춘 무료 데모 확인 카드."""

    return CompanyCard(
        legal_name=DEMO_COMPANY,
        typed_name=typed_name,
        address="인천광역시 서구 마중로 129(오류동)",
        ceo="심영수",
        founded="19960109",
        homepage="http://www.jyp21.co.kr",
        homepage_url="http://www.jyp21.co.kr",
        ref=DEMO_REF,
    )


def _sources() -> list[Source]:
    sources = [
        Source(
            number=1,
            kind=SourceKind.FILING,
            label="2026년 반기보고서",
            disclosed_at="2026-08-13",
            collected_at="2026-08-19",
            source_id="JY-S1",
            title="주식회사 진영 반기보고서 (2026.06)",
            publisher="주식회사 진영",
            host="dart.fss.or.kr",
            url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260813000494",
            document_id="20260813000494",
            location="II. 사업의 내용; III. 재무에 관한 사항",
            source_type="공식 공시",
            fact_status="실제·현재",
            used_in=[
                "business_model",
                "portfolio",
                "current_challenges",
                "operations_partners",
            ],
            evidence_hashes=_demo_hashes("JY-S1"),
        ),
        Source(
            number=2,
            kind=SourceKind.FILING,
            label="2025년 사업보고서",
            disclosed_at="2026-03-18",
            collected_at="2026-08-19",
            source_id="JY-S2",
            title="주식회사 진영 사업보고서 (2025.12)",
            publisher="주식회사 진영",
            host="kind.krx.co.kr",
            url="https://kind.krx.co.kr/external/2026/03/18/000954/20260318004614/11011.htm",
            document_id="20260318004614",
            location="I. 회사의 개요; II. 사업의 내용; III. 재무에 관한 사항",
            source_type="공식 공시",
            fact_status="실제",
            used_in=["identity", "past_changes", "competitive_position"],
            evidence_hashes=_demo_hashes("JY-S2"),
        ),
        Source(
            number=3,
            kind=SourceKind.FILING,
            label="기업가치 제고 계획",
            disclosed_at="2026-08-13",
            collected_at="2026-08-19",
            source_id="JY-S3",
            title="(주)진영 기업가치 제고 계획",
            publisher="주식회사 진영",
            host="dart.fss.or.kr",
            url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260813900332",
            document_id="20260813900332",
            location="중장기 기업가치 제고 계획",
            source_type="공식 계획",
            fact_status="미실행 계획",
            used_in=["future_strategy"],
            evidence_hashes=_demo_hashes("JY-S3"),
        ),
        Source(
            number=4,
            kind=SourceKind.OTHER,
            label="회사 개요·경영철학",
            collected_at="2026-08-19",
            source_id="JY-S4",
            title="Company Overview",
            publisher="주식회사 진영",
            host="www.jyp21.co.kr",
            url="http://www.jyp21.co.kr/en/company/overview.php",
            document_id="company-overview",
            location="Company > Overview",
            source_type="공식 웹",
            fact_status="현재",
            used_in=["culture"],
            evidence_hashes=_demo_hashes("JY-S4"),
            domain_attestation_source_id="JY-S1",
            domain_attestation_evidence=(
                "반기보고서 회사 개요: 홈페이지 http://www.jyp21.co.kr"
            ),
        ),
        Source(
            number=5,
            kind=SourceKind.OTHER,
            label="Business Ethics",
            collected_at="2026-08-19",
            source_id="JY-S5",
            title="Business Ethics",
            publisher="주식회사 진영",
            host="www.jyp21.co.kr",
            url="http://www.jyp21.co.kr/ko/esg/management_policy_business_ethics.php",
            document_id="business-ethics",
            location="ESG > Business Ethics",
            source_type="공식 웹",
            fact_status="현재",
            used_in=["culture"],
            evidence_hashes=_demo_hashes("JY-S5"),
            domain_attestation_source_id="JY-S1",
            domain_attestation_evidence=(
                "반기보고서 회사 개요: 홈페이지 http://www.jyp21.co.kr"
            ),
        ),
        Source(
            number=6,
            kind=SourceKind.FILING,
            label="LX하우시스 2025년 사업보고서",
            disclosed_at="2026-03-16",
            collected_at="2026-08-19",
            source_id="JY-S6",
            title="주식회사 LX하우시스 사업보고서 (2025.12)",
            publisher="주식회사 LX하우시스",
            host="kind.krx.co.kr",
            url="https://kind.krx.co.kr/external/2026/03/16/002513/20260316007072/11011.htm",
            document_id="20260316007072",
            location="II. 사업의 내용",
            source_type="비교사 공식 공시",
            fact_status="실제",
            used_in=["competitive_position"],
            evidence_hashes=_demo_hashes("JY-S6"),
        ),
    ]
    return [seal_collected_source(source) for source in sources]


def _fact(
    fact_id: str,
    *,
    claim: str,
    owner: str,
    source: Source,
    scope: str,
    action: str,
    claim_type: str,
    time_state: str,
    as_of: str,
    state_evidence: str,
    evidence_support_terms: list[str],
    raw_value: str = "",
    calculation: str = "",
    display_value: str = "",
    rounding_rule: str = "",
    limitations: str = "",
    **structured: object,
) -> FactRecord:
    source_date = source.published_at or source.disclosed_at or source.collected_at
    fact_status = str(
        structured.pop(
            "fact_status",
            "planned" if time_state == "future_plan" else "actual",
        )
    )
    fact = FactRecord(
        fact_id=fact_id,
        legal_entity=DEMO_COMPANY,
        subject_scope=scope,
        relationship_or_action=action,
        claim=claim,
        claim_type=claim_type,
        section_owner=owner,
        time_state=time_state,
        as_of=as_of,
        source_id=source.source_id,
        source_type=source.source_type,
        source_title=source.title,
        source_publisher=source.publisher,
        source_host=source.host,
        source_url=source.url,
        source_document_id=source.document_id,
        location=source.location,
        status="verified",
        fact_status=fact_status,
        verification_status="verified",
        state_evidence=state_evidence,
        source_date=source_date,
        evidence_support_terms=evidence_support_terms,
        raw_value=raw_value,
        calculation=calculation,
        display_value=display_value,
        rounding_rule=rounding_rule,
        limitations=limitations,
        **structured,
    )
    return replace(fact, evidence_binding=fact_evidence_binding(fact))


def _section(
    section_id: str,
    facts: list[FactRecord],
    source_numbers: dict[str, int],
    tables: list[ReportTable] | None = None,
    prose_fact_ids: list[str] | None = None,
) -> ReportSection:
    table_list = tables or []
    table_fact_ids = {
        fact_id
        for fact_id in (fact.fact_id for fact in facts)
        if any(
            fact_id.startswith(prefix)
            for prefix in ("biz-mix-", "past-fin-", "ops-branch-")
        )
    }
    selected_prose = prose_fact_ids or [
        fact.fact_id for fact in facts if fact.fact_id not in table_fact_ids
    ]
    by_id = {fact.fact_id: fact for fact in facts}
    prose_lines = [
        (by_id[fact_id].claim, f"[{source_numbers[by_id[fact_id].source_id]}]")
        for fact_id in selected_prose
    ]
    return ReportSection(
        cell=section_id,
        title=section_id,
        lines=list(prose_lines),
        prose_lines=prose_lines,
        tables=table_list,
        fact_ids=[fact.fact_id for fact in facts],
    )


def build_demo_report() -> Report:
    """검증 장부와 공개 문장이 일치하는 무료 데모 보고서를 만든다."""

    sources = _sources()
    source_by_id = {source.source_id: source for source in sources}
    source_numbers = {source.source_id: source.number for source in sources}
    s1, s2, s3, s4, s5, s6 = (source_by_id[f"JY-S{i}"] for i in range(1, 7))
    facts: list[FactRecord] = []
    sections: list[ReportSection] = []

    identity_facts = [
        _fact(
            "identity-01",
            claim="진영은 플라스틱 소재를 시트·필름으로 가공해 가구·가전·건축 고객에 공급하고, 종속회사를 통해 자원순환 사업을 운영하는 B2B 소재 회사다.",
            owner="identity",
            source=s2,
            scope="전사 사업 범위",
            action="시트·필름 제조와 자원순환 사업 운영",
            claim_type="identity_summary",
            time_state="standing",
            as_of="2025-12-31",
            state_evidence="2025 사업보고서의 사업 내용과 연결대상 종속회사",
            evidence_support_terms=["사업", "종속회사"],
        )
    ]
    facts.extend(identity_facts)
    sections.append(_section("identity", identity_facts, source_numbers))

    business_facts = [
        _fact(
            "biz-model-01",
            claim="고객 주문 사양에 맞춘 가구용·산업용 시트 납품이 주 수익 경로이고, 열분해유와 폐기물 처리 매출이 추가된다.",
            owner="business_model",
            source=s1,
            scope="전사 수익 경로",
            action="주문생산 납품과 자원순환 매출",
            claim_type="revenue_model",
            time_state="standing",
            as_of="2026-06-30",
            state_evidence="반기보고서의 주문생산 납품과 열분해유 매출",
            evidence_support_terms=["납품", "열분해유"],
            limitations="구체적인 단가·결제조건·반복계약 주기는 공시되지 않음",
        ),
        _fact(
            "biz-customer-01",
            claim="가구·인테리어 제조·유통사가 핵심 고객이며, 국내 매출이 중심이고 중국·인도 등 해외 매출도 발생한다.",
            owner="business_model",
            source=s1,
            scope="핵심 고객과 지역 시장",
            action="가구 고객 납품과 국내외 판매",
            claim_type="customer_market",
            time_state="standing",
            as_of="2026-06-30",
            state_evidence="반기보고서의 가구 고객과 국내·중국·인도 매출",
            evidence_support_terms=["가구", "국내"],
            market_stage="",
            market_observation="국내·중국·인도 매출",
            limitations="구매자·사용자·수혜자 역할은 공식 자료에서 별도 구분되지 않음",
        ),
    ]
    mix_rows = [
        ("biz-mix-01", "가구용 시트·엣지", "70.0%", "69.98%", "가구용 제품 매출 비중"),
        ("biz-mix-02", "산업용 시트", "9.1%", "9.13%", "산업용 제품 매출 비중"),
        ("biz-mix-03", "열분해유", "6.3%", "6.25%", "열분해유 매출 비중"),
        ("biz-mix-04", "기타 제품·상품·매출", "14.6%", "14.64%", "기타 매출 비중 합계"),
    ]
    for fact_id, name, shown, raw, action in mix_rows:
        business_facts.append(
            _fact(
                fact_id,
                claim=f"2026년 상반기 매출 구성 (단위: %): {name} | {shown.rstrip('%')}",
                owner="business_model",
                source=s1,
                scope=name,
                action=action,
                claim_type="revenue_mix",
                time_state="standing",
                as_of="2026-06-30",
                state_evidence=f"반기보고서 주요 제품 매출 비중: {name} {raw}",
                evidence_support_terms=[name, "매출"],
                raw_value=raw.rstrip("%"),
                calculation=(
                    "9.97%+1.05%+3.62%=14.64%; 표시값은 소수 첫째 자리 반올림"
                    if fact_id == "biz-mix-04"
                    else "원문 비율을 소수 첫째 자리로 반올림"
                ),
                display_value=shown.rstrip("%"),
                rounding_rule="ROUND_HALF_UP, 소수 첫째 자리",
                numeric_checks=[
                    f"{raw.rstrip('%')}|1|1|{shown.rstrip('%')}"
                ],
            )
        )
    facts.extend(business_facts)
    sections.append(
        _section(
            "business_model",
            business_facts,
            source_numbers,
            tables=[
                ReportTable(
                    caption="2026년 상반기 매출 구성 (단위: %)",
                    headers=["사업", "매출 비중"],
                    rows=[
                        [name, shown.rstrip("%")]
                        for _fact_id, name, shown, _raw, _action in mix_rows
                    ],
                    cite="[1]",
                    numeric=True,
                    raw_rows=[
                        [name, raw.rstrip("%")]
                        for _fact_id, name, _shown, raw, _action in mix_rows
                    ],
                    scale_divisor="1",
                    scale_places=1,
                    presentation="composition",
                    evidence_rows=[
                        f"반기보고서 주요 제품 매출 비중: {name} {raw}"
                        for _fact_id, name, _shown, raw, _action in mix_rows
                    ],
                )
            ],
            prose_fact_ids=["biz-model-01", "biz-customer-01"],
        )
    )

    portfolio_facts = [
        _fact(
            "portfolio-02",
            claim="리얼 알루미늄 합지 필름은 생산량을 늘려 국내 고객사에 공급 중이다.",
            owner="portfolio",
            source=s1,
            scope="리얼 알루미늄 합지 필름",
            action="국내 고객사 납품",
            claim_type="priority_product",
            time_state="standing",
            as_of="2026-06-30",
            state_evidence="반기보고서 제품 현황: 리얼 알루미늄 합지 필름 생산량 증가와 국내 고객사 공급",
            evidence_support_terms=["알루미늄", "공급"],
            product_role="가전 표면재 납품 제품",
            revenue_model_fact_id="biz-model-01",
            priority_signals=["출시·운영", "생산확대"],
        ),
        _fact(
            "portfolio-03",
            claim="한국에코에너지는 열분해 설비에 투자해 폐플라스틱 열분해유를 생산한다.",
            owner="portfolio",
            source=s1,
            scope="폐플라스틱 열분해유",
            action="열분해유 생산",
            claim_type="priority_product",
            time_state="standing",
            as_of="2026-06-30",
            state_evidence="반기보고서 종속회사 사업 현황: 한국에코에너지 열분해 설비 투자와 폐플라스틱 열분해유 생산",
            evidence_support_terms=["한국에코에너지", "열분해유"],
            product_role="자원순환 사업의 판매 제품",
            revenue_model_fact_id="biz-model-01",
            priority_signals=["출시·운영", "투자·증설"],
        ),
    ]
    facts.extend(portfolio_facts)
    sections.append(_section("portfolio", portfolio_facts, source_numbers))

    past_facts = [
        _fact(
            "past-execution-01",
            claim="2025년 말 한국에코에너지·네체로·진영에코에너지가 연결 대상에 포함됐다.",
            owner="past_changes",
            source=s2,
            scope="자원순환 종속회사 연결 범위",
            action="자원순환 종속회사 연결 편입",
            claim_type="completed_execution",
            time_state="completed",
            as_of="2025-12-31",
            state_evidence="사업보고서 연결 범위: 2025년 한국에코에너지·네체로·진영에코에너지 포함 완료",
            evidence_support_terms=["한국에코에너지", "진영에코에너지"],
            raw_value="2025",
            calculation="원문 사건 연도를 1로 나누어 직접 대조",
            display_value="2025",
            rounding_rule="ROUND_HALF_UP 미적용(원문 연도 그대로)",
            numeric_checks=["2025|1|0|2025"],
            event_date="2025",
        ),
        _fact(
            "past-change-01",
            claim="종속회사 편입으로 자원순환 사업 범위가 열분해유 생산과 지정폐기물 처리까지 넓어졌다.",
            owner="past_changes",
            source=s2,
            scope="자원순환 사업 범위 변화",
            action="종속회사 편입 후 사업 범위 확장",
            claim_type="change_interpretation",
            time_state="completed",
            as_of="2025-12-31",
            state_evidence="사업보고서 연결 범위: 종속회사 편입, 열분해유 생산과 지정폐기물 처리 사업 포함",
            evidence_support_terms=["종속회사", "열분해유"],
            basis_fact_ids=["past-execution-01"],
            supports_causality=True,
            causal_subject="자원순환 사업 범위",
            causal_mechanism="자원순환 종속회사 연결 편입",
            causal_outcome="열분해유 생산과 지정폐기물 처리까지 범위 확대",
            causal_evidence="종속회사 편입, 열분해유 생산과 지정폐기물 처리 사업 포함",
        ),
    ]
    finance_rows = [
        (
            "past-fin-2025", "2025", "324.2", "-26.6",
            "32423000000", "-2656000000",
        ),
        (
            "past-fin-2024", "2024", "342.2", "-29.4",
            "34221000000", "-2939000000",
        ),
        (
            "past-fin-2023", "2023", "309.0", "-23.6",
            "30903000000", "-2361000000",
        ),
    ]
    for fact_id, year, revenue, operating, revenue_won, operating_won in finance_rows:
        past_facts.append(
            _fact(
                fact_id,
                claim=(
                    "완료 사업연도 연결 실적 (단위: 억원): "
                    f"{year} | {revenue} | {operating}"
                ),
                owner="past_changes",
                source=s2,
                scope=f"{year} 연결 손익",
                action="연결 매출과 영업손익",
                claim_type="historical_performance",
                time_state="completed",
                as_of=f"{year}-12-31",
                state_evidence=(
                    f"사업보고서 요약 연결재무정보: {year} 매출 원값 "
                    f"{revenue_won} 영업손익 원값 {operating_won}"
                ),
                evidence_support_terms=[year, "연결"],
                raw_value=f"{revenue_won} | {operating_won}",
                calculation="원÷100,000,000=억원",
                display_value=f"{revenue} | {operating}",
                rounding_rule="원값을 억원 환산 후 ROUND_HALF_UP, 소수 첫째 자리",
                numeric_checks=[
                    f"{revenue_won}|100000000|1|{revenue}",
                    f"{operating_won}|100000000|1|{operating}",
                ],
                fiscal_year=int(year),
            )
        )
    past_facts.append(
        _fact(
            "past-change-finance-01",
            claim=(
                "완료 사업연도 실적은 매출의 등락과 영업손익 적자가 "
                "이어졌음을 보여준다."
            ),
            owner="past_changes",
            source=s2,
            scope="완료 사업연도 연결 실적 변화",
            action="매출 등락과 영업손익 적자 지속",
            claim_type="change_interpretation",
            time_state="completed",
            as_of="2025-12-31",
            state_evidence=(
                "사업보고서 요약 연결재무정보: 2025 매출 원값 "
                "32423000000 영업손익 원값 -2656000000"
            ),
            evidence_support_terms=["매출", "영업손익"],
            basis_fact_ids=["past-fin-2023", "past-fin-2024", "past-fin-2025"],
            limitations="완료 사업연도 세 시점의 관찰이며 원인·지속성은 확인되지 않음",
        )
    )
    facts.extend(past_facts)
    sections.append(
        _section(
            "past_changes",
            past_facts,
            source_numbers,
            tables=[
                ReportTable(
                    caption="완료 사업연도 연결 실적 (단위: 억원)",
                    headers=["사업연도", "매출", "영업이익(손실)"],
                    rows=[
                        [year, revenue, operating]
                        for _fid, year, revenue, operating, _revenue_won, _operating_won
                        in finance_rows
                    ],
                    cite="[2]",
                    numeric=True,
                    raw_rows=[
                        [year, revenue_won, operating_won]
                        for _fid, year, _revenue, _operating, revenue_won, operating_won
                        in finance_rows
                    ],
                    scale_divisor="100000000",
                    scale_places=1,
                    display_unit="억원",
                    presentation="trend",
                    evidence_rows=[
                        (
                            f"사업보고서 요약 연결재무정보: {year} 매출 원값 "
                            f"{revenue_won} 영업손익 원값 {operating_won}"
                        )
                        for _fid, year, _revenue, _operating, revenue_won, operating_won
                        in finance_rows
                    ],
                )
            ],
            prose_fact_ids=[
                "past-execution-01",
                "past-change-01",
                "past-change-finance-01",
            ],
        )
    )

    current_facts = [
        _fact(
            "current-01",
            claim="해외 신규 유통은 기준일 현재 협의·시험 단계이며 본계약과 매출이 확인되지 않는다.",
            owner="current_challenges",
            source=s1,
            scope="해외 신규 유통의 상용화",
            action="본계약·매출 미확인",
            claim_type="current_issue",
            time_state="current_issue",
            as_of="2026-06-30",
            state_evidence="반기보고서 해외 영업 현황: 협의·시험 단계, 본계약과 매출 미확인",
            evidence_support_terms=["본계약", "매출"],
            next_check_metric="본계약",
            limitations="계약·매출 성립 전 단계",
        ),
        _fact(
            "current-02",
            claim="회사는 중국 대리점 8곳과 MOU를 체결했다.",
            owner="current_challenges",
            source=s1,
            scope="해외 유통 확대",
            action="중국 대리점 MOU 체결",
            claim_type="current_response",
            time_state="current_response",
            as_of="2026-06-30",
            state_evidence="반기보고서 해외 영업 현황: 중국 대리점 8곳 MOU 체결",
            evidence_support_terms=["중국", "MOU"],
            raw_value="중국 대리점 8개",
            calculation="원문 정수 값을 그대로 대조",
            display_value="8개",
            rounding_rule="ROUND_HALF_UP 미적용(원문 정수 그대로)",
            numeric_checks=["8|1|0|8"],
            response_to_fact_id="current-01",
            response_action="중국 대리점 8곳 MOU 체결",
            initial_signal="",
            limitations="MOU 단계이며 본계약으로 해석하지 않음",
        ),
        _fact(
            "current-03",
            claim="회사는 러시아와 폴란드에서 제품 품질시험을 마쳤다.",
            owner="current_challenges",
            source=s1,
            scope="동유럽 신규 유통",
            action="러시아·폴란드 품질시험 완료",
            claim_type="current_response",
            time_state="current_response",
            as_of="2026-06-30",
            state_evidence="반기보고서 해외 영업 현황: 러시아·폴란드 제품 품질시험 완료",
            evidence_support_terms=["러시아", "품질시험"],
            response_to_fact_id="current-01",
            response_action="러시아·폴란드 제품 품질시험 완료",
            initial_signal="",
            limitations="품질시험 완료가 본계약을 뜻하지 않음",
        ),
    ]
    facts.extend(current_facts)
    sections.append(_section("current_challenges", current_facts, source_numbers))

    future_facts = [
        _fact(
            "future-01",
            claim="회사는 열분해 설비 14기를 2026년 하반기에 가동하는 계획을 밝혔다.",
            owner="future_strategy",
            source=s3,
            scope="열분해 설비",
            action="열분해 설비 가동 계획",
            claim_type="future_plan",
            time_state="future_plan",
            as_of="2026-08-13",
            state_evidence="기업가치 제고 계획: 열분해 설비 14기, 2026년 하반기 가동 계획",
            evidence_support_terms=["열분해", "가동"],
            raw_value="14기; 2026년 하반기",
            calculation="원문 설비 수와 계획 연도를 각각 직접 대조",
            display_value="14기; 2026년 하반기",
            rounding_rule="ROUND_HALF_UP 미적용(원문 정수와 기간 그대로)",
            numeric_checks=["14|1|0|14", "2026|1|0|2026"],
            plan_status="announced",
            plan_timing="2026년 하반기",
            plan_execution_signal="가동",
            limitations="가동 전 공식 계획",
        ),
        _fact(
            "future-02",
            claim="회사는 2026~2028년 미국·호주·러시아·유럽 납품과 차량 외장재·방염·난연 필름 확대를 계획했다.",
            owner="future_strategy",
            source=s3,
            scope="해외 납품과 특수 필름",
            action="해외 납품과 제품군 확대 계획",
            claim_type="future_plan",
            time_state="future_plan",
            as_of="2026-08-13",
            state_evidence="기업가치 제고 계획: 2026~2028년 해외 납품과 특수 필름 확대 계획",
            evidence_support_terms=["납품", "필름"],
            raw_value="2026~2028년",
            calculation="원문 시작·종료 연도를 각각 직접 대조",
            display_value="2026~2028년",
            rounding_rule="ROUND_HALF_UP 미적용(원문 기간 그대로)",
            numeric_checks=["2026|1|0|2026", "2028|1|0|2028"],
            plan_status="announced",
            plan_timing="2026~2028년",
            plan_execution_signal="해외 납품",
            limitations="납품 실적이 아닌 계획",
        ),
        _fact(
            "future-03",
            claim="회사는 2026~2028년 개인 업무 자동화와 전사 프로세스 지능화를 위한 자체 AX 솔루션 구축을 계획했다.",
            owner="future_strategy",
            source=s3,
            scope="업무 자동화와 AX 솔루션",
            action="자체 AX 솔루션 구축 계획",
            claim_type="future_plan",
            time_state="future_plan",
            as_of="2026-08-13",
            state_evidence="기업가치 제고 계획: 2026~2028년 업무 자동화와 AX 솔루션 구축 계획",
            evidence_support_terms=["업무 자동화", "AX"],
            raw_value="2026~2028년",
            calculation="원문 시작·종료 연도를 각각 직접 대조",
            display_value="2026~2028년",
            rounding_rule="ROUND_HALF_UP 미적용(원문 기간 그대로)",
            numeric_checks=["2026|1|0|2026", "2028|1|0|2028"],
            plan_status="announced",
            plan_timing="2026~2028년",
            plan_execution_signal="AX 솔루션 구축",
            limitations="구축 전 공식 계획",
        ),
    ]
    facts.extend(future_facts)
    sections.append(_section("future_strategy", future_facts, source_numbers))

    operations_facts = []
    branch_rows = [
        ("operations-core-01", "한국에코에너지", "폐플라스틱 열분해유 생산", "운영 중"),
        ("ops-branch-02", "네체로", "폐산·폐알칼리 지정폐기물 처리", "운영 중"),
    ]
    for fact_id, company, role, state in branch_rows:
        operations_facts.append(
            _fact(
                fact_id,
                claim=(
                    f"{company}는 {role} 사업을 {state}이다."
                    if fact_id == "operations-core-01"
                    else f"자원순환 운영 구조: {company} | 생산·운영 | "
                    f"종속회사 | {role} | {state}"
                ),
                owner="operations_partners",
                source=s1,
                scope=company,
                action=f"{role} {state}",
                claim_type="operating_core",
                time_state="standing",
                as_of="2026-06-30",
                state_evidence=f"반기보고서 종속회사 사업 현황: {company} {role} {state}",
                evidence_support_terms=[company, role.split(" ")[0]],
                value_chain_stage="production",
                relationship_type="subsidiary",
                limitations=("상업 가동 전" if company == "진영에코에너지" else ""),
            )
        )
    facts.extend(operations_facts)
    sections.append(
        _section(
            "operations_partners",
            operations_facts,
            source_numbers,
            tables=[
                ReportTable(
                    caption="자원순환 운영 구조",
                    headers=[
                        "주체",
                        "가치사슬 단계",
                        "관계 유형",
                        "확인된 역할",
                        "현재 상태",
                    ],
                    rows=[
                        [company, "생산·운영", "종속회사", role, state]
                        for _fid, company, role, state in branch_rows
                        if _fid != "operations-core-01"
                    ],
                    cite="[1]",
                    evidence_rows=[
                        f"반기보고서 종속회사 사업 현황: {company} {role} {state}"
                        for _fid, company, role, state in branch_rows
                        if _fid != "operations-core-01"
                    ],
                )
            ],
            prose_fact_ids=["operations-core-01"],
        )
    )

    culture_facts = [
        _fact(
            "culture-01",
            claim="진영은 공식 경영철학으로 고객 최우선·인간 존중·기술 혁신을 제시한다.",
            owner="culture",
            source=s4,
            scope="공식 경영철학",
            action="고객·사람·기술 가치 제시",
            claim_type="official_value",
            time_state="standing",
            as_of="2026-08-19",
            state_evidence="공식 홈페이지 Company Overview: 고객 최우선·인간 존중·기술 혁신",
            evidence_support_terms=["고객", "기술 혁신"],
        ),
        _fact(
            "culture-02",
            claim="공식 윤리 기준은 공정성·주인의식·책임감·열정·안전·투명성과 신뢰를 행동 원칙으로 제시한다.",
            owner="culture",
            source=s5,
            scope="공식 윤리 행동 원칙",
            action="윤리 행동 원칙 제시",
            claim_type="official_value",
            time_state="standing",
            as_of="2026-08-19",
            state_evidence="공식 홈페이지 Business Ethics: 공정성·주인의식·책임감·열정·안전·투명성과 신뢰",
            evidence_support_terms=["공정성", "신뢰"],
        ),
    ]
    facts.extend(culture_facts)
    sections.append(_section("culture", culture_facts, source_numbers))

    comparison_claim = (
        "같은 회계연도 공식 공시에서 진영은 ASA·ABS·PP·PMMA 기반 주문맞춤 생산을, "
        "LX하우시스는 PVC·PET 등을 포함한 표면소재 제품군과 미국·중국 생산거점을 각각 제시했다."
    )
    comparison_facts = [
        _fact(
            "competition-01",
            claim=comparison_claim,
            owner="competitive_position",
            source=s2,
            scope="표면소재 제품군과 생산거점",
            action="동일 회계연도 공식 공시 비교",
            claim_type="competitive_comparison",
            time_state="standing",
            as_of="2025-12-31",
            state_evidence=_JY_COMPARISON_PRODUCTS,
            evidence_support_terms=["진영", "ASA"],
            limitations="제품 범위와 회사 규모가 달라 우월성으로 해석하지 않음",
            comparison_target="주식회사 LX하우시스",
            comparison_metric="표면소재 소재군과 생산거점",
            comparison_definition="각사 사업보고서에 열거된 소재와 생산거점",
            comparison_basis="2025 사업보고서 공식 공시",
            comparison_period="2025 회계연도",
            comparison_scope="연결 사업 범위",
            comparison_judgment="operating_characteristic",
            comparator_source_id=s6.source_id,
            comparator_state_evidence=_LX_COMPARISON_PRODUCTS,
            comparator_evidence_support_terms=["LX하우시스", "PVC"],
            comparison_conditions={
                "customer": "가구·건축",
                "product": "제품군·표면소재",
                "market": "인테리어·표면재",
                "self_period": "2025 회계연도",
                "comparator_period": "2025 회계연도",
                "self_definition": "각사 사업보고서에 열거된 소재와 생산거점",
                "comparator_definition": "각사 사업보고서에 열거된 소재와 생산거점",
                "self_accounting_scope": "연결 사업 범위",
                "comparator_accounting_scope": "연결 사업 범위",
            },
        ),
    ]
    facts.extend(comparison_facts)
    sections.append(_section("competitive_position", comparison_facts, source_numbers))

    facts_by_id = {fact.fact_id: fact for fact in facts}

    def summary_item(
        text: str,
        section_id: str,
        fact_ids: list[str],
        support_terms: list[str],
    ) -> SummaryItem:
        evidence_text = summary_evidence_text(fact_ids, facts_by_id)
        status = "independently_verified"
        return SummaryItem(
            text=text,
            section_id=section_id,
            fact_ids=fact_ids,
            evidence_text=evidence_text,
            verification_status=status,
            verification_binding=summary_verification_binding(
                text,
                section_id,
                fact_ids,
                evidence_text,
                status,
                support_terms,
            ),
            support_terms=support_terms,
        )

    draft = Report(
        company=DEMO_COMPANY,
        job="",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=sections,
        citations=sources,
        cells={section.cell: True for section in sections},
        generated_at="2026-08-19",
        schema_version=CANONICAL_SCHEMA_VERSION,
        summary_items=[
            summary_item(
                "주문 사양 제품 매출과 가구 고객 중심의 수익 구조다.",
                "business_model",
                ["biz-model-01", "biz-customer-01"],
                ["매출", "가구"],
            ),
            summary_item(
                "알루미늄 합지 필름과 열분해유가 포트폴리오의 서로 다른 역할을 맡는다.",
                "portfolio",
                ["portfolio-02", "portfolio-03"],
                ["알루미늄 합지 필름", "열분해유"],
            ),
            summary_item(
                "종속회사 편입 뒤 자원순환 사업 범위가 넓어졌다.",
                "past_changes",
                ["past-execution-01", "past-change-01"],
                ["종속회사", "사업 범위"],
            ),
            summary_item(
                "해외 신규 유통은 본계약 전이며 품질시험으로 대응 중이다.",
                "current_challenges",
                ["current-01", "current-03"],
                ["본계약", "품질시험"],
            ),
            summary_item(
                "열분해 설비와 업무 자동화가 공식 성장 계획에 포함된다.",
                "future_strategy",
                ["future-01", "future-03"],
                ["열분해", "업무 자동화"],
            ),
        ],
        fact_records=facts,
        as_of_date="2026-08-19",
        analysis_period=(
            "2023~2025 완료 사업연도(12월 결산·연결) / "
            "사건: 2023-08-19~2026-08-19"
        ),
        latest_performance_period="2026년 반기 공식 공시",
    )
    return build_published_report(draft)


def with_typed_company(report: Report, typed_name: str) -> Report:
    """표지에는 공식 법인명을 유지하므로 현재는 그대로 반환한다."""

    del typed_name
    return replace(report, company=DEMO_COMPANY)
