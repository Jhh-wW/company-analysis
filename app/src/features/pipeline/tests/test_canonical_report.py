from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from src.features.pipeline.canonical_report import (
    PublishBlockedError,
    WrittenClaim,
    _drop_stale_completed_executions,
    _prune_unbound_optional_claims,
    _structured_company_binding_is_visible,
    assemble_report,
    basic_report_selection_is_complete,
    basic_report_selection_is_minimum_usable,
    basic_report_selection_subset,
    combine_validated_picks,
    historical_performance_bases_are_complete,
    majority_picks,
    sections_from_picks,
    write_and_verify_sections,
)
from src.features.pipeline.port import Grade, ReportSection, ReportTable
from src.features.company_comparison.official_sources import (
    VERIFIED_FINAL_URL_FIELD,
    VERIFIED_FINAL_URL_VALUE,
    bind_dart_profile_attestation,
    register_candidate_sentence_evidence,
)
from src.features.provenance.citations import build_citations
from src.features.provenance.sources import (
    Source,
    SourceKind,
    evidence_text_hash,
    seal_collected_source,
)
from src.features.spanselect.canonical import (
    CanonicalPick,
    historical_performance_basis_sid,
)
from src.features.writer.logic import Sentence
from src.shared.official_ir import (
    IR_METADATA_VERIFICATION_FIELD,
    IR_METADATA_VERIFICATION_VALUE,
    IR_REPORTING_PERIOD_FIELD,
)


def _source(number: int, evidence: str = "공식 원문") -> Source:
    document_id = f"202608130000{number:02d}"
    return seal_collected_source(
        Source(
            number=number,
            kind=SourceKind.FILING,
            label=f"공식 자료 {number}",
            disclosed_at="2026-08-13",
            source_id=f"source-{number}",
            title=f"공식 자료 {number}",
            publisher="가나다 주식회사",
            host="dart.fss.or.kr",
            url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={document_id}",
            document_id=document_id,
            location=f"본문 {number}",
            source_type="공식 공시",
            fact_status="실제",
            evidence_hashes=[evidence_text_hash(evidence)],
        )
    )


def test_majority_vote_drops_cross_section_tie() -> None:
    same = CanonicalPick("identity", "가나다는 소재 기업이다.", 1)
    rounds = [
        [same, CanonicalPick("portfolio", "제품 A를 판매한다.", 2)],
        [same, CanonicalPick("business_model", "제품 A를 판매한다.", 2)],
        [CanonicalPick("identity", "다른 문장", 3)],
    ]

    assert majority_picks(rounds) == [same]


def test_Writer뒤_끊어진_관계는_독립검증할수없는_쪽만_생략한다() -> None:
    revenue = WrittenClaim(
        "business_model", "수익 구조", "조각 1·사업", "수익 구조", 1,
        sid="revenue-1", claim_type="revenue_model",
    )
    product = WrittenClaim(
        "portfolio", "핵심 제품", "조각 2·사업", "핵심 제품", 2,
        sid="product-1", claim_type="priority_product",
        revenue_model_sid="없는-수익",
    )
    issue = WrittenClaim(
        "current_challenges", "현재 문제", "조각 3·사업", "현재 문제", 3,
        sid="issue-1", claim_type="current_issue",
    )
    response = WrittenClaim(
        "current_challenges", "현재 대응", "조각 4·사업", "현재 대응", 4,
        sid="response-1", claim_type="current_response",
        response_to_sid="없는-문제",
    )
    execution = WrittenClaim(
        "past_changes", "완료 실행", "조각 5·사업", "완료 실행", 5,
        sid="execution-1", claim_type="completed_execution",
    )
    interpretation = WrittenClaim(
        "past_changes", "변화 해석", "조각 6·사업", "변화 해석", 6,
        sid="interpretation-1", claim_type="change_interpretation",
        basis_sids=("없는-실행",),
    )

    assert _prune_unbound_optional_claims(
        [revenue, product, issue, response, execution, interpretation]
    ) == [revenue, issue, execution]


def test_Writer뒤_완결된_선택형_관계는_함께_유지한다() -> None:
    revenue = WrittenClaim(
        "business_model", "수익 구조", "조각 1·사업", "수익 구조", 1,
        sid="revenue-1", claim_type="revenue_model",
    )
    product = WrittenClaim(
        "portfolio", "핵심 제품", "조각 2·사업", "핵심 제품", 2,
        sid="product-1", claim_type="priority_product",
        revenue_model_sid="revenue-1",
    )
    issue = WrittenClaim(
        "current_challenges", "현재 문제", "조각 3·사업", "현재 문제", 3,
        sid="issue-1", claim_type="current_issue",
    )
    response = WrittenClaim(
        "current_challenges", "현재 대응", "조각 4·사업", "현재 대응", 4,
        sid="response-1", claim_type="current_response", response_to_sid="issue-1",
    )
    execution = WrittenClaim(
        "past_changes", "완료 실행", "조각 5·사업", "완료 실행", 5,
        sid="execution-1", claim_type="completed_execution",
    )
    interpretation = WrittenClaim(
        "past_changes", "변화 해석", "조각 6·사업", "변화 해석", 6,
        sid="interpretation-1", claim_type="change_interpretation",
        basis_sids=("execution-1", "historical-performance:2025"),
    )

    assert _prune_unbound_optional_claims(
        [revenue, product, issue, response, execution, interpretation]
    ) == [revenue, product, issue, response, execution, interpretation]


def test_36개월밖_완료실행과_그것만_참조한_해석은_부분보고서에서_생략한다() -> None:
    stale = WrittenClaim(
        "past_changes",
        "오래된 완료 실행",
        "조각 1·사업",
        "오래된 완료 실행",
        1,
        sid="execution-old",
        claim_type="completed_execution",
        event_date="2022-12-31",
    )
    interpretation = WrittenClaim(
        "past_changes",
        "오래된 실행에 대한 변화 해석",
        "조각 2·사업",
        "오래된 실행에 대한 변화 해석",
        2,
        sid="interpretation-old",
        claim_type="change_interpretation",
        basis_sids=("execution-old",),
    )
    revenue = WrittenClaim(
        "business_model",
        "검증된 수익 구조",
        "조각 3·사업",
        "검증된 수익 구조",
        3,
        sid="revenue-1",
        claim_type="revenue_model",
    )

    kept, dropped = _drop_stale_completed_executions(
        [stale, interpretation, revenue],
        as_of_date="2026-08-24",
    )

    assert dropped == 1
    assert kept == [revenue]


def test_미래이거나_날짜없는_완료실행은_생략하지않고_출고게이트에_맡긴다() -> None:
    future = WrittenClaim(
        "past_changes",
        "미래 완료 실행",
        "조각 1·사업",
        "미래 완료 실행",
        1,
        sid="execution-future",
        claim_type="completed_execution",
        event_date="2027-01-01",
    )
    undated = replace(future, sid="execution-undated", event_date="")
    malformed = replace(future, sid="execution-week-date", event_date="2022-W52-7")

    kept, dropped = _drop_stale_completed_executions(
        [future, undated, malformed],
        as_of_date="2026-08-24",
    )

    assert dropped == 0
    assert kept == [future, undated, malformed]


def test_Writer가_같은_선택근거를_두번_쓰면_첫_검증문장만_남긴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_evidence = "가나다는 음반 판매와 공연으로 매출을 얻는다."
    second_evidence = "가나다는 광고와 라이선스로 매출을 얻는다."
    fragments = {
        1: {"종류": "사업내용", "원문": first_evidence},
        2: {"종류": "사업내용", "원문": second_evidence},
    }
    picks = [
        CanonicalPick(
            "business_model",
            first_evidence,
            1,
            sid="revenue-1",
            claim_type="revenue_model",
        ),
        CanonicalPick(
            "business_model",
            second_evidence,
            2,
            sid="revenue-2",
            claim_type="revenue_model",
        ),
    ]
    sections = sections_from_picks(picks, fragments)
    written = {
        "business_model": [
            Sentence(first_evidence, "business_model-1"),
            Sentence(
                "가나다는 음반 판매 및 공연에서 매출을 얻는다.",
                "business_model-1",
            ),
            Sentence(second_evidence, "business_model-2"),
        ]
    }
    monkeypatch.setattr(
        "src.features.pipeline.canonical_report.writer_logic.write_with_ai",
        lambda *_args, **_kwargs: (written, {"쓴문장": 3}),
    )
    monkeypatch.setattr(
        "src.features.pipeline.canonical_report.writer_revision.review_with_single_rewrite",
        lambda *_args, **_kwargs: (written, []),
    )
    steps: list[dict[str, object]] = []

    made_sections, claims = write_and_verify_sections(
        engine=object(),
        client=object(),
        company="가나다",
        sections=sections,
        fragments=fragments,
        picks=picks,
        steps=steps,
        model="",
    )

    assert [claim.sid for claim in claims] == ["revenue-1", "revenue-2"]
    assert [claim.text for claim in claims] == [first_evidence, second_evidence]
    assert made_sections[0].prose_lines == [
        (first_evidence, "조각 1·사업내용"),
        (second_evidence, "조각 2·사업내용"),
    ]
    assert steps[-1] == {"step": "11_작성_동일선택중복제거", "생략": 1}


def test_majority_vote_agrees_each_structured_field_without_exact_object_match() -> None:
    base = CanonicalPick(
        "future_strategy",
        "가나다는 2027년 AlphaX 생산 설비 가동을 계획했다.",
        1,
        sid="1-1",
        claim_type="future_plan",
        subject_label="AlphaX 생산 설비",
        plan_status="announced",
        plan_timing="2027년",
        plan_execution_signal="생산 설비 가동",
    )
    rounds = [
        [base],
        [replace(base, plan_timing="2027년 중")],
        [replace(base, plan_execution_signal="AlphaX 생산 설비 가동")],
    ]

    assert majority_picks(rounds) == [base]


def test_majority_vote_drops_reference_when_its_target_loses_field_consensus() -> None:
    revenue = CanonicalPick(
        "business_model",
        "가나다는 SmartX를 기업 고객에게 판매한다.",
        1,
        sid="1-1",
        claim_type="revenue_model",
        subject_label="SmartX",
    )
    priority = CanonicalPick(
        "portfolio",
        "가나다는 SmartX 출시와 해외 판매망 확대를 추진한다.",
        2,
        sid="2-1",
        claim_type="priority_product",
        subject_label="SmartX",
        product_role="기업 고객용 제품",
        revenue_model_sid="1-1",
        priority_signals=("출시·운영", "유통·지역확대"),
    )
    rounds = [
        [revenue, priority],
        [replace(revenue, subject_label="SmartX 제품"), priority],
        [],
    ]

    assert majority_picks(rounds) == []


def test_majority_vote_keeps_change_bound_only_to_historical_performance() -> None:
    reference = historical_performance_basis_sid(2025)
    change = CanonicalPick(
        "past_changes",
        "가나다는 2025년 SmartX 매출이 증가했다고 밝혔다.",
        1,
        sid="1-1",
        claim_type="change_interpretation",
        basis_sids=(reference,),
    )

    assert majority_picks([[change], [change], []]) == [change]


def test_B형_회차간_SID와_원문소유권_충돌은_모두_버린다() -> None:
    safe = CanonicalPick(
        "identity",
        "충돌 없는 정체성 원문",
        1,
        sid="1-1",
        claim_type="identity_summary",
    )
    same_sid_first = CanonicalPick(
        "business_model",
        "첫 번째 수익 원문",
        2,
        sid="2-1",
        claim_type="revenue_model",
    )
    same_sid_second = CanonicalPick(
        "business_model",
        "두 번째 수익 원문",
        3,
        sid="2-1",
        claim_type="revenue_model",
    )
    same_evidence_first = CanonicalPick(
        "business_model",
        "소유권이 갈린 공통 원문",
        4,
        sid="4-1",
        claim_type="customer_market",
    )
    same_evidence_second = CanonicalPick(
        "portfolio",
        "소유권이 갈린 공통 원문",
        4,
        sid="4-2",
        claim_type="priority_product",
    )

    combined = combine_validated_picks(
        [
            [safe, same_sid_first, same_evidence_first],
            [same_sid_second, same_evidence_second],
        ]
    )

    assert combined == [safe]


def test_C형_충돌로_대상이_사라진_수익대응해석_연결도_버린다() -> None:
    safe = CanonicalPick(
        "identity",
        "충돌 없는 정체성 원문",
        1,
        sid="1-1",
        claim_type="identity_summary",
    )
    revenue = CanonicalPick(
        "business_model",
        "수익 구조 원문",
        2,
        sid="2-1",
        claim_type="revenue_model",
    )
    product = CanonicalPick(
        "portfolio",
        "제품 원문",
        3,
        sid="3-1",
        claim_type="priority_product",
        revenue_model_sid="2-1",
    )
    issue = CanonicalPick(
        "current_challenges",
        "현재 문제 원문",
        4,
        sid="4-1",
        claim_type="current_issue",
    )
    response = CanonicalPick(
        "current_challenges",
        "현재 대응 원문",
        5,
        sid="5-1",
        claim_type="current_response",
        response_to_sid="4-1",
    )
    execution = CanonicalPick(
        "past_changes",
        "완료 실행 원문",
        6,
        sid="6-1",
        claim_type="completed_execution",
    )
    interpretation = CanonicalPick(
        "past_changes",
        "변화 해석 원문",
        7,
        sid="7-1",
        claim_type="change_interpretation",
        basis_sids=("6-1",),
    )

    combined = combine_validated_picks(
        [
            [safe, revenue, product, issue, response, execution, interpretation],
            [
                replace(revenue, sentence="충돌한 수익 구조", fragment_id=12),
                replace(issue, sentence="충돌한 현재 문제", fragment_id=14),
                replace(execution, sentence="충돌한 완료 실행", fragment_id=16),
            ],
        ]
    )

    assert combined == [safe]


def _complete_basic_picks() -> list[CanonicalPick]:
    return [
        CanonicalPick("identity", "회사 정체성", 1, "1-1", "identity_summary"),
        CanonicalPick("business_model", "수익 구조", 2, "2-1", "revenue_model"),
        CanonicalPick("business_model", "고객 시장", 3, "3-1", "customer_market"),
        CanonicalPick(
            "portfolio",
            "핵심 제품",
            4,
            "4-1",
            "priority_product",
            subject_label="SmartX",
            product_role="기업 고객용 검사 장비",
            revenue_model_sid="2-1",
        ),
        CanonicalPick(
            "past_changes",
            "완료 실행",
            5,
            "5-1",
            "completed_execution",
            event_date="2025",
        ),
        CanonicalPick(
            "past_changes",
            "변화 해석",
            6,
            "6-1",
            "change_interpretation",
            basis_sids=("5-1",),
        ),
        CanonicalPick(
            "current_challenges",
            "현재 문제",
            7,
            "7-1",
            "current_issue",
        ),
        CanonicalPick(
            "current_challenges",
            "현재 대응",
            8,
            "8-1",
            "current_response",
            response_to_sid="7-1",
        ),
        CanonicalPick("future_strategy", "미래 계획", 9, "9-1", "future_plan"),
        CanonicalPick(
            "operations_partners", "운영 체계", 10, "10-1", "operating_core"
        ),
        CanonicalPick("culture", "공식 가치", 11, "11-1", "official_value"),
    ]


def test_A형_서로_다른_회차의_검증사실을_합쳐_핵심보고서를_완성한다() -> None:
    picks = _complete_basic_picks()
    combined = combine_validated_picks([picks[::2], picks[1::2]])
    bases = {
        "historical-performance:2023",
        "historical-performance:2024",
        "historical-performance:2025",
    }

    assert set(combined) == set(picks)
    assert basic_report_selection_is_minimum_usable(
        combined,
        historical_performance_bases=bases,
    )
    assert basic_report_selection_is_complete(
        combined,
        historical_performance_bases=bases,
    )


def test_F형_공식_현재과제와_문화가_없어도_핵심보고서는_완성한다() -> None:
    picks = [
        item
        for item in _complete_basic_picks()
        if item.section_id not in {"current_challenges", "culture"}
    ]

    subset = basic_report_selection_subset(
        picks,
        historical_performance_bases={
            "historical-performance:2023",
            "historical-performance:2024",
            "historical-performance:2025",
        },
    )

    assert subset
    assert not any(
        item.section_id in {"current_challenges", "culture"} for item in subset
    )


def test_완전한_기본보고서_선택은_기존_순서와_항목을_그대로_유지한다() -> None:
    picks = _complete_basic_picks()
    bases = {
        "historical-performance:2023",
        "historical-performance:2024",
        "historical-performance:2025",
    }

    assert basic_report_selection_subset(
        picks,
        historical_performance_bases=bases,
    ) == picks
    assert basic_report_selection_is_minimum_usable(
        picks,
        historical_performance_bases=bases,
    )
    assert basic_report_selection_is_complete(
        picks,
        historical_performance_bases=bases,
    )


def test_최소_부분보고서는_정체성_수익구조_삼개년표만으로_성립한다() -> None:
    picks = [
        item
        for item in _complete_basic_picks()
        if item.claim_type in {"identity_summary", "revenue_model"}
    ]
    bases = {
        "historical-performance:2023",
        "historical-performance:2024",
        "historical-performance:2025",
    }

    subset = basic_report_selection_subset(
        picks,
        historical_performance_bases=bases,
    )

    assert subset == picks
    assert basic_report_selection_is_minimum_usable(
        picks,
        historical_performance_bases=bases,
    )
    assert not basic_report_selection_is_complete(
        picks,
        historical_performance_bases=bases,
    )


def test_정체성요약이_없어도_공식자기정의로_부분보고서만_성립한다() -> None:
    revenue = next(
        item
        for item in _complete_basic_picks()
        if item.claim_type == "revenue_model"
    )
    picks = [
        CanonicalPick(
            "identity",
            "공식 자기정의",
            12,
            "12-1",
            "official_self_definition",
        ),
        revenue,
    ]
    bases = {
        "historical-performance:2023",
        "historical-performance:2024",
        "historical-performance:2025",
    }

    subset = basic_report_selection_subset(
        picks,
        historical_performance_bases=bases,
    )

    assert subset == picks
    assert basic_report_selection_is_minimum_usable(
        picks,
        historical_performance_bases=bases,
    )
    assert not basic_report_selection_is_complete(
        picks,
        historical_performance_bases=bases,
    )


def test_완료실행은_해석이_없어도_부분보고서에_보존한다() -> None:
    bases = {
        "historical-performance:2023",
        "historical-performance:2024",
        "historical-performance:2025",
    }

    picks = [
        item
        for item in _complete_basic_picks()
        if item.claim_type != "change_interpretation"
    ]
    subset = basic_report_selection_subset(
        picks,
        historical_performance_bases=bases,
    )

    assert any(item.claim_type == "completed_execution" for item in subset)


def test_근거실행이_없는_변화해석은_부분보고서에서_생략한다() -> None:
    picks = [
        item
        for item in _complete_basic_picks()
        if item.claim_type != "completed_execution"
    ]

    subset = basic_report_selection_subset(
        picks,
        historical_performance_bases={
            "historical-performance:2023",
            "historical-performance:2024",
            "historical-performance:2025",
        },
    )

    assert subset
    assert not any(item.section_id == "past_changes" for item in subset)


def test_최소_부분보고서_선택이_성립하면_한번으로_충분하다() -> None:
    assert basic_report_selection_is_minimum_usable(
        _complete_basic_picks(),
        historical_performance_bases={
            "historical-performance:2023",
            "historical-performance:2024",
            "historical-performance:2025",
        },
    )


def test_필수사실이_장별상한_뒤에_있어도_Writer_부분집합에는_남는다() -> None:
    base = _complete_basic_picks()
    identity = [item for item in base if item.section_id == "identity"]
    other = [
        item
        for item in base
        if item.section_id not in {"identity", "business_model"}
    ]
    early_customers = [
        CanonicalPick(
            "business_model",
            f"고객 시장 추가 {number}",
            20 + number,
            f"{20 + number}-1",
            "customer_market",
        )
        for number in range(4)
    ]
    business = [item for item in base if item.section_id == "business_model"]
    over_limit = [*identity, *early_customers, *business, *other]

    subset = basic_report_selection_subset(
        over_limit,
        historical_performance_bases={
            "historical-performance:2023",
            "historical-performance:2024",
            "historical-performance:2025",
        },
    )

    selected_business = [
        item for item in subset if item.section_id == "business_model"
    ]
    assert len(selected_business) <= 4
    assert any(item.sid == "2-1" for item in selected_business)
    assert any(item.claim_type == "customer_market" for item in selected_business)
    assert any(
        item.claim_type == "priority_product"
        and item.revenue_model_sid == "2-1"
        for item in subset
    )


def test_복구불가능한_삼개년실적참조를_AI전에_판별한다() -> None:
    assert historical_performance_bases_are_complete(
        {
            "historical-performance:2023",
            "historical-performance:2024",
            "historical-performance:2025",
        }
    )
    assert not historical_performance_bases_are_complete(
        {
            "historical-performance:2023",
            "historical-performance:2025",
            "historical-performance:2026",
        }
    )


def test_완결부분집합은_연결되지_않은_여분항목을_공개하지_않는다() -> None:
    extra = CanonicalPick(
        "current_challenges",
        "연결되지 않은 대응",
        30,
        "30-1",
        "current_response",
        response_to_sid="없는-SID",
    )
    subset = basic_report_selection_subset(
        [*_complete_basic_picks(), extra],
        historical_performance_bases={
            "historical-performance:2023",
            "historical-performance:2024",
            "historical-performance:2025",
        },
    )

    assert subset
    assert extra not in subset


def test_Writer_후_구조예외는_구체대상과_행동이_문장에_남아야_한다() -> None:
    future = CanonicalPick(
        "future_strategy",
        "JYP는 2027년 미국 공연 유통망을 확대할 계획이다.",
        40,
        "40-1",
        "future_plan",
        subject_label="미국 공연 유통망",
        plan_execution_signal="미국 공연 유통망을 확대",
    )
    response = CanonicalPick(
        "current_challenges",
        "JYP는 공연 원가 부담에 대응해 투어 동선을 재설계하고 있다.",
        41,
        "41-1",
        "current_response",
        response_action="투어 동선을 재설계하고 있다",
    )

    assert _structured_company_binding_is_visible(
        future,
        "JYP는 2027년 미국 공연 유통망을 확대할 계획이다.",
    )
    assert not _structured_company_binding_is_visible(
        future,
        "회사는 앞으로 사업을 확대할 계획이다.",
    )
    assert _structured_company_binding_is_visible(
        response,
        "JYP는 투어 동선을 재설계하고 있다.",
    )
    assert not _structured_company_binding_is_visible(
        response,
        "회사는 비용 문제에 대응하고 있다.",
    )


@pytest.mark.parametrize(
    "missing_claim_type",
    [
        "identity_summary",
        "revenue_model",
    ],
)
def test_최소_부분보고서_필수사실이_하나라도_빠지면_재선택한다(
    missing_claim_type: str,
) -> None:
    picks = [
        item for item in _complete_basic_picks() if item.claim_type != missing_claim_type
    ]

    assert not basic_report_selection_is_minimum_usable(
        picks,
        historical_performance_bases={
            "historical-performance:2023",
            "historical-performance:2024",
            "historical-performance:2025",
        },
    )


@pytest.mark.parametrize(
    "missing_claim_type",
    [
        "identity_summary",
        "customer_market",
        "revenue_model",
        "priority_product",
        "completed_execution",
        "change_interpretation",
        "future_plan",
        "operating_core",
    ],
)
def test_FULL_기본보고서_필수사실이_하나라도_빠지면_완결이_아니다(
    missing_claim_type: str,
) -> None:
    picks = [
        item for item in _complete_basic_picks() if item.claim_type != missing_claim_type
    ]

    assert not basic_report_selection_is_complete(
        picks,
        historical_performance_bases={
            "historical-performance:2023",
            "historical-performance:2024",
            "historical-performance:2025",
        },
    )


def test_참조가_깨진_선택형_제품은_빼고_최소_부분보고서를_유지한다() -> None:
    broken_reference = [
        replace(item, revenue_model_sid="없는-SID")
        if item.claim_type == "priority_product"
        else item
        for item in _complete_basic_picks()
    ]

    subset = basic_report_selection_subset(
        broken_reference,
        historical_performance_bases={
            "historical-performance:2023",
            "historical-performance:2024",
            "historical-performance:2025",
        },
    )

    assert subset
    assert not any(item.claim_type == "priority_product" for item in subset)
    assert basic_report_selection_is_minimum_usable(
        broken_reference,
        historical_performance_bases={
            "historical-performance:2023",
            "historical-performance:2024",
            "historical-performance:2025",
        },
    )
    assert not basic_report_selection_is_complete(
        broken_reference,
        historical_performance_bases={
            "historical-performance:2023",
            "historical-performance:2024",
            "historical-performance:2025",
        },
    )


def test_최소_부분보고서는_삼개년표가_깨지면_성립하지_않는다() -> None:
    assert not basic_report_selection_is_minimum_usable(
        _complete_basic_picks(),
        historical_performance_bases={
            "historical-performance:2024",
            "historical-performance:2025",
        },
    )
    assert not basic_report_selection_is_minimum_usable(
        _complete_basic_picks(),
        historical_performance_bases={
            "historical-performance:2022",
            "historical-performance:2024",
            "historical-performance:2025",
        },
    )


def test_현재문제는_대응연결이_깨져도_문제사실만_보존한다() -> None:
    broken_current = [
        replace(item, response_to_sid="없는-SID")
        if item.claim_type == "current_response"
        else item
        for item in _complete_basic_picks()
    ]

    subset = basic_report_selection_subset(
        broken_current,
        historical_performance_bases={
            "historical-performance:2023",
            "historical-performance:2024",
            "historical-performance:2025",
        },
    )

    assert subset
    current = [
        item for item in subset if item.section_id == "current_challenges"
    ]
    assert [item.claim_type for item in current] == ["current_issue"]


def test_assemble_report_locks_visible_claims_to_sources() -> None:
    fragments = {
        1: {"종류": "사업내용", "원문": "가나다는 산업용 소재 기업이다."},
        2: {"종류": "사업내용", "원문": "가나다는 고객에게 소재를 판매한다."},
        3: {"종류": "사업내용", "원문": "가나다는 제품 A를 판매한다."},
        4: {"종류": "MD&A", "원문": "2025년 설비 A 도입을 완료했다."},
    }
    picks = [
        CanonicalPick("identity", fragments[1]["원문"], 1),
        CanonicalPick("business_model", fragments[2]["원문"], 2),
        CanonicalPick("portfolio", fragments[3]["원문"], 3),
        CanonicalPick("past_changes", fragments[4]["원문"], 4),
    ]
    sections = sections_from_picks(picks, fragments)
    claims = [
        WrittenClaim(item.section_id, item.sentence, f"조각 {item.fragment_id}·{fragments[item.fragment_id]['종류']}", item.sentence, item.fragment_id)
        for item in picks
    ]
    calls = 0

    def ask(_prompt, _schema, _max_tokens):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "items": [
                    {"section_id": "identity", "text": "회사의 산업 내 역할이 분명하다"},
                    {"section_id": "business_model", "text": "고객과 수익 경로가 연결된다"},
                    {"section_id": "portfolio", "text": "현재 판매 제품의 역할이 드러난다"},
                ]
            }, {}
        return {"판정": [{"번호": 1, "근거에있다": True}, {"번호": 2, "근거에있다": True}, {"번호": 3, "근거에있다": True}]}, {}

    report = assemble_report(
        company="가나다",
        corp_type="상장사",
        sections=sections,
        written_claims=claims,
        sources=[_source(i, fragments[i]["원문"]) for i in range(1, 5)],
        summary_ask=ask,
        steps=[],
        as_of_date="2026-08-19",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2025년 공식 공시",
        publish=False,
    )

    assert report.schema_version == "company-report-v4-canonical"
    assert [section.cell for section in report.sections] == [
        "identity",
        "business_model",
        "portfolio",
        "past_changes",
    ]
    assert len(report.fact_records) == 4
    assert all(fact.status == "verified" for fact in report.fact_records)
    assert all(fact.verification_status == "verified" for fact in report.fact_records)
    assert all(fact.evidence_binding for fact in report.fact_records)
    assert all(source.evidence_hashes for source in report.citations)
    assert report.job == ""
    assert report.requirements == []


def test_assemble_report는_오래된_완료실행만_버리고_다른_검증사실을_유지한다() -> None:
    identity_evidence = "가나다는 산업용 소재 기업으로 제품을 생산한다."
    stale_evidence = "2022년 기존 생산 설비의 전환을 완료했다."
    interpretation_evidence = "기존 설비 전환으로 생산 체계가 바뀌었다."
    picks = [
        CanonicalPick(
            "identity",
            identity_evidence,
            1,
            sid="identity-1",
            claim_type="identity_summary",
        ),
        CanonicalPick(
            "past_changes",
            stale_evidence,
            2,
            sid="execution-old",
            claim_type="completed_execution",
            event_date="2022-12-31",
        ),
        CanonicalPick(
            "past_changes",
            interpretation_evidence,
            3,
            sid="interpretation-old",
            claim_type="change_interpretation",
            basis_sids=("execution-old",),
        ),
    ]
    fragments = {
        index: {"종류": "사업내용", "원문": pick.sentence}
        for index, pick in enumerate(picks, start=1)
    }
    sections = sections_from_picks(picks, fragments)
    claims = [
        WrittenClaim(
            pick.section_id,
            pick.sentence,
            f"조각 {pick.fragment_id}·사업내용",
            pick.sentence,
            pick.fragment_id,
            sid=pick.sid,
            claim_type=pick.claim_type,
            basis_sids=pick.basis_sids,
            event_date=pick.event_date,
        )
        for pick in picks
    ]
    steps: list[dict[str, object]] = []

    report = assemble_report(
        company="가나다",
        corp_type="상장사",
        sections=sections,
        written_claims=claims,
        sources=[
            _source(index, pick.sentence)
            for index, pick in enumerate(picks, start=1)
        ],
        summary_ask=lambda *_args: ({"items": []}, {}),
        steps=steps,
        as_of_date="2026-08-24",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2025년 공식 공시",
        publish=False,
    )

    assert [fact.claim_type for fact in report.fact_records] == ["identity_summary"]
    assert [section.cell for section in report.sections] == ["identity"]
    assert steps[-1] == {
        "step": "완료실행_기간후검증",
        "생략": 1,
        "사유": "보고서 기준일 전 최근 36개월 밖의 완료 실행",
    }


def test_삼개년표가있는_보고서는_오래된실행_raw도_지우고_PARTIAL로_출고한다() -> None:
    identity = "가나다는 산업용 소재를 생산하고 판매하는 기업이다."
    revenue = "가나다는 산업용 소재를 고객에게 판매해 매출을 얻는다."
    stale = "2022년 기존 생산 설비의 전환을 완료했다."
    interpretation = "기존 설비 전환으로 생산 체계가 바뀌었다."
    financial_payloads = [
        '{"bsns_year":"2025","account_nm":"매출액","thstrm_amount":"12000000000"}',
        '{"bsns_year":"2024","account_nm":"매출액","thstrm_amount":"10000000000"}',
        '{"bsns_year":"2023","account_nm":"매출액","thstrm_amount":"9000000000"}',
    ]
    picks = [
        CanonicalPick(
            "identity", identity, 1, sid="identity-1", claim_type="identity_summary"
        ),
        CanonicalPick(
            "business_model",
            revenue,
            2,
            sid="revenue-1",
            claim_type="revenue_model",
        ),
        CanonicalPick(
            "past_changes",
            stale,
            3,
            sid="execution-old",
            claim_type="completed_execution",
            event_date="2022-12-31",
        ),
        CanonicalPick(
            "past_changes",
            interpretation,
            4,
            sid="interpretation-old",
            claim_type="change_interpretation",
            basis_sids=("execution-old",),
        ),
    ]
    fragments = {
        pick.fragment_id: {"종류": "사업내용", "원문": pick.sentence}
        for pick in picks
    }
    performance_table = ReportTable(
        caption="전자공시 최근 세 사업연도 연결 주요 실적 (단위: 억원)",
        headers=["사업연도", "매출액"],
        rows=[["2025", "120"], ["2024", "100"], ["2023", "90"]],
        cite="조각 5·재무",
        numeric=True,
        raw_rows=[
            ["2025", "12000000000"],
            ["2024", "10000000000"],
            ["2023", "9000000000"],
        ],
        scale_divisor="100000000",
        scale_places=0,
        display_unit="억원",
        evidence_rows=financial_payloads,
    )
    sections = [
        replace(section, tables=[performance_table])
        if section.cell == "past_changes"
        else section
        for section in sections_from_picks(picks, fragments)
    ]
    claims = [
        WrittenClaim(
            pick.section_id,
            pick.sentence,
            f"조각 {pick.fragment_id}·사업내용",
            pick.sentence,
            pick.fragment_id,
            sid=pick.sid,
            claim_type=pick.claim_type,
            basis_sids=pick.basis_sids,
            event_date=pick.event_date,
        )
        for pick in picks
    ]

    table_source = seal_collected_source(
        replace(
            _source(5, financial_payloads[0]),
            evidence_hashes=[
                evidence_text_hash(payload) for payload in financial_payloads
            ],
        )
    )
    report = assemble_report(
        company="가나다",
        corp_type="상장사",
        sections=sections,
        written_claims=claims,
        sources=[
            _source(1, identity),
            _source(2, revenue),
            _source(3, stale),
            _source(4, interpretation),
            table_source,
        ],
        summary_ask=lambda *_args: ({"items": []}, {}),
        steps=[],
        as_of_date="2026-08-24",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2025년 공식 공시",
    )

    past = next(section for section in report.sections if section.cell == "past_changes")
    assert past.lines == []
    assert past.prose_lines == []
    assert len(past.tables) == 1
    assert not any(fact.claim_type == "completed_execution" for fact in report.fact_records)
    assert report.grade is Grade.PARTIAL
    assert any("완료 실행" in reason for reason in report.shortfall_reasons)


def test_assembly_never_self_registers_written_claim_as_source_evidence() -> None:
    evidence = "가나다는 산업용 소재 기업이다."
    pick = CanonicalPick(
        "identity", evidence, 1, sid="identity-1", claim_type="identity_summary"
    )
    sections = sections_from_picks(
        [pick], {1: {"종류": "사업내용", "원문": evidence}}
    )
    claim = WrittenClaim(
        "identity",
        evidence,
        "조각 1·사업내용",
        evidence,
        1,
        sid="identity-1",
        claim_type="identity_summary",
    )
    source = _source(1, "서로 다른 실제 원문")

    report = assemble_report(
        company="가나다",
        corp_type="상장사",
        sections=sections,
        written_claims=[claim],
        sources=[source],
        summary_ask=lambda *_args: ({"items": []}, {}),
        steps=[],
        as_of_date="2026-08-19",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2025년 공식 공시",
        publish=False,
    )

    assert report.fact_records == []
    assert report.citations == []
    assert evidence_text_hash(evidence) not in source.evidence_hashes


def test_customer_market_metadata_stays_bound_from_pick_to_fact() -> None:
    evidence = "가나다는 중국에서 제품을 판매해 매출을 얻는다."
    pick = CanonicalPick(
        "business_model",
        evidence,
        1,
        sid="business-1",
        claim_type="customer_market",
        subject_label="중국",
        market_stage="",
        market_observation="중국에서 제품을 판매",
    )
    sections = sections_from_picks(
        [pick], {1: {"종류": "사업내용", "원문": evidence}}
    )
    claim = WrittenClaim(
        "business_model",
        evidence,
        "조각 1·사업내용",
        evidence,
        1,
        sid=pick.sid,
        claim_type=pick.claim_type,
        subject_label=pick.subject_label,
        market_stage=pick.market_stage,
        market_observation=pick.market_observation,
    )

    report = assemble_report(
        company="가나다",
        corp_type="상장사",
        sections=sections,
        written_claims=[claim],
        sources=[_source(1, evidence)],
        summary_ask=lambda *_args: ({"items": []}, {}),
        steps=[],
        as_of_date="2026-08-19",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2025년 공식 공시",
        publish=False,
    )

    assert len(report.fact_records) == 1
    fact = report.fact_records[0]
    assert fact.claim_type == "customer_market"
    assert fact.market_stage == ""
    assert fact.market_observation == "중국에서 제품을 판매"
    assert fact.market_priority == ""


def test_assemble_report_rejects_duplicate_source_numbers_before_mapping() -> None:
    duplicate = replace(_source(2), number=1)

    with pytest.raises(PublishBlockedError, match="출처 번호"):
        assemble_report(
            company="가나다",
            corp_type="상장사",
            sections=[],
            written_claims=[],
            sources=[_source(1), duplicate],
            summary_ask=lambda *_args: ({"items": []}, {}),
            steps=[],
            as_of_date="2026-08-19",
            analysis_period="2023~2025 완료 회계연도",
            latest_performance_period="2025년 공식 공시",
        )


def test_complete_news_metadata_is_not_promoted_to_a_draft_fact() -> None:
    evidence = "가나다는 소재 기업이라고 외부 매체가 분석했다."
    fragments = {1: {"종류": "뉴스", "원문": evidence}}
    pick = CanonicalPick(
        "identity",
        evidence,
        1,
        sid="1-1",
        claim_type="identity_summary",
    )
    sections = sections_from_picks([pick], fragments)
    claim = WrittenClaim(
        "identity",
        evidence,
        "조각 1·뉴스",
        evidence,
        1,
        sid="1-1",
        claim_type="identity_summary",
    )
    news = Source(
        number=1,
        kind=SourceKind.NEWS,
        label="회사 전략 분석",
        published_at="2026-08-13",
        domain="news.example",
        source_id="news-1",
        title="회사 전략 분석",
        publisher="OO경제",
        host="news.example",
        url="https://news.example/1",
        document_id="article-1",
        location="기사 본문",
        source_type="공식 분석 기사",
        fact_status="실제",
        evidence_hashes=[evidence_text_hash(evidence)],
    )
    assert news.is_canonical_valid is True
    assert news.is_canonical_official is False

    report = assemble_report(
        company="가나다",
        corp_type="상장사",
        sections=sections,
        written_claims=[claim],
        sources=[news],
        summary_ask=lambda *_args: ({"items": []}, {}),
        steps=[],
        as_of_date="2026-08-19",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2025년 공식 공시",
        publish=False,
    )

    assert report.sections == []
    assert report.fact_records == []
    assert report.citations == []


def test_문서일_미결속_공식IR은_registry가_유효해도_canonical_fact가_아니다() -> None:
    evidence = "당사는 2015년부터 베타와 경쟁합니다."
    fragments = register_candidate_sentence_evidence(
        {
            1: {
                "종류": "공식 IR",
                "원문": evidence,
                "출처": "https://alpha.example/ir/archive-2015.pdf",
                "문서ID": "a" * 64,
                "문서명": "2015 Investor Presentation",
                "원문위치": "PDF p.2 1문단 · pypdf 6.16.1",
                VERIFIED_FINAL_URL_FIELD: VERIFIED_FINAL_URL_VALUE,
            }
        }
    )
    bound = bind_dart_profile_attestation(
        fragments,
        profile={
            "status": "000",
            "corp_code": "00000001",
            "corp_name": "주식회사 알파",
            "hm_url": "alpha.example",
        },
        corp_code="00000001",
        company_name="주식회사 알파",
        collected_on="2026-08-23",
    )
    sources = build_citations(
        bound.fragments,
        filing=None,
        collected_on=date(2026, 8, 23),
        company_publisher="주식회사 알파",
    )
    assert bound.attester is not None
    sources.append(bound.attester)
    ir_source = next(source for source in sources if source.source_type == "회사 공식 IR")
    assert ir_source.is_canonical_valid

    pick = CanonicalPick(
        "identity",
        evidence,
        1,
        sid="1-1",
        claim_type="identity_summary",
    )
    section = sections_from_picks([pick], bound.fragments)[0]
    claim = WrittenClaim(
        "identity",
        evidence,
        "조각 1·공식 IR",
        evidence,
        1,
        sid="1-1",
        claim_type="identity_summary",
    )

    report = assemble_report(
        company="주식회사 알파",
        corp_type="상장사",
        sections=[section],
        written_claims=[claim],
        sources=sources,
        summary_ask=lambda *_args: ({"items": []}, {}),
        steps=[],
        as_of_date="2026-08-23",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2025년 공식 공시",
        publish=False,
    )

    assert report.sections == []
    assert report.fact_records == []
    assert report.citations == []


def test_발행일_기준기간_DART법인이_모두_결속된_공식IR만_canonical_fact가_된다() -> None:
    evidence = "주식회사 알파는 반도체 검사 장비 기업입니다."
    fragments = {
        1: {
            "종류": "공식 IR",
            "원문": evidence,
            "출처": "https://alpha.example/ir/2026-q2",
            "첨부URL": "https://cdn.example/alpha-2q26.pdf",
            "문서일": "2026-08-12",
            IR_REPORTING_PERIOD_FIELD: "2026-Q2",
            IR_METADATA_VERIFICATION_FIELD: IR_METADATA_VERIFICATION_VALUE,
            "문서ID": "b" * 64,
            "문서명": "26년 2분기 IR자료",
            "원문위치": "PDF p.2 1문단 · pypdf 6.16.1",
            VERIFIED_FINAL_URL_FIELD: VERIFIED_FINAL_URL_VALUE,
        }
    }
    bound = bind_dart_profile_attestation(
        fragments,
        profile={
            "status": "000",
            "corp_code": "00000001",
            "corp_name": "주식회사 알파",
            "hm_url": "alpha.example",
        },
        corp_code="00000001",
        company_name="주식회사 알파",
        collected_on="2026-08-23",
    )
    sources = build_citations(
        bound.fragments,
        filing=None,
        collected_on=date(2026, 8, 23),
        company_publisher="주식회사 알파",
    )
    assert bound.attester is not None
    sources.append(bound.attester)
    ir_source = next(item for item in sources if item.source_type == "회사 공식 IR")
    assert ir_source.published_at == "2026-08-12"
    assert ir_source.reporting_period == "2026-Q2"

    pick = CanonicalPick(
        "identity", evidence, 1, sid="1-1", claim_type="identity_summary"
    )
    report = assemble_report(
        company="주식회사 알파",
        corp_type="상장사",
        sections=sections_from_picks([pick], bound.fragments),
        written_claims=[
            WrittenClaim(
                "identity",
                evidence,
                "조각 1·공식 IR",
                evidence,
                1,
                sid="1-1",
                claim_type="identity_summary",
            )
        ],
        sources=sources,
        summary_ask=lambda *_args: ({"items": []}, {}),
        steps=[],
        as_of_date="2026-08-23",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2026년 2분기",
        publish=False,
    )

    assert len(report.sections) == 1
    assert len(report.fact_records) == 1
    assert report.fact_records[0].source_date == "2026-08-12"
    assert any(item.source_type == "회사 공식 IR" for item in report.citations)


def test_오래된_newsroom_공식웹은_registry가_유효해도_canonical_fact가_아니다() -> None:
    evidence = "당사의 경쟁사는 베타입니다."
    fragments = register_candidate_sentence_evidence(
        {
            1: {
                "종류": "홈페이지",
                "원문": evidence,
                "출처": "https://alpha.example/newsroom/2015-competition",
                "문서일": "2015-06-01",
                "문서명": "2015 경쟁 환경",
                "원문위치": "/newsroom/2015-competition",
                VERIFIED_FINAL_URL_FIELD: VERIFIED_FINAL_URL_VALUE,
            }
        }
    )
    bound = bind_dart_profile_attestation(
        fragments,
        profile={
            "status": "000",
            "corp_code": "00000001",
            "corp_name": "주식회사 알파",
            "hm_url": "alpha.example",
        },
        corp_code="00000001",
        company_name="주식회사 알파",
        collected_on="2026-08-23",
    )
    sources = build_citations(
        bound.fragments,
        filing=None,
        collected_on=date(2026, 8, 23),
        company_publisher="주식회사 알파",
    )
    assert bound.attester is not None
    sources.append(bound.attester)
    web_source = next(source for source in sources if source.source_type == "회사 공식 웹")
    assert web_source.is_canonical_valid
    assert web_source.fact_status == "과거·현재성 미확정 문서 수집 참고"

    pick = CanonicalPick(
        "identity",
        evidence,
        1,
        sid="1-1",
        claim_type="identity_summary",
    )
    report = assemble_report(
        company="주식회사 알파",
        corp_type="상장사",
        sections=sections_from_picks([pick], bound.fragments),
        written_claims=[
            WrittenClaim(
                "identity",
                evidence,
                "조각 1·홈페이지",
                evidence,
                1,
                sid="1-1",
                claim_type="identity_summary",
            )
        ],
        sources=sources,
        summary_ask=lambda *_args: ({"items": []}, {}),
        steps=[],
        as_of_date="2026-08-23",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2025년 공식 공시",
        publish=False,
    )

    assert report.sections == []
    assert report.fact_records == []
    assert report.citations == []


def test_assembly_resolves_public_performance_reference_to_verified_table_fact() -> None:
    interpretation_evidence = (
        "가나다는 2025년 SmartX 매출이 2024년보다 증가했다고 밝혔다."
    )
    financial_payload = '{"status":"000","source":"DART"}'
    performance_table = ReportTable(
        caption="전자공시 최근 세 사업연도 연결 주요 실적 (단위: 억원)",
        headers=["사업연도", "매출액"],
        rows=[["2025", "120"], ["2024", "100"], ["2023", "90"]],
        cite="조각 2·재무",
        numeric=True,
        raw_rows=[
            ["2025", "12000000000"],
            ["2024", "10000000000"],
            ["2023", "9000000000"],
        ],
        scale_divisor="100000000",
        scale_places=0,
        display_unit="억원",
        evidence_rows=[financial_payload, financial_payload, financial_payload],
    )
    reference = historical_performance_basis_sid(2025)
    section = ReportSection(
        cell="past_changes",
        title="3개년 주요 변화와 실행",
        tables=[performance_table],
    )
    interpretation = WrittenClaim(
        section_id="past_changes",
        text=interpretation_evidence,
        cite="조각 1·MD&A",
        evidence=interpretation_evidence,
        fragment_id=1,
        sid="1-1",
        claim_type="change_interpretation",
        basis_sids=(reference,),
    )

    report = assemble_report(
        company="가나다",
        corp_type="상장사",
        sections=[section],
        written_claims=[interpretation],
        sources=[_source(1, interpretation_evidence), _source(2, financial_payload)],
        summary_ask=lambda *_args: ({"items": []}, {}),
        steps=[],
        as_of_date="2026-08-19",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2025년 공식 공시",
        publish=False,
    )

    interpretation_fact = next(
        fact
        for fact in report.fact_records
        if fact.claim_type == "change_interpretation"
    )
    performance_fact = next(
        fact
        for fact in report.fact_records
        if fact.claim_type == "historical_performance" and fact.fiscal_year == 2025
    )
    assert interpretation_fact.basis_fact_ids == [performance_fact.fact_id]
    assert reference not in interpretation_fact.basis_fact_ids


def test_assembly_drops_entire_basis_link_when_internal_or_unknown_reference_is_mixed() -> None:
    evidence = "가나다는 2025년 SmartX 매출이 증가했다고 밝혔다."
    reference = historical_performance_basis_sid(2025)
    table = ReportTable(
        caption="전자공시 최근 세 사업연도 연결 주요 실적 (단위: 억원)",
        headers=["사업연도", "매출액"],
        rows=[["2025", "120"]],
        cite="조각 2·재무",
        numeric=True,
        raw_rows=[["2025", "12000000000"]],
        scale_divisor="100000000",
        scale_places=0,
        display_unit="억원",
        evidence_rows=["DART 원 payload"],
    )
    claim = WrittenClaim(
        "past_changes",
        evidence,
        "조각 1·MD&A",
        evidence,
        1,
        sid="1-1",
        claim_type="change_interpretation",
        basis_sids=(reference, "fact-internal-value"),
    )
    report = assemble_report(
        company="가나다",
        corp_type="상장사",
        sections=[ReportSection("past_changes", "과거", tables=[table])],
        written_claims=[claim],
        sources=[_source(1, evidence), _source(2, "DART 원 payload")],
        summary_ask=lambda *_args: ({"items": []}, {}),
        steps=[],
        as_of_date="2026-08-19",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2025년 공식 공시",
        publish=False,
    )

    interpretation_fact = next(
        fact
        for fact in report.fact_records
        if fact.claim_type == "change_interpretation"
    )
    assert interpretation_fact.basis_fact_ids == []
