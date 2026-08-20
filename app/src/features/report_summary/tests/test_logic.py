from src.features.report_summary.logic import build_summary_with_ai


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
