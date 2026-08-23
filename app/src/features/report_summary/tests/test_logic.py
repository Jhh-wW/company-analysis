from src.features.report_summary.logic import (
    VerifiedSummarySource,
    build_summary_from_verified_claims,
    build_summary_with_ai,
)


def test_검수된_본문을_그대로_요약하면_ai를_부르지_않는다():
    candidates = [
        VerifiedSummarySource(
            section_id="identity",
            text="가나다는 산업용 소재를 만드는 기업이다.",
            fact_id="fact-1",
            support_terms=("산업용 소재", "기업"),
        ),
        VerifiedSummarySource(
            section_id="business_model",
            text="가나다는 기업 고객에게 소재를 판매한다.",
            fact_id="fact-2",
            support_terms=("기업 고객", "소재"),
        ),
        VerifiedSummarySource(
            section_id="culture",
            text="가나다는 존중과 책임을 핵심 가치로 제시한다.",
            fact_id="fact-3",
            support_terms=("존중", "책임"),
        ),
    ]

    result, steps = build_summary_from_verified_claims(candidates)

    assert [item.text for item in result] == [item.text for item in candidates]
    assert [item.fact_ids for item in result] == [
        ("fact-1",),
        ("fact-2",),
        ("fact-3",),
    ]
    assert steps == [
        {
            "step": "12_핵심요약",
            "방식": "검증 완료 본문 재사용",
            "후보": 3,
            "규칙통과": 3,
            "AI호출": 0,
        }
    ]


def test_결정론_요약은_숫자와_AI사업은_허용하고_메타와_근거어부족만_버린다():
    candidates = [
        VerifiedSummarySource("identity", "매출 100억원 기업이다.", "fact-1", ("매출", "기업")),
        VerifiedSummarySource("business_model", "AI 분석 사업을 운영한다.", "fact-2", ("AI 분석", "사업")),
        VerifiedSummarySource("portfolio", "소재 제품을 판매한다.", "fact-3", ("소재",)),
        VerifiedSummarySource("culture", "존중과 책임을 핵심 가치로 삼는다.", "fact-4", ("존중", "책임")),
        VerifiedSummarySource("future_strategy", "검증 절차를 정리했다.", "fact-5", ("검증", "절차")),
    ]

    result, steps = build_summary_from_verified_claims(candidates)

    assert [item.fact_ids for item in result] == [
        ("fact-1",),
        ("fact-2",),
        ("fact-4",),
    ]
    assert steps[0]["AI호출"] == 0


def test_같은_장에서는_숫자없는_검증문장을_먼저_고른다():
    candidates = [
        VerifiedSummarySource("identity", "2026년 소재 기업이다.", "fact-1", ("소재", "기업")),
        VerifiedSummarySource("identity", "산업용 소재 기업이다.", "fact-2", ("산업용", "소재")),
        VerifiedSummarySource("business_model", "기업 고객에게 소재를 판매한다.", "fact-3", ("기업 고객", "소재")),
        VerifiedSummarySource("culture", "존중과 책임을 핵심 가치로 삼는다.", "fact-4", ("존중", "책임")),
    ]

    result, _steps = build_summary_from_verified_claims(candidates)

    assert result[0].fact_ids == ("fact-2",)


def test_숫자와_제작메타가_든_요약은_근거대조_전에_버린다():
    calls = 0

    def ask(prompt, schema, max_tokens):
        nonlocal calls
        calls += 1
        return (
            {
                "items": [
                    {"section_id": "identity", "text": "소재 전문기업이다"},
                    {"section_id": "business_model", "text": "매출 100억원을 만든다"},
                    {"section_id": "portfolio", "text": "AI가 검증한 제품군이다"},
                    {"section_id": "past_changes", "text": "사업 범위가 넓어졌다"},
                    {"section_id": "current_challenges", "text": "수익성 정착이 과제다"},
                ]
            }
            if calls == 1
            else {
                "판정": [
                    {"번호": 1, "근거에있다": True},
                    {"번호": 2, "근거에있다": True},
                    {"번호": 3, "근거에있다": True},
                ]
            },
            {},
        )

    result, _steps = build_summary_with_ai(
        ask,
        company="진영",
        sections={
            "identity": ["진영은 소재 전문기업이다"],
            "business_model": ["기업 고객에게 소재를 판매한다"],
            "portfolio": ["제품군을 운영한다"],
            "past_changes": ["사업 범위가 넓어졌다"],
            "current_challenges": ["수익성 정착이 과제다"],
        },
    )

    assert [item.section_id for item in result] == [
        "identity",
        "past_changes",
        "current_challenges",
    ]
    assert all(not any(char.isdigit() for char in item.text) for item in result)
