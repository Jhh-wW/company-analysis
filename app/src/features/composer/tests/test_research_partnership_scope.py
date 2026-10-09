"""원문 보충과 최초/재검수에서 제휴 슬롯 우회를 닫는다."""
from dataclasses import replace
import pytest

from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.news_usage import supplement_news_candidates
from src.features.composer.tests.test_news_block_channels import _news_fragment
from src.features.composer.verify import _apply_grounding, REVIEW_GROUNDING_REJECTED
from src.features.composer import verify
from src.features.composer.news_usage import attribution_prefix
from src.features.composer.tests.test_section_public_manifest import _BoundGroupedReviewer
from src.shared.report_evidence.partnership_scope_constants import PARTNERSHIP_SLOT, OPERATING_ROLE_SLOT

SOLO = "온빛연구소가 기업 80곳과 금융사 20곳의 사업보고서를 전수 조사해 결과를 공개했다."
JOINT = "온빛연구소가 새봄대학과 함께 기업 80곳의 사업보고서를 전수 조사해 결과를 공개했다."


def _fragment(text, slot):
    return replace(_news_fragment("81", "2026-09-01", text, section_id="operations_partners"),
                   supported_claim_slots=(slot,))


def test_낡은_제휴조각_원문보충으로_칸을_우회하지_않는다():
    report = ComposedReport(sections=(ComposedSection("operations_partners", ()),))
    blocked, added = supplement_news_candidates(report, (_fragment(SOLO, PARTNERSHIP_SLOT),))
    assert added == () and blocked.sections[0].sentences == ()
    for text, slot in ((SOLO, OPERATING_ROLE_SLOT), (JOINT, PARTNERSHIP_SLOT)):
        rebuilt, added = supplement_news_candidates(report, (_fragment(text, slot),))
        assert added == ("81",)
        assert rebuilt.sections[0].sentences[0].planned_claim_slot == slot
        assert rebuilt.sections[0].sentences[0].text.endswith(text)


def test_참승인도_제휴칸_또는_제휴주장_원문조건을_우회하지_않는다():
    for claim, slot in ((SOLO, PARTNERSHIP_SLOT),
                        ("온빛연구소가 기업들과 공동으로 사업보고서를 전수 조사했다.", OPERATING_ROLE_SLOT)):
        problems = {}
        verdicts = _apply_grounding("", {1: "참"}, {1: (claim, {"81": SOLO})},
            diagnostic_contexts={1: ("operations_partners", "본문", claim)},
            claim_slots_by_number={1: slot}, grounding_problems=problems)
        assert verdicts[1] == REVIEW_GROUNDING_REJECTED
        assert problems[1] == "scope_condition_unbound"


def test_운영원문과_정상제휴의_참승인은_유지한다():
    for text, slot in ((SOLO, OPERATING_ROLE_SLOT), (JOINT, PARTNERSHIP_SLOT)):
        verdicts = _apply_grounding("", {1: "참"}, {1: (text, {"81": text})},
            diagnostic_contexts={1: ("operations_partners", "본문", text)},
            claim_slots_by_number={1: slot})
        assert verdicts[1] == "참"


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("text,slot,rejected", [
    (SOLO, PARTNERSHIP_SLOT, True), (SOLO, OPERATING_ROLE_SLOT, False),
    (JOINT, PARTNERSHIP_SLOT, False),
])
def test_실제_최초묶음과_재검수입구가_선택슬롯을_동일가드에_넘긴다(grouped, text, slot, rejected):
    fragment = _fragment(text, slot)
    sentence = ComposedSentence(attribution_prefix(fragment) + text, ("81",), "확인",
                                planned_claim_slot=slot)
    reviewer = _BoundGroupedReviewer()
    problems = {}
    if grouped:
        items = (verify._GroupedReviewItem(1, "operations_partners", verify.REVIEW_KIND_SENTENCE,
                                          ("81",), sentence=sentence),)
        result = verify._ask_grouped_verdicts(reviewer, items, {"81": fragment}, None,
            allowed_fragment_ids_by_section={"operations_partners": frozenset({"81"})},
            grounding_problems=problems)
    else:
        items = (verify._ReviewItem(1, sentence, "operations_partners"),)
        result = verify._ask_verdicts(reviewer, items, {"81": fragment}, "",
                                     grounding_problems=problems)
    assert result[1] == (REVIEW_GROUNDING_REJECTED if rejected else "참")
    assert (problems.get(1) == "scope_condition_unbound") is rejected
