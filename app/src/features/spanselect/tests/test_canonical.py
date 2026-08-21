from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.features.spanselect.canonical import (
    CANONICAL_SOURCE_SECTION_IDS,
    answer_schema,
    build_prompt,
    historical_performance_basis_options,
    historical_performance_basis_sid,
    select_canonical_spans,
)
from src.features.pipeline.section567_contract import (
    expected_plan_status,
    has_current_operating_role,
    is_company_stated_plan_effect,
    is_observed_initial_signal,
    is_objective_next_check_metric,
    looks_like_customer_outbound,
    ownership_is_company_held,
    plan_timing_has_passed,
)
from src.features.pipeline.port import ReportTable


@dataclass(frozen=True)
class _DraftItem:
    sentence: str
    fragment_id: int | None
    block: str


@dataclass(frozen=True)
class _Checked:
    kept: list[_DraftItem]
    deleted: list[tuple[_DraftItem, str]]


class _Engine:
    MODEL = "old"
    GEN_MAX_TOKENS = 500
    DraftItem = _DraftItem

    @staticmethod
    def split_sentences(text: str) -> list[str]:
        return [part.strip() + "." for part in text.split(".") if part.strip()]

    @staticmethod
    def check_draft(items, originals, requirements):
        kept = [
            item
            for item in items
            if item.fragment_id in originals
            and item.sentence in originals[item.fragment_id]
        ]
        return _Checked(
            kept=kept,
            deleted=[(item, "원문 불일치") for item in items if item not in kept],
        )

    @staticmethod
    def _ask(client, prompt, schema, max_tokens):
        return client(prompt, schema), {"in": 1, "out": 1}


def test_정본_프롬프트는_의미_ID와_시간상태_분리를_강제한다():
    prompt = build_prompt(["[1-1] (홈페이지) 회사는 소재 전문기업이다."])

    assert all(section_id in prompt for section_id in CANONICAL_SOURCE_SECTION_IDS)
    assert "개발완료·검증·MOU·계약·납품·매출·반복매출" in prompt
    assert "직무별 KPI" in prompt
    assert "competitive_position" not in prompt


def test_선택기_응답은_정체성과_시장_의미를_분리한_닫힌_스키마다() -> None:
    item_schema = answer_schema()["properties"]["items"]["items"]
    claim_types = set(item_schema["properties"]["claim_type"]["enum"])
    required = set(item_schema["required"])

    assert {"identity_summary", "official_self_definition"} <= claim_types
    assert "official_identity" not in claim_types
    assert set(item_schema["properties"]["market_stage"]["enum"]) == {
        "",
        "핵심",
        "성장",
        "진입",
    }
    assert {"market_stage", "market_observation"} <= required
    assert "market_priority" not in item_schema["properties"]
    assert {
        "response_action",
        "initial_signal",
        "next_check_metric",
        "plan_status",
        "plan_timing",
        "plan_condition",
        "plan_expected_effect",
        "plan_execution_signal",
        "operation_role",
        "value_chain_stage",
        "relationship_type",
    } <= required


def test_AI는_번호와_배치만_고르고_원문과_섹션게이트가_최종결정한다():
    frags = {
        1: {"종류": "홈페이지", "원문": "진영은 친환경 소재 전문기업이다."},
        2: {
            "종류": "사업내용",
            "원문": "2026년 AlphaX 제품을 해외에 출시해 기업 고객에게 판매한다.",
        },
        3: {
            "종류": "사업내용",
            "원문": "2026년 AlphaX 제품을 해외에 출시할 계획이다.",
        },
        4: {
            "종류": "사업내용",
            "원문": "AlphaX 제품은 기업 고객 판매로 장비 매출을 만든다.",
        },
    }

    def answer(_prompt, _schema):
        return {
            "items": [
                {
                    "section_id": "identity",
                    "sid": "1-1",
                    "claim_type": "identity_summary",
                    "subject_label": "",
                    "market_stage": "",
                    "market_observation": "",
                    "product_role": "",
                    "response_to_sid": "",
                    "basis_sids": [],
                    "priority_signals": [],
                },
                {
                    "section_id": "business_model",
                    "sid": "4-1",
                    "claim_type": "revenue_model",
                    "subject_label": "AlphaX",
                    "market_stage": "",
                    "market_observation": "",
                    "product_role": "",
                    "portfolio_stage": "",
                    "revenue_model_sid": "",
                    "response_to_sid": "",
                    "basis_sids": [],
                    "priority_signals": [],
                },
                {
                    "section_id": "portfolio",
                    "sid": "2-1",
                    "claim_type": "priority_product",
                    "subject_label": "AlphaX",
                    "market_stage": "",
                    "market_observation": "",
                    "product_role": "기업 고객 판매 제품",
                    "portfolio_stage": "성장",
                    "revenue_model_sid": "4-1",
                    "response_to_sid": "",
                    "basis_sids": [],
                    "priority_signals": ["출시·운영", "유통·지역확대"],
                },
                # 같은 원문을 다른 장에 다시 넣으면 먼저 배치한 한 건만 남는다.
                {
                    "section_id": "past_changes",
                    "sid": "2-1",
                    "claim_type": "completed_execution",
                    "subject_label": "",
                    "market_stage": "",
                    "market_observation": "",
                    "product_role": "",
                    "response_to_sid": "",
                    "basis_sids": [],
                    "priority_signals": [],
                },
                {
                    "section_id": "future_strategy",
                    "sid": "3-1",
                    "claim_type": "future_plan",
                    "subject_label": "AlphaX",
                    "market_stage": "",
                    "market_observation": "",
                    "product_role": "",
                    "response_to_sid": "",
                    "basis_sids": [],
                    "priority_signals": [],
                    "plan_status": "announced",
                    "plan_timing": "2026년",
                    "plan_condition": "",
                    "plan_expected_effect": "",
                    "plan_execution_signal": "해외에 출시",
                },
                {
                    "section_id": "identity",
                    "sid": "999-1",
                    "claim_type": "identity_summary",
                    "subject_label": "",
                    "market_stage": "",
                    "market_observation": "",
                    "product_role": "",
                    "response_to_sid": "",
                    "basis_sids": [],
                    "priority_signals": [],
                },
            ]
        }

    steps: list[dict] = []
    kept, rejected = select_canonical_spans(
        answer,
        frags,
        steps,
        engine=_Engine(),
        company="진영",
    )

    assert [(item.section_id, item.fragment_id) for item in kept] == [
        ("identity", 1),
        ("business_model", 4),
        ("portfolio", 2),
        ("future_strategy", 3),
    ]
    assert any(item["reason"] == "같은 사실 중복 배치" for item in rejected)
    assert any(item["reason"] == "없는 번호 또는 섹션" for item in rejected)
    assert kept[0].sentence == "진영은 친환경 소재 전문기업이다."
    assert kept[2].claim_type == "priority_product"
    assert kept[2].product_role == "기업 고객 판매 제품"
    assert kept[2].portfolio_stage == "성장"
    assert kept[2].revenue_model_sid == "4-1"


def test_생산량_증가만으로_매출이용증가_신호를_만들지_않는다():
    from src.features.spanselect.canonical import PRIORITY_SIGNAL_PATTERNS

    sentence = "생산량을 늘려 국내 고객사에 공급 중이다."

    assert PRIORITY_SIGNAL_PATTERNS["생산확대"].search(sentence)
    assert PRIORITY_SIGNAL_PATTERNS["매출·이용증가"].search(sentence) is None


def _customer_market_item(
    *, market_stage: str, market_observation: str
) -> dict[str, object]:
    return {
        "section_id": "business_model",
        "sid": "1-1",
        "claim_type": "customer_market",
        "subject_label": "중국",
        "market_stage": market_stage,
        "market_observation": market_observation,
        "product_role": "",
        "portfolio_stage": "",
        "revenue_model_sid": "",
        "response_to_sid": "",
        "basis_sids": [],
        "priority_signals": [],
        "event_date": "",
    }


def test_단순_해외_매출은_시장_단계_없이_관찰로만_남긴다() -> None:
    sentence = "가나다는 중국에서 제품 매출이 발생했다."
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [
                _customer_market_item(
                    market_stage="",
                    market_observation="중국에서 제품 매출이 발생했다",
                )
            ]
        },
        {1: {"종류": "사업내용", "원문": sentence}},
        [],
        engine=_Engine(),
        company="가나다",
    )

    assert rejected == []
    assert len(kept) == 1
    assert kept[0].market_stage == ""
    assert kept[0].market_observation == "중국에서 제품 매출이 발생했다"


def test_매출_성장이라는_말만으로_성장_시장_단계를_만들지_않는다() -> None:
    sentence = "가나다의 중국 매출은 전년보다 성장했다."
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [
                _customer_market_item(
                    market_stage="성장",
                    market_observation="중국 매출은 전년보다 성장했다",
                )
            ]
        },
        {1: {"종류": "사업내용", "원문": sentence}},
        [],
        engine=_Engine(),
        company="가나다",
    )

    assert kept == []
    assert any(item["reason"] == "시장 단계의 직접 근거 없음" for item in rejected)


def test_원문이_성장_시장으로_직접_분류할_때만_단계를_받는다() -> None:
    sentence = "가나다는 중국을 성장 시장으로 분류하고 제품을 판매해 매출을 얻는다."
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [
                _customer_market_item(
                    market_stage="성장",
                    market_observation="중국을 성장 시장으로 분류",
                )
            ]
        },
        {1: {"종류": "사업내용", "원문": sentence}},
        [],
        engine=_Engine(),
        company="가나다",
    )

    assert rejected == []
    assert [item.market_stage for item in kept] == ["성장"]


def _section567_item(
    section_id: str,
    sid: str,
    claim_type: str,
    subject_label: str,
    **changes: object,
) -> dict[str, object]:
    item: dict[str, object] = {
        "section_id": section_id,
        "sid": sid,
        "claim_type": claim_type,
        "subject_label": subject_label,
        "market_stage": "",
        "market_observation": "",
        "product_role": "",
        "portfolio_stage": "",
        "revenue_model_sid": "",
        "response_to_sid": "",
        "basis_sids": [],
        "priority_signals": [],
        "event_date": "",
        "response_action": "",
        "initial_signal": "",
        "next_check_metric": "",
        "plan_status": "",
        "plan_timing": "",
        "plan_condition": "",
        "plan_expected_effect": "",
        "plan_execution_signal": "",
        "operation_role": "",
        "value_chain_stage": "",
        "relationship_type": "",
    }
    item.update(changes)
    return item


def test_현재_문제와_대응은_다음_지표와_초기_신호를_분리한다() -> None:
    frags = {
        1: {
            "종류": "MD&A",
            "원문": "가나다전자는 2026년 SmartX 원가율 부담을 현재 미해결 과제로 관리한다.",
        },
        2: {
            "종류": "MD&A",
            "원문": "가나다전자는 2026년 SmartX 원가 부담에 대응해 생산 공정 재설계를 추진 중이다.",
        },
    }
    items = [
        _section567_item(
            "current_challenges",
            "1-1",
            "current_issue",
            "SmartX 원가율 부담",
            next_check_metric="원가율",
        ),
        _section567_item(
            "current_challenges",
            "2-1",
            "current_response",
            "생산 공정 재설계",
            response_to_sid="1-1",
            response_action="생산 공정 재설계를 추진 중",
        ),
    ]

    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": items},
        frags,
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert rejected == []
    assert [item.next_check_metric for item in kept] == ["원가율", ""]
    assert kept[1].initial_signal == ""


def test_대응_문장_전체를_초기_신호로_중복할_수_없다() -> None:
    sentence = "가나다전자는 2026년 SmartX 원가 부담에 대응해 생산 공정 재설계를 추진 중이다."
    item = _section567_item(
        "current_challenges",
        "1-1",
        "current_response",
        "생산 공정 재설계",
        response_to_sid="issue-1",
        response_action="생산 공정 재설계를 추진 중",
        initial_signal=sentence.rstrip("."),
    )

    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": [item]},
        {1: {"종류": "MD&A", "원문": sentence}},
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert kept == []
    assert any(item["reason"] == "대응 행동을 초기 신호로 중복" for item in rejected)


def test_조건이_있는_미래_계획은_발표_상태로_낮춰_숨길_수_없다() -> None:
    sentence = "가나다전자는 2027년 미국 AlphaX 생산 설비 가동을 계획하며 현지 규제 허가 완료를 선행 조건으로 밝혔다."
    common = dict(
        plan_timing="2027년",
        plan_condition="현지 규제 허가 완료를 선행 조건",
        plan_execution_signal="AlphaX 생산 설비 가동",
    )

    rejected_pick, rejected = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [
                _section567_item(
                    "future_strategy",
                    "1-1",
                    "future_plan",
                    "AlphaX 생산 설비",
                    plan_status="announced",
                    **common,
                )
            ]
        },
        {1: {"종류": "전략", "원문": sentence}},
        [],
        engine=_Engine(),
        company="가나다전자",
    )
    kept, accepted_rejections = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [
                _section567_item(
                    "future_strategy",
                    "1-1",
                    "future_plan",
                    "AlphaX 생산 설비",
                    plan_status="conditional",
                    **common,
                )
            ]
        },
        {1: {"종류": "전략", "원문": sentence}},
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert rejected_pick == []
    assert any("승인·조건 상태" in item["reason"] for item in rejected)
    assert accepted_rejections == []
    assert [item.plan_status for item in kept] == ["conditional"]


def test_과거_결과를_미래_계획의_예상효과로_승격하지_않는다() -> None:
    sentence = (
        "가나다전자는 2025년 매출 증가를 확인했고 "
        "2027년 AlphaX 설비 가동을 계획했다."
    )
    item = _section567_item(
        "future_strategy",
        "1-1",
        "future_plan",
        "AlphaX 설비",
        plan_status="announced",
        plan_timing="2027년",
        plan_expected_effect="매출 증가",
        plan_execution_signal="AlphaX 설비 가동",
    )

    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": [item]},
        {1: {"종류": "전략", "원문": sentence}},
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert kept == []
    assert any(item["reason"] == "회사 제시 효과의 직접 근거 없음" for item in rejected)


def test_취소된_계획은_현재_미래전략으로_선택하지_않는다() -> None:
    sentence = "가나다전자는 2024년 AlphaX 출시 계획을 취소했다."
    item = _section567_item(
        "future_strategy",
        "1-1",
        "future_plan",
        "AlphaX",
        plan_status="announced",
        plan_timing="2024년",
        plan_execution_signal="AlphaX 출시",
    )

    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": [item]},
        {1: {"종류": "전략", "원문": sentence}},
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert kept == []
    assert any(item["reason"] == "취소·철회·중단된 계획" for item in rejected)


def test_고객사를_7장_내부운영이나_파트너로_분류하지_않는다() -> None:
    sentence = "가나다는 가구 제조 고객사와 유통 관계를 운영하며 제품을 판매한다."
    item = _section567_item(
        "operations_partners",
        "1-1",
        "partner_role",
        "가구 제조 고객사",
        operation_role="유통 관계를 운영",
        value_chain_stage="distribution",
        relationship_type="distribution",
    )

    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": [item]},
        {1: {"종류": "사업내용", "원문": sentence}},
        [],
        engine=_Engine(),
        company="가나다",
    )

    assert kept == []
    assert any(item["reason"] == "고객사를 운영 파트너로 분류" for item in rejected)


def test_이름만_있는_납품_대상도_7장_유통파트너로_승격하지_않는다() -> None:
    sentence = "가나다전자는 2026년 ABC 유통망에 SmartX 제품을 납품했다."
    item = _section567_item(
        "operations_partners",
        "1-1",
        "partner_role",
        "ABC 유통망",
        operation_role="ABC 유통망에 SmartX 제품을 납품",
        value_chain_stage="sales",
        relationship_type="distribution",
    )

    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": [item]},
        {1: {"종류": "사업내용", "원문": sentence}},
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert kept == []
    assert any(item["reason"] == "고객사를 운영 파트너로 분류" for item in rejected)


def test_외주사의_직접생산을_분석법인_내부운영으로_승격하지_않는다() -> None:
    sentence = "가나다전자는 2026년 외주 계약으로 AlphaWorks가 SmartX 제품을 직접 생산한다고 밝혔다."
    item = _section567_item(
        "operations_partners",
        "1-1",
        "operating_core",
        "AlphaWorks",
        operation_role="SmartX 제품을 직접 생산한다고",
        value_chain_stage="production",
        relationship_type="internal_operation",
    )

    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": [item]},
        {1: {"종류": "사업내용", "원문": sentence}},
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert kept == []
    assert any(item["reason"] == "분석 법인의 직접 운영 근거 없음" for item in rejected)


def test_가동준비는_현재_운영구조가_아니다() -> None:
    sentence = "가나다전자는 2027년 AlphaX 자체 생산 설비의 가동 준비를 위한 공급 계약을 검토 중이다."
    item = _section567_item(
        "operations_partners",
        "1-1",
        "operating_core",
        "AlphaX",
        operation_role="AlphaX 자체 생산 설비의 가동 준비",
        value_chain_stage="production",
        relationship_type="internal_operation",
    )

    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": [item]},
        {1: {"종류": "사업내용", "원문": sentence}},
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert kept == []
    assert any(item["reason"] == "미실행 계획을 현재 운영으로 분류" for item in rejected)


def test_실명파트너가_없어도_검증된_법인_내부운영은_7장에_남긴다() -> None:
    sentence = "가나다전자는 2026년 SmartX 자체 생산 공정을 직접 운영한다."
    item = _section567_item(
        "operations_partners",
        "1-1",
        "operating_core",
        "SmartX 자체 생산 공정",
        operation_role="SmartX 자체 생산 공정을 직접 운영한다",
        value_chain_stage="production",
        relationship_type="internal_operation",
    )

    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": [item]},
        {1: {"종류": "사업내용", "원문": sentence}},
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert rejected == []
    assert [pick.claim_type for pick in kept] == ["operating_core"]


def test_대응행동과_별도_초기신호는_서로_다른_원문절에서만_받는다() -> None:
    sentence = (
        "가나다전자는 2026년 SmartX 생산 공정 재설계를 추진 중이다; "
        "시험 생산에서 원가율 개선을 확인했다."
    )
    item = _section567_item(
        "current_challenges",
        "2-1",
        "current_response",
        "SmartX 생산 공정",
        response_to_sid="1-1",
        response_action="생산 공정 재설계를 추진 중",
        initial_signal="시험 생산에서 원가율 개선",
    )
    issue = _section567_item(
        "current_challenges",
        "1-1",
        "current_issue",
        "SmartX 원가율 부담",
        next_check_metric="원가율",
    )

    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": [issue, item]},
        {
            1: {
                "종류": "MD&A",
                "원문": "가나다전자는 2026년 SmartX 원가율 부담을 현재 미해결 과제로 관리한다.",
            },
            2: {"종류": "MD&A", "원문": sentence},
        },
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert rejected == []
    assert kept[1].initial_signal == "시험 생산에서 원가율 개선"


def test_계획_상태는_부정과_남은_승인조건을_먼저_읽는다() -> None:
    assert expected_plan_status("투자 계획은 미확정 상태다") == "announced"
    assert expected_plan_status("이사회 승인 완료 전인 계획이다") == "conditional"
    assert expected_plan_status("공식 조건 없음으로 발표했다") == "announced"
    assert expected_plan_status("투자 계획 확정 여부를 검토 중이다") == "announced"
    assert expected_plan_status("계획 확정을 위해 이사회 승인을 요청했다") == "conditional"
    assert expected_plan_status("이사회가 투자 계획을 승인했다") == "approved"
    assert expected_plan_status("규제기관 허가를 취득했고 출시를 계획했다") == "approved"
    assert expected_plan_status("파트너 계약을 체결했고 공동사업 가동을 계획했다") == "approved"
    for no_condition in (
        "선행 조건 없음",
        "승인 필요 없음",
        "파트너 확보 불필요",
        "자금 조달 조건 없음",
    ):
        assert expected_plan_status(f"투자 계획은 {no_condition}") == "announced"


def test_미관찰_상태명은_초기_진척으로_승격하지_않는다() -> None:
    assert all(
        not is_observed_initial_signal(value)
        for value in (
            "허가 필요",
            "승인 대기",
            "승인 요청",
            "가동 준비",
            "출시 검토",
            "매출 목표",
        )
    )
    assert all(
        is_observed_initial_signal(value)
        for value in (
            "허가 취득",
            "승인 완료",
            "가동 시작",
            "출시 완료",
            "매출 발생",
            "수주 확보",
        )
    )


def test_계획_예상효과는_회사_기대목표_절에_결속되어야_한다() -> None:
    past_result = "2025년 매출 증가를 확인했고 2027년 AlphaX 설비 가동을 계획했다."
    stated_effect = "회사는 원가 절감을 목표로 2027년 AlphaX 설비 가동을 계획했다."

    assert not is_company_stated_plan_effect(past_result, "매출 증가")
    assert is_company_stated_plan_effect(stated_effect, "원가 절감")


def test_지난_계획시점은_보고서_기준일로_계산한다() -> None:
    report_date = date(2026, 8, 19)

    assert plan_timing_has_passed("2024년", report_date)
    assert plan_timing_has_passed("2026년 상반기", report_date)
    assert not plan_timing_has_passed("2026년 하반기", report_date)
    assert not plan_timing_has_passed("2026~2028년", report_date)


def test_고객판매가_함께_적혀도_내부_판매망_자체는_고객사가_아니다() -> None:
    assert not looks_like_customer_outbound(
        "가나다는 직영 판매망을 운영해 고객사에 판매한다.", "직영 판매망"
    )
    assert not looks_like_customer_outbound(
        "가나다는 자체 플랫폼을 운영해 기업 고객에게 판매한다.", "자체 플랫폼"
    )
    assert not looks_like_customer_outbound(
        "가나다는 자체 플랫폼에서 고객사에 판매한다.", "자체 플랫폼"
    )
    assert looks_like_customer_outbound(
        "가나다는 Alpha사에 제품을 납품한다.", "Alpha사"
    )


def test_현재_운영역할은_긍정_상태와_종료상태를_구분한다() -> None:
    evidence = " ".join(
        (
            "생산 설비를 보유한다.",
            "생산 자회사 지분을 보유한다.",
            "제품을 유통하고 있다.",
            "제품을 판매하고 있다.",
            "공동 연구개발을 진행한다.",
        )
    )
    for role in (
        "생산 설비를 보유한다",
        "생산 자회사 지분을 보유한다",
        "제품을 유통하고 있다",
        "제품을 판매하고 있다",
        "공동 연구개발을 진행한다",
    ):
        assert has_current_operating_role(evidence, role)

    assert not has_current_operating_role(
        "계약 유효 여부를 검토 중이다.", "계약 유효 여부 검토"
    )
    assert not has_current_operating_role(
        "유통을 운영한다고 밝혔으나 계약 종료.",
        "유통을 운영한다고 밝혔으나 계약 종료",
    )
    assert ownership_is_company_held(
        "당사는 생산 자회사 지분을 보유한다.",
        "가나다전자",
        source_publisher="가나다전자",
    )
    assert not ownership_is_company_held(
        "당사는 생산 자회사 지분을 보유한다.",
        "가나다전자",
        source_publisher="다른회사",
    )


def test_업종별_객관지표는_받고_문제반복_여부는_거부한다() -> None:
    metrics = (
        "재고일수",
        "재고",
        "불량률",
        "유료 고객",
        "객단가",
        "장애",
        "점포당 판매",
        "연체율",
        "수주잔고",
        "납기",
        "마진",
        "채널별 마진",
        "제작비",
        "조달비용",
        "임상 이벤트",
        "재계약",
        "재구매",
        "조치 이행",
        "심사 진행",
        "사용 기관",
        "수가 적용",
        "IP별 기여",
        "비활동기 매출",
        "반복 활동",
    )
    assert all(is_objective_next_check_metric(value) for value in metrics)
    assert not any(
        is_objective_next_check_metric(value)
        for value in ("문제 여부", "과제 여부", "부담 여부", "해결 여부", "개선 여부")
    )


def test_MOU_체결만으로_현재_운영파트너가_되지는_않는다() -> None:
    sentence = "가나다전자는 2026년 AlphaWorks와 유통 협력 MOU를 체결했다."
    item = _section567_item(
        "operations_partners",
        "1-1",
        "partner_role",
        "AlphaWorks",
        operation_role="유통 협력",
        value_chain_stage="distribution",
        relationship_type="joint_business",
    )

    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": [item]},
        {1: {"종류": "사업내용", "원문": sentence}},
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert kept == []
    assert any(item["reason"] == "현재 반복 운영 역할의 직접 근거 없음" for item in rejected)


def test_대응은_다른_제품의_문제에_오연결할_수_없다() -> None:
    issue_sentence = "가나다전자는 2026년 SmartX 원가율 부담을 현재 미해결 과제로 관리한다."
    response_sentence = "가나다전자는 2026년 BetaY 재고 부담에 대응해 할인 판매를 진행 중이다."
    items = [
        _section567_item(
            "current_challenges",
            "1-1",
            "current_issue",
            "SmartX 원가율 부담",
            next_check_metric="원가율",
        ),
        _section567_item(
            "current_challenges",
            "2-1",
            "current_response",
            "BetaY 재고 부담",
            response_to_sid="1-1",
            response_action="할인 판매를 진행 중",
        ),
    ]

    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": items},
        {
            1: {"종류": "MD&A", "원문": issue_sentence},
            2: {"종류": "MD&A", "원문": response_sentence},
        },
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert [pick.claim_type for pick in kept] == ["current_issue"]
    assert any("문제 대상과 결속되지 않음" in item["reason"] for item in rejected)


def test_미래_계획문구를_현재_대응의_초기신호로_쓸_수_없다() -> None:
    response_sentence = (
        "가나다전자는 SmartX 공정 개선을 추진 중이다; "
        "내년 판매 확대를 계획한다."
    )
    items = [
        _section567_item(
            "current_challenges",
            "1-1",
            "current_issue",
            "SmartX 원가율 부담",
            next_check_metric="원가율",
        ),
        _section567_item(
            "current_challenges",
            "2-1",
            "current_response",
            "SmartX 공정 개선",
            response_to_sid="1-1",
            response_action="SmartX 공정 개선을 추진 중",
            initial_signal="내년 판매 확대를 계획",
        ),
    ]

    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": items},
        {
            1: {
                "종류": "MD&A",
                "원문": "가나다전자는 SmartX 원가율 부담을 현재 미해결 과제로 관리한다.",
            },
            2: {"종류": "MD&A", "원문": response_sentence},
        },
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert [pick.claim_type for pick in kept] == ["current_issue"]
    assert any(item["reason"] == "관찰된 초기 진척·결과가 아님" for item in rejected)


def test_현재_대응은_같은_답의_미해결_문제와_연결되어야_한다():
    frags = {
        1: {
            "종류": "사업내용",
            "원문": "진영은 2026년 PMMA 원가율 부담이 아직 남아 있다고 밝혔다.",
        },
        2: {
            "종류": "사업내용",
            "원문": "진영은 2026년 PMMA 생산비 절감에 착수했다.",
        },
    }

    def answer(_prompt, _schema):
        common = {
            "market_stage": "",
            "market_observation": "",
            "subject_label": "",
            "product_role": "",
            "basis_sids": [],
            "priority_signals": [],
        }
        return {
            "items": [
                {
                    **common,
                    "section_id": "current_challenges",
                    "sid": "1-1",
                    "claim_type": "current_issue",
                    "response_to_sid": "",
                    "next_check_metric": "원가율",
                },
                {
                    **common,
                    "section_id": "current_challenges",
                    "sid": "2-1",
                    "claim_type": "current_response",
                    "response_to_sid": "없는-sid",
                    "response_action": "PMMA 생산비 절감에 착수",
                },
            ]
        }

    kept, rejected = select_canonical_spans(
        answer,
        frags,
        [],
        engine=_Engine(),
        company="진영",
    )

    assert [item.claim_type for item in kept] == ["current_issue"]
    assert any("미해결 문제와 대응" in item["reason"] for item in rejected)


def _past_item(
    sid: str,
    claim_type: str,
    *,
    basis_sids: list[str] | None = None,
    event_date: str = "",
) -> dict[str, object]:
    return {
        "section_id": "past_changes",
        "sid": sid,
        "claim_type": claim_type,
        "subject_label": "",
        "market_stage": "",
        "market_observation": "",
        "product_role": "",
        "response_to_sid": "",
        "basis_sids": list(basis_sids or []),
        "priority_signals": [],
        "event_date": event_date,
    }


def test_변화_해석은_제공된_완료_실적_참조를_근거로_선택할_수_있다():
    reference = historical_performance_basis_sid(2025)
    frags = {
        1: {
            "종류": "MD&A",
            "원문": "가나다는 2025년 SmartX 매출이 2024년보다 증가했다고 밝혔다.",
        }
    }

    def answer(prompt, _schema):
        assert reference in prompt
        assert "fact-deadbeef" not in prompt
        return {
            "items": [
                _past_item(
                    "1-1",
                    "change_interpretation",
                    basis_sids=[reference],
                )
            ]
        }

    kept, rejected = select_canonical_spans(
        answer,
        frags,
        [],
        engine=_Engine(),
        company="가나다",
        historical_performance_bases={
            reference: "완료 사업연도 2025 · 매출액 120"
        },
    )

    assert rejected == []
    assert [item.basis_sids for item in kept] == [(reference,)]


def test_변화_해석은_목록에_없는_실적_참조와_내부_fact_id를_거부한다():
    allowed = historical_performance_basis_sid(2025)
    frags = {
        1: {
            "종류": "MD&A",
            "원문": "가나다는 2025년 SmartX 매출이 2024년보다 증가했다고 밝혔다.",
        }
    }

    for basis_sids in ([historical_performance_basis_sid(2024)], ["fact-deadbeef"]):
        kept, rejected = select_canonical_spans(
            lambda _prompt, _schema, values=basis_sids: {
                "items": [
                    _past_item(
                        "1-1",
                        "change_interpretation",
                        basis_sids=values,
                    )
                ]
            },
            frags,
            [],
            engine=_Engine(),
            company="가나다",
            historical_performance_bases={allowed: "2025 완료 실적"},
        )

        assert kept == []
        assert any("완료 실행·제공된 완료 실적" in item["reason"] for item in rejected)


def test_변화_해석은_중복_근거_참조를_조용히_합치지_않는다():
    reference = historical_performance_basis_sid(2025)
    frags = {
        1: {
            "종류": "MD&A",
            "원문": "가나다는 2025년 SmartX 매출이 2024년보다 증가했다고 밝혔다.",
        }
    }
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [
                _past_item(
                    "1-1",
                    "change_interpretation",
                    basis_sids=[reference, reference],
                )
            ]
        },
        frags,
        [],
        engine=_Engine(),
        company="가나다",
        historical_performance_bases={reference: "2025 완료 실적"},
    )

    assert kept == []
    assert any(item["reason"] == "변화 해석 근거 참조 중복" for item in rejected)


def test_변화_해석은_빈_근거_참조를_조용히_버리지_않는다():
    reference = historical_performance_basis_sid(2025)
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [
                _past_item(
                    "1-1",
                    "change_interpretation",
                    basis_sids=[reference, ""],
                )
            ]
        },
        {
            1: {
                "종류": "MD&A",
                "원문": "가나다는 2025년 SmartX 매출이 증가했다고 밝혔다.",
            }
        },
        [],
        engine=_Engine(),
        company="가나다",
        historical_performance_bases={reference: "2025 완료 실적"},
    )

    assert kept == []
    assert any(item["reason"] == "변화 해석 근거 참조 형식 오류" for item in rejected)


def test_기존_완료_실행_sid_근거_연결은_그대로_유지한다():
    frags = {
        1: {
            "종류": "MD&A",
            "원문": "가나다는 2025년 SmartX 생산 설비를 도입했다.",
        },
        2: {
            "종류": "MD&A",
            "원문": "가나다는 2025년 SmartX 매출이 증가했다고 밝혔다.",
        },
    }
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [
                _past_item("1-1", "completed_execution", event_date="2025"),
                _past_item(
                    "2-1",
                    "change_interpretation",
                    basis_sids=["1-1"],
                ),
            ]
        },
        frags,
        [],
        engine=_Engine(),
        company="가나다",
    )

    assert rejected == []
    assert [item.claim_type for item in kept] == [
        "completed_execution",
        "change_interpretation",
    ]
    assert kept[1].basis_sids == ("1-1",)


def test_완료_실적_참조는_유일한_정본_FY_행에서만_만든다():
    table = ReportTable(
        caption="전자공시 최근 세 사업연도 연결 주요 실적",
        headers=["사업연도", "매출액"],
        rows=[["2025", "120"], ["2024", "100"]],
        display_unit="억원",
    )

    options = historical_performance_basis_options([table])

    assert list(options) == [
        historical_performance_basis_sid(2025),
        historical_performance_basis_sid(2024),
    ]
    assert all(not reference.startswith("fact-") for reference in options)
    # 같은 FY가 두 표에 나타나면 내부 사실을 임의 선택하지 않는다.
    assert historical_performance_basis_options([table, table]) == {}
