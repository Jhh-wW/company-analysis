from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from src.features.spanselect import canonical as canonical_module
from src.features.spanselect.canonical import (
    CANONICAL_SOURCE_SECTION_IDS,
    answer_schema,
    build_prompt,
    historical_performance_basis_options,
    historical_performance_basis_sid,
    select_canonical_spans,
)
from src.features.pipeline.section567_contract import (
    PLAN_EXECUTION_SIGNAL_PATTERN,
    PLAN_TIMING_PATTERN,
    expected_plan_status,
    has_current_operating_role,
    has_executed_current_distribution_partnership,
    is_company_stated_plan_effect,
    is_observed_initial_signal,
    is_objective_next_check_metric,
    looks_like_customer_outbound,
    ownership_is_company_held,
    plan_timing_has_passed,
)
from src.features.pipeline.port import ReportTable
from src.features.spanselect.constants import CANONICAL_SELECTION_MAX_TOKENS
from src.features.spanselect.logic import number_sentences
from src.shared.official_ir import (
    IR_ATTACHMENT_URL_FIELD,
    IR_COLLECTED_ON_FIELD,
    IR_METADATA_VERIFICATION_FIELD,
    IR_METADATA_VERIFICATION_VALUE,
    IR_REPORTING_PERIOD_FIELD,
)


@dataclass(frozen=True)
class _DraftItem:
    sentence: str
    fragment_id: int | None
    block: str


@dataclass(frozen=True)
class _Checked:
    kept: list[_DraftItem]
    deleted: list[tuple[_DraftItem, str]]


@dataclass(frozen=True)
class _GateDecision:
    passed: bool
    score: int
    reason: str = ""


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


def test_provider_출력상한_파싱실패를_선택단계에_원문없이_남긴다() -> None:
    class 절단엔진(_Engine):
        @staticmethod
        def _ask(client, prompt, schema, max_tokens):
            return None, {
                "in": 2,
                "out": CANONICAL_SELECTION_MAX_TOKENS,
                "requested_max_tokens": CANONICAL_SELECTION_MAX_TOKENS,
                "stop_reason": "max_tokens",
                "output_limit_reached": True,
                "parse_failed": True,
                "error": "파싱실패",
            }

    steps: list[dict] = []
    kept, rejected = select_canonical_spans(
        None,
        {1: {"종류": "사업", "원문": "가나다전자는 기업 고객에게 검사 장비를 판매하고 있다."}},
        steps,
        engine=절단엔진(),
        company="가나다전자",
    )

    assert (kept, rejected) == ([], [])
    diagnostic = steps[0]["span_selection_diagnostic"]
    assert diagnostic["output_tokens"] == CANONICAL_SELECTION_MAX_TOKENS
    assert diagnostic["requested_max_tokens"] == CANONICAL_SELECTION_MAX_TOKENS
    assert diagnostic["output_limit_reached"] is True
    assert diagnostic["parse_failed"] is True
    assert diagnostic["provider_selected"] == 0
    assert diagnostic["validation_kept"] == 0
    assert diagnostic["validation_rejected"] == 0
    assert diagnostic["validation_rejection_reason_counts"] == ()
    assert diagnostic["empty_reason"] == "output_limit_empty"
    assert set(diagnostic) == {
        "requested_max_tokens",
        "output_tokens",
        "provider_stop_reason",
        "output_limit_reached",
        "parse_failed",
        "provider_selected",
        "validation_kept",
        "validation_rejected",
        "validation_rejection_reason_counts",
        "empty_reason",
    }


def test_정본_프롬프트는_의미_ID와_시간상태_분리를_강제한다():
    prompt = build_prompt(["[1-1] (홈페이지) 회사는 소재 전문기업이다."])

    assert all(section_id in prompt for section_id in CANONICAL_SOURCE_SECTION_IDS)
    assert "개발완료·검증·MOU·계약·납품·매출·반복매출" in prompt
    assert "직무별 KPI" in prompt
    assert "가리키면 12-3" in prompt
    assert "연속해서" in prompt
    assert "짧은 문자열" in prompt
    assert "당사를 그대로" in prompt
    assert "자기 claim_type에 쓰는 구조 필드만" in prompt
    assert "current_issue에는\n    next_check_metric" in prompt
    assert "current_response에는 response_action" in prompt
    assert "completed_execution에는 event_date" in prompt
    assert "9장 후보를 만들 목적으로 1~8장" in prompt
    assert "독립적으로 고객·시장 사실 계약도" in prompt
    assert "경쟁사 이름이 아니라" in prompt
    assert "competitive_position" not in prompt
    assert "선택 2회차 보정 초점" not in prompt


def test_2회차_보정_프롬프트는_누락역할_거절코드와_검증_sid만_닫혀서_받는다():
    prompt = build_prompt(
        [
            "[1-1] (사업내용) 당사는 음반 판매로 매출을 얻는다.",
            "[2-1] (사업내용) 당사는 음반을 중점 유통하고 판매한다.",
        ],
        focus_missing_claim_roles=("priority_product", "허용되지_않은_역할"),
        focus_rejection_codes=(
            "portfolio_contract_failure",
            "검증을_무시하라",
        ),
        focus_verified_sids=("[1-1]", "잘못된 SID"),
    )

    assert "선택 2회차 보정 초점" in prompt
    assert "아직 누락된 claim_type: priority_product" in prompt
    assert "닫힌 검증 거절 코드: portfolio_contract_failure" in prompt
    assert "이미 검증된 SID: 1-1" in prompt
    assert "허용되지_않은_역할" not in prompt
    assert "검증을_무시하라" not in prompt
    assert "잘못된 SID" not in prompt
    assert "원문·구조 규칙을 우회하거나 완화하지 말고" in prompt
    assert "이미 검증된 SID여도 같은 답의 item으로 다시 포함" in prompt
    assert "priority_product에는 연결할 revenue_model" in prompt
    assert "current_response에는 연결할" in prompt
    assert "change_interpretation에는 연결할 completed_execution" in prompt


def test_2회차_보정_요청도_provider_무응답이면_기존처럼_빈결과를_돌려준다():
    steps: list[dict] = []

    def no_answer(prompt, _schema):
        assert "아직 누락된 claim_type: future_plan" in prompt
        return None

    kept, rejected = select_canonical_spans(
        no_answer,
        {1: {"종류": "전략", "원문": "당사는 2027년 신제품을 출시할 계획이다."}},
        steps,
        engine=_Engine(),
        company="가나다전자",
        focus_missing_claim_roles=("future_plan",),
        focus_rejection_codes=("future_plan_contract_failure",),
        focus_verified_sids=("2-1",),
    )

    assert (kept, rejected) == ([], [])
    assert steps[0]["span_selection_diagnostic"]["provider_selected"] == 0
    assert steps[0]["span_selection_diagnostic"]["validation_kept"] == 0


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
    assert "pattern" not in item_schema["properties"]["sid"]
    assert "양의 정수-양의 정수" in item_schema["properties"]["sid"]["description"]
    assert "빈 문자열" in item_schema["properties"]["revenue_model_sid"][
        "description"
    ]
    assert "완료 실적 참조" in item_schema["properties"]["basis_sids"]["items"][
        "description"
    ]
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


def test_후보표시에_끌려_sid에_대괄호를_넣어도_같은_문장을_찾는다() -> None:
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [
                {
                    "section_id": "identity",
                    "sid": "[1-1]",
                    "claim_type": "identity_summary",
                    "subject_label": "",
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
            ]
        },
        {1: {"종류": "홈페이지", "원문": "진영은 친환경 소재 전문기업이다."}},
        [],
        engine=_Engine(),
        company="진영",
    )

    assert [item.sid for item in kept] == ["1-1"]
    assert rejected == []


def test_sid_대괄호_정규화는_중복을_우회하지_못한다() -> None:
    common = {
        "section_id": "identity",
        "claim_type": "identity_summary",
        "subject_label": "",
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
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [{**common, "sid": "1-1"}, {**common, "sid": "[1-1]"}]
        },
        {1: {"종류": "홈페이지", "원문": "진영은 친환경 소재 전문기업이다."}},
        [],
        engine=_Engine(),
        company="진영",
    )

    assert [item.sid for item in kept] == ["1-1"]
    assert [item["reason"] for item in rejected] == ["같은 사실 중복 배치"]


def test_sid_이중_또는_편측_대괄호는_정규화하지_않는다() -> None:
    invalid_sids = ("[[1-1]]", "[1-1", "1-1]", "[0-1]", "[1-0]")
    common = {
        "section_id": "identity",
        "claim_type": "identity_summary",
        "subject_label": "",
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
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [{**common, "sid": sid} for sid in invalid_sids]
        },
        {1: {"종류": "홈페이지", "원문": "진영은 친환경 소재 전문기업이다."}},
        [],
        engine=_Engine(),
        company="진영",
    )

    assert kept == []
    assert len(rejected) == len(invalid_sids)
    assert {item["reason"] for item in rejected} == {"없는 번호 또는 섹션"}


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
                    "product_role": "기업 고객에게 판매",
                    "portfolio_stage": "성장",
                    "revenue_model_sid": "[4-1]",
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
    assert kept[2].revenue_model_sid == "4-1"
    assert kept[2].product_role == "기업 고객에게 판매"
    assert kept[2].portfolio_stage == ""
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


def test_2회차_보정_focus도_잘못된_대상_계획상태_운영관계를_통과시키지_않는다():
    def answer(prompt, _schema):
        assert "아직 누락된 claim_type: customer_market, future_plan, partner_role" in prompt
        assert "이미 검증된 SID: 9-1" in prompt
        return {
            "items": [
                _section567_item(
                    "business_model",
                    "1-1",
                    "customer_market",
                    "원문에 없는 시장",
                    market_observation="중국 시장에 판매",
                ),
                _section567_item(
                    "future_strategy",
                    "2-1",
                    "future_plan",
                    "FANS 플랫폼",
                    plan_status="approved",
                    plan_timing="2027년",
                    plan_execution_signal="출시",
                ),
                _section567_item(
                    "operations_partners",
                    "3-1",
                    "partner_role",
                    "ABC 고객",
                    operation_role="ABC 고객에게 SmartX 제품을 유통해 판매하고 있다",
                    value_chain_stage="distribution",
                    relationship_type="distribution",
                ),
            ]
        }

    steps: list[dict] = []
    kept, rejected = select_canonical_spans(
        answer,
        {
            1: {
                "종류": "사업내용",
                "원문": "당사는 중국 시장에 판매해 제품 매출을 만든다.",
            },
            2: {
                "종류": "전략",
                "원문": "당사는 2027년 FANS 플랫폼을 출시할 계획이다.",
            },
            3: {
                "종류": "사업내용",
                "원문": "당사는 ABC 고객에게 SmartX 제품을 유통해 판매하고 있다.",
            },
        },
        steps,
        engine=_Engine(),
        company="가나다전자",
        focus_missing_claim_roles=(
            "customer_market",
            "future_plan",
            "partner_role",
        ),
        focus_rejection_codes=(
            "subject_label_not_in_source",
            "future_plan_contract_failure",
            "operations_partner_contract_failure",
        ),
        focus_verified_sids=("9-1",),
    )

    assert kept == []
    assert {item["reason"] for item in rejected} == {
        "대상 이름이 원문에 없음",
        "계획 승인·조건 상태가 원문과 다름",
        "고객사를 운영 파트너로 분류",
    }
    assert set(
        steps[0]["span_selection_diagnostic"][
            "validation_rejection_reason_counts"
        ]
    ) == {
        ("subject_label_not_in_source", 1),
        ("future_plan_contract_failure", 1),
        ("operations_partner_contract_failure", 1),
    }


def test_고객시장_계약도_충족하는_경쟁문장은_실제_시장범위를_보존한다() -> None:
    sentence = (
        "가나다전자는 SmartX 제품을 판매하며 SmartX 시장에서 "
        "주식회사 베타와 경쟁 관계에 있다."
    )
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [
                _section567_item(
                    "business_model",
                    "1-1",
                    "customer_market",
                    "SmartX 시장",
                    market_observation="SmartX 시장에서 주식회사 베타와 경쟁 관계",
                )
            ]
        },
        {1: {"종류": "사업보고서", "원문": sentence}},
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert rejected == []
    assert len(kept) == 1
    assert kept[0].section_id == "business_model"
    assert kept[0].claim_type == "customer_market"
    assert kept[0].subject_label == "SmartX 시장"
    assert (
        kept[0].market_observation
        == "SmartX 시장에서 주식회사 베타와 경쟁 관계"
    )


def test_순수_경쟁사_문장을_고객시장_사실로_위장하지_않는다() -> None:
    sentence = "주요 경쟁사는 주식회사 베타와 주식회사 감마이다."

    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [
                _section567_item(
                    "business_model",
                    "1-1",
                    "customer_market",
                    "주식회사 베타",
                    market_observation="경쟁사는 주식회사 베타",
                )
            ]
        },
        {1: {"종류": "사업보고서", "원문": sentence}},
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert kept == []
    assert rejected


def test_선택사항인_대상라벨이_원문에_없으면_라벨만_버리고_원문을_검증한다(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        canonical_module,
        "assess_claim",
        lambda *_args, **_kwargs: _GateDecision(True, 1, ""),
    )
    steps: list[dict] = []
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [
                _section567_item(
                    "identity",
                    "1-1",
                    "identity_summary",
                    "원문에 없는 회사명",
                )
            ]
        },
        {1: {"종류": "홈페이지", "원문": "당사는 친환경 소재 전문기업이다."}},
        steps,
        engine=_Engine(),
        company="가나다전자",
    )

    assert [item.subject_label for item in kept] == [""]
    assert rejected == []
    diagnostic = steps[0]["span_selection_diagnostic"]
    assert diagnostic["validation_kept"] == 1
    assert diagnostic["validation_rejected"] == 0


def test_필수_대상라벨은_원문에_없으면_종전처럼_거절한다() -> None:
    steps: list[dict] = []
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [
                _section567_item(
                    "business_model",
                    "1-1",
                    "customer_market",
                    "원문에 없는 시장",
                    market_observation="중국 시장에 판매",
                )
            ]
        },
        {
            1: {
                "종류": "사업내용",
                "원문": "가나다전자는 중국 시장에 판매해 제품 매출을 만든다.",
            }
        },
        steps,
        engine=_Engine(),
        company="가나다전자",
    )

    assert kept == []
    assert [item["reason"] for item in rejected] == ["대상 이름이 원문에 없음"]
    assert steps[0]["span_selection_diagnostic"][
        "validation_rejection_reason_counts"
    ] == (("subject_label_not_in_source", 1),)


def _jyp_verified_ir_fragment(text: str) -> dict[str, str]:
    corp_code = "00258689"
    corp_name = "(주)제이와이피엔터테인먼트"
    profile = (
        '{"corp_code":"00258689","corp_name":'
        '"(주)제이와이피엔터테인먼트","hm_url":"https://www.jype.com"}'
    )
    return {
        "종류": "공식 IR",
        "원문": text,
        "출처": "https://www.jype.com/ko/board/ir-data/22sgoywh",
        "발행처": corp_name,
        "문서일": "2026-08-12",
        "후보출처검증": "https_exact_dart_host",
        "도메인근거SourceID": f"dart-company-profile-{corp_code}",
        "도메인근거원문": profile,
        IR_METADATA_VERIFICATION_FIELD: IR_METADATA_VERIFICATION_VALUE,
        IR_REPORTING_PERIOD_FIELD: "2026-Q2",
        IR_ATTACHMENT_URL_FIELD: "https://cdn.jype.com/ir/26Q2_Result_EN.pdf",
        IR_COLLECTED_ON_FIELD: "2026-08-24",
    }


_JYP_COMPANY_IDENTITY = (
    "(주)제이와이피엔터테인먼트 JYP Ent. JYP Entertainment Corporation"
)


def _production_style_split_sentences(text: str) -> list[str]:
    """운영 splitter처럼 종결 표지가 없는 마지막 꼬리를 버린다."""
    pieces = [text.strip()] if len(text.strip()) >= 20 else []
    if pieces and not pieces[-1].rstrip().endswith(
        (".", "!", "?", "다", "음", "됨", ")")
    ):
        pieces = pieces[:-1]
    return pieces[:12]


def test_검증된_JYP_영문IR은_splitter가_버린_무마침표_원문도_후보로_보존한다() -> None:
    original = "Stray Kids IP Leverage Impact Maximization in 2026 H2 & 2027"

    sent_map, lines, _excluded = number_sentences(
        {1: _jyp_verified_ir_fragment(original)},
        _production_style_split_sentences,
    )

    assert sent_map == {"1-1": (1, original)}
    assert lines == [f"[1-1] (공식 IR) {original}"]


def test_일반출처의_무마침표_영문꼬리는_후보로_복구하지_않는다() -> None:
    original = "Stray Kids IP Leverage Impact Maximization in 2026 H2 & 2027"

    for kind in ("홈페이지", "뉴스"):
        sent_map, lines, _excluded = number_sentences(
            {1: {"종류": kind, "원문": original}},
            _production_style_split_sentences,
        )

        assert sent_map == {}
        assert lines == []


def _jyp_future_plan_item() -> dict[str, object]:
    return _section567_item(
        "future_strategy",
        "1-1",
        "future_plan",
        "Stray Kids",
        plan_status="announced",
        plan_timing="2026 H2 & 2027",
        plan_execution_signal="IP Leverage Impact Maximization",
    )


def test_DART결속_JYP_영문IR은_미래계획_후보와_H2를_보존한다() -> None:
    sentence = "Stray Kids IP Leverage Impact Maximization in 2026 H2 & 2027."
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": [_jyp_future_plan_item()]},
        {1: _jyp_verified_ir_fragment(sentence)},
        [],
        engine=_Engine(),
        company=_JYP_COMPANY_IDENTITY,
    )

    assert rejected == []
    assert [(pick.claim_type, pick.subject_label) for pick in kept] == [
        ("future_plan", "Stray Kids")
    ]
    assert kept[0].plan_timing == "2026 H2 & 2027"


def test_JYP_영문_IR의_현재공연실적은_제품축과_우선신호를_보존한다() -> None:
    revenue_sentence = (
        "제이와이피엔터테인먼트는 공연 티켓과 MD를 판매해 매출을 얻는다."
    )
    portfolio_sentence = (
        "2026 Q2 2025 Q2 YoY(%) Change Expansion of domestic & overseas "
        "streaming sales; continuous global sales of Stray Kids catalogues. "
        "Physical 37.0 BN, +36.7% YoY. Steady growth in Stray Kids catalogues "
        "and increased new releases."
    )
    items = [
        _section567_item(
            "business_model",
            "1-1",
            "revenue_model",
            "",
        ),
        _section567_item(
            "portfolio",
            "2-1",
            "priority_product",
            "Stray Kids",
            product_role="Stray Kids catalogues",
            portfolio_stage="",
            revenue_model_sid="1-1",
            priority_signals=["매출·이용증가", "유통·지역확대"],
        ),
    ]
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": items},
        {
            1: {"종류": "사업내용", "원문": revenue_sentence},
            2: _jyp_verified_ir_fragment(portfolio_sentence),
        },
        [],
        engine=_Engine(),
        company=_JYP_COMPANY_IDENTITY,
    )

    assert rejected == []
    assert [pick.claim_type for pick in kept] == [
        "revenue_model",
        "priority_product",
    ]
    assert kept[1].priority_signals == ("매출·이용증가", "유통·지역확대")


def test_JYP_영문IR의_현재문제는_객관지표와_미해결표현이_함께_있을때만_보존한다() -> None:
    sentence = (
        "Decline in operating profit and OPM due to limited leverage and "
        "increased SG&A commission fee."
    )
    item = _section567_item(
        "current_challenges",
        "1-1",
        "current_issue",
        "operating profit and OPM",
        next_check_metric="OPM",
    )
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": [item]},
        {1: _jyp_verified_ir_fragment(sentence)},
        [],
        engine=_Engine(),
        company=_JYP_COMPANY_IDENTITY,
    )

    assert rejected == []
    assert [(pick.claim_type, pick.next_check_metric) for pick in kept] == [
        ("current_issue", "OPM")
    ]

    unverified = _jyp_verified_ir_fragment(sentence)
    unverified["도메인근거SourceID"] = ""
    blocked, blocked_reasons = select_canonical_spans(
        lambda _prompt, _schema: {"items": [item]},
        {1: unverified},
        [],
        engine=_Engine(),
        company=_JYP_COMPANY_IDENTITY,
    )
    assert blocked == []
    assert blocked_reasons


def test_JYP_영문IR이어도_원문에_없는_제품역할은_만들지_않는다() -> None:
    revenue_sentence = (
        "제이와이피엔터테인먼트는 공연 티켓과 MD를 판매해 매출을 얻는다."
    )
    portfolio_sentence = (
        "2026 Q2 2025 Q2 YoY(%) Change Expansion of domestic & overseas "
        "streaming sales; continuous global sales of Stray Kids catalogues. "
        "Physical 37.0 BN, +36.7% YoY. Steady growth in Stray Kids catalogues "
        "and increased new releases."
    )
    items = [
        _section567_item(
            "business_model",
            "1-1",
            "revenue_model",
            "",
        ),
        _section567_item(
            "portfolio",
            "2-1",
            "priority_product",
            "Stray Kids",
            product_role="global revenue leader",
            revenue_model_sid="1-1",
            priority_signals=["출시·운영", "매출·이용증가"],
        ),
    ]
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": items},
        {
            1: {"종류": "사업내용", "원문": revenue_sentence},
            2: _jyp_verified_ir_fragment(portfolio_sentence),
        },
        [],
        engine=_Engine(),
        company=_JYP_COMPANY_IDENTITY,
    )

    assert [pick.claim_type for pick in kept] == ["revenue_model"]
    assert any(item["reason"] == "제품 포트폴리오 역할 없음" for item in rejected)


def test_IR영문_회사특이성예외는_뉴스_홈페이지_미검증IR_다른회사에_열리지_않는다() -> None:
    sentence = (
        "Decline in operating profit and OPM due to limited leverage and "
        "increased SG&A commission fee."
    )
    item = _section567_item(
        "current_challenges",
        "1-1",
        "current_issue",
        "operating profit and OPM",
        next_check_metric="OPM",
    )
    valid = _jyp_verified_ir_fragment(sentence)
    news = {**valid, "종류": "뉴스"}
    homepage = {**valid, "종류": "홈페이지"}
    unverified = {**valid, "도메인근거SourceID": ""}
    wrong_company = {
        **valid,
        "발행처": "(주)에스엠엔터테인먼트",
        "도메인근거SourceID": "dart-company-profile-00136377",
        "도메인근거원문": (
            '{"corp_code":"00136377","corp_name":"(주)에스엠엔터테인먼트",'
            '"hm_url":"https://www.jype.com"}'
        ),
    }

    for fragment in (news, homepage, unverified, wrong_company):
        kept, rejected = select_canonical_spans(
            lambda _prompt, _schema: {"items": [item]},
            {1: fragment},
            [],
            engine=_Engine(),
            company=_JYP_COMPANY_IDENTITY,
        )

        assert kept == []
        assert rejected


def test_DART결속_영문IR도_취소된_계획은_살리지_않는다() -> None:
    sentence = (
        "Stray Kids IP Leverage Impact Maximization in 2026 H2 & 2027 was cancelled."
    )
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": [_jyp_future_plan_item()]},
        {1: _jyp_verified_ir_fragment(sentence)},
        [],
        engine=_Engine(),
        company=_JYP_COMPANY_IDENTITY,
    )

    assert kept == []
    assert any(item["reason"] == "취소·철회·중단된 계획" for item in rejected)


def test_다른_분석회사에는_JYP_영문IR_미래계획_예외가_열리지_않는다() -> None:
    sentence = "Stray Kids IP Leverage Impact Maximization in 2026 H2 & 2027."

    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": [_jyp_future_plan_item()]},
        {1: _jyp_verified_ir_fragment(sentence)},
        [],
        engine=_Engine(),
        company="(주)에스엠엔터테인먼트",
    )

    assert kept == []
    assert rejected


def test_대상라벨의_따옴표와_붙임표_오류는_원문_표기로만_복구한다(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        canonical_module,
        "assess_claim",
        lambda *_args, **_kwargs: _GateDecision(True, 1, ""),
    )
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [
                _section567_item(
                    "business_model",
                    "1-1",
                    "customer_market",
                    "‘Blue-Garage’",
                    market_observation="Blue Garage 시장에 판매",
                )
            ]
        },
        {
            1: {
                "종류": "사업내용",
                "원문": "가나다전자는 Blue Garage 시장에 판매해 제품 매출을 만든다.",
            }
        },
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert rejected == []
    assert [item.subject_label for item in kept] == ["Blue Garage"]


def test_대상라벨은_유사한_다른_이름으로_의미복구하지_않는다() -> None:
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [
                _section567_item(
                    "business_model",
                    "1-1",
                    "customer_market",
                    "Blue Garden",
                    market_observation="Blue Garage 시장에 판매",
                )
            ]
        },
        {
            1: {
                "종류": "사업내용",
                "원문": "가나다전자는 Blue Garage 시장에 판매해 제품 매출을 만든다.",
            }
        },
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert kept == []
    assert [item["reason"] for item in rejected] == ["대상 이름이 원문에 없음"]


def test_subject_label은_원문의_줄바꿈만_공백으로_정규화해_대조한다(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        canonical_module,
        "assess_claim",
        lambda *_args, **_kwargs: _GateDecision(True, 1, ""),
    )
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [
                _section567_item(
                    "identity",
                    "1-1",
                    "identity_summary",
                    "SmartX 제품",
                )
            ]
        },
        {1: {"종류": "홈페이지", "원문": "당사는 SmartX\n제품 전문기업이다."}},
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert [item.subject_label for item in kept] == ["SmartX 제품"]
    assert rejected == []


def test_여러_원문에서_검증된_혼합대소문자_실명을_회사특이성에_사용한다() -> None:
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [
                _section567_item(
                    "current_challenges",
                    "1-1",
                    "current_issue",
                    "Blue Garage",
                    next_check_metric="재계약 여부",
                )
            ]
        },
        {
            1: {
                "종류": "MD&A",
                "원문": (
                    "가나다전자는 Blue Garage 매출 의존을 현재 과제로 관리하며 "
                    "재계약 여부를 확인한다."
                ),
            },
            2: {
                "종류": "사업내용",
                "원문": "Blue Garage 계약은 북미 음원 유통 범위를 포함한다.",
            },
        },
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert rejected == []
    assert [item.subject_label for item in kept] == ["Blue Garage"]


def test_원문결속된_미래계획은_구형_실명_휴리스틱만으로_다시_삭제하지_않는다() -> None:
    sentence = (
        "가나다전자는 2027년 북미 음원 플랫폼을 구축할 계획이며 "
        "출시를 실행 확인 신호로 제시했다."
    )
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [
                _section567_item(
                    "future_strategy",
                    "1-1",
                    "future_plan",
                    "북미 음원 플랫폼",
                    plan_status="announced",
                    plan_timing="2027년",
                    plan_execution_signal="출시",
                )
            ]
        },
        {1: {"종류": "전략", "원문": sentence}},
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert rejected == []
    assert [item.claim_type for item in kept] == ["future_plan"]


def test_원문결속이_되어도_오래된_계획은_회사특이성_예외로_살리지_않는다() -> None:
    sentence = (
        "가나다전자는 2020년 북미 음원 플랫폼을 구축할 계획이며 "
        "출시를 실행 확인 신호로 제시했다."
    )
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [
                _section567_item(
                    "future_strategy",
                    "1-1",
                    "future_plan",
                    "북미 음원 플랫폼",
                    plan_status="announced",
                    plan_timing="2020년",
                    plan_execution_signal="출시",
                )
            ]
        },
        {1: {"종류": "전략", "원문": sentence}},
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert kept == []
    assert [item["reason"] for item in rejected] == [
        "최근 3년보다 오래된 계획 근거"
    ]


def test_후단에서_탈락한_대상을_참조하는_항목도_최종_제외된다(
    monkeypatch,
) -> None:
    items = [
        _section567_item(
            "current_challenges",
            "1-1",
            "current_issue",
            "SmartX",
            next_check_metric="원가율",
        ),
        _section567_item(
            "current_challenges",
            "2-1",
            "current_response",
            "SmartX",
            response_to_sid="[1-1]",
            response_action="SmartX 생산비 절감을 추진 중",
        ),
    ]
    monkeypatch.setattr(
        canonical_module,
        "assess_claim",
        lambda _section, sentence, **_kwargs: _GateDecision(
            passed="원가율 부담" not in sentence,
            score=0,
            reason="회사 고유 위험·변화 근거가 없음",
        ),
    )
    steps: list[dict] = []
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": items},
        {
            1: {
                "종류": "MD&A",
                "원문": "가나다전자는 SmartX 원가율 부담을 현재 미해결 과제로 관리한다.",
            },
            2: {
                "종류": "MD&A",
                "원문": "가나다전자는 SmartX 원가 부담에 대응해 SmartX 생산비 절감을 추진 중이다.",
            },
        },
        steps,
        engine=_Engine(),
        company="가나다전자",
    )

    assert kept == []
    assert len(rejected) == 2
    assert any("미해결 문제와 대응" in item["reason"] for item in rejected)
    assert steps[-1]["유지"] == 0
    assert steps[-1]["삭제"] == 2
    assert steps[0]["span_selection_diagnostic"][
        "validation_rejection_reason_counts"
    ] == (
        ("company_specificity_failure", 1),
        ("cross_reference_contract_failure", 1),
    )


def test_후단에서_수익모델이_탈락하면_중점제품도_최종_제외된다(
    monkeypatch,
) -> None:
    revenue = _section567_item(
        "business_model",
        "1-1",
        "revenue_model",
        "SmartX",
    )
    priority = _section567_item(
        "portfolio",
        "2-1",
        "priority_product",
        "SmartX",
        product_role="해외 판매망",
        revenue_model_sid="[1-1]",
        priority_signals=["출시·운영", "유통·지역확대"],
    )
    monkeypatch.setattr(
        canonical_module,
        "assess_claim",
        lambda section, _sentence, **_kwargs: _GateDecision(
            passed=section != "business_model",
            score=0,
            reason="사업·수익 방식이 없음",
        ),
    )

    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": [revenue, priority]},
        {
            1: {
                "종류": "사업내용",
                "원문": "가나다전자는 SmartX를 기업 고객에게 판매해 제품 매출을 만든다.",
            },
            2: {
                "종류": "사업내용",
                "원문": "가나다전자는 SmartX를 출시하고 해외 판매망을 확대했다.",
            },
        },
        [],
        engine=_Engine(),
        company="가나다전자",
    )

    assert kept == []
    assert any("2장 수익 분류" in item["reason"] for item in rejected)


def test_후단에서_완료실행이_탈락하면_변화해석도_최종_제외된다(
    monkeypatch,
) -> None:
    completed = _past_item("1-1", "completed_execution", event_date="2025")
    change = _past_item(
        "2-1",
        "change_interpretation",
        basis_sids=["[1-1]"],
    )
    monkeypatch.setattr(
        canonical_module,
        "assess_claim",
        lambda _section, sentence, **_kwargs: _GateDecision(
            passed="설비를 도입" not in sentence,
            score=0,
            reason="최근 실행과 고유 단서가 함께 있지 않음",
        ),
    )

    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": [completed, change]},
        {
            1: {"종류": "MD&A", "원문": "가나다는 2025년 SmartX 설비를 도입했다."},
            2: {
                "종류": "MD&A",
                "원문": "가나다는 2025년 SmartX 매출이 증가했다고 밝혔다.",
            },
        },
        [],
        engine=_Engine(),
        company="가나다",
    )

    assert kept == []
    assert any("완료 실행·제공된 완료 실적" in item["reason"] for item in rejected)


def test_원문대조와_회사특이성_탈락은_삭제수에_한번씩만_센다(
    monkeypatch,
) -> None:
    class 한건원문삭제엔진(_Engine):
        @staticmethod
        def check_draft(items, originals, requirements):
            return _Checked(
                kept=[items[0]],
                deleted=[(items[1], "원문 불일치")],
            )

    monkeypatch.setattr(
        canonical_module,
        "assess_claim",
        lambda *_args, **_kwargs: _GateDecision(
            False, 0, "사업·수익 방식이 없음"
        ),
    )
    items = [
        _section567_item("identity", "1-1", "identity_summary", ""),
        _section567_item("identity", "2-1", "identity_summary", ""),
    ]
    steps: list[dict] = []
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": items},
        {
            1: {"종류": "홈페이지", "원문": "가나다전자는 소재 기업이다."},
            2: {"종류": "홈페이지", "원문": "가나다전자는 장비 기업이다."},
        },
        steps,
        engine=한건원문삭제엔진(),
        company="가나다전자",
    )

    assert kept == []
    assert len(rejected) == 2
    assert steps[-1]["삭제"] == 2
    diagnostic = steps[0]["span_selection_diagnostic"]
    assert diagnostic["validation_rejected"] == 2
    assert diagnostic["validation_rejection_reason_counts"] == (
        ("company_specificity_failure", 1),
        ("source_verification_failure", 1),
    )


def test_원문대조기가_항목을_누락하면_라운드_전체를_닫힌_사유로_거부한다() -> None:
    class 한건누락엔진(_Engine):
        @staticmethod
        def check_draft(items, originals, requirements):
            return _Checked(kept=[items[0]], deleted=[])

    items = [
        _section567_item("identity", "1-1", "identity_summary", ""),
        _section567_item("identity", "2-1", "identity_summary", ""),
    ]
    steps: list[dict] = []
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": items},
        {
            1: {"종류": "홈페이지", "원문": "가나다전자는 소재 기업이다."},
            2: {"종류": "홈페이지", "원문": "가나다전자는 장비 기업이다."},
        },
        steps,
        engine=한건누락엔진(),
        company="가나다전자",
    )

    assert kept == []
    assert len(rejected) == 2
    diagnostic = steps[0]["span_selection_diagnostic"]
    assert diagnostic["provider_selected"] == 2
    assert diagnostic["validation_rejected"] == 2
    assert diagnostic["validation_rejection_reason_counts"] == (
        ("source_verification_failure", 2),
    )


def test_원문대조기가_같은값의_새객체를_반환해도_라운드_전체를_거부한다() -> None:
    class 복제반환엔진(_Engine):
        @staticmethod
        def check_draft(items, originals, requirements):
            cloned = [
                _DraftItem(item.sentence, item.fragment_id, item.block)
                for item in items
            ]
            return _Checked(kept=cloned, deleted=[])

    item = _section567_item("identity", "1-1", "identity_summary", "")
    steps: list[dict] = []
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": [item]},
        {1: {"종류": "홈페이지", "원문": "가나다전자는 소재 기업이다."}},
        steps,
        engine=복제반환엔진(),
        company="가나다전자",
    )

    assert kept == []
    assert [item["reason"] for item in rejected] == ["원문 대조 결과 회계 불일치"]
    assert steps[0]["span_selection_diagnostic"][
        "validation_rejection_reason_counts"
    ] == (("source_verification_failure", 1),)


def test_같은_조각에_반복된_동일문장은_서로_다른_sid여도_한번만_남긴다() -> None:
    sentence = "가나다전자는 친환경 소재 전문기업이다."
    common = _section567_item("identity", "1-1", "identity_summary", "")
    steps: list[dict] = []
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {
            "items": [common, {**common, "sid": "1-2"}]
        },
        {1: {"종류": "홈페이지", "원문": f"{sentence} {sentence}"}},
        steps,
        engine=_Engine(),
        company="가나다전자",
    )

    assert [item.sid for item in kept] == ["1-1"]
    assert [item["reason"] for item in rejected] == ["같은 사실 중복 배치"]
    diagnostic = steps[0]["span_selection_diagnostic"]
    assert diagnostic["provider_selected"] == 2
    assert diagnostic["validation_kept"] == 1
    assert diagnostic["validation_rejected"] == 1


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
            response_to_sid="[1-1]",
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
    assert kept[1].response_to_sid == "1-1"
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

    steps: list[dict] = []
    kept, rejected = select_canonical_spans(
        lambda _prompt, _schema: {"items": [item]},
        {1: {"종류": "전략", "원문": sentence}},
        steps,
        engine=_Engine(),
        company="가나다전자",
    )

    assert kept == []
    assert any(item["reason"] == "회사 제시 효과의 직접 근거 없음" for item in rejected)
    assert steps[0]["span_selection_diagnostic"][
        "validation_rejection_reason_counts"
    ] == (("future_plan_contract_failure", 1),)


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
    assert plan_timing_has_passed("H1 2026", report_date)
    assert plan_timing_has_passed("2026 Q2", report_date)
    assert not plan_timing_has_passed("H2 2026", report_date)
    assert not plan_timing_has_passed("Q4 2026", report_date)
    assert plan_timing_has_passed("H1 '26", report_date)
    assert not plan_timing_has_passed("H2 '26", report_date)


def test_검증된_영문IR의_계획시점과_실행신호를_닫힌패턴으로_읽는다() -> None:
    assert PLAN_TIMING_PATTERN.search("H2 2026 & 2027")
    assert PLAN_TIMING_PATTERN.search("Q1 2027")
    assert PLAN_EXECUTION_SIGNAL_PATTERN.search("world tour RUN IT")
    assert PLAN_EXECUTION_SIGNAL_PATTERN.search("new album release")
    assert PLAN_EXECUTION_SIGNAL_PATTERN.search("city pop-ups in Japan")
    assert PLAN_EXECUTION_SIGNAL_PATTERN.search("IP Leverage Impact Maximization")


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
        "Revenue",
        "Operating Profit",
        "OPM",
        "MD sales",
        "Concerts",
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


_JYP_DISTRIBUTION_PARTNERSHIP = (
    "당사는 Sony Music, TME (Tencent Music Entertainment), Republic Records 등 "
    "글로벌 유수의 음반/음원 유통 전문 회사들과 파트너십을 체결하여 당사가 "
    "제작한 음악 컨텐츠에 대한 글로벌 유통처를 확대해 가고 있습니다."
)
_JYP_DISTRIBUTION_ROLE = (
    "당사가 제작한 음악 컨텐츠에 대한 글로벌 유통처를 확대해 가고 있습니다"
)


def _select_partner_role(
    sentence: str,
    *,
    subject_label: str,
    operation_role: str,
    source_kind: str = "사업내용",
    value_chain_stage: str = "distribution",
    relationship_type: str = "distribution",
) -> tuple[list, list[dict[str, str]]]:
    item = _section567_item(
        "operations_partners",
        "1-1",
        "partner_role",
        subject_label,
        operation_role=operation_role,
        value_chain_stage=value_chain_stage,
        relationship_type=relationship_type,
    )
    return select_canonical_spans(
        lambda _prompt, _schema: {"items": [item]},
        {1: {"종류": source_kind, "원문": sentence}},
        [],
        engine=_Engine(),
        company="JYP Ent.",
    )


def test_JYP_공식유통파트너십_체결과_현재유통처확대가_함께_있으면_통과한다() -> None:
    kept, rejected = _select_partner_role(
        _JYP_DISTRIBUTION_PARTNERSHIP,
        subject_label="Sony Music",
        operation_role=_JYP_DISTRIBUTION_ROLE,
    )

    assert rejected == []
    assert [(item.claim_type, item.subject_label) for item in kept] == [
        ("partner_role", "Sony Music")
    ]
    for partner in ("Sony Music", "TME", "Republic Records"):
        assert has_executed_current_distribution_partnership(
            _JYP_DISTRIBUTION_PARTNERSHIP,
            _JYP_DISTRIBUTION_ROLE,
            partner,
        )


def test_계약문장_근처에_이름만_나온_다른회사를_계약당사자로_오인하지_않는다() -> None:
    cases = (
        (
            "가나다전자는 AlphaWorks를 신규 파트너 후보로 소개했다. "
            "가나다전자는 BetaWorks와 유통 파트너십을 체결하여 글로벌 "
            "유통처를 확대해 가고 있습니다.",
            "가치사슬 단계의 직접 근거 없음",
        ),
        (
            "가나다전자는 AlphaWorks를 소개한 뒤 BetaWorks와 유통 "
            "파트너십을 체결하여 글로벌 유통처를 확대해 가고 있습니다.",
            "현재 반복 운영 역할의 직접 근거 없음",
        ),
    )

    for sentence, expected_reason in cases:
        kept, rejected = _select_partner_role(
            sentence,
            subject_label="AlphaWorks",
            operation_role="글로벌 유통처를 확대해 가고 있습니다",
        )

        assert kept == []
        assert any(
            item["reason"] == expected_reason
            for item in rejected
        )


def test_유통계약_결속함수는_실제_체결당사자만_직접_가리킨다() -> None:
    sentence = (
        "가나다전자는 AlphaWorks를 신규 파트너 후보로 소개했다. "
        "가나다전자는 BetaWorks와 유통 파트너십을 체결하여 글로벌 "
        "유통처를 확대해 가고 있습니다."
    )
    operation_role = "글로벌 유통처를 확대해 가고 있습니다"

    assert not has_executed_current_distribution_partnership(
        sentence,
        operation_role,
        "AlphaWorks",
    )
    assert has_executed_current_distribution_partnership(
        sentence,
        operation_role,
        "BetaWorks",
    )


def test_유통계약_열거구는_서술문을_제외하고_실제_열거된_회사만_가리킨다() -> None:
    sentence = (
        "가나다전자는 AlphaWorks, 신규 파트너 후보를 소개한 뒤 BetaWorks, "
        "GammaWorks 등 글로벌 유통 전문 회사들과 파트너십을 체결하여 "
        "글로벌 유통처를 확대해 가고 있습니다."
    )
    operation_role = "글로벌 유통처를 확대해 가고 있습니다"

    assert not has_executed_current_distribution_partnership(
        sentence,
        operation_role,
        "AlphaWorks",
    )
    for partner in ("BetaWorks", "GammaWorks"):
        assert has_executed_current_distribution_partnership(
            sentence,
            operation_role,
            partner,
        )


def test_MOU에_현재유통처확대_문구를_붙여도_운영파트너가_되지는_않는다() -> None:
    sentence = (
        "가나다전자는 AlphaWorks와 유통 파트너십 MOU를 체결하고 "
        "현재 글로벌 유통처를 확대해 가고 있습니다."
    )
    kept, rejected = _select_partner_role(
        sentence,
        subject_label="AlphaWorks",
        operation_role="현재 글로벌 유통처를 확대해 가고 있습니다",
    )

    assert kept == []
    assert any(
        item["reason"] == "현재 반복 운영 역할의 직접 근거 없음"
        for item in rejected
    )


def test_일회성_유통계약_체결만으로_운영파트너가_되지는_않는다() -> None:
    sentence = "가나다전자는 2026년 AlphaWorks와 유통 파트너십을 체결했다."
    kept, rejected = _select_partner_role(
        sentence,
        subject_label="AlphaWorks",
        operation_role="유통 파트너십을 체결했다",
    )

    assert kept == []
    assert any(
        item["reason"] == "현재 반복 운영 역할의 직접 근거 없음"
        for item in rejected
    )


def test_유통처_확대가_미래계획이면_현재_운영파트너가_되지는_않는다() -> None:
    sentence = (
        "가나다전자는 AlphaWorks와 유통 파트너십을 체결했고 "
        "향후 글로벌 유통처를 확대할 계획이다."
    )
    kept, rejected = _select_partner_role(
        sentence,
        subject_label="AlphaWorks",
        operation_role="향후 글로벌 유통처를 확대할 계획이다",
    )

    assert kept == []
    assert any(
        item["reason"] == "미실행 계획을 현재 운영으로 분류"
        for item in rejected
    )


def test_종료된_유통계약은_현재_운영파트너가_되지는_않는다() -> None:
    sentence = (
        "가나다전자는 AlphaWorks와 유통 파트너십을 체결하여 글로벌 유통처를 "
        "확대해 가고 있었으나 계약이 종료되었습니다."
    )
    kept, rejected = _select_partner_role(
        sentence,
        subject_label="AlphaWorks",
        operation_role=(
            "글로벌 유통처를 확대해 가고 있었으나 계약이 종료되었습니다"
        ),
    )

    assert kept == []
    assert any(
        item["reason"] == "현재 반복 운영 역할의 직접 근거 없음"
        for item in rejected
    )


def test_과거에_유통처를_확대해_가고_있었다는_문장도_현재역할이_아니다() -> None:
    sentence = (
        "가나다전자는 AlphaWorks와 유통 파트너십을 체결하여 당시 글로벌 "
        "유통처를 확대해 가고 있었습니다."
    )
    kept, rejected = _select_partner_role(
        sentence,
        subject_label="AlphaWorks",
        operation_role="당시 글로벌 유통처를 확대해 가고 있었습니다",
    )

    assert kept == []
    assert any(
        item["reason"] == "현재 반복 운영 역할의 직접 근거 없음"
        for item in rejected
    )


def test_JYP_Live_Nation_과거체결과_과거공연만으로_현재역할을_만들지_않는다() -> None:
    cases = (
        (
            "2023년에는 글로벌 최대 공연 프로모터인 Live Nation과 전략적 "
            "파트너십을 체결하였고 당사 아티스트의 글로벌 투어를 위한 구조적 "
            "협력 체계를 구축하였습니다.",
            "글로벌 투어를 위한 구조적 협력 체계를 구축하였습니다",
        ),
        (
            "K-POP 산업의 리더인 당사와 방대한 글로벌 공연 인프라를 보유한 "
            "Live Nation은 이번 파트너십 체결 이전에도 수많은 월드투어를 "
            "성공적으로 이끌어왔습니다.",
            "수많은 월드투어를 성공적으로 이끌어왔습니다",
        ),
    )

    for sentence, operation_role in cases:
        kept, _rejected = _select_partner_role(
            sentence,
            subject_label="Live Nation",
            operation_role=operation_role,
            value_chain_stage="production",
            relationship_type="joint_business",
        )
        assert kept == []


def test_JYP_유통파트너십_문장이_뉴스이면_공식예외를_적용하지_않는다() -> None:
    kept, rejected = _select_partner_role(
        _JYP_DISTRIBUTION_PARTNERSHIP,
        subject_label="Sony Music",
        operation_role=_JYP_DISTRIBUTION_ROLE,
        source_kind="뉴스",
    )

    assert kept == []
    assert any(
        item["reason"] == "현재 반복 운영 역할의 직접 근거 없음"
        for item in rejected
    )


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
                    basis_sids=["[1-1]"],
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
