"""작성·재검수·요약·공개 메타에서 예시 문맥을 잃지 않는다."""
import hashlib
import json

import pytest

from src.features.composer.port import CollectedFragment
from src.features.composer.port import ComposedSentence
from src.features.composer.render import _fragment_metas
from src.features.composer.evidence_pair_selection import build_evidence_pair_map
from src.features.composer.verify import _apply_grounding, _review_fragment_metadata, _is_grounding_rewrite_target
from src.shared.report_evidence.practice_context import build_practice_context


def _fragment():
    ranges = ("예를 들어 다음과 같이 요청합니다.", "항목별로 후보를 분류하고 검토합니다.")
    digest = hashlib.sha256("\n".join(ranges).encode()).hexdigest()
    location = "https://example.com/guide · 목록 2번째 항목"
    context = build_practice_context(
        ranges=ranges, document_id="guide", document_sha256=digest,
        fragment_index=1, fragment_location=location,
    )
    assert context
    return CollectedFragment(
        fragment_id="a", kind="홈페이지", text=ranges[1],
        source_document_id="guide", document_content_sha256=digest,
        location=location, practice_context_json=context,
        supported_claim_slots=("past_changes:completed_execution",),
    )


def test_writer_choices_exclude_example_and_keep_original_packet():
    fragment = _fragment()
    assert not build_evidence_pair_map("past_changes", (fragment,))
    assert fragment.supported_claim_slots == ("past_changes:completed_execution",)
    assert fragment.text == "항목별로 후보를 분류하고 검토합니다."


@pytest.mark.parametrize("kind", ["본문", "요약", "도식"])
def test_review_cannot_publish_instruction_as_company_execution(kind):
    fragment = _fragment()
    claim = "회사는 항목별로 후보를 분류하고 검토하는 절차를 운영한다."
    raw = json.dumps({"판정": [{"번호": 1, "결과": "참"}]}, ensure_ascii=False)
    problems = {}
    kwargs = {"flow_cells_by_number": {1: (claim,)}} if kind == "도식" else {}
    result = _apply_grounding(
        raw, {1: "참"}, {1: (claim, {"a": fragment.text})},
        diagnostic_contexts={1: ("past_changes", kind, claim)},
        claim_slots_by_number={1: "past_changes:completed_execution"},
        source_fragments_by_id={"a": fragment}, grounding_problems=problems,
        **kwargs,
    )
    assert result[1] != "참"
    assert problems[1] == "source_practice_unbound"


def test_review_and_render_metadata_keep_the_exact_context():
    fragment = _fragment()
    metadata = _review_fragment_metadata(fragment)
    assert "예를 들어 다음과 같이 요청합니다." in metadata
    assert _fragment_metas((fragment,))[0].practice_context_json == fragment.practice_context_json


def test_example_only_cannot_start_paid_fact_rewrite():
    sentence = ComposedSentence("회사는 검수 절차를 운영한다.", ("a",), "확인",
                                planned_claim_slot="past_changes:completed_execution")
    assert not _is_grounding_rewrite_target(sentence, "past_changes", "source_practice_unbound")
