from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

import pytest

from src.features.company_comparison import logic as comparison_logic
from src.features.business_candidate.dart_identity import DartCompanyRecord
from src.features.company_comparison.logic import (
    ComparisonBlockedError,
    ComparisonSourceConfigurationError,
    ComparisonSourceInternalError,
    ComparisonSourceTransientError,
    OfficialCompanyBundle,
    build_competitive_position,
    comparison_candidate_preflight_possible,
    discover_candidates,
    discover_official_source_candidates,
)
from src.features.company_comparison.official_sources import (
    VERIFIED_FINAL_URL_FIELD,
    VERIFIED_FINAL_URL_VALUE,
    bind_dart_profile_attestation,
    candidate_sentences_from_fragments,
    register_candidate_sentence_evidence,
)
from src.features.pipeline.port import FactRecord, Grade, Report, ReportSection
from src.features.provenance.sources import (
    Source,
    SourceKind,
    evidence_text_hash,
    has_valid_provenance_seal,
    seal_collected_source,
)
from src.features.provenance.citations import build_citations
from src.features.report_standard.constants import CANONICAL_SCHEMA_VERSION
from src.features.report_standard.publish import _fact_problems, fact_evidence_binding
from src.features.storage import reports as report_storage
from src.shared import comparison_candidate_basis as comparison_basis_contract
from src.shared.comparison_candidate_basis import (
    COMPARISON_BASIS_VERSION,
    COMPARISON_SOURCE_BASIS_VERSION,
    comparison_alias_is_competition_target,
    comparison_candidate_sentence_matches,
    comparison_evidence_exact_sha256,
    comparison_evidence_sha256,
    parse_comparison_basis_v1,
    parse_comparison_source_basis_v2,
    comparison_source_basis_is_allowed,
    comparison_source_sentence_has_marker,
    comparison_source_sentence_has_self_subject,
)


def _v2_packet_set_for_bridge():
    from src.features.composer.constants import SECTION_IDS
    from src.features.composer.port import (
        CollectedFragment,
        SectionEvidencePacket,
        SectionEvidencePacketSet,
    )
    from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
    from src.shared.report_quality.source_identity import document_identity_from_parts

    packets = []
    for number, section_id in enumerate(SECTION_IDS, start=1):
        fragment = CollectedFragment(
            fragment_id=str(number),
            kind="공식 공시",
            text=f"{section_id} 공식 원문",
            source_url=f"https://dart.fss.or.kr/{number}",
            document_title=f"문서 {number}",
            location="본문",
            document_date="2026-03-15",
            document_identity=document_identity_from_parts(
                host="dart.fss.or.kr", document_id=f"2026031500{number:04d}"
            ),
            document_content_sha256=(f"{number:064x}"),
            supported_claim_slots=tuple(CLAIM_SLOTS_BY_SECTION[section_id][:3]),
        )
        packets.append(
            SectionEvidencePacket(
                company_id="00000001",
                evidence_generation_sha256="a" * 64,
                section_id=section_id,
                fragments=(fragment,),
            )
        )
    return SectionEvidencePacketSet(
        company_id="00000001",
        evidence_generation_sha256="a" * 64,
        packets=tuple(packets),
    )


COMPETITION_EVIDENCE = "주식회사 베타는 주식회사 알파의 경쟁사입니다."


def _official_source(evidence: str = COMPETITION_EVIDENCE) -> Source:
    return seal_collected_source(Source(
        number=1,
        kind=SourceKind.FILING,
        label="알파 2025 사업보고서",
        disclosed_at="2026-03-15",
        collected_at="2026-08-19",
        source_id="source-alpha-official",
        title="알파 2025 사업보고서",
        publisher="주식회사 알파",
        host="dart.fss.or.kr",
        url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260315000001",
        document_id="20260315000001",
        location="사업의 내용 · 경쟁 현황",
        source_type="공식 공시",
        fact_status="공시 실제값",
        used_in=["identity"],
        evidence_hashes=[evidence_text_hash(evidence)],
    ))


def _report(evidence: str = COMPETITION_EVIDENCE) -> Report:
    source = _official_source(evidence)
    fact = FactRecord(
        fact_id="fact-alpha-market",
        legal_entity="주식회사 알파",
        subject_scope="반도체 검사장비 시장",
        relationship_or_action="시장 참여",
        claim=evidence,
        claim_type="identity",
        section_owner="identity",
        time_state="standing",
        as_of="2025-12-31",
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
        state_evidence=evidence,
        supports_causality=True,
    )
    fact = replace(fact, evidence_binding=fact_evidence_binding(fact))
    return Report(
        company="주식회사 알파",
        job="",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[
            ReportSection(
                cell="identity",
                title="기업 정체성",
                display_number="1",
                prose_lines=[(evidence, "1")],
                fact_ids=[fact.fact_id],
            )
        ],
        citations=[source],
        fact_records=[fact],
        schema_version=CANONICAL_SCHEMA_VERSION,
    )


def _row(
    account_id: str,
    account_nm: str,
    amount: str,
    *,
    period: str = "2025.01.01 ~ 2025.12.31",
    scope: str = "CFS",
) -> dict[str, str]:
    return {
        "account_id": account_id,
        "account_nm": account_nm,
        "sj_div": "IS",
        "fs_div": scope,
        "thstrm_dt": period,
        "thstrm_amount": amount,
        "bsns_year": period[:4],
        "reprt_code": "11011",
        "currency": "KRW",
    }


def _financials(
    *,
    period: str = "2025.01.01 ~ 2025.12.31",
    scale: int = 1,
    scope: str = "CFS",
    operating_amount: int | None = None,
):
    operating_value = 100 * scale if operating_amount is None else operating_amount
    return {
        "status": "000",
        "reprt_code": "11011",
        "list": [
            _row("ifrs-full_Revenue", "매출액", str(1000 * scale), period=period, scope=scope),
            _row("dart_OperatingIncomeLoss", "영업이익", str(operating_value), period=period, scope=scope),
            _row("ifrs-full_ProfitLoss", "당기순이익", str(80 * scale), period=period, scope=scope),
        ]
    }


def _bundle(
    corp_code: str,
    name: str,
    *,
    period: str = "2025.01.01 ~ 2025.12.31",
    scale: int = 1,
    scope: str = "CFS",
    operating_amount: int | None = None,
    official_text: str = (
        "전자 제조 고객 대상 반도체 검사장비 제품을 반도체 검사장비 시장에 공급한다. "
        "연결재무제표의 매출액과 영업이익을 공시한다."
    ),
) -> OfficialCompanyBundle:
    year = period[:4]
    return OfficialCompanyBundle(
        corp_code=corp_code,
        company_name=name,
        financials=_financials(
            period=period,
            scale=scale,
            scope=scope,
            operating_amount=operating_amount,
        ),
        filing={
            "report_nm": f"사업보고서 ({year}.12)",
            "rcept_no": f"{year}0315000001",
            "rcept_dt": f"{int(year) + 1}0315",
            "reprt_code": "11011",
        },
        official_text=official_text,
    )


CATALOG = (
    DartCompanyRecord("00000001", "주식회사 알파", stock_code="000001"),
    DartCompanyRecord("00000002", "주식회사 베타", stock_code="000002"),
    DartCompanyRecord("00000003", "주식회사 감마", stock_code="000003"),
)


def test_정상_양사공식원문은_동일조건_비교사실을_만든다() -> None:
    report = _report()
    self_bundle = _bundle("00000001", "주식회사 알파", scale=2)
    comparator = _bundle(
        "00000002",
        "주식회사 베타",
        scale=1,
        operating_amount=50,
    )

    result = build_competitive_position(
        report,
        self_bundle=self_bundle,
        catalog=CATALOG,
        fetch_comparator=lambda record: comparator if record.corp_code == "00000002" else None,
        collected_on="2026-08-19",
    )

    assert result.section.cell == "competitive_position"
    assert len(result.facts) == 2
    assert len(result.sources) == 2
    assert {fact.comparison_metric for fact in result.facts} == {
        "영업이익률",
        "매출 규모",
    }
    assert {source.publisher for source in result.sources} == {
        "주식회사 알파",
        "주식회사 베타",
    }
    for fact in result.facts:
        assert fact.comparison_target == "주식회사 베타"
        assert "ifrs-full_Revenue" in fact.comparison_definition
        assert set(fact.comparison_conditions) == {
            "customer", "product", "market", "self_period", "comparator_period",
            "self_definition", "comparator_definition", "self_accounting_scope",
            "comparator_accounting_scope",
        }
        assert fact.comparison_period == "2025-01-01~2025-12-31"
        assert fact.comparison_scope == "연결재무제표(CFS)"
        assert fact.comparator_source_id != fact.source_id
        assert "경쟁우위" in fact.limitations
        if fact.comparison_metric == "영업이익률":
            assert fact.display_value == "자사 10.0%; 비교사 5.0%; 차이 5.0%p"
            assert fact.comparison_judgment == "competitive_advantage"
        else:
            assert fact.display_value == "2.0배"
            assert fact.comparison_judgment == "operating_characteristic"
            assert "경쟁우위 판정이 아니다" in fact.claim
        fact_errors = _fact_problems(
            fact,
            {
                source.source_id: source
                for source in [*report.citations, *result.sources]
            },
            {
                item.fact_id: item
                for item in [*report.fact_records, *result.facts]
            },
            {item.fact_id for item in report.fact_records},
        )
        assert fact_errors == [], fact_errors


def test_v2_bridge는_회사선언만_있어도_9장_프로그램근거를_만든다() -> None:
    from src.features.company_comparison.v2_bridge import (
        attach_comparison_program_evidence,
    )
    from src.features.company_comparison.official_sources import (
        OfficialCandidateSentence,
    )
    from src.features.composer.quality_projection import (
        _sentence_fact_id,
        _valid_fact_registries,
    )
    from src.features.provenance.sources import exact_evidence_text_hash

    evidence = "당사는 세계 최초로 초정밀 센서를 독자 개발했습니다."
    source = seal_collected_source(
        replace(
            _official_source(evidence),
            provenance_seal="",
            exact_evidence_hashes=[exact_evidence_text_hash(evidence)],
        )
    )
    result = build_competitive_position(
        _report(evidence),
        self_bundle=OfficialCompanyBundle(
            corp_code="00000001",
            company_name="주식회사 알파",
            financials=None,
            filing=None,
            official_text="",
        ),
        catalog=(),
        fetch_comparator=lambda _record: None,
        collected_on="2026-08-22",
        official_candidate_sentences=(OfficialCandidateSentence(source, evidence),),
        candidate_source_registry=(source,),
    )
    competitive = next(
        packet
        for packet in attach_comparison_program_evidence(
            _v2_packet_set_for_bridge(), result
        ).packets
        if packet.section_id == "competitive_position"
    )
    assert competitive.program_evidence is not None
    assert {fact.claim_slot for fact in competitive.program_evidence.facts} == {
        "competitive_position:stated_differentiator",
        "competitive_position:limitation",
    }
    assert not any(
        fact.claim_type == "competitive_comparison"
        for fact in competitive.program_evidence.facts
    )
    assert {
        fact.claim_type for fact in competitive.program_evidence.facts
    } == {"stated_differentiator"}
    by_id, by_key = _valid_fact_registries(competitive.program_evidence.facts)
    assert {
        _sentence_fact_id(
            "competitive_position", sentence, by_id=by_id, by_key=by_key
        )
        for sentence in competitive.program_evidence.sentences
    } == {fact.fact_id for fact in competitive.program_evidence.facts}


def test_DART_list에_reprt_code가_없어도_연차선택_내부표식으로_비교한다() -> None:
    def actual_list_shape(bundle: OfficialCompanyBundle) -> OfficialCompanyBundle:
        filing = dict(bundle.filing or {})
        filing.pop("reprt_code", None)
        filing["engine_selected_report_period_kind"] = "annual"
        return replace(bundle, filing=filing)

    self_bundle = actual_list_shape(
        _bundle("00000001", "주식회사 알파", scale=2)
    )
    comparator = actual_list_shape(
        _bundle(
            "00000002",
            "주식회사 베타",
            scale=1,
            operating_amount=50,
        )
    )

    result = build_competitive_position(
        _report(),
        self_bundle=self_bundle,
        catalog=CATALOG,
        fetch_comparator=lambda record: (
            comparator if record.corp_code == "00000002" else None
        ),
        collected_on="2026-08-19",
    )

    assert len(result.facts) == 2
    assert all(
        fact.comparison_period.endswith("2025-12-31") for fact in result.facts
    )


def test_성과지표가_비교사보다_낮으면_경쟁우위로_표시하지_않는다() -> None:
    result = build_competitive_position(
        _report(),
        self_bundle=_bundle(
            "00000001",
            "주식회사 알파",
            scale=1,
            operating_amount=50,
        ),
        catalog=CATALOG,
        fetch_comparator=lambda record: (
            _bundle("00000002", "주식회사 베타", scale=2)
            if record.corp_code == "00000002"
            else None
        ),
        collected_on="2026-08-19",
    )

    profitability = next(
        fact for fact in result.facts if fact.comparison_metric == "영업이익률"
    )

    assert "낮았다" in profitability.claim
    assert profitability.comparison_judgment == "operating_characteristic"


def test_규모만_같은_조건이어도_수익성_차이가_없으면_9장을_차단한다() -> None:
    with pytest.raises(ComparisonBlockedError, match="영업이익률 차이"):
        build_competitive_position(
            _report(),
            self_bundle=_bundle("00000001", "주식회사 알파", scale=2),
            catalog=CATALOG,
            fetch_comparator=lambda _record: _bundle(
                "00000002",
                "주식회사 베타",
                scale=1,
            ),
            collected_on="2026-08-19",
        )


def test_경쟁관계_근거가_없으면_후보를_임의로_고르지_않는다() -> None:
    report = _report("알파는 베타에 반도체 검사장비를 납품한다.")

    assert discover_candidates(report, CATALOG, self_corp_code="00000001") == ()
    with pytest.raises(ComparisonBlockedError, match="경쟁 관계"):
        build_competitive_position(
            report,
            self_bundle=_bundle("00000001", "주식회사 알파"),
            catalog=CATALOG,
            fetch_comparator=lambda _record: _bundle("00000002", "주식회사 베타"),
            collected_on="2026-08-19",
        )


def test_한_문장이_서로_다른_고유_법인_여럿을_지목하면_각각_후보다() -> None:
    evidence = "알파의 주요 경쟁사는 베타, 감마와 같은 반도체 장비 회사다."

    candidates = discover_candidates(
        _report(evidence),
        CATALOG,
        self_corp_code="00000001",
    )

    assert [item.candidate_corp_code for item in candidates] == [
        "00000002",
        "00000003",
    ]


def test_별칭_소유가_모호하거나_부분문자열이면_후보가_아니다() -> None:
    ambiguous = (
        DartCompanyRecord("00000001", "주식회사 알파"),
        DartCompanyRecord("00000002", "주식회사 베타"),
        DartCompanyRecord("00000003", "베타"),
    )
    substring = (
        DartCompanyRecord("00000001", "주식회사 알파"),
        DartCompanyRecord("00000004", "대상"),
    )

    assert discover_candidates(
        _report("알파는 베타와 경쟁 관계에 있다."),
        ambiguous,
        self_corp_code="00000001",
    ) == ()
    assert discover_candidates(
        _report("알파는 고객을 대상으로 시장에서 경쟁 관계를 관리한다."),
        substring,
        self_corp_code="00000001",
    ) == ()


def test_후보는_verified_단일문장_binding과_실제_장_참조가_모두_필요하다() -> None:
    report = _report()
    fact = report.fact_records[0]

    mutations = (
        replace(report, fact_records=[replace(fact, status="")]),
        replace(report, fact_records=[replace(fact, verification_status="")]),
        replace(report, fact_records=[replace(fact, evidence_binding="")]),
        replace(
            report,
            sections=[replace(report.sections[0], fact_ids=[])],
        ),
        _report(COMPETITION_EVIDENCE + " 두 번째 문장이다."),
    )

    for mutated in mutations:
        assert discover_candidates(
            mutated,
            CATALOG,
            self_corp_code="00000001",
        ) == ()


def test_전체공시_preflight는_false만_결정하고_같은문장만_인정한다() -> None:
    assert comparison_candidate_preflight_possible(
        "알파의 경쟁사는 베타, 감마와 같은 장비 회사다.",
        CATALOG,
        self_corp_code="00000001",
    ) is True
    assert comparison_candidate_preflight_possible(
        "베타를 언급한다. 반도체 장비 시장에는 경쟁 관계가 있다.",
        CATALOG,
        self_corp_code="00000001",
    ) is False
    assert comparison_candidate_preflight_possible(
        "반도체 장비를 공급한다.",
        CATALOG,
        self_corp_code="00000001",
    ) is False
    assert comparison_candidate_preflight_possible(
        "알파는 시장에서 경쟁 관계에 있다.",
        (),
        self_corp_code="00000001",
    ) is None


def test_preflight도_v2_명시경쟁표지와_자사주어가_같은문장이어야_한다() -> None:
    assert comparison_candidate_preflight_possible(
        "알파는 베타와 경쟁 관계에 있다.",
        CATALOG,
        self_corp_code="00000001",
        self_company="주식회사 알파",
    ) is True
    assert comparison_candidate_preflight_possible(
        "베타는 감마의 경쟁사다.",
        CATALOG,
        self_corp_code="00000001",
        self_company="주식회사 알파",
    ) is False
    assert comparison_candidate_preflight_possible(
        "알파의 시장점유율은 베타와 함께 집계된다.",
        CATALOG,
        self_corp_code="00000001",
        self_company="주식회사 알파",
    ) is False
    assert comparison_candidate_preflight_possible(
        "Beta is our principal competitor.",
        (
            DartCompanyRecord("00000001", "Alpha"),
            DartCompanyRecord("00000002", "Beta"),
        ),
        self_corp_code="00000001",
        self_company="Alpha",
    ) is True


def test_preflight_catalog에_self_record가_없으면_후보없음이_아니라_unknown이다() -> None:
    assert comparison_candidate_preflight_possible(
        "당사는 베타와 경쟁합니다.",
        (DartCompanyRecord("00000002", "주식회사 베타"),),
        self_corp_code="00000001",
        self_company="주식회사 알파",
    ) is None


@pytest.mark.parametrize(
    ("sentence", "expected_aliases"),
    [
        ("We compete with Gamma for Beta business.", {"Gamma"}),
        ("We compete with Gamma to supply Beta.", {"Gamma"}),
        ("We compete with Beta, while Gamma supplies us.", {"Beta"}),
        ("We compete with Beta and Gamma supplies us.", {"Beta"}),
        ("We compete with Beta for Gamma's business.", {"Beta"}),
        ("We compete with Beta for a contract from Gamma.", {"Beta"}),
        ("We compete with Beta to serve Gamma.", {"Beta"}),
        ("We compete with Beta using technology licensed from Gamma.", {"Beta"}),
        ("We compete with Beta after acquiring Gamma.", {"Beta"}),
        ("We compete with Beta, a subsidiary of Gamma.", {"Beta"}),
        ("Our competitors include Beta, whose supplier is Gamma.", {"Beta"}),
        ("Gamma is a customer, while Beta is our competitor.", {"Beta"}),
        ("Beta competes with us for Gamma's business.", {"Beta"}),
        ("Beta competes with us and supplies Gamma.", {"Beta"}),
        ("Beta competes with us through reseller Gamma.", {"Beta"}),
        ("Our competitors include Beta and Gamma.", {"Beta", "Gamma"}),
        ("We compete with Beta and Gamma.", {"Beta", "Gamma"}),
        ("Gamma and Delta supply us and Beta competes with us.", set()),
        ("Gamma and Delta are customers, and Beta competes with us.", set()),
        ("Gamma and Delta buy from Beta, which competes with us.", set()),
        ("Gamma and Delta are customers and Beta is our competitor.", set()),
        ("Beta and Gamma compete with us.", {"Beta", "Gamma"}),
        ("Beta is our competitor's customer.", set()),
        ("Beta is our competitor-like partner.", set()),
        ("Beta is our competitor candidate.", set()),
    ],
)
def test_영문_후보는_경쟁술어의_실제_argument_list에만_결속된다(
    sentence: str,
    expected_aliases: set[str],
) -> None:
    actual = {
        alias
        for alias in ("Beta", "Gamma")
        if comparison_alias_is_competition_target(
            alias,
            sentence,
            self_company="Alpha",
            known_company_aliases=("Alpha", "Beta", "Gamma", "Delta"),
        )
    }

    assert actual == expected_aliases


@pytest.mark.parametrize(
    ("sentence", "expected_aliases"),
    [
        ("당사는 베타가 감마와 경쟁한다고 판단합니다.", set()),
        ("알파는 베타가 감마와 경쟁한다고 판단합니다.", set()),
        ("당사는 베타가 감마와 경쟁하는 것을 지원합니다.", set()),
        ("당사는 베타의 경쟁사인 감마를 인수했습니다.", set()),
        ("당사의 자회사 베타는 감마와 경쟁합니다.", set()),
        ("당사의 계열사 베타는 감마와 경쟁합니다.", set()),
        ("당사의 투자사 베타는 감마와 경쟁합니다.", set()),
        ("당사는 베타와 경쟁하고 감마에 납품합니다.", set()),
        ("당사는 베타와 계약하고 감마와 경쟁합니다.", set()),
        ("당사는 베타와 거래한 뒤 감마와 경쟁합니다.", set()),
        ("당사는 베타와 공동개발하고 감마와 경쟁합니다.", set()),
        ("당사는 베타와 공동연구하며 감마와 경쟁합니다.", set()),
        ("당사는 베타와 공동연구 후 감마와 경쟁합니다.", set()),
        ("당사는 베타와 공동사업 후 감마와 경쟁합니다.", set()),
        ("당사는 베타와 거래 이후 감마와 경쟁합니다.", set()),
        ("당사는 베타와 공급계약을 체결하고 감마와 경쟁합니다.", set()),
        ("당사는 베타와 계약했지만 반도체 시장에서 경쟁 관계에 있다.", set()),
        ("당사는 베타와 거래했으나 반도체 시장에서 경쟁 관계에 있다.", set()),
        ("당사는 베타와 공동개발하면서 반도체 시장에서 경쟁 관계에 있다.", set()),
        ("당사는 베타와 협력하는 동시에 반도체 시장에서 경쟁 관계에 있다.", set()),
        ("당사는 베타와 소송 중이나 반도체 시장에서 경쟁 관계에 있다.", set()),
        ("당사는 베타와 고객 관계로 반도체 시장에서 경쟁 관계에 있다.", set()),
        ("당사는 베타와 합작했던 반도체 시장에서 경쟁 관계에 있다.", set()),
        ("당사는 베타와 협약한 반도체 시장에서 경쟁 관계에 있다.", set()),
        ("당사는 베타와 분쟁하던 반도체 시장에서 경쟁 관계에 있다.", set()),
        ("당사는 베타와 구매한 반도체 시장에서 경쟁 관계에 있다.", set()),
        ("당사는 베타와 위탁받은 반도체 시장에서 경쟁 관계에 있다.", set()),
        ("당사는 베타와 동맹인 반도체 시장에서 경쟁 관계에 있다.", set()),
        ("당사는 베타와 파트너였던 반도체 시장에서 경쟁 관계에 있다.", set()),
        ("당사는 베타와 반도체 검사장비 시장에서 경쟁 관계에 있다.", set()),
        ("당사는 베타와 감마와 경쟁합니다.", {"베타", "감마"}),
        ("당사의 경쟁사는 베타, 감마.", {"베타", "감마"}),
        ("당사의 경쟁사는 베타 및 감마입니다.", {"베타", "감마"}),
        ("베타는 당사의 경쟁사인 감마를 인수했습니다.", set()),
        ("베타는 당사의 경쟁사인 감마의 고객입니다.", set()),
    ],
)
def test_한국어_후보도_자사와_직접_경쟁하는_argument_list만_허용한다(
    sentence: str,
    expected_aliases: set[str],
) -> None:
    actual = {
        alias
        for alias in ("베타", "감마")
        if comparison_alias_is_competition_target(
            alias,
            sentence,
            self_company="주식회사 알파",
            known_company_aliases=(
                "주식회사 알파",
                "주식회사 베타",
                "주식회사 감마",
            ),
        )
    }

    assert actual == expected_aliases


@pytest.mark.parametrize(
    "sentence",
    [
        "Beta competes with US.",
        "Beta competes with US manufacturers.",
        "Beta competes against US firms.",
    ],
)
def test_US_국가약어는_lowercase_us_자사목적격으로_오인하지_않는다(
    sentence: str,
) -> None:
    assert comparison_source_sentence_has_marker(sentence)
    assert not comparison_source_sentence_has_self_subject(sentence, "Alpha")
    assert not comparison_alias_is_competition_target(
        "Beta",
        sentence,
        self_company="Alpha",
        known_company_aliases=("Alpha", "Beta"),
    )


def test_preflight도_incidental_alias를_후보로_승격하지_않는다() -> None:
    alpha_beta = (
        DartCompanyRecord("00000001", "Alpha"),
        DartCompanyRecord("00000002", "Beta"),
    )
    alpha_gamma = (
        DartCompanyRecord("00000001", "Alpha"),
        DartCompanyRecord("00000003", "Gamma"),
    )
    sentence = "We compete with Beta for Gamma's business."

    assert comparison_candidate_preflight_possible(
        sentence,
        alpha_beta,
        self_corp_code="00000001",
        self_company="Alpha",
    ) is True
    assert comparison_candidate_preflight_possible(
        sentence,
        alpha_gamma,
        self_corp_code="00000001",
        self_company="Alpha",
    ) is False


    assert comparison_candidate_preflight_possible(
        "Gamma and Delta supply us and Beta competes with us.",
        alpha_gamma,
        self_corp_code="00000001",
        self_company="Alpha",
    ) is False

    korean_alpha_beta = (
        DartCompanyRecord("00000001", "주식회사 알파"),
        DartCompanyRecord("00000002", "주식회사 베타"),
    )
    korean_alpha_gamma = (
        DartCompanyRecord("00000001", "주식회사 알파"),
        DartCompanyRecord("00000003", "주식회사 감마"),
    )
    korean_sentence = "당사는 베타와 공동개발하고 감마와 경쟁합니다."
    assert comparison_candidate_preflight_possible(
        korean_sentence,
        korean_alpha_beta,
        self_corp_code="00000001",
        self_company="주식회사 알파",
    ) is False
    assert comparison_candidate_preflight_possible(
        korean_sentence,
        korean_alpha_gamma,
        self_corp_code="00000001",
        self_company="주식회사 알파",
    ) is False


def test_10만_catalog에서도_문장에_나온_별칭만_확장한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    known = tuple(
        [f"Company {index:06d}" for index in range(100_000)]
        + ["Alpha", "Beta"]
    )
    expanded: list[str] = []
    original = comparison_basis_contract.comparison_candidate_aliases

    def tracked(value: str) -> tuple[str, ...]:
        expanded.append(value)
        return original(value)

    monkeypatch.setattr(
        comparison_basis_contract,
        "comparison_candidate_aliases",
        tracked,
    )

    assert comparison_alias_is_competition_target(
        "Beta",
        "We compete with Beta.",
        self_company="Alpha",
        known_company_aliases=known,
    )
    assert len(expanded) < 20


def test_10만_catalog_index는_문장검색때_전체_owner를_순회하지_않는다() -> None:
    records = tuple(
        [
            DartCompanyRecord("00000001", "Alpha"),
            DartCompanyRecord("00000002", "Beta"),
        ]
        + [
            DartCompanyRecord(f"{index + 100:08d}", f"Company {index:06d}")
            for index in range(100_000)
        ]
    )
    alias_index = comparison_logic._candidate_alias_index(records)

    class NoIterationOwners(dict[str, str]):
        def __iter__(self):
            raise AssertionError("문장마다 전체 DART 별칭을 순회하면 안 됩니다")

    guarded = replace(
        alias_index,
        owners=NoIterationOwners(alias_index.owners),
    )
    for _ in range(100):
        matches = comparison_logic._candidate_matches_for_sentence(
            "We compete with Beta.",
            alias_index=guarded,
            self_corp_code="00000001",
            self_company="Alpha",
        )

    assert [record.corp_code for record, _aliases in matches] == ["00000002"]
    assert len(alias_index.lengths_by_prefix) < 100
    assert comparison_logic._candidate_alias_index(records) is alias_index


def test_catalog와_alias_hard_cap_초과는_부분색인을_쓰지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = (
        DartCompanyRecord("00000001", "Alpha"),
        DartCompanyRecord("00000002", "Beta"),
        DartCompanyRecord("00000003", "Gamma"),
    )
    monkeypatch.setattr(comparison_logic, "MAX_CANDIDATE_CATALOG_RECORDS", 2)
    assert not comparison_logic._candidate_alias_index(records).complete

    monkeypatch.setattr(comparison_logic, "MAX_CANDIDATE_CATALOG_RECORDS", 3)
    monkeypatch.setattr(comparison_logic, "MAX_CANDIDATE_ALIASES", 1)
    assert not comparison_logic._candidate_alias_index(records).complete


def test_v1은_원문을_복제하지_않고_실제_후보_fact와_source를_가리킨다() -> None:
    report = _report()
    result = build_competitive_position(
        report,
        self_bundle=_bundle("00000001", "주식회사 알파", scale=2),
        catalog=CATALOG,
        fetch_comparator=lambda _record: _bundle(
            "00000002", "주식회사 베타", operating_amount=50
        ),
        collected_on="2026-08-19",
    )

    payload = parse_comparison_basis_v1(result.facts[0].comparison_basis)

    assert payload is not None
    assert payload["version"] == COMPARISON_BASIS_VERSION
    assert payload["candidate_fact_id"] == report.fact_records[0].fact_id
    assert payload["candidate_source_id"] == report.citations[0].source_id
    assert payload["evidence_sha256"] == evidence_text_hash(COMPETITION_EVIDENCE)
    assert "evidence_text" not in json.loads(result.facts[0].comparison_basis)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("candidate_fact_id", "fact-missing"),
        ("candidate_source_id", "source-missing"),
        ("candidate_corp_code", "00000003"),
        ("candidate_name", "주식회사 감마"),
        ("filing_document_id", "20990101000000"),
        ("evidence_sha256", "0" * 64),
        ("overlap_dimension", "고객 겹침"),
    ],
)
def test_publish_재검산은_v1_식별자_해시_축_변조를_닫는다(
    field: str,
    value: str,
) -> None:
    report = _report()
    result = build_competitive_position(
        report,
        self_bundle=_bundle("00000001", "주식회사 알파", scale=2),
        catalog=CATALOG,
        fetch_comparator=lambda _record: _bundle(
            "00000002", "주식회사 베타", operating_amount=50
        ),
        collected_on="2026-08-19",
    )
    target = result.facts[0]
    payload = json.loads(target.comparison_basis)
    payload[field] = value
    changed = replace(
        target,
        comparison_basis=json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    changed = replace(changed, evidence_binding=fact_evidence_binding(changed))
    sources = {
        source.source_id: source
        for source in [*report.citations, *result.sources]
    }
    facts = {
        fact.fact_id: fact
        for fact in [*report.fact_records, changed, *result.facts[1:]]
    }

    errors = _fact_problems(
        changed,
        sources,
        facts,
        {report.fact_records[0].fact_id},
    )

    assert any("[comparison]" in error for error in errors)


def test_뉴스나_외부분석의_경쟁사_언급은_후보근거로_쓰지_않는다() -> None:
    report = _report()
    external = replace(
        _official_source(),
        kind=SourceKind.NEWS,
        source_type="외부 분석",
        publisher="가상리서치",
        published_at="2026-08-18",
        domain="research.example.com",
    )
    report = replace(report, citations=[external])

    assert discover_candidates(report, CATALOG, self_corp_code="00000001") == ()


@pytest.mark.parametrize(
    "fetcher",
    [
        lambda _record: None,
        lambda _record: _bundle("00000002", "주식회사 베타", official_text=""),
    ],
)
def test_한쪽_공식근거가_없으면_9장을_차단한다(fetcher) -> None:
    with pytest.raises(ComparisonBlockedError, match="비교사 공식 원문"):
        build_competitive_position(
            _report(),
            self_bundle=_bundle("00000001", "주식회사 알파"),
            catalog=CATALOG,
            fetch_comparator=fetcher,
            collected_on="2026-08-19",
        )


def test_비교사_DART_일시장애는_자료부족으로_삼키지_않는다() -> None:
    def fail_transient(_record: DartCompanyRecord) -> OfficialCompanyBundle:
        raise ComparisonSourceTransientError()

    with pytest.raises(ComparisonSourceTransientError):
        build_competitive_position(
            _report(),
            self_bundle=_bundle("00000001", "주식회사 알파"),
            catalog=CATALOG,
            fetch_comparator=fail_transient,
            collected_on="2026-08-19",
        )


@pytest.mark.parametrize(
    "error_type",
    (ComparisonSourceConfigurationError, ComparisonSourceInternalError),
)
def test_비교사_설정과_내부오류도_자료부족으로_삼키지_않는다(
    error_type: type[RuntimeError],
) -> None:
    def fail_closed(_record: DartCompanyRecord) -> OfficialCompanyBundle:
        raise error_type()

    with pytest.raises(error_type):
        build_competitive_position(
            _report(),
            self_bundle=_bundle("00000001", "주식회사 알파"),
            catalog=CATALOG,
            fetch_comparator=fail_closed,
            collected_on="2026-08-19",
        )


@pytest.mark.parametrize("error", (TypeError("배선 오류"), ZeroDivisionError()))
def test_비교사_callback의_예상밖_예외는_내부오류로_즉시_닫는다(
    error: Exception,
) -> None:
    def fail_programming(_record: DartCompanyRecord) -> OfficialCompanyBundle:
        raise error

    with pytest.raises(ComparisonSourceInternalError) as caught:
        build_competitive_position(
            _report(),
            self_bundle=_bundle("00000001", "주식회사 알파"),
            catalog=CATALOG,
            fetch_comparator=fail_programming,
            collected_on="2026-08-19",
        )

    assert caught.value.__cause__ is error
    assert "배선 오류" not in str(caught.value)


def test_기간이_다르면_같은_계정이어도_비교하지_않는다() -> None:
    with pytest.raises(ComparisonBlockedError, match="지표 정의·기간·연결범위"):
        build_competitive_position(
            _report(),
            self_bundle=_bundle("00000001", "주식회사 알파"),
            catalog=CATALOG,
            fetch_comparator=lambda _record: _bundle(
                "00000002",
                "주식회사 베타",
                period="2024.01.01 ~ 2024.12.31",
            ),
            collected_on="2026-08-19",
        )


def test_연결과_별도_범위가_다르면_비교하지_않는다() -> None:
    with pytest.raises(ComparisonBlockedError, match="지표 정의·기간·연결범위"):
        build_competitive_position(
            _report(),
            self_bundle=_bundle("00000001", "주식회사 알파", scope="CFS"),
            catalog=CATALOG,
            fetch_comparator=lambda _record: _bundle(
                "00000002",
                "주식회사 베타",
                scope="OFS",
            ),
            collected_on="2026-08-19",
        )


def test_자사_공식원문이_없어도_한쪽자료로_비교하지_않는다() -> None:
    with pytest.raises(ComparisonBlockedError, match="자사 공식 원문"):
        build_competitive_position(
            _report(),
            self_bundle=replace(_bundle("00000001", "주식회사 알파"), official_text=""),
            catalog=CATALOG,
            fetch_comparator=lambda _record: _bundle("00000002", "주식회사 베타"),
            collected_on="2026-08-19",
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "status",
        "missing_top_report_code",
        "report_code",
        "currency",
        "conflicting_row",
        "conflicting_account_name",
    ],
)
def test_상태_보고서코드_통화_충돌행이_모호하면_비교를_차단한다(
    mutation: str,
) -> None:
    comparator = _bundle(
        "00000002", "주식회사 베타", scale=1, operating_amount=50
    )
    payload = {
        **dict(comparator.financials or {}),
        "list": [dict(row) for row in (comparator.financials or {}).get("list", [])],
    }
    if mutation == "status":
        payload["status"] = "013"
    elif mutation == "missing_top_report_code":
        payload.pop("reprt_code")
    elif mutation == "report_code":
        payload["list"][0]["reprt_code"] = "11013"
    elif mutation == "currency":
        payload["list"][0]["currency"] = "USD"
    else:
        conflict = dict(payload["list"][0])
        conflict["thstrm_amount"] = "9999"
        if mutation == "conflicting_account_name":
            conflict["account_nm"] = "영업수익"
        payload["list"].append(conflict)

    with pytest.raises(ComparisonBlockedError):
        build_competitive_position(
            _report(),
            self_bundle=_bundle("00000001", "주식회사 알파", scale=2),
            catalog=CATALOG,
            fetch_comparator=lambda _record: replace(
                comparator, financials=payload
            ),
            collected_on="2026-08-19",
        )


def test_양사_공식원문에_같은_고객제품시장이_없으면_비교를_차단한다() -> None:
    comparator = _bundle(
        "00000002",
        "주식회사 베타",
        operating_amount=50,
        official_text="공식 재무제표의 수치만 공시한다.",
    )
    with pytest.raises(ComparisonBlockedError, match="고객·제품·시장"):
        build_competitive_position(
            _report(),
            self_bundle=_bundle("00000001", "주식회사 알파", scale=2),
            catalog=CATALOG,
            fetch_comparator=lambda _record: comparator,
            collected_on="2026-08-19",
        )


def test_일반_축_단어만_같으면_동일_비교범위로_승격하지_않는다() -> None:
    generic = "고객 대상 제품을 시장에 공급한다. 연결재무제표 수치를 공시한다."
    with pytest.raises(ComparisonBlockedError, match="고객·제품·시장"):
        build_competitive_position(
            _report(),
            self_bundle=_bundle(
                "00000001", "주식회사 알파", scale=2, official_text=generic
            ),
            catalog=CATALOG,
            fetch_comparator=lambda _record: _bundle(
                "00000002", "주식회사 베타", official_text=generic
            ),
            collected_on="2026-08-19",
        )


def test_비교사실_해시는_공개문장이_아니라_실제_양사_payload에_결속된다() -> None:
    result = build_competitive_position(
        _report(),
        self_bundle=_bundle("00000001", "주식회사 알파", scale=2),
        catalog=CATALOG,
        fetch_comparator=lambda _record: _bundle(
            "00000002", "주식회사 베타", operating_amount=50
        ),
        collected_on="2026-08-19",
    )
    sources = {source.source_id: source for source in result.sources}
    for fact in result.facts:
        assert evidence_text_hash(fact.state_evidence) in sources[fact.source_id].evidence_hashes
        assert evidence_text_hash(fact.comparator_state_evidence) in sources[
            fact.comparator_source_id
        ].evidence_hashes
        assert evidence_text_hash(fact.claim) not in sources[fact.source_id].evidence_hashes


def _v2_official_registry(
    evidence: str = "당사는 베타와 경쟁 관계에 있다.",
    *,
    url: str = "https://www.alpha.example/ir/competition",
    profile_name: str = "주식회사 알파",
    profile_code: str = "00000001",
    response_code: str = "00000001",
    fragment_kind: str = "홈페이지",
    profile_url: str = "https://www.alpha.example",
    company_name: str = "주식회사 알파",
    published_at: str = "",
):
    fragment = {
        "종류": fragment_kind,
        "원문": evidence,
        "출처": url,
        VERIFIED_FINAL_URL_FIELD: VERIFIED_FINAL_URL_VALUE,
        "문서명": "경쟁 환경",
        "원문위치": "/ir/competition",
    }
    if published_at:
        fragment["문서일"] = published_at
    fragments = register_candidate_sentence_evidence(
        {21: fragment}
    )
    bound = bind_dart_profile_attestation(
        fragments,
        profile={
            "status": "000",
            "corp_code": response_code,
            "corp_name": profile_name,
            "hm_url": profile_url,
        },
        corp_code=profile_code,
        company_name=company_name,
        collected_on="2026-08-22",
    )
    sources = build_citations(
        bound.fragments,
        filing=None,
        collected_on=date(2026, 8, 22),
        company_publisher=company_name,
    )
    if bound.attester is not None:
        sources.append(bound.attester)
    rows = candidate_sentences_from_fragments(bound.fragments, sources)
    return bound, sources, rows


def _official_candidate_codes(
    evidence: str,
    *,
    catalog: tuple[DartCompanyRecord, ...] = CATALOG,
    self_company: str = "주식회사 알파",
) -> list[str]:
    profile_name = self_company
    _bound, sources, rows = _v2_official_registry(
        evidence,
        profile_name=profile_name,
        company_name=self_company,
    )
    return [
        candidate.candidate_corp_code
        for candidate in discover_official_source_candidates(
            rows,
            sources,
            catalog,
            self_corp_code="00000001",
            self_company=self_company,
        )
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://www.alpha.example/newsroom/2015-competition",
        "https://www.alpha.example/%6eewsroom/2015-competition",
        "https://www.alpha.example/PRESS-RELEASES/2015-competition",
        "https://www.alpha.example/archive/2015-competition",
    ],
)
def test_문서일없는_archive형_공식웹은_v2현재후보가_아니다(url: str) -> None:
    _bound, sources, rows = _v2_official_registry(
        "당사의 경쟁사는 베타입니다.",
        url=url,
    )

    assert discover_official_source_candidates(
        rows,
        sources,
        CATALOG,
        self_corp_code="00000001",
        self_company="주식회사 알파",
    ) == ()


@pytest.mark.parametrize(
    ("published_at", "expected_codes"),
    [("2015-06-01", []), ("2026-08-01", ["00000002"])],
)
def test_archive형_공식웹은_검증된_최근문서일만_v2후보로_쓴다(
    published_at: str,
    expected_codes: list[str],
) -> None:
    _bound, sources, rows = _v2_official_registry(
        "당사의 경쟁사는 베타입니다.",
        url="https://www.alpha.example/newsroom/competition",
        published_at=published_at,
    )

    assert [
        candidate.candidate_corp_code
        for candidate in discover_official_source_candidates(
            rows,
            sources,
            CATALOG,
            self_corp_code="00000001",
            self_company="주식회사 알파",
        )
    ] == expected_codes


def test_v2_discovery는_limit충족후_나머지_원문행을_처리하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _bound, sources, rows = _v2_official_registry()
    calls = 0
    original = comparison_logic._candidate_matches_for_sentence

    def tracked(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(
        comparison_logic,
        "_candidate_matches_for_sentence",
        tracked,
    )
    candidates = discover_official_source_candidates(
        rows * 5_000,
        sources,
        CATALOG,
        self_corp_code="00000001",
        self_company="주식회사 알파",
        limit=1,
    )

    assert [candidate.candidate_corp_code for candidate in candidates] == [
        "00000002"
    ]
    assert calls == 1


@pytest.mark.parametrize(
    ("evidence", "expected_codes"),
    [
        ("We compete with Gamma for Beta business.", ["00000003"]),
        ("We compete with Gamma to supply Beta.", ["00000003"]),
        ("We compete with Beta, while Gamma supplies us.", ["00000002"]),
        ("We compete with Beta and Gamma supplies us.", ["00000002"]),
        ("We compete with Beta for Gamma's business.", ["00000002"]),
        ("We compete with Beta for a contract from Gamma.", ["00000002"]),
        ("We compete with Beta to serve Gamma.", ["00000002"]),
        ("We compete with Beta using technology licensed from Gamma.", ["00000002"]),
        ("We compete with Beta after acquiring Gamma.", ["00000002"]),
        ("We compete with Beta, a subsidiary of Gamma.", ["00000002"]),
        ("Our competitors include Beta, whose supplier is Gamma.", ["00000002"]),
        ("Gamma is a customer, while Beta is our competitor.", ["00000002"]),
        ("Beta competes with us for Gamma's business.", ["00000002"]),
        ("Beta competes with us and supplies Gamma.", ["00000002"]),
        ("Beta competes with us through reseller Gamma.", ["00000002"]),
        ("Our competitors include Beta and Gamma.", ["00000003", "00000002"]),
        ("We compete with Beta and Gamma.", ["00000003", "00000002"]),
        ("Gamma and Delta supply us and Beta competes with us.", []),
        ("Gamma and Delta are customers, and Beta competes with us.", []),
        ("Gamma and Delta buy from Beta, which competes with us.", []),
        ("Gamma and Delta are customers and Beta is our competitor.", []),
        ("Beta and Gamma compete with us.", ["00000003", "00000002"]),
        ("Beta is our competitor's customer.", []),
        ("Beta is our competitor-like partner.", []),
        ("Beta is our competitor candidate.", []),
        ("Beta competes with US.", []),
        ("Beta competes with US manufacturers.", []),
        ("Beta competes against US firms.", []),
        ("Beta competes with us.", ["00000002"]),
    ],
)
def test_v2_실제발견도_영문_경쟁argument밖의_법인을_버린다(
    evidence: str,
    expected_codes: list[str],
) -> None:
    english_catalog = (
        DartCompanyRecord("00000001", "Alpha"),
        DartCompanyRecord("00000002", "Beta"),
        DartCompanyRecord("00000003", "Gamma"),
    )

    assert _official_candidate_codes(
        evidence,
        catalog=english_catalog,
        self_company="Alpha",
    ) == expected_codes


@pytest.mark.parametrize(
    "sentence",
    [
        "Beta is our former competitor.",
        "Beta is our potential competitor.",
        "Beta is our prospective competitor.",
        "Beta is our possible competitor.",
        "Beta is our alleged competitor.",
        "Beta is our candidate competitor.",
        "Beta is our likely competitor.",
        "Beta is our purported competitor.",
        "Beta is our historical competitor.",
        "Our former competitors include Beta.",
        "Our potential competitors include Beta.",
        "Our prospective competitors include Beta.",
        "Our possible competitors include Beta.",
        "Our alleged competitors include Beta.",
        "Our candidate competitors include Beta.",
        "Our likely competitors include Beta.",
        "Our purported competitors include Beta.",
        "Our historical competitors include Beta.",
    ],
)
def test_영문_비현재_경쟁사_수식어는_후보관계를_확정하지_않는다(
    sentence: str,
) -> None:
    catalog = (
        DartCompanyRecord("00000001", "Alpha"),
        DartCompanyRecord("00000002", "Beta"),
    )

    assert not comparison_source_sentence_has_marker(sentence)
    assert not comparison_source_sentence_has_self_subject(sentence, "Alpha")
    assert not comparison_alias_is_competition_target(
        "Beta",
        sentence,
        self_company="Alpha",
        known_company_aliases=("Alpha", "Beta"),
    )
    assert comparison_candidate_preflight_possible(
        sentence,
        catalog,
        self_corp_code="00000001",
        self_company="Alpha",
    ) is False
    assert _official_candidate_codes(
        sentence,
        catalog=catalog,
        self_company="Alpha",
    ) == []


@pytest.mark.parametrize(
    "sentence",
    [
        "Beta is our principal competitor.",
        "Beta is our primary competitor.",
        "Beta is our direct competitor.",
        "Beta is our major competitor.",
        "Our principal competitors include Beta.",
        "Our primary competitors include Beta.",
        "Our direct competitors include Beta.",
        "Our major competitors include Beta.",
    ],
)
def test_영문_현재_경쟁관계_닫힌_수식어는_후보를_허용한다(sentence: str) -> None:
    catalog = (
        DartCompanyRecord("00000001", "Alpha"),
        DartCompanyRecord("00000002", "Beta"),
    )

    assert comparison_source_sentence_has_marker(sentence)
    assert comparison_source_sentence_has_self_subject(sentence, "Alpha")
    assert comparison_alias_is_competition_target(
        "Beta",
        sentence,
        self_company="Alpha",
        known_company_aliases=("Alpha", "Beta"),
    )
    assert comparison_candidate_preflight_possible(
        sentence,
        catalog,
        self_corp_code="00000001",
        self_company="Alpha",
    ) is True
    assert _official_candidate_codes(
        sentence,
        catalog=catalog,
        self_company="Alpha",
    ) == ["00000002"]


@pytest.mark.parametrize(
    "sentence",
    [
        "Beta was our competitor.",
        "Beta were our competitor.",
        "Beta became our competitor.",
        "We competed with Beta.",
        "Beta competed with us.",
        "Our competitors included Beta.",
        "Our competitors were Beta.",
        "We used to compete with Beta.",
        "We would formerly compete with Beta.",
        "Beta had been our competitor.",
    ],
)
def test_영문_과거_경쟁관계는_현재_후보로_승격하지_않는다(sentence: str) -> None:
    catalog = (
        DartCompanyRecord("00000001", "Alpha"),
        DartCompanyRecord("00000002", "Beta"),
    )

    assert not comparison_source_sentence_has_marker(sentence)
    assert not comparison_source_sentence_has_self_subject(sentence, "Alpha")
    assert not comparison_alias_is_competition_target(
        "Beta",
        sentence,
        self_company="Alpha",
        known_company_aliases=("Alpha", "Beta"),
    )
    assert comparison_candidate_preflight_possible(
        sentence,
        catalog,
        self_corp_code="00000001",
        self_company="Alpha",
    ) is False
    assert _official_candidate_codes(
        sentence,
        catalog=catalog,
        self_company="Alpha",
    ) == []


@pytest.mark.parametrize(
    "sentence",
    [
        "베타는 당사의 경쟁사였다.",
        "베타는 당사의 경쟁사였습니다.",
        "당사의 경쟁사는 베타였다.",
        "당사는 베타와 경쟁하였다.",
        "당사는 베타와 경쟁했습니다.",
        "당사는 베타와 경쟁했었다.",
        "당사는 베타와 경쟁해왔다.",
    ],
)
def test_한국어_과거_경쟁관계는_현재_후보로_승격하지_않는다(sentence: str) -> None:
    assert not comparison_source_sentence_has_marker(sentence)
    assert not comparison_source_sentence_has_self_subject(
        sentence, "주식회사 알파"
    )
    assert not comparison_alias_is_competition_target(
        "베타",
        sentence,
        self_company="주식회사 알파",
        known_company_aliases=("주식회사 알파", "주식회사 베타"),
    )
    assert comparison_candidate_preflight_possible(
        sentence,
        CATALOG[:2],
        self_corp_code="00000001",
        self_company="주식회사 알파",
    ) is False
    assert _official_candidate_codes(sentence, catalog=CATALOG[:2]) == []


@pytest.mark.parametrize(
    "sentence",
    [
        "Beta is our competitor.",
        "Beta remains our competitor.",
        "We compete with Beta.",
        "Beta competes with us.",
        "We are competing with Beta.",
        "Our competitors include Beta.",
        "Our competitors are Beta.",
    ],
)
def test_영문_현재형_경쟁관계는_후보를_유지한다(sentence: str) -> None:
    assert comparison_alias_is_competition_target(
        "Beta",
        sentence,
        self_company="Alpha",
        known_company_aliases=("Alpha", "Beta"),
    )


@pytest.mark.parametrize(
    "sentence",
    [
        "베타는 당사의 경쟁사이다.",
        "베타는 당사의 경쟁사입니다.",
        "당사는 베타와 경쟁한다.",
        "당사는 베타와 경쟁합니다.",
        "당사는 베타와 경쟁 중이다.",
        "당사는 베타와 경쟁 중입니다.",
    ],
)
def test_한국어_현재형_경쟁관계는_후보를_유지한다(sentence: str) -> None:
    assert comparison_alias_is_competition_target(
        "베타",
        sentence,
        self_company="주식회사 알파",
        known_company_aliases=("주식회사 알파", "주식회사 베타"),
    )


@pytest.mark.parametrize(
    ("evidence", "expected_codes"),
    [
        ("당사는 베타가 감마와 경쟁한다고 판단합니다.", []),
        ("알파는 베타가 감마와 경쟁한다고 판단합니다.", []),
        ("당사는 베타가 감마와 경쟁하는 것을 지원합니다.", []),
        ("당사는 베타의 경쟁사인 감마를 인수했습니다.", []),
        ("당사의 자회사 베타는 감마와 경쟁합니다.", []),
        ("당사의 투자사 베타는 감마와 경쟁합니다.", []),
        ("당사는 베타와 경쟁하고 감마에 납품합니다.", []),
        ("당사는 베타와 공동개발하고 감마와 경쟁합니다.", []),
        ("당사는 베타와 공동연구 후 감마와 경쟁합니다.", []),
        ("당사는 베타와 계약했지만 반도체 시장에서 경쟁 관계에 있다.", []),
        ("당사는 베타와 거래했으나 반도체 시장에서 경쟁 관계에 있다.", []),
        ("당사는 베타와 공동개발하면서 반도체 시장에서 경쟁 관계에 있다.", []),
        ("당사는 베타와 협력하는 동시에 반도체 시장에서 경쟁 관계에 있다.", []),
        ("당사는 베타와 소송 중이나 반도체 시장에서 경쟁 관계에 있다.", []),
        ("당사는 베타와 고객 관계로 반도체 시장에서 경쟁 관계에 있다.", []),
        ("당사는 베타와 합작했던 반도체 시장에서 경쟁 관계에 있다.", []),
        ("당사는 베타와 협약한 반도체 시장에서 경쟁 관계에 있다.", []),
        ("당사는 베타와 분쟁하던 반도체 시장에서 경쟁 관계에 있다.", []),
        ("당사는 베타와 구매한 반도체 시장에서 경쟁 관계에 있다.", []),
        ("당사는 베타와 위탁받은 반도체 시장에서 경쟁 관계에 있다.", []),
        ("당사는 베타와 동맹인 반도체 시장에서 경쟁 관계에 있다.", []),
        ("당사는 베타와 파트너였던 반도체 시장에서 경쟁 관계에 있다.", []),
        ("당사는 베타와 반도체 검사장비 시장에서 경쟁 관계에 있다.", []),
        ("당사는 베타와 감마와 경쟁합니다.", ["00000002", "00000003"]),
        ("당사의 경쟁사는 베타, 감마.", ["00000002", "00000003"]),
        ("당사의 경쟁사는 베타 및 감마입니다.", ["00000002", "00000003"]),
        ("베타는 당사의 경쟁사인 감마를 인수했습니다.", []),
        ("베타는 당사의 경쟁사인 감마의 고객입니다.", []),
    ],
)
def test_v2_실제발견도_한국어_제3자관계와_후속별칭을_버린다(
    evidence: str,
    expected_codes: list[str],
) -> None:
    assert _official_candidate_codes(evidence) == expected_codes


def test_한국어_관형_경쟁관계는_닫힌_자사_후보_설명골격만_허용한다() -> None:
    known = ("가나다전자", "베타전자", "감마전자")
    valid = "가나다전자는 베타전자와 경쟁 관계인 반도체 검사 장비 전문기업이다."

    assert comparison_alias_is_competition_target(
        "베타전자",
        valid,
        self_company="가나다전자",
        known_company_aliases=known,
    )
    assert not comparison_alias_is_competition_target(
        "베타전자",
        "가나다전자는 베타전자와 경쟁 관계인 감마전자 전문기업이다.",
        self_company="가나다전자",
        known_company_aliases=known,
    )
    assert not comparison_alias_is_competition_target(
        "베타전자",
        "가나다전자는 베타전자와 경쟁 관계인 반도체 시장은 성장하는 기업이다.",
        self_company="가나다전자",
        known_company_aliases=known,
    )

    assert _official_candidate_codes(
        valid,
        catalog=(
            DartCompanyRecord("00000001", "가나다전자"),
            DartCompanyRecord("00000002", "베타전자"),
            DartCompanyRecord("00000003", "감마전자"),
        ),
        self_company="가나다전자",
    ) == ["00000002"]


def _v2_comparison_result():
    _bound, sources, rows = _v2_official_registry()
    result = build_competitive_position(
        _report("알파는 반도체 검사장비를 전자 제조 고객 시장에 공급한다."),
        self_bundle=_bundle("00000001", "주식회사 알파", scale=2),
        catalog=CATALOG,
        fetch_comparator=lambda _record: _bundle(
            "00000002", "주식회사 베타", operating_amount=50
        ),
        collected_on="2026-08-22",
        official_candidate_sentences=rows,
        candidate_source_registry=sources,
    )
    return result


def _actual_v2_program():
    from src.features.company_comparison.v2_bridge import (
        attach_comparison_program_evidence,
    )

    competitive = next(
        packet
        for packet in attach_comparison_program_evidence(
            _v2_packet_set_for_bridge(),
            _v2_comparison_result(),
        ).packets
        if packet.section_id == "competitive_position"
    )
    assert competitive.program_evidence is not None
    return competitive.program_evidence


def test_v2_bridge는_실제_양사_fact_source와_다섯_의미칸을_봉인한다() -> None:
    from src.features.company_comparison.v2_bridge import (
        attach_comparison_program_evidence,
    )
    from src.features.provenance.sources import exact_evidence_text_hash

    bridged = attach_comparison_program_evidence(
        _v2_packet_set_for_bridge(),
        _v2_comparison_result(),
    )
    competitive = next(
        packet
        for packet in bridged.packets
        if packet.section_id == "competitive_position"
    )
    assert competitive.program_evidence is not None
    program = competitive.program_evidence
    assert {fact.claim_slot for fact in program.facts} == {
        "competitive_position:comparison_target",
        "competitive_position:comparison_metric",
        "competitive_position:comparison_basis",
        "competitive_position:comparison_judgment",
        "competitive_position:limitation",
    }
    assert {source.publisher for source in program.registry_sources} >= {
        "주식회사 알파",
        "주식회사 베타",
    }
    assert all(has_valid_provenance_seal(source) for source in program.registry_sources)
    assert all(
        exact_evidence_text_hash(fragment.text)
        in fragment.bound_source.exact_evidence_hashes
        for fragment in program.source_fragments
    )
    assert all(fact.supporting_source_ids for fact in program.facts)
    assert all(sentence.verified_fact_id for sentence in program.sentences)
    context_facts = tuple(
        fact
        for fact in program.facts
        if fact.claim_type == "competitive_comparison_context"
    )
    assert len(context_facts) == 4
    assert all(fact.state_evidence not in fact.claim for fact in context_facts)
    assert all(len(set(fact.evidence_support_terms)) >= 2 for fact in context_facts)
    assert all(
        fragment.document_content_sha256 == ""
        for fragment in program.source_fragments
        if fragment.kind == "공식 공시·재무 API"
    )


def test_v2_bridge는_비교_fact를_고친_가짜_프로그램근거를_거절한다() -> None:
    from src.features.company_comparison.v2_bridge import (
        attach_comparison_program_evidence,
    )
    from src.features.composer.port import VerifiedProgramEvidence

    competitive = next(
        packet
        for packet in attach_comparison_program_evidence(
            _v2_packet_set_for_bridge(),
            _v2_comparison_result(),
        ).packets
        if packet.section_id == "competitive_position"
    )
    assert competitive.program_evidence is not None
    program = competitive.program_evidence
    with pytest.raises(ValueError, match="FactRecord"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=program.source_fragments,
            registry_sources=program.registry_sources,
            facts=(replace(program.facts[0], claim="근거 없이 바꾼 문장"), *program.facts[1:]),
            sentences=program.sentences,
        )


def test_v2_bridge는_formal_Source와_조각의_문서전체지문_불일치를_거절한다() -> None:
    from src.features.company_comparison.v2_bridge import (
        attach_comparison_program_evidence,
    )
    from src.features.composer.port import VerifiedProgramEvidence
    from src.shared.report_evidence.constants import (
        SOURCE_KIND_DART_BUSINESS_REPORT,
    )
    from src.shared.report_quality.source_identity import (
        bound_source_fragment_provenance,
    )

    competitive = next(
        packet
        for packet in attach_comparison_program_evidence(
            _v2_packet_set_for_bridge(),
            _v2_comparison_result(),
        ).packets
        if packet.section_id == "competitive_position"
    )
    assert competitive.program_evidence is not None
    program = competitive.program_evidence
    original_fragment = program.source_fragments[0]
    original_source = original_fragment.bound_source
    assert type(original_source) is Source
    formal_source = seal_collected_source(
        replace(
            original_source,
            formal_source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
            identity_binding="typed-company-binding",
            document_content_sha256="a" * 64,
            provenance_seal="",
        )
    )
    formal_projection = bound_source_fragment_provenance(formal_source)
    formal_projection["document_content_sha256"] = "b" * 64
    tampered_fragment = replace(
        original_fragment,
        bound_source=formal_source,
        **formal_projection,
    )
    registry = tuple(
        formal_source if source.source_id == original_source.source_id else source
        for source in program.registry_sources
    )

    with pytest.raises(ValueError, match="document_content_sha256"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=(tampered_fragment, *program.source_fragments[1:]),
            registry_sources=registry,
            facts=program.facts,
            sentences=program.sentences,
        )


def test_v2_bridge의_bound_Source는_packet과_program에_정확히_같아야한다() -> None:
    from src.features.company_comparison.v2_bridge import (
        attach_comparison_program_evidence,
    )

    competitive = next(
        packet
        for packet in attach_comparison_program_evidence(
            _v2_packet_set_for_bridge(),
            _v2_comparison_result(),
        ).packets
        if packet.section_id == "competitive_position"
    )
    assert competitive.program_evidence is not None
    program_fragment_ids = {
        fragment.fragment_id
        for fragment in competitive.program_evidence.source_fragments
    }
    first_program_index = next(
        index
        for index, fragment in enumerate(competitive.fragments)
        if fragment.fragment_id in program_fragment_ids
    )
    first_plain_index = next(
        index
        for index, fragment in enumerate(competitive.fragments)
        if fragment.fragment_id not in program_fragment_ids
    )
    program_fragment = competitive.fragments[first_program_index]
    plain_fragment = competitive.fragments[first_plain_index]

    with pytest.raises(ValueError, match="프로그램 근거에 결속"):
        replace(competitive, program_evidence=None)

    missing = list(competitive.fragments)
    missing[first_program_index] = replace(program_fragment, bound_source=None)
    with pytest.raises(ValueError, match="프로그램 원문 조각"):
        replace(competitive, fragments=tuple(missing))

    extra = list(competitive.fragments)
    extra[first_plain_index] = replace(
        plain_fragment,
        bound_source=program_fragment.bound_source,
    )
    with pytest.raises(ValueError, match="프로그램 원문 조각"):
        replace(competitive, fragments=tuple(extra))

    mismatched = list(competitive.fragments)
    mismatched[first_program_index] = replace(
        program_fragment,
        text=program_fragment.text + " 변조",
    )
    with pytest.raises(ValueError, match="프로그램 원문 조각"):
        replace(competitive, fragments=tuple(mismatched))


def test_v2_bridge_program은_unsigned_tampered_registry_Source를_거절한다() -> None:
    from src.features.company_comparison.v2_bridge import (
        attach_comparison_program_evidence,
    )
    from src.features.composer.port import VerifiedProgramEvidence

    competitive = next(
        packet
        for packet in attach_comparison_program_evidence(
            _v2_packet_set_for_bridge(),
            _v2_comparison_result(),
        ).packets
        if packet.section_id == "competitive_position"
    )
    assert competitive.program_evidence is not None
    program = competitive.program_evidence
    original_fragment = program.source_fragments[0]
    original_source = original_fragment.bound_source
    assert type(original_source) is Source

    unsigned = replace(original_source, provenance_seal="")
    unsigned_fragment = replace(original_fragment, bound_source=unsigned)
    unsigned_registry = tuple(
        unsigned if source.source_id == original_source.source_id else source
        for source in program.registry_sources
    )
    with pytest.raises(ValueError, match="provenance 도장"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=(unsigned_fragment, *program.source_fragments[1:]),
            registry_sources=unsigned_registry,
            facts=program.facts,
            sentences=program.sentences,
        )

    tampered = replace(original_source, title=original_source.title + " 변조")
    tampered_fragment = replace(original_fragment, bound_source=tampered)
    tampered_registry = tuple(
        tampered if source.source_id == original_source.source_id else source
        for source in program.registry_sources
    )
    with pytest.raises(ValueError, match="provenance 도장"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=(tampered_fragment, *program.source_fragments[1:]),
            registry_sources=tampered_registry,
            facts=program.facts,
            sentences=program.sentences,
        )

    replacement = seal_collected_source(
        replace(
            original_source,
            title=original_source.title + " 다른 등록부 값",
            provenance_seal="",
        )
    )
    assert has_valid_provenance_seal(replacement)
    replacement_registry = tuple(
        replacement if source.source_id == original_source.source_id else source
        for source in program.registry_sources
    )
    with pytest.raises(ValueError, match="등록부의 봉인 값"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=program.source_fragments,
            registry_sources=replacement_registry,
            facts=program.facts,
            sentences=program.sentences,
        )


@pytest.mark.parametrize(
    "field_name",
    (
        "source_id",
        "source_type",
        "source_title",
        "source_publisher",
        "source_host",
        "source_url",
        "source_document_id",
        "location",
        "source_date",
    ),
)
def test_v2_program_Fact대표출처_모든메타는_Source와_정확히같아야한다(
    field_name: str,
) -> None:
    from src.features.composer.port import VerifiedProgramEvidence

    program = _actual_v2_program()
    fact = program.facts[0]
    forged = replace(
        fact,
        **{field_name: str(getattr(fact, field_name)) + "-FORGED"},
        evidence_binding="",
    )
    forged = replace(forged, evidence_binding=fact_evidence_binding(forged))

    with pytest.raises(ValueError, match="대표 출처 메타데이터"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=program.source_fragments,
            registry_sources=program.registry_sources,
            facts=(forged, *program.facts[1:]),
            sentences=program.sentences,
        )


def test_v2_program은_Source와_fragment를_함께_재봉인해도_Fact위조를_거절한다() -> None:
    from src.features.composer.port import VerifiedProgramEvidence
    from src.shared.report_quality.source_identity import (
        bound_source_fragment_provenance,
    )

    program = _actual_v2_program()
    fragment = program.source_fragments[0]
    source = fragment.bound_source
    assert type(source) is Source
    replacement = seal_collected_source(
        replace(
            source,
            title=source.title + "-FORGED",
            provenance_seal="",
        )
    )
    replacement_fragment = replace(
        fragment,
        bound_source=replacement,
        **bound_source_fragment_provenance(replacement),
    )
    replacement_registry = tuple(
        replacement if item.source_id == source.source_id else item
        for item in program.registry_sources
    )

    with pytest.raises(ValueError, match="대표 출처 메타데이터"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=(replacement_fragment, *program.source_fragments[1:]),
            registry_sources=replacement_registry,
            facts=program.facts,
            sentences=program.sentences,
        )


def test_v2_program은_일관되게_재봉인한_nonformal_비공식_Source도_거절한다() -> None:
    """HMAC·fragment·Fact를 함께 고쳐도 citation 자체가 공식이어야 한다."""

    from src.features.composer.port import VerifiedProgramEvidence
    from src.shared.report_quality.source_identity import (
        bound_source_fragment_provenance,
    )

    program = _actual_v2_program()
    fragment_index = next(
        index
        for index, fragment in enumerate(program.source_fragments)
        if not fragment.bound_source.formal_source_kind
    )
    fragment = program.source_fragments[fragment_index]
    source = fragment.bound_source
    assert type(source) is Source
    replacement = seal_collected_source(
        replace(source, source_type="", provenance_seal="")
    )
    replacement_fragment = replace(
        fragment,
        bound_source=replacement,
        **bound_source_fragment_provenance(replacement),
    )
    fragments = list(program.source_fragments)
    fragments[fragment_index] = replacement_fragment
    registry = tuple(
        replacement if item.source_id == source.source_id else item
        for item in program.registry_sources
    )
    facts = []
    for fact in program.facts:
        if fact.source_id != source.source_id:
            facts.append(fact)
            continue
        changed = replace(fact, source_type="", evidence_binding="")
        facts.append(
            replace(changed, evidence_binding=fact_evidence_binding(changed))
        )

    with pytest.raises(ValueError, match="citation Source가 공식 출처"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=tuple(fragments),
            registry_sources=registry,
            facts=tuple(facts),
            sentences=program.sentences,
        )


def test_v2_program_sentence인용_세열은_실제fragment순서와_exact여야한다() -> None:
    from src.features.composer.port import VerifiedProgramEvidence
    from src.shared.report_quality.source_identity import (
        bound_source_fragment_provenance,
    )

    program = _actual_v2_program()
    target_index = next(
        index
        for index, fact in enumerate(program.facts)
        if len(fact.supporting_source_ids) > 1
    )
    fact = program.facts[target_index]
    source_id = fact.supporting_source_ids[0]
    fragment_index = next(
        index
        for index, fragment in enumerate(program.source_fragments)
        if fragment.bound_source.source_id == source_id
    )
    fragment = program.source_fragments[fragment_index]
    source = fragment.bound_source
    assert type(source) is Source
    second_hash = "f" * 64
    replacement = seal_collected_source(
        replace(
            source,
            exact_evidence_hashes=[*source.exact_evidence_hashes, second_hash],
            provenance_seal="",
        )
    )
    replacement_fragment = replace(
        fragment,
        bound_source=replacement,
        **bound_source_fragment_provenance(replacement),
    )
    fragments = list(program.source_fragments)
    fragments[fragment_index] = replacement_fragment
    registry = tuple(
        replacement if item.source_id == source.source_id else item
        for item in program.registry_sources
    )
    hashes = list(fact.supporting_evidence_hashes)
    hashes[0] = second_hash
    forged_fact = replace(
        fact,
        supporting_evidence_hashes=hashes,
        evidence_binding="",
    )
    forged_fact = replace(
        forged_fact,
        evidence_binding=fact_evidence_binding(forged_fact),
    )
    facts = list(program.facts)
    facts[target_index] = forged_fact

    with pytest.raises(ValueError, match="공개 문장과 사실·인용 결속"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=tuple(fragments),
            registry_sources=registry,
            facts=tuple(facts),
            sentences=program.sentences,
        )


def test_v2_program은_claim만_바꿔_자기서명한_사실을_거절한다() -> None:
    from src.features.composer.port import VerifiedProgramEvidence
    from src.shared.report_quality.comparison_claims import (
        comparison_context_claim_problems,
    )

    program = _actual_v2_program()
    fact = program.facts[0]
    forged = replace(
        fact,
        claim="근거 없이 세계 1위다",
        evidence_binding="",
    )
    forged = replace(forged, evidence_binding=fact_evidence_binding(forged))
    sentences = tuple(
        replace(sentence, text=forged.claim)
        if sentence.verified_fact_id == fact.fact_id
        else sentence
        for sentence in program.sentences
    )

    assert any(
        "구조 필드의 정본" in problem
        for problem in comparison_context_claim_problems(forged)
    )
    with pytest.raises(ValueError, match="claim·근거어·원문 결속"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=program.source_fragments,
            registry_sources=program.registry_sources,
            facts=(forged, *program.facts[1:]),
            sentences=sentences,
        )


def test_v2_program은_원문단어를_비교법인으로_바꿔_자기서명할수없다() -> None:
    from src.features.composer.port import VerifiedProgramEvidence
    from src.shared.comparison_candidate_basis import (
        comparison_source_candidate_support_terms,
    )
    from src.shared.report_quality.comparison_claims import (
        comparison_context_claim_problems,
        expected_comparison_context_claim,
    )

    program = _actual_v2_program()
    fact_index = next(
        index
        for index, fact in enumerate(program.facts)
        if fact.claim_slot == "competitive_position:comparison_target"
    )
    fact = program.facts[fact_index]
    changed = replace(
        fact,
        comparison_target="경쟁",
        evidence_support_terms=list(
            comparison_source_candidate_support_terms(fact.state_evidence, "경쟁")
        ),
        evidence_binding="",
    )
    changed = replace(
        changed,
        claim=expected_comparison_context_claim(changed),
        evidence_binding="",
    )
    changed = replace(changed, evidence_binding=fact_evidence_binding(changed))
    assert comparison_context_claim_problems(changed) == ()
    facts = list(program.facts)
    facts[fact_index] = changed
    sentences = tuple(
        replace(sentence, text=changed.claim)
        if sentence.verified_fact_id == fact.fact_id
        else sentence
        for sentence in program.sentences
    )

    with pytest.raises(ValueError, match="비교 대상과 비교사 공식 Source"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=program.source_fragments,
            registry_sources=program.registry_sources,
            facts=tuple(facts),
            sentences=sentences,
        )


def test_v2_program은_비교사_state를_자사원문으로_바꿔_자기서명할수없다() -> None:
    from src.features.composer.port import VerifiedProgramEvidence

    program = _actual_v2_program()
    fact_index = next(
        index
        for index, fact in enumerate(program.facts)
        if fact.claim_type == "competitive_comparison"
    )
    fact = program.facts[fact_index]
    changed = replace(
        fact,
        comparator_state_evidence=fact.state_evidence,
        evidence_binding="",
    )
    changed = replace(changed, evidence_binding=fact_evidence_binding(changed))
    facts = list(program.facts)
    facts[fact_index] = changed

    with pytest.raises(ValueError, match="비교사 state_evidence"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=program.source_fragments,
            registry_sources=program.registry_sources,
            facts=tuple(facts),
            sentences=program.sentences,
        )


def test_v2_program은_DART원문과_다른_비교사원값을_일관재계산해도_거절한다() -> None:
    from src.features.composer.port import VerifiedProgramEvidence
    from src.shared.report_quality.comparison_claims import (
        comparison_profitability_claim,
    )
    from src.shared.report_quality.comparison_numeric import (
        comparison_numeric_problems,
    )

    program = _actual_v2_program()
    fact_index = next(
        index
        for index, fact in enumerate(program.facts)
        if fact.comparison_metric == "영업이익률"
        and fact.claim_type == "competitive_comparison"
    )
    fact = program.facts[fact_index]
    claim = comparison_profitability_claim(
        comparison_target=fact.comparison_target,
        difference="7.5",
        direction="높았다",
    )
    changed = replace(
        fact,
        raw_value="2000;200;1000;25",
        claim=claim,
        display_value="자사 10.0%; 비교사 2.5%; 차이 7.5%p",
        numeric_checks=[
            "2000|1|0|2000",
            "200|1|0|200",
            "1000|1|0|1000",
            "25|1|0|25",
            "200|20|1|10.0",
            "25|10|1|2.5",
            "7.5|1|1|7.5",
        ],
        evidence_binding="",
    )
    changed = replace(changed, evidence_binding=fact_evidence_binding(changed))
    numeric_problems = comparison_numeric_problems(changed)
    assert numeric_problems is not None
    assert "공식 비교 raw 원값이 양사 DART 계정 행의 당기금액과 다릅니다" in numeric_problems
    facts = list(program.facts)
    facts[fact_index] = changed
    sentences = tuple(
        replace(sentence, text=changed.claim)
        if sentence.verified_fact_id == fact.fact_id
        else sentence
        for sentence in program.sentences
    )

    with pytest.raises(ValueError, match="DART 계정 행"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=program.source_fragments,
            registry_sources=program.registry_sources,
            facts=tuple(facts),
            sentences=sentences,
        )


def test_v2_program은_실제수치축과_다른_metric맥락을_거절한다() -> None:
    from src.features.composer.port import VerifiedProgramEvidence
    from src.shared.report_quality.comparison_claims import (
        expected_comparison_context_claim,
    )

    program = _actual_v2_program()
    fact_index = next(
        index
        for index, fact in enumerate(program.facts)
        if fact.claim_slot == "competitive_position:comparison_metric"
    )
    fact = program.facts[fact_index]
    changed = replace(
        fact,
        comparison_metric="매출액·영업이익",
        evidence_binding="",
    )
    changed = replace(
        changed,
        claim=expected_comparison_context_claim(changed),
        evidence_binding="",
    )
    changed = replace(changed, evidence_binding=fact_evidence_binding(changed))
    facts = list(program.facts)
    facts[fact_index] = changed
    sentences = tuple(
        replace(sentence, text=changed.claim)
        if sentence.verified_fact_id == fact.fact_id
        else sentence
        for sentence in program.sentences
    )

    with pytest.raises(ValueError, match="실제 수치 Fact 지표 집합"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=program.source_fragments,
            registry_sources=program.registry_sources,
            facts=tuple(facts),
            sentences=sentences,
        )


def test_v2_program은_미사용_fragment를_남긴채_대상Fact를_지울수없다() -> None:
    from src.features.composer.port import VerifiedProgramEvidence

    program = _actual_v2_program()
    target_fact = next(
        fact
        for fact in program.facts
        if fact.claim_slot == "competitive_position:comparison_target"
    )
    facts = tuple(fact for fact in program.facts if fact is not target_fact)
    sentences = tuple(
        sentence
        for sentence in program.sentences
        if sentence.verified_fact_id != target_fact.fact_id
    )

    with pytest.raises(ValueError, match="원문 조각이 공개 Fact·문장 인용과 정확히"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=program.source_fragments,
            registry_sources=program.registry_sources,
            facts=facts,
            sentences=sentences,
        )


def test_v2_program은_fact_id만_바꾼_동일수치사실로_분량을_부풀릴수없다() -> None:
    from src.features.composer.port import VerifiedProgramEvidence

    program = _actual_v2_program()
    fact = next(
        item
        for item in program.facts
        if item.comparison_metric == "매출 규모"
        and item.claim_type == "competitive_comparison"
    )
    cloned = replace(fact, fact_id=fact.fact_id + "-clone", evidence_binding="")
    cloned = replace(cloned, evidence_binding=fact_evidence_binding(cloned))
    sentence = next(
        item for item in program.sentences if item.verified_fact_id == fact.fact_id
    )
    cloned_sentence = replace(sentence, verified_fact_id=cloned.fact_id)

    with pytest.raises(ValueError, match="같은 의미 사실을 중복"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=program.source_fragments,
            registry_sources=program.registry_sources,
            facts=(*program.facts, cloned),
            sentences=(*program.sentences, cloned_sentence),
        )


def _rebind_program_facts(program, facts):
    rebound = tuple(
        replace(
            replace(fact, evidence_binding=""),
            evidence_binding=fact_evidence_binding(replace(fact, evidence_binding="")),
        )
        for fact in facts
    )
    claims_by_id = {fact.fact_id: fact.claim for fact in rebound}
    sentences = tuple(
        replace(sentence, text=claims_by_id[sentence.verified_fact_id])
        if sentence.verified_fact_id in claims_by_id
        else sentence
        for sentence in program.sentences
    )
    return rebound, sentences


@pytest.mark.parametrize(
    ("field_name", "expected_problem"),
    (
        ("comparison_target", "비교 대상과 비교사 공식 Source"),
        ("comparison_metric", "실제 수치 Fact 지표 집합"),
        ("comparison_definition", "계정 정의"),
        ("comparison_basis", "비교 후보 basis"),
        ("comparison_period", "DART의 정확한 계정 행"),
        ("comparison_scope", "comparison_scope"),
        ("comparison_judgment", "comparison_judgment"),
        ("comparison_conditions", "고객·제품·시장 범위"),
    ),
)
def test_v2_program_비교구조_8필드는_공통허위값으로_재봉인해도_거절한다(
    field_name: str,
    expected_problem: str,
) -> None:
    """같은 거짓값을 여러 Fact에 복사해도 '서로 같음'만으로 통과할 수 없다."""

    from src.features.composer.port import VerifiedProgramEvidence
    from src.shared.report_quality.comparison_claims import (
        expected_comparison_context_claim,
    )

    program = _actual_v2_program()
    changed_facts = []
    for fact in program.facts:
        changes: dict[str, object] = {}
        if field_name == "comparison_target":
            changes[field_name] = "경쟁"
        elif field_name == "comparison_metric":
            if fact.claim_slot == "competitive_position:comparison_metric":
                changes[field_name] = "세계시장 점유율"
        elif field_name == "comparison_definition":
            if fact.claim_type == "competitive_comparison_context":
                changes[field_name] = "임의정의"
                conditions = dict(fact.comparison_conditions)
                conditions["self_definition"] = "임의정의"
                conditions["comparator_definition"] = "임의정의"
                changes["comparison_conditions"] = conditions
        elif field_name == "comparison_basis":
            changes[field_name] = "임의 비교 근거"
        elif field_name == "comparison_period":
            changes[field_name] = "2024-01-01~2024-12-31"
            changes["period_start"] = "2024-01-01"
            changes["period_end"] = "2024-12-31"
            conditions = dict(fact.comparison_conditions)
            conditions["self_period"] = "2024-01-01~2024-12-31"
            conditions["comparator_period"] = "2024-01-01~2024-12-31"
            changes["comparison_conditions"] = conditions
        elif field_name == "comparison_scope":
            changes[field_name] = "별도재무제표(OFS)"
            conditions = dict(fact.comparison_conditions)
            conditions["self_accounting_scope"] = "별도재무제표(OFS)"
            conditions["comparator_accounting_scope"] = "별도재무제표(OFS)"
            changes["comparison_conditions"] = conditions
        elif field_name == "comparison_judgment":
            changes[field_name] = "세계 1위"
        else:
            conditions = dict(fact.comparison_conditions)
            conditions.update(
                customer="임의고객",
                product="임의제품",
                market="임의시장",
            )
            changes[field_name] = conditions
        changed = replace(fact, **changes)
        if (
            changed.claim_type == "competitive_comparison_context"
            and field_name in {"comparison_target", "comparison_metric"}
        ):
            expected_claim = expected_comparison_context_claim(changed)
            if expected_claim:
                changed = replace(changed, claim=expected_claim)
        changed_facts.append(changed)
    facts, sentences = _rebind_program_facts(program, changed_facts)

    with pytest.raises(ValueError, match=expected_problem):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=program.source_fragments,
            registry_sources=program.registry_sources,
            facts=facts,
            sentences=sentences,
        )


def test_v2_program은_허용값이어도_수치의미와_다른_judgment를_거절한다() -> None:
    from src.features.composer.port import VerifiedProgramEvidence

    program = _actual_v2_program()
    changed_facts = tuple(
        replace(fact, comparison_judgment="competitive_advantage")
        for fact in program.facts
    )
    facts, sentences = _rebind_program_facts(program, changed_facts)

    with pytest.raises(ValueError, match="comparison_judgment"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=program.source_fragments,
            registry_sources=program.registry_sources,
            facts=facts,
            sentences=sentences,
        )


def test_v2_program은_정상비교문장_뒤에_근거없는절을_붙일수없다() -> None:
    from src.features.composer.port import VerifiedProgramEvidence

    program = _actual_v2_program()
    fact_index = next(
        index
        for index, fact in enumerate(program.facts)
        if fact.claim_type == "competitive_comparison_context"
    )
    fact = program.facts[fact_index]
    forged = replace(
        fact,
        claim=fact.claim + " 그러므로 세계 1위다.",
        evidence_binding="",
    )
    forged = replace(forged, evidence_binding=fact_evidence_binding(forged))
    facts = list(program.facts)
    facts[fact_index] = forged
    sentences = tuple(
        replace(sentence, text=forged.claim)
        if sentence.verified_fact_id == fact.fact_id
        else sentence
        for sentence in program.sentences
    )

    with pytest.raises(ValueError, match="비교 맥락 문장이 구조 필드의 정본"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=program.source_fragments,
            registry_sources=program.registry_sources,
            facts=tuple(facts),
            sentences=sentences,
        )


def test_v2_program은_claim_state_sentence를_함께_바꾼_자기서명을_거절한다() -> None:
    from src.features.composer.port import VerifiedProgramEvidence

    program = _actual_v2_program()
    fact = program.facts[0]
    forged = replace(
        fact,
        claim="근거 없이 세계 1위라고 주장한다",
        state_evidence="근거 없이 세계 1위라는 조작된 원문",
        evidence_support_terms=["근거", "세계 1위"],
        evidence_binding="",
    )
    forged = replace(forged, evidence_binding=fact_evidence_binding(forged))
    sentences = tuple(
        replace(sentence, text=forged.claim)
        if sentence.verified_fact_id == fact.fact_id
        else sentence
        for sentence in program.sentences
    )

    with pytest.raises(ValueError, match="state_evidence가 Source 원문 등록부"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=program.source_fragments,
            registry_sources=program.registry_sources,
            facts=(forged, *program.facts[1:]),
            sentences=sentences,
        )


@pytest.mark.parametrize("column", ("ids", "identities", "hashes"))
def test_v2_program_supporting세열의_extra는_zip으로_숨길수없다(column: str) -> None:
    from src.features.composer.port import VerifiedProgramEvidence

    program = _actual_v2_program()
    fact = program.facts[0]
    changes: dict[str, object] = {}
    if column == "ids":
        changes["supporting_source_ids"] = [*fact.supporting_source_ids, "extra"]
    elif column == "identities":
        changes["supporting_source_identities"] = [
            *fact.supporting_source_identities,
            "url:https://extra.example/",
        ]
    else:
        changes["supporting_evidence_hashes"] = [
            *fact.supporting_evidence_hashes,
            "e" * 64,
        ]
    forged = replace(fact, **changes, evidence_binding="")
    forged = replace(forged, evidence_binding=fact_evidence_binding(forged))

    with pytest.raises(ValueError, match="다중 출처 결속"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=program.source_fragments,
            registry_sources=program.registry_sources,
            facts=(forged, *program.facts[1:]),
            sentences=program.sentences,
        )


def test_v2_program_fragment의_문서identity만_바꿔도_즉시_거절한다() -> None:
    from src.features.composer.port import VerifiedProgramEvidence

    program = _actual_v2_program()
    forged = replace(
        program.source_fragments[0],
        document_identity="url:https://forged.example/other-doc",
    )

    with pytest.raises(ValueError, match="Source provenance"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=(forged, *program.source_fragments[1:]),
            registry_sources=program.registry_sources,
            facts=program.facts,
            sentences=program.sentences,
        )


def test_v2_program은_같은장_다른slot_fragment로_Fact를_증명할수없다() -> None:
    from src.features.composer.port import VerifiedProgramEvidence

    program = _actual_v2_program()
    fact = next(
        item
        for item in program.facts
        if item.claim_slot == "competitive_position:comparison_target"
    )
    sentence = next(
        item for item in program.sentences if item.verified_fact_id == fact.fact_id
    )
    fragment_id = sentence.citations[0]
    fragment_index = next(
        index
        for index, item in enumerate(program.source_fragments)
        if item.fragment_id == fragment_id
    )
    forged = replace(
        program.source_fragments[fragment_index],
        supported_claim_slots=("competitive_position:comparison_basis",),
    )
    fragments = list(program.source_fragments)
    fragments[fragment_index] = forged

    with pytest.raises(ValueError, match="공개 문장과 사실·인용 결속"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=tuple(fragments),
            registry_sources=program.registry_sources,
            facts=program.facts,
            sentences=program.sentences,
        )


def test_v2_program_registry는_bound_citation과_직접attester로만_닫힌다() -> None:
    from src.features.composer.port import VerifiedProgramEvidence

    program = _actual_v2_program()
    citation = next(
        source for source in program.registry_sources if source.provenance_role == "citation"
    )
    attester = next(
        source
        for source in program.registry_sources
        if source.provenance_role == "attestation_only"
    )
    unused_citation = seal_collected_source(
        replace(
            citation,
            number=999,
            source_id="unused-citation",
            provenance_seal="",
        )
    )
    with pytest.raises(ValueError, match="citation이 실제 인용"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=program.source_fragments,
            registry_sources=(*program.registry_sources, unused_citation),
            facts=program.facts,
            sentences=program.sentences,
        )

    unused_attester = seal_collected_source(
        replace(
            attester,
            number=998,
            source_id="unused-attester",
            provenance_seal="",
        )
    )
    with pytest.raises(ValueError, match="attester가 직접 참조"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=program.source_fragments,
            registry_sources=(*program.registry_sources, unused_attester),
            facts=program.facts,
            sentences=program.sentences,
        )

    without_attester = tuple(
        source for source in program.registry_sources if source is not attester
    )
    with pytest.raises(ValueError, match="attester가 직접 참조"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=program.source_fragments,
            registry_sources=without_attester,
            facts=program.facts,
            sentences=program.sentences,
        )

    with pytest.raises(ValueError, match="중복"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=program.source_fragments,
            registry_sources=(*program.registry_sources, attester),
            facts=program.facts,
            sentences=program.sentences,
        )


def test_v2_program의_공식웹_후보는_DART도메인attester를_직접가리켜야한다() -> None:
    from src.features.composer.port import VerifiedProgramEvidence
    from src.shared.report_quality.comparison_basis import (
        comparison_basis_v2_problems,
    )
    from src.shared.report_quality.source_identity import (
        bound_source_fragment_provenance,
    )

    program = _actual_v2_program()
    target_fact = next(
        fact
        for fact in program.facts
        if fact.claim_slot == "competitive_position:comparison_target"
    )
    fragment_index = next(
        index
        for index, fragment in enumerate(program.source_fragments)
        if fragment.bound_source.source_id == target_fact.source_id
    )
    fragment = program.source_fragments[fragment_index]
    source = fragment.bound_source
    assert type(source) is Source
    assert source.kind is SourceKind.OTHER
    assert source.domain_attestation_source_id
    unbound_source = seal_collected_source(
        replace(
            source,
            domain_attestation_source_id="",
            domain_attestation_evidence="",
            provenance_seal="",
        )
    )
    unbound_fragment = replace(
        fragment,
        bound_source=unbound_source,
        **bound_source_fragment_provenance(unbound_source),
    )
    fragments = list(program.source_fragments)
    fragments[fragment_index] = unbound_fragment
    registry = tuple(
        unbound_source if item.source_id == source.source_id else item
        for item in program.registry_sources
    )
    assert "비교 후보의 직접 DART 법인 attester 결속이 다릅니다" in (
        comparison_basis_v2_problems(
            program.facts[0],
            {item.source_id: item for item in registry},
        )
    )

    with pytest.raises(ValueError, match="공식 출처 등록부 계약"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=tuple(fragments),
            registry_sources=registry,
            facts=program.facts,
            sentences=program.sentences,
        )


def test_v2_program은_wrong_role과_chained_attester를_거절한다() -> None:
    from src.features.composer.port import VerifiedProgramEvidence
    from src.shared.report_quality.source_identity import (
        bound_source_fragment_provenance,
    )

    program = _actual_v2_program()
    fragment = program.source_fragments[0]
    citation = fragment.bound_source
    assert type(citation) is Source
    wrong_role = seal_collected_source(
        replace(citation, provenance_role="attestation_only", provenance_seal="")
    )
    wrong_fragment = replace(
        fragment,
        bound_source=wrong_role,
        **bound_source_fragment_provenance(wrong_role),
    )
    wrong_registry = tuple(
        wrong_role if source.source_id == citation.source_id else source
        for source in program.registry_sources
    )
    with pytest.raises(ValueError, match="citation이 실제 인용"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=(wrong_fragment, *program.source_fragments[1:]),
            registry_sources=wrong_registry,
            facts=program.facts,
            sentences=program.sentences,
        )

    attester = next(
        source
        for source in program.registry_sources
        if source.provenance_role == "attestation_only"
    )
    chained = seal_collected_source(
        replace(
            attester,
            domain_attestation_source_id=attester.source_id,
            provenance_seal="",
        )
    )
    chained_registry = tuple(
        chained if source.source_id == attester.source_id else source
        for source in program.registry_sources
    )
    with pytest.raises(ValueError, match="연쇄 참조"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=program.source_fragments,
            registry_sources=chained_registry,
            facts=program.facts,
            sentences=program.sentences,
        )


def test_v2_program_fragment와_Source의_formal상태가_양방향으로_같아야한다() -> None:
    from src.features.composer.port import VerifiedProgramEvidence
    from src.shared.report_evidence.constants import SOURCE_KIND_OFFICIAL_WEB_PAGE
    from src.shared.report_quality.source_identity import (
        bound_source_fragment_provenance,
    )

    program = _actual_v2_program()
    fragment = program.source_fragments[0]
    source = fragment.bound_source
    assert type(source) is Source

    fragment_only_formal = replace(
        fragment,
        formal_source_kind=SOURCE_KIND_OFFICIAL_WEB_PAGE,
        source_document_id=source.document_id,
        source_publisher=source.publisher,
        identity_binding="typed-binding",
        source_collected_on=source.collected_at,
        document_content_sha256="a" * 64,
    )
    with pytest.raises(ValueError, match="Source provenance"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=(fragment_only_formal, *program.source_fragments[1:]),
            registry_sources=program.registry_sources,
            facts=program.facts,
            sentences=program.sentences,
        )

    source_only_formal = seal_collected_source(
        replace(
            source,
            formal_source_kind=SOURCE_KIND_OFFICIAL_WEB_PAGE,
            identity_binding="typed-binding",
            document_content_sha256="a" * 64,
            provenance_seal="",
        )
    )
    source_registry = tuple(
        source_only_formal if item.source_id == source.source_id else item
        for item in program.registry_sources
    )
    with pytest.raises(ValueError, match="Source provenance"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=(
                replace(fragment, bound_source=source_only_formal),
                *program.source_fragments[1:],
            ),
            registry_sources=source_registry,
            facts=program.facts,
            sentences=program.sentences,
        )

    invalid_formal_source = seal_collected_source(
        replace(
            source_only_formal,
            source_type="날조된 공식 종류",
            provenance_seal="",
        )
    )
    invalid_formal_registry = tuple(
        invalid_formal_source if item.source_id == source.source_id else item
        for item in program.registry_sources
    )
    invalid_formal_fragment = replace(
        fragment,
        bound_source=invalid_formal_source,
        **bound_source_fragment_provenance(invalid_formal_source),
    )
    with pytest.raises(ValueError, match="공식 출처 등록부 계약"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=(invalid_formal_fragment, *program.source_fragments[1:]),
            registry_sources=invalid_formal_registry,
            facts=program.facts,
            sentences=program.sentences,
        )


def test_일반_nonprogram_packet은_bound_Source없이_기존동작을_유지한다() -> None:
    packets = _v2_packet_set_for_bridge()

    assert all(
        fragment.bound_source is None
        for packet in packets.packets
        for fragment in packet.fragments
    )


def test_v2_bridge의_양사_fact와_source는_실제_renderer와_수치검산까지_간다() -> None:
    from src.features.company_comparison.v2_bridge import (
        attach_comparison_program_evidence,
    )
    from src.features.composer.constants import SECTION_IDS
    from src.features.composer.logic import _prepare_section_evidence_packets
    from src.features.composer.port import ComposedReport, ComposedSection
    from src.features.composer.quality_projection import _claim_fact
    from src.features.composer.quality_projection import build_generation_quality_candidate
    from src.features.composer.public_manifest import (
        assert_report_matches_public_structure,
        build_public_structure_seal,
    )
    from src.features.composer.render import render_report
    from src.shared.report_quality.comparison_numeric import (
        comparison_numeric_problems,
    )
    from src.shared.report_quality.assessment import assess_safety
    from src.shared.report_quality.contract import contract_for_generation
    from src.shared.report_quality.constants import STRICT_QUALITY_CONTRACT_VERSION

    packet_set = attach_comparison_program_evidence(
        _v2_packet_set_for_bridge(),
        _v2_comparison_result(),
    )
    prepared = _prepare_section_evidence_packets(packet_set)
    report = ComposedReport(
        sections=tuple(
            ComposedSection(
                section_id=section_id,
                sentences=tuple(
                    prepared.program_evidence_by_section[section_id].sentences
                    if section_id in prepared.program_evidence_by_section
                    else ()
                ),
            )
            for section_id in SECTION_IDS
        )
    )
    seal = build_public_structure_seal(
        report,
        prepared.flat_union,
        None,
        filing_meta=None,
        composition_tables=(),
        table_presentation="table",
        company_id=packet_set.company_id,
        evidence_generation_sha256=packet_set.evidence_generation_sha256,
        evidence_packet_sha256s=packet_set.packet_sha256s,
        company_name="주식회사 알파",
        corp_type="상장사",
        generated_at="",
        as_of_date="",
        analysis_period="",
        latest_performance_period="",
        citation_style="inline",
        program_registry_sources=prepared.program_sources,
    )
    rendered = render_report(
        "주식회사 알파",
        report,
        prepared.flat_union,
        None,
        corp_type="상장사",
        grade=Grade.COMPLETE,
        citation_style="inline",
        public_structure_seal=seal,
        company_id=packet_set.company_id,
        release_mode="FULL",
        verified_program_facts=prepared.program_facts,
        program_registry_sources=prepared.program_sources,
    )
    assert_report_matches_public_structure(rendered, seal)

    assert {fact.fact_id for fact in rendered.fact_records} == {
        fact.fact_id for fact in prepared.program_facts
    }
    assert {source.publisher for source in rendered.citations} >= {
        "주식회사 알파",
        "주식회사 베타",
    }
    numeric = tuple(
        fact for fact in rendered.fact_records if fact.claim_type == "competitive_comparison"
    )
    assert numeric
    assert all(comparison_numeric_problems(_claim_fact(fact)) == () for fact in numeric)
    safety = assess_safety(
        build_generation_quality_candidate(rendered, report),
        contract_for_generation(STRICT_QUALITY_CONTRACT_VERSION),
    )
    assert safety.problems == ()


def _v2_english_comparison_result(
    evidence: str = "Beta competes with us.",
):
    _bound, sources, rows = _v2_official_registry(
        evidence,
        profile_name="Alpha",
        company_name="Alpha",
    )
    base = replace(
        _report("Alpha supplies semiconductor inspection equipment."),
        company="Alpha",
    )
    result = build_competitive_position(
        base,
        self_bundle=_bundle("00000001", "Alpha", scale=2),
        catalog=(
            DartCompanyRecord("00000001", "Alpha"),
            DartCompanyRecord("00000002", "Beta"),
            DartCompanyRecord("00000003", "Gamma"),
        ),
        fetch_comparator=lambda _record: _bundle(
            "00000002", "Beta", operating_amount=50
        ),
        collected_on="2026-08-22",
        official_candidate_sentences=rows,
        candidate_source_registry=sources,
    )
    return base, result


def test_공식_HTML_IR_순수경쟁문장은_v2로_1_8장과_독립결속된다() -> None:
    _bound, sources, rows = _v2_official_registry()
    candidates = discover_official_source_candidates(
        rows,
        sources,
        CATALOG,
        self_corp_code="00000001",
        self_company="주식회사 알파",
    )

    assert len(candidates) == 1
    assert candidates[0].candidate_corp_code == "00000002"
    assert candidates[0].evidence_fact_id == ""
    assert candidates[0].overlap_dimension == "경쟁 관계 명시"

    result = build_competitive_position(
        _report("알파는 반도체 검사장비를 전자 제조 고객 시장에 공급한다."),
        self_bundle=_bundle("00000001", "주식회사 알파", scale=2),
        catalog=CATALOG,
        fetch_comparator=lambda record: (
            _bundle("00000002", "주식회사 베타", operating_amount=50)
            if record.corp_code == "00000002"
            else None
        ),
        collected_on="2026-08-22",
        official_candidate_sentences=rows,
        candidate_source_registry=sources,
    )
    payload = parse_comparison_source_basis_v2(result.facts[0].comparison_basis)
    assert payload is not None
    assert payload["version"] == COMPARISON_SOURCE_BASIS_VERSION
    assert payload["self_corp_code"] == "00000001"
    assert payload["source_type"] == "회사 공식 웹"
    assert payload["source_location"] == "/ir/competition"
    assert {source.provenance_role for source in result.sources} >= {
        "citation",
        "attestation_only",
    }

    all_sources = {
        source.source_id: source
        for source in [*_report().citations, *result.sources]
    }
    all_facts = {
        fact.fact_id: fact for fact in [*_report().fact_records, *result.facts]
    }
    assert _fact_problems(
        result.facts[0], all_sources, all_facts, set()
    ) == []


def test_공식_IR_PDF는_DART_domain에_결속해도_문서일없이는_후보가_아니다() -> None:
    bound, sources, rows = _v2_official_registry(fragment_kind="공식 IR")

    assert bound.attester is not None
    assert bound.fragments[21]["도메인근거SourceID"] == bound.attester.source_id
    candidate_source = next(source for source in sources if source.number == 21)
    assert candidate_source.source_type == "회사 공식 IR"
    assert candidate_source.collected_at == "2026-08-22"
    assert not candidate_source.published_at
    assert not candidate_source.disclosed_at
    assert discover_official_source_candidates(
        rows,
        sources,
        CATALOG,
        self_corp_code="00000001",
        self_company="주식회사 알파",
    ) == ()

    old_source = seal_collected_source(
        replace(candidate_source, published_at="2015-03-01")
    )
    old_sources = tuple(
        old_source if source.source_id == old_source.source_id else source
        for source in sources
    )
    old_rows = tuple(
        replace(row, source=old_source)
        if row.source.source_id == old_source.source_id
        else row
        for row in rows
    )
    assert discover_official_source_candidates(
        old_rows,
        old_sources,
        CATALOG,
        self_corp_code="00000001",
        self_company="주식회사 알파",
    ) == ()


@pytest.mark.parametrize(
    "profile_url", ["http://www.alpha.example", "www.alpha.example"]
)
def test_DART_hm_url이_http나_scheme없는값이어도_검증된_HTTPS원문에_결속한다(
    profile_url: str,
) -> None:
    bound, sources, rows = _v2_official_registry(
        profile_url=profile_url
    )

    assert bound.attester is not None
    assert json.loads(bound.attester.domain_attestation_evidence)["hm_url"] == (
        profile_url
    )
    assert bound.fragments[21]["도메인근거SourceID"] == bound.attester.source_id
    assert discover_official_source_candidates(
        rows,
        sources,
        CATALOG,
        self_corp_code="00000001",
        self_company="주식회사 알파",
    )


@pytest.mark.parametrize(
    ("evidence", "url"),
    [
        ("베타는 경쟁사다.", "https://www.alpha.example/ir/competition"),
        ("당사의 시장점유율은 베타와 함께 집계된다.", "https://www.alpha.example/ir/share"),
        ("당사는 베타와 경쟁 관계에 있다.", "https://www.alpha.example/blog/competition"),
        ("당사는 베타와 경쟁 관계에 있다.", "https://www.alpha.example/search?q=beta"),
        ("당사는 베타와 경쟁 관계에 있다.", "http://www.alpha.example/ir/competition"),
        ("당사는 베타와 경쟁 관계에 있다.", "https://ir.alpha.example/competition"),
    ],
)
def test_v2는_자사주어_명시경쟁표지_exact_https_host와_비검색페이지만_허용한다(
    evidence: str,
    url: str,
) -> None:
    _bound, sources, rows = _v2_official_registry(evidence, url=url)

    assert discover_official_source_candidates(
        rows,
        sources,
        CATALOG,
        self_corp_code="00000001",
        self_company="주식회사 알파",
    ) == ()


@pytest.mark.parametrize(
    ("profile_name", "profile_code"),
    [
        ("알파", "00000001"),
        ("주식회사 알파", "00000009"),
    ],
)
def test_DART_profile_attester는_exact_법인명과_self_corp_code가_필수다(
    profile_name: str,
    profile_code: str,
) -> None:
    bound, sources, rows = _v2_official_registry(
        profile_name=profile_name,
        profile_code=profile_code,
    )

    assert bound.attester is None or discover_official_source_candidates(
        rows,
        sources,
        CATALOG,
        self_corp_code="00000001",
        self_company="주식회사 알파",
    ) == ()


def test_v2_attester가_등록부에서_빠지면_출고_역검산이_닫힌다() -> None:
    _bound, sources, rows = _v2_official_registry()
    result = build_competitive_position(
        _report("알파는 반도체 검사장비를 전자 제조 고객 시장에 공급한다."),
        self_bundle=_bundle("00000001", "주식회사 알파", scale=2),
        catalog=CATALOG,
        fetch_comparator=lambda _record: _bundle(
            "00000002", "주식회사 베타", operating_amount=50
        ),
        collected_on="2026-08-22",
        official_candidate_sentences=rows,
        candidate_source_registry=sources,
    )
    payload = parse_comparison_source_basis_v2(result.facts[0].comparison_basis)
    assert payload is not None
    without_attester = {
        source.source_id: source
        for source in [*_report().citations, *result.sources]
        if source.source_id != payload["self_attestation_source_id"]
    }

    errors = _fact_problems(
        result.facts[0],
        without_attester,
        {fact.fact_id: fact for fact in [*_report().fact_records, *result.facts]},
        set(),
    )

    assert any("attester" in error or "공식 자료" in error for error in errors)


def test_DART_profile_hm_url이_비어도_공시_v2_후보는_독립적으로_통과한다() -> None:
    evidence = "당사는 베타와 경쟁 관계에 있다."
    fragments = register_candidate_sentence_evidence(
        {7: {"종류": "경쟁현황", "원문": evidence}}
    )
    bound = bind_dart_profile_attestation(
        fragments,
        profile={
            "status": "000",
            "corp_code": "00000001",
            "corp_name": "주식회사 알파",
            "hm_url": "",
        },
        corp_code="00000001",
        company_name="주식회사 알파",
        collected_on="2026-08-22",
    )
    sources = build_citations(
        bound.fragments,
        filing={
            "report_nm": "사업보고서 (2025.12)",
            "rcept_dt": "20260315",
            "rcept_no": "20260315000001",
            "corp_name": "주식회사 알파",
        },
        collected_on=date(2026, 8, 22),
        company_publisher="주식회사 알파",
    )
    assert bound.attester is not None
    sources.append(bound.attester)
    rows = candidate_sentences_from_fragments(bound.fragments, sources)

    candidates = discover_official_source_candidates(
        rows,
        sources,
        CATALOG,
        self_corp_code="00000001",
        self_company="주식회사 알파",
    )

    assert len(candidates) == 1
    assert candidates[0].source is not None
    assert candidates[0].source.kind is SourceKind.FILING
    attestation = json.loads(bound.attester.domain_attestation_evidence)
    assert attestation == {
        "corp_code": "00000001",
        "corp_name": "주식회사 알파",
        "hm_url": "",
    }


@pytest.mark.parametrize("response_code", ["", "00000009"])
def test_DART_profile_응답_corp_code가_누락되거나_호출값과_다르면_거부한다(
    response_code: str,
) -> None:
    bound, sources, rows = _v2_official_registry(response_code=response_code)

    assert bound.attester is None
    assert discover_official_source_candidates(
        rows,
        sources,
        CATALOG,
        self_corp_code="00000001",
        self_company="주식회사 알파",
    ) == ()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_type", "공식 IR"),
        ("source_publisher", "주식회사 감마"),
        ("source_host", "evil.example"),
        ("source_url", "https://www.alpha.example/ir/other"),
        ("source_document_id", "/ir/other"),
        ("source_location", "/ir/other"),
        ("source_date", "2026-08-21"),
        ("candidate_name", "주식회사 감마"),
        ("self_corp_code", "00000009"),
        ("self_attestation_source_id", "dart-company-profile-00000009"),
        (
            "self_attestation_evidence",
            '{"corp_code":"00000001","corp_name":"주식회사 감마","hm_url":"www.alpha.example"}',
        ),
        ("evidence_text", "당사는 베타와 협력 관계에 있다."),
        ("evidence_sha256", "a" * 64),
        ("evidence_exact_sha256", "b" * 64),
        ("overlap_dimension", "시장 겹침"),
    ],
)
def test_publish_재검산은_v2_메타데이터_문장_해시_신원_변조를_닫는다(
    field: str,
    value: str,
) -> None:
    result = _v2_comparison_result()
    target = result.facts[0]
    payload = json.loads(target.comparison_basis)
    payload[field] = value
    changed = replace(
        target,
        comparison_basis=json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    source_registry = {
        source.source_id: source
        for source in [*_report().citations, *result.sources]
    }
    fact_registry = {
        fact.fact_id: fact for fact in [*_report().fact_records, changed]
    }

    assert _fact_problems(changed, source_registry, fact_registry, set())


def test_v2는_casefold_충돌이어도_Source와_basis의_exact_원문변조를_닫는다() -> None:
    lower = "Beta competes with us."
    upper = "Beta competes with US."
    assert comparison_evidence_sha256(lower) == comparison_evidence_sha256(upper)
    assert comparison_evidence_exact_sha256(lower) != (
        comparison_evidence_exact_sha256(upper)
    )

    base, result = _v2_english_comparison_result()
    target = result.facts[0]
    payload = parse_comparison_source_basis_v2(target.comparison_basis)
    assert payload is not None
    assert payload["evidence_text"] == lower
    sources = {
        source.source_id: source for source in [*base.citations, *result.sources]
    }
    candidate_source = sources[payload["candidate_source_id"]]
    # 정규화 해시는 같은 상태에서 Source가 보존한 raw 원문만 대문자로 바꾼다.
    sources[candidate_source.source_id] = seal_collected_source(
        replace(
            candidate_source,
            exact_evidence_hashes=[comparison_evidence_exact_sha256(upper)],
        )
    )
    assert has_valid_provenance_seal(sources[candidate_source.source_id])

    errors = _fact_problems(
        target,
        sources,
        {fact.fact_id: fact for fact in [*base.fact_records, target]},
        set(),
    )
    assert any("byte-exact" in error for error in errors)


def test_publish는_경쟁술어_밖의_법인으로_재봉인한_v2_basis를_거부한다() -> None:
    base, result = _v2_english_comparison_result(
        "We compete with Beta for Gamma's business."
    )
    target = result.facts[0]
    payload = json.loads(target.comparison_basis)
    payload["candidate_corp_code"] = "00000003"
    payload["candidate_name"] = "Gamma"
    changed = replace(
        target,
        comparison_target="Gamma",
        comparison_basis=json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    errors = _fact_problems(
        changed,
        {
            source.source_id: source
            for source in [*base.citations, *result.sources]
        },
        {fact.fact_id: fact for fact in [*base.fact_records, changed]},
        set(),
    )

    assert any("봉인된 공식 원문 한 문장" in error for error in errors)


def test_publish는_비현재_경쟁사_수식어로_재봉인한_v2_basis를_거부한다() -> None:
    base, result = _v2_english_comparison_result(
        "Beta is our principal competitor."
    )
    target = result.facts[0]
    payload = json.loads(target.comparison_basis)
    invalid = "Beta is our potential competitor."
    payload.update(
        {
            "evidence_text": invalid,
            "evidence_sha256": comparison_evidence_sha256(invalid),
            "evidence_exact_sha256": comparison_evidence_exact_sha256(invalid),
        }
    )
    changed = replace(
        target,
        comparison_basis=json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    sources = {
        source.source_id: source for source in [*base.citations, *result.sources]
    }
    candidate_source = sources[payload["candidate_source_id"]]
    sources[candidate_source.source_id] = seal_collected_source(
        replace(
            candidate_source,
            evidence_hashes=[comparison_evidence_sha256(invalid)],
            exact_evidence_hashes=[comparison_evidence_exact_sha256(invalid)],
        )
    )

    errors = _fact_problems(
        changed,
        sources,
        {fact.fact_id: fact for fact in [*base.fact_records, changed]},
        set(),
    )
    assert any("봉인된 공식 원문 한 문장" in error for error in errors)
    assert any("명시적 경쟁 관계 표지" in error for error in errors)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda source: replace(
            source,
            publisher="주식회사 감마",
        ),
        lambda source: replace(
            source,
            host="dart.fss.or.kr",
            url="https://dart.fss.or.kr/api/company.json?corp_code=00000001",
        ),
        lambda source: replace(
            source,
            document_id="00000009",
            url="https://opendart.fss.or.kr/api/company.json?corp_code=00000009",
        ),
        lambda source: replace(source, provenance_role="citation"),
    ],
)
def test_v2_attester는_재봉인해도_역할_endpoint_법인신원_변조를_닫는다(
    mutate,
) -> None:
    result = _v2_comparison_result()
    target = result.facts[0]
    payload = parse_comparison_source_basis_v2(target.comparison_basis)
    assert payload is not None
    sources = {
        source.source_id: source
        for source in [*_report().citations, *result.sources]
    }
    attester = sources[payload["self_attestation_source_id"]]
    sources[attester.source_id] = seal_collected_source(mutate(attester))

    assert _fact_problems(
        target,
        sources,
        {fact.fact_id: fact for fact in [*_report().fact_records, target]},
        set(),
    )


@pytest.mark.parametrize(
    "sentence",
    [
        "Beta is our principal competitor.",
        "We compete directly with Beta.",
        "We actively compete with Beta.",
        "Beta competes with us.",
        "Our competitors include Beta.",
        "Alpha competes with Beta.",
        "Beta competes with Alpha.",
        "당사는 베타와 경쟁합니다.",
    ],
)
def test_v2_영문_명시경쟁표지와_self_published_자사대명사를_허용한다(
    sentence: str,
) -> None:
    assert comparison_source_sentence_has_marker(sentence)
    assert comparison_source_sentence_has_self_subject(sentence, "Alpha")


@pytest.mark.parametrize(
    ("sentence", "marker", "self_subject"),
    [
        ("Beta is not our principal competitor.", False, True),
        ("Our competitors do not include Beta.", False, False),
        ("We list competitors, excluding Beta.", False, False),
        ("Beta is excluded from our competitors.", False, False),
        ("Our competitors include companies other than Beta.", False, True),
        ("Our competitors include Gamma, but not Beta.", False, True),
        ("Our competitor is Alpha rather than Beta.", False, False),
        ("Unlike Beta, Alpha is our competitor.", False, True),
        ("Our competitors include Alpha instead of Beta.", False, True),
        ("Our competitors include Beta.", True, True),
        ("Our products are competitive with Beta products.", False, False),
        (
            'Beta CEO said, “Our principal competitor is Gamma.”',
            False,
            False,
        ),
        ("According to Beta, we compete with Gamma.", False, False),
        ("Our customer Alpha competes with Beta.", True, False),
        ("Our supplier Alpha competes with Beta.", True, False),
        ("We help Beta compete with Gamma.", False, False),
        ("We enable Beta to compete with Gamma.", False, False),
        ("Together with Beta, we compete against Gamma.", False, False),
        ("We collaborate with Beta and compete against Gamma.", False, False),
        ("Alpha's customer Gamma competes with Beta.", True, False),
        ("당사의 고객 알파는 베타와 경쟁합니다.", True, False),
        ("당사는 베타와 함께 감마와 경쟁합니다.", False, False),
        ("당사는 베타와 협력하여 감마와 경쟁합니다.", False, False),
        ("당사의 경쟁사 목록에서 베타를 제외한다.", False, True),
        ("베타가 아니라 알파가 당사의 경쟁사입니다.", False, True),
        ("베타 대신 알파가 당사의 경쟁사입니다.", False, True),
        ("베타 대표는 ‘당사는 감마와 경쟁 관계’라고 밝혔다.", False, False),
        ("The competitorium project mentions Beta.", False, False),
        ("Beta is Gamma's principal competitor.", True, False),
        ("The US market lists Beta as a competitor.", True, False),
    ],
)
def test_v2_영문_부정_부분문자_타사주어_US약어는_후보조건을_통과하지_않는다(
    sentence: str,
    marker: bool,
    self_subject: bool,
) -> None:
    assert comparison_source_sentence_has_marker(sentence) is marker
    assert (
        comparison_source_sentence_has_self_subject(sentence, "Alpha")
        is self_subject
    )


@pytest.mark.parametrize(
    "evidence",
    [
        '베타 대표는 “당사는 감마와 경쟁 관계에 있다”고 밝혔다.',
        'Beta CEO said, “Our principal competitor is Gamma.”',
        "Our competitors include Gamma, but not Beta.",
        "Our competitor is Alpha rather than Beta.",
        "Unlike Beta, Alpha is our competitor.",
        "Our competitors include Alpha instead of Beta.",
        "Our customer Alpha competes with Beta.",
        "We help Beta compete with Gamma.",
        "Together with Beta, we compete against Gamma.",
        "We collaborate with Beta and compete against Gamma.",
        "당사의 고객 알파는 베타와 경쟁합니다.",
        "당사는 베타와 함께 감마와 경쟁합니다.",
        "당사는 베타와 제휴하여 감마와 경쟁합니다.",
        "당사의 경쟁사 목록에서 베타를 제외한다.",
        "베타가 아니라 알파가 당사의 경쟁사입니다.",
        "베타 대신 알파가 당사의 경쟁사입니다.",
    ],
)
def test_v2_인용_배제_제3자주어문장은_공식source여도_후보가_아니다(
    evidence: str,
) -> None:
    _bound, sources, rows = _v2_official_registry(evidence)

    assert discover_official_source_candidates(
        rows,
        sources,
        CATALOG,
        self_corp_code="00000001",
        self_company="주식회사 알파",
    ) == ()


def test_v2_영문_대표_IR문장은_DART_exact_법인후보로_발견된다() -> None:
    evidence = "Beta is our principal competitor."
    fragments = register_candidate_sentence_evidence(
        {
            21: {
                "종류": "홈페이지",
                "원문": evidence,
                "출처": "https://www.alpha.example/ir/competition",
                VERIFIED_FINAL_URL_FIELD: VERIFIED_FINAL_URL_VALUE,
                "문서명": "Competitive landscape",
                "원문위치": "/ir/competition",
            }
        }
    )
    bound = bind_dart_profile_attestation(
        fragments,
        profile={
            "status": "000",
            "corp_code": "00000001",
            "corp_name": "Alpha",
            "hm_url": "www.alpha.example",
        },
        corp_code="00000001",
        company_name="Alpha",
        collected_on="2026-08-23",
    )
    sources = build_citations(
        bound.fragments,
        filing=None,
        collected_on=date(2026, 8, 23),
        company_publisher="Alpha",
    )
    assert bound.attester is not None
    sources.append(bound.attester)

    candidates = discover_official_source_candidates(
        candidate_sentences_from_fragments(bound.fragments, sources),
        sources,
        (
            DartCompanyRecord("00000001", "Alpha"),
            DartCompanyRecord("00000002", "Beta"),
        ),
        self_corp_code="00000001",
        self_company="Alpha",
    )

    assert [candidate.candidate_corp_code for candidate in candidates] == [
        "00000002"
    ]


def test_DART_공식영문명도_후보색인과_preflight에_동일하게_쓰인다() -> None:
    catalog = (
        DartCompanyRecord(
            "00000001",
            "주식회사 알파",
            corp_eng_name="Alpha Corporation",
        ),
        DartCompanyRecord(
            "00000002",
            "주식회사 베타",
            corp_eng_name="Beta Corporation",
        ),
    )
    evidence = "We compete with Beta Corporation."

    assert comparison_candidate_preflight_possible(
        evidence,
        catalog,
        self_corp_code="00000001",
        self_company="주식회사 알파",
    ) is True
    assert _official_candidate_codes(
        evidence,
        catalog=catalog,
        self_company="주식회사 알파",
    ) == ["00000002"]


@pytest.mark.parametrize(
    "url",
    [
        "https://www.alpha.example/blogs/competition",
        "https://www.alpha.example/blog-post/competition",
        "https://www.alpha.example/search-results/competition",
        "https://www.alpha.example/%62log-post/competition",
        "https://www.alpha.example/%2562log-post/competition",
        "https://www.alpha.example/ir/competition?s=beta",
        "https://www.alpha.example/ir/competition?q=beta",
        "https://www.alpha.example/ir/competition?query=beta",
        "https://www.alpha.example/ir/competition?keyword=beta",
        "https://user@www.alpha.example/ir/competition",
        "https://www.alpha.example:444/ir/competition",
        "https://www.alpha.example/ir/competition#search-results",
    ],
)
def test_v2_URL_정책은_검색_블로그_variant와_percent_encoding을_닫는다(
    url: str,
) -> None:
    assert not comparison_source_basis_is_allowed(
        {
            "version": COMPARISON_SOURCE_BASIS_VERSION,
            "source_kind": "기타",
            "source_type": "회사 공식 웹",
            "source_host": "www.alpha.example",
            "source_url": url,
        }
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://www.alpha.example/newsroom/competition",
        "https://www.alpha.example/press-releases/competition",
    ],
)
def test_v2_URL_정책은_회사_공식_뉴스룸과_보도자료를_허용한다(url: str) -> None:
    assert comparison_source_basis_is_allowed(
        {
            "version": COMPARISON_SOURCE_BASIS_VERSION,
            "source_kind": "기타",
            "source_type": "회사 공식 웹",
            "source_host": "www.alpha.example",
            "source_url": url,
        }
    )


def test_v2_후보와_attester는_JSON_재로드뒤_seal과_출고역검산을_통과한다() -> None:
    base = _report("알파는 반도체 검사장비를 전자 제조 고객 시장에 공급한다.")
    result = _v2_comparison_result()
    stored = Report(
        company=base.company,
        job="",
        corp_type=base.corp_type,
        grade=base.grade,
        sections=[*base.sections, result.section],
        citations=[*base.citations, *result.sources],
        fact_records=[*base.fact_records, *result.facts],
        schema_version=CANONICAL_SCHEMA_VERSION,
    )

    restored = report_storage.report_from_json(
        report_storage.report_to_json(stored)
    )
    target = result.facts[0]
    restored_target = next(
        fact for fact in restored.fact_records if fact.fact_id == target.fact_id
    )
    payload = parse_comparison_source_basis_v2(restored_target.comparison_basis)
    assert payload is not None
    source_registry = {
        source.source_id: source for source in restored.citations
    }
    attester = source_registry[payload["self_attestation_source_id"]]

    assert attester.provenance_role == "attestation_only"
    assert has_valid_provenance_seal(attester)
    assert _fact_problems(
        restored_target,
        source_registry,
        {fact.fact_id: fact for fact in restored.fact_records},
        set(),
    ) == []


def test_v2_parser는_비문자_제어문자_추가필드_전체길이_초과를_거부한다() -> None:
    payload = json.loads(_v2_comparison_result().facts[0].comparison_basis)
    variants: list[dict[str, object] | str] = []

    non_string = dict(payload)
    non_string["candidate_name"] = ["주식회사 베타"]
    variants.append(non_string)
    controlled = dict(payload)
    controlled["source_host"] = "www.alpha.example\nforged.example"
    variants.append(controlled)
    extra = dict(payload)
    extra["unexpected"] = "field"
    variants.append(extra)
    unicode_digits = dict(payload)
    unicode_digits["candidate_corp_code"] = "０００００００２"
    variants.append(unicode_digits)
    variants.append("{" + ("x" * 25_001) + "}")

    assert all(
        parse_comparison_source_basis_v2(
            variant
            if isinstance(variant, str)
            else json.dumps(variant, ensure_ascii=False)
        )
        is None
        for variant in variants
    )


@pytest.mark.parametrize(
    ("sentence", "self_company", "catalog"),
    [
        (
            "Beta or Gamma is our competitor.",
            "Alpha",
            (
                DartCompanyRecord("00000001", "Alpha"),
                DartCompanyRecord("00000002", "Beta"),
                DartCompanyRecord("00000003", "Gamma"),
            ),
        ),
        (
            "Our competitors include Beta or Gamma.",
            "Alpha",
            (
                DartCompanyRecord("00000001", "Alpha"),
                DartCompanyRecord("00000002", "Beta"),
                DartCompanyRecord("00000003", "Gamma"),
            ),
        ),
        (
            "We compete with Beta or Gamma.",
            "Alpha",
            (
                DartCompanyRecord("00000001", "Alpha"),
                DartCompanyRecord("00000002", "Beta"),
                DartCompanyRecord("00000003", "Gamma"),
            ),
        ),
        ("당사의 경쟁사는 베타 또는 감마입니다.", "주식회사 알파", CATALOG),
        ("당사는 베타 혹은 감마와 경쟁합니다.", "주식회사 알파", CATALOG),
    ],
)
def test_선택형_경쟁사_문장은_확정후보가_아니다(
    sentence: str,
    self_company: str,
    catalog: tuple[DartCompanyRecord, ...],
) -> None:
    assert not comparison_source_sentence_has_marker(sentence)
    assert not comparison_source_sentence_has_self_subject(sentence, self_company)
    for candidate in ("Beta", "Gamma", "베타", "감마"):
        assert not comparison_alias_is_competition_target(
            candidate,
            sentence,
            self_company=self_company,
            known_company_aliases=tuple(record.corp_name for record in catalog),
        )
    assert comparison_candidate_preflight_possible(
        sentence,
        catalog,
        self_corp_code="00000001",
        self_company=self_company,
    ) is False
    assert _official_candidate_codes(
        sentence,
        catalog=catalog,
        self_company=self_company,
    ) == []


@pytest.mark.parametrize(
    ("candidate_name", "sentence"),
    [
        ("US", "Alpha competes with us."),
        ("WE", "We compete with Alpha."),
        ("IT", "It competes with Alpha."),
        ("IN", "In competes with Alpha."),
        ("US", "We compete with us."),
    ],
)
def test_영문_대명사와_관계어_alias는_casefold로_후보가_되지_않는다(
    candidate_name: str,
    sentence: str,
) -> None:
    catalog = (
        DartCompanyRecord("00000001", "Alpha"),
        DartCompanyRecord("00000002", candidate_name),
    )
    assert not comparison_alias_is_competition_target(
        candidate_name,
        sentence,
        self_company="Alpha",
        known_company_aliases=("Alpha", candidate_name),
    )
    assert comparison_candidate_preflight_possible(
        sentence,
        catalog,
        self_corp_code="00000001",
        self_company="Alpha",
    ) is False
    assert _official_candidate_codes(
        sentence,
        catalog=catalog,
        self_company="Alpha",
    ) == []


@pytest.mark.parametrize(
    "invalid",
    [
        "Beta was our competitor.",
        "We competed with Beta.",
        "Our competitors included Beta.",
        "Beta or Gamma is our competitor.",
    ],
)
def test_publish는_과거형과_선택형_v2_basis를_재봉인해도_거부한다(
    invalid: str,
) -> None:
    base, result = _v2_english_comparison_result()
    target = result.facts[0]
    payload = json.loads(target.comparison_basis)
    payload.update(
        {
            "evidence_text": invalid,
            "evidence_sha256": comparison_evidence_sha256(invalid),
            "evidence_exact_sha256": comparison_evidence_exact_sha256(invalid),
        }
    )
    changed = replace(
        target,
        comparison_basis=json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    sources = {
        source.source_id: source for source in [*base.citations, *result.sources]
    }
    candidate_source = sources[payload["candidate_source_id"]]
    sources[candidate_source.source_id] = seal_collected_source(
        replace(
            candidate_source,
            evidence_hashes=[comparison_evidence_sha256(invalid)],
            exact_evidence_hashes=[comparison_evidence_exact_sha256(invalid)],
        )
    )

    assert _fact_problems(
        changed,
        sources,
        {fact.fact_id: fact for fact in [*base.fact_records, changed]},
        set(),
    )


def test_publish는_reserved_alias를_소문자_대명사로_재봉인해도_거부한다() -> None:
    base, result = _v2_english_comparison_result()
    target = result.facts[0]
    payload = json.loads(target.comparison_basis)
    invalid = "Alpha competes with us."
    payload.update(
        {
            "candidate_name": "US",
            "evidence_text": invalid,
            "evidence_sha256": comparison_evidence_sha256(invalid),
            "evidence_exact_sha256": comparison_evidence_exact_sha256(invalid),
        }
    )
    changed = replace(
        target,
        comparison_target="US",
        comparison_basis=json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    sources = {
        source.source_id: source for source in [*base.citations, *result.sources]
    }
    candidate_source = sources[payload["candidate_source_id"]]
    sources[candidate_source.source_id] = seal_collected_source(
        replace(
            candidate_source,
            evidence_hashes=[comparison_evidence_sha256(invalid)],
            exact_evidence_hashes=[comparison_evidence_exact_sha256(invalid)],
        )
    )

    assert _fact_problems(
        changed,
        sources,
        {fact.fact_id: fact for fact in [*base.fact_records, changed]},
        set(),
    )
