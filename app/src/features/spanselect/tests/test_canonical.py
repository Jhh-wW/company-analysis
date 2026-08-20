from __future__ import annotations

from dataclasses import dataclass

from src.features.spanselect.canonical import (
    CANONICAL_SOURCE_SECTION_IDS,
    build_prompt,
    select_canonical_spans,
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
    }

    def answer(_prompt, _schema):
        return {
            "items": [
                {
                    "section_id": "identity",
                    "sid": "1-1",
                    "claim_type": "official_identity",
                    "subject_label": "",
                    "market_priority": "",
                    "product_role": "",
                    "response_to_sid": "",
                    "basis_sids": [],
                    "priority_signals": [],
                },
                {
                    "section_id": "portfolio",
                    "sid": "2-1",
                    "claim_type": "priority_product",
                    "subject_label": "AlphaX",
                    "market_priority": "",
                    "product_role": "성장",
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
                    "market_priority": "",
                    "product_role": "",
                    "response_to_sid": "",
                    "basis_sids": [],
                    "priority_signals": [],
                },
                {
                    "section_id": "future_strategy",
                    "sid": "3-1",
                    "claim_type": "future_plan",
                    "subject_label": "",
                    "market_priority": "",
                    "product_role": "",
                    "response_to_sid": "",
                    "basis_sids": [],
                    "priority_signals": [],
                },
                {
                    "section_id": "identity",
                    "sid": "999-1",
                    "claim_type": "official_identity",
                    "subject_label": "",
                    "market_priority": "",
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
        ("portfolio", 2),
        ("future_strategy", 3),
    ]
    assert any(item["reason"] == "같은 사실 중복 배치" for item in rejected)
    assert any(item["reason"] == "없는 번호 또는 섹션" for item in rejected)
    assert kept[0].sentence == "진영은 친환경 소재 전문기업이다."
    assert kept[1].claim_type == "priority_product"
    assert kept[1].product_role == "성장"


def test_현재_대응은_같은_답의_미해결_문제와_연결되어야_한다():
    frags = {
        1: {
            "종류": "사업내용",
            "원문": "진영은 2026년 PMMA 원재료 가격 부담이 아직 남아 있다고 밝혔다.",
        },
        2: {
            "종류": "사업내용",
            "원문": "진영은 2026년 PMMA 생산비 절감에 착수했다.",
        },
    }

    def answer(_prompt, _schema):
        common = {
            "market_priority": "",
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
                },
                {
                    **common,
                    "section_id": "current_challenges",
                    "sid": "2-1",
                    "claim_type": "current_response",
                    "response_to_sid": "없는-sid",
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
