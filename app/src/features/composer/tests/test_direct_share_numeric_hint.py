"""직접 비중 안내는 기존 검증기 통과와 범위 결속을 함께 요구한다."""
import json
import pytest

from src.features.composer.direct_share_numeric_hint import direct_share_numeric_hint
from src.features.composer.direct_share_numeric_hint_constants import DIRECT_SHARE_NUMERIC_HINT_GUIDE
from src.features.composer.grounding import _numeric_valid, grounding_hint

SOURCE = ('연결재무제표 기준 매출액 품목 매출액 비율 센서 40 40%. '
          '개별재무제표 기준 매출액 품목 매출액 비율 센서 60 60%. '
          '당사의 경우 센서 매출이 전체 매출의 약 60%를 차지하고 있습니다.')
CANDIDATE = '개별재무제표 기준으로 센서 사업 매출이 전체 매출의 약 60%를 차지한다.'


def test_compound_subject_hint_keeps_exact_candidate_quote_and_approximation():
    hint = direct_share_numeric_hint(CANDIDATE, {'1': SOURCE})
    proof = json.loads(hint.splitlines()[0].removeprefix(DIRECT_SHARE_NUMERIC_HINT_GUIDE))
    assert proof['표현'] == CANDIDATE
    assert proof['원문'] in SOURCE
    assert '약 60%' in proof['표현'] and '약 60%' in proof['원문']
    assert proof['원문값'] == proof['후보값'] == '60%'
    assert _numeric_valid(CANDIDATE, [proof], {'1': SOURCE})
    assert hint in grounding_hint(CANDIDATE, {'1': SOURCE})


@pytest.mark.parametrize('candidate, source', [
    (CANDIDATE.replace('개별재무제표 기준으로 ', ''), SOURCE),
    (CANDIDATE.replace('개별', '연결'), SOURCE),
    (CANDIDATE.replace('센서', '설비'), SOURCE),
    (CANDIDATE.replace('60%', '70%'), SOURCE),
    (CANDIDATE.replace('전체 매출', '전체 시장 매출'), SOURCE),
    (CANDIDATE, '[제조 부문] ' + SOURCE),
    (CANDIDATE, SOURCE.replace('차지하고 있습니다', '차지할 예정입니다')),
    (CANDIDATE, SOURCE.replace('차지하고 있습니다', '차지하지 않습니다')),
    (CANDIDATE, SOURCE.replace('당사의', '다른 회사의')),
    (CANDIDATE + ' 다른 사업 매출은 20%이다.', SOURCE),
])
def test_unsupported_scope_or_numeric_proof_does_not_get_hint(candidate, source):
    assert not direct_share_numeric_hint(candidate, {'1': source})


def test_table_percentage_without_direct_statement_does_not_get_hint():
    assert not direct_share_numeric_hint(CANDIDATE, {'1': SOURCE.split('당사의')[0]})


def test_numeric_value_cannot_be_rounded_to_a_new_literal_percentage():
    assert not direct_share_numeric_hint(CANDIDATE, {'1': SOURCE.replace('약 60%', '약 60.6%')})


def test_another_item_with_the_same_percentage_does_not_supply_the_hint_quote():
    source = SOURCE.replace('당사의 경우 센서',
                            '당사의 경우 설비 매출이 전체 매출의 약 60%를 차지하고 있습니다. 당사의 경우 센서')
    hint = direct_share_numeric_hint(CANDIDATE, {'1': source})
    proof = json.loads(hint.splitlines()[0].removeprefix(DIRECT_SHARE_NUMERIC_HINT_GUIDE))
    assert '센서' in proof['원문'] and '설비' not in proof['원문']


def test_another_source_with_the_same_percentage_does_not_supply_the_hint_quote():
    hint = direct_share_numeric_hint(CANDIDATE, {'other': SOURCE.replace('센서', '설비'), 'own': SOURCE})
    proof = json.loads(hint.splitlines()[0].removeprefix(DIRECT_SHARE_NUMERIC_HINT_GUIDE))
    assert proof['근거'] == 'own'


def test_compact_item_keeps_full_quote_with_valid_same_denominator_metric():
    source = SOURCE.replace('센서 매출이 전체 매출', '센서매출이 전체매출')
    hint = direct_share_numeric_hint(CANDIDATE, {'1': source})
    proof = json.loads(hint.splitlines()[0].removeprefix(DIRECT_SHARE_NUMERIC_HINT_GUIDE))
    assert proof['항목'] == '전체 매출' and proof['원문항목'] == '전체매출'
    assert '센서매출이' in proof['원문'] and proof['표현'] == CANDIDATE
    assert _numeric_valid(CANDIDATE, [proof], {'1': source})


def test_denominator_fallback_cannot_borrow_another_item_direct_unit():
    source = SOURCE.replace('당사의 경우 센서',
                            '당사의 경우 설비매출이 전체매출의 약 60%를 차지하고 있습니다. 당사의 경우 센서')
    source = source.replace('센서 매출이 전체 매출', '센서매출이 전체매출')
    hint = direct_share_numeric_hint(CANDIDATE, {'1': source})
    proof = json.loads(hint.splitlines()[0].removeprefix(DIRECT_SHARE_NUMERIC_HINT_GUIDE))
    assert '센서매출이' in proof['원문'] and '설비' not in proof['원문']
    assert _numeric_valid(CANDIDATE, [proof], {'1': source})


def test_denominator_fallback_still_rejects_other_item_even_with_equal_value():
    source = SOURCE.replace('센서 매출이 전체 매출', '설비매출이 전체매출')
    assert not direct_share_numeric_hint(CANDIDATE, {'1': source})
