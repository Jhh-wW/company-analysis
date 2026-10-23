"""실제 PG 도식의 근거 없는 자산 추정만 정리하고 검수 상태를 보존한다."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.features.composer.draft_flow_assets import omit_unstated_draft_flow_asset
from src.features.composer.logic import _compose_one_section
from src.features.composer.port import CollectedFragment, FlowReviewBinding, FlowRow

FIXTURE = json.loads((Path(__file__).parent / "fixtures/payment_flow_asset.json").read_text(encoding="utf-8"))


def _inputs():
    row = FlowRow(tuple(FIXTURE["row"]["칸"]), tuple(FIXTURE["row"]["인용"]))
    sources = {key: CollectedFragment(key, "공시", value["text"], supported_claim_slots=tuple(value["slots"]))
               for key, value in FIXTURE["sources"].items()}
    return row, sources


def test_saved_pg_path_preserves_action_and_evidence_but_does_not_gain_approval():
    row, sources = _inputs()
    checked = omit_unstated_draft_flow_asset(row, sources)
    assert checked.cells == ("", *row.cells[1:])
    assert checked.citations == row.citations and checked.review_binding is None
    assert omit_unstated_draft_flow_asset(checked, sources) is checked


@pytest.mark.parametrize("text", (
    "회사는 자체 거래승인 시스템을 운영합니다.", "회사는 결제 인프라를 보유합니다.",
    "회사는 자체 운영 역량을 활용합니다.",
))
def test_asset_concept_in_own_source_remains_for_semantic_review(text):
    row, sources = _inputs()
    row = replace(row, cells=("거래승인 시스템 및 결제 인프라 운영 역량", *row.cells[1:]))
    sources["9"] = replace(sources["9"], text=text)
    assert omit_unstated_draft_flow_asset(row, sources) is row


def test_unknown_untyped_or_signed_input_is_not_rewritten():
    row, sources = _inputs()
    assert omit_unstated_draft_flow_asset(row, {}) is row
    untyped = {key: replace(source, supported_claim_slots=()) for key, source in sources.items()}
    assert omit_unstated_draft_flow_asset(row, untyped) is row
    signed = replace(row, review_binding=FlowReviewBinding("business_model", "지문", (), "검수", "규칙"))
    assert omit_unstated_draft_flow_asset(signed, sources) is signed
    named = replace(row, cells=("특허 제어 기술", *row.cells[1:]))
    assert omit_unstated_draft_flow_asset(named, sources) is named


@pytest.mark.parametrize("has_prose", (True, False))
def test_writer_boundary_sends_same_path_with_empty_unstated_asset_to_review(has_prose):
    row, sources = _inputs()
    raw = json.dumps({"문장들": [{"글": "회사는 PG 서비스로부터 수익을 인식합니다.",
                                  "인용": ["9"], "등급": "확인", "주장슬롯": "business_model:revenue_model"}] if has_prose else [],
                      "경로표": [FIXTURE["row"]]}, ensure_ascii=False)
    section = _compose_one_section("business_model", "저장 응답", lambda _: raw, source_fragments=tuple(sources.values()))
    assert len(section.flow_rows) == 1
    assert section.flow_rows[0] == replace(row, cells=("", *row.cells[1:]))
    assert section.flow_rows[0].review_binding is None
