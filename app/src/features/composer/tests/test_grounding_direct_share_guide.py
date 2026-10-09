"""직접 비중 안내는 기존 수치 결속에 실제로 통과하는 정확 구절만 쓴다."""
from copy import deepcopy

from src.features.composer.grounding import _numeric_valid
from src.features.composer.grounding_constants import GROUNDING_GUIDE

TEXT = '센서 매출이 전체 매출의 약 60%를 차지한다'
PROOF = {
    '표현': TEXT, '항목': '센서 매출이 전체 매출', '근거': '1',
    '원문': TEXT, '원문항목': '센서 매출이 전체 매출', '원문값': '60%', '후보값': '60%',
}


def test_direct_share_guide_example_passes_existing_numeric_contract():
    assert TEXT in GROUNDING_GUIDE
    assert '항목=원문항목=「센서 매출이 전체 매출」' in GROUNDING_GUIDE
    assert _numeric_valid(TEXT, [PROOF], {'1': TEXT})


def test_renamed_share_metric_is_still_rejected():
    proof = deepcopy(PROOF)
    proof.update(항목='센서 매출 비중', 원문항목='센서 매출 비중')
    detail = {}
    assert not _numeric_valid(TEXT, [proof], {'1': TEXT}, detail)
    assert detail['stage'] == 'expression_not_in_candidate'


def test_another_denominator_cannot_reuse_the_same_percentage():
    source = TEXT.replace('전체 매출', '전체 시장 매출')
    proof = deepcopy(PROOF)
    proof.update(원문=source, 원문항목='센서 매출이 전체 시장 매출')
    assert not _numeric_valid(TEXT, [proof], {'1': source})
