"""직전 문장 선택은 정확 원문 기회만 넓히며 회사 귀속과 상한은 유지한다."""
from copy import deepcopy
import hashlib

import pytest

from src.features.news_intake import quote_selection_constants as qc
from src.features.news_intake.grounded import validate_grounded_response
from src.features.news_intake.quote_selection import quote_candidates, restore_quote_response
from src.features.news_intake.tests.test_collection import AS_OF, BODY, COMPANY, accepted, item, snapshot


def candidate_pair():
    return snapshot([item(), item(1)])[0].candidates


def selected_response(candidate, body, span):
    quote = next(row for row in quote_candidates(candidate, body, COMPANY)
                 if (row['start'], row['end']) == span)
    text = body[span[0]:span[1]]
    row = accepted({'id': candidate.id, 'body': text}, section='operations_partners', topic='partnerships')
    row['entity_evidence_quote_id'] = quote['id']
    row.pop('entity_evidence')
    excerpt = row['excerpts'][0]
    for key in ('text', 'time_evidence', 'subject_evidence'):
        value = excerpt.pop(key)
        excerpt[key + '_quote_id'] = quote['id'] if value else ''
    excerpt['subject_is_target'] = True
    excerpt['temporal_status'] = 'ongoing'
    return {'items': [row]}


def test_previous_actor_and_relationship_are_available_before_forward_ranges():
    candidate = candidate_pair()[0]
    background = ' '.join(f'기자는 산업설비 시장의 배경 자료 {number}를 설명했다.'
                          for number in range(qc.QUOTE_MAX_CANDIDATES_PER_ARTICLE)) + ' '
    actor = '이음전자도 산업설비 시장에서 사업 범위를 넓히고 있다. '
    relation = '가나다전자와 생산설비 고도화를 위한 협력을 진행하고 있으며 공동 기술개발 과제를 추진한다.'
    body = background + actor + relation
    before = body.encode()
    rows = quote_candidates(candidate, body, COMPANY)
    start, end = len(background), len(body)
    quote = next(row for row in rows if (row['start'], row['end']) == (start, end))
    assert body[start:end] == actor + relation
    expected = hashlib.sha256(f'{candidate.id}\n{hashlib.sha256(before).hexdigest()}\n{start}:{end}'.encode()).hexdigest()
    assert quote['id'] == qc.QUOTE_ID_PREFIX + expected[:qc.QUOTE_ID_HASH_CHARS]
    assert len(rows) == qc.QUOTE_MAX_CANDIDATES_PER_ARTICLE
    assert len({row['id'] for row in rows}) == len(rows)
    assert body.encode() == before


def test_normal_partner_relationship_preserves_exact_text():
    candidate = candidate_pair()[0]
    body = ('이음전자도 산업설비 시장에서 사업 범위를 넓히고 있다. '
            '가나다전자와 생산설비 고도화를 위한 협력을 진행하고 있으며 공동 기술개발 과제를 추진한다.')
    raw = selected_response(candidate, body, (0, len(body)))
    before = deepcopy(raw)
    result, rejected = validate_grounded_response(raw, articles=[(candidate, body)], company=COMPANY, as_of=AS_OF)
    assert result and not rejected
    assert result[0].text == body and raw == before


@pytest.mark.parametrize('body', [
    '기사 제공: 가나다전자. 다른기술은 산업설비 신제품을 개발해 고객에게 공급하고 제조 현장 운영을 지원했다.',
    '공학자는 과거 가나다전자에서 근무했다. 현재 공학자의 독립 회사는 산업설비 신제품을 개발해 고객에게 공급하고 운영을 지원했다.',
    '다른기술은 가나다전자를 방문했다. 다른기술은 산업설비 신제품을 개발해 고객에게 공급하고 제조 현장 운영을 지원했다.',
])
def test_previous_context_does_not_promote_publisher_or_other_actor(body):
    candidate = candidate_pair()[0]
    raw = selected_response(candidate, body, (0, len(body)))
    assert not validate_grounded_response(raw, articles=[(candidate, body)], company=COMPANY, as_of=AS_OF)[0]


def test_previous_context_does_not_recover_wrong_article_or_changed_body():
    first, second = candidate_pair()
    body = ('이음전자도 산업설비 시장에서 사업 범위를 넓히고 있다. '
            '가나다전자와 생산설비 고도화를 위한 협력을 진행하고 있으며 공동 기술개발 과제를 추진한다.')
    raw = selected_response(first, body, (0, len(body)))
    switched = deepcopy(raw)
    switched['items'][0]['id'] = second.id
    assert not validate_grounded_response(switched, articles=[(second, body)], company=COMPANY, as_of=AS_OF)[0]
    assert not validate_grounded_response(raw, articles=[(first, body + ' ')], company=COMPANY, as_of=AS_OF)[0]


def test_industry_without_company_keeps_the_same_candidates():
    candidate = candidate_pair()[0]
    body = '국내 산업설비 업계는 원자재 가격 상승으로 생산원가 부담이 커졌다. 공급망 재편에 따른 납기 지연도 이어지고 있다.'
    assert quote_candidates(candidate, body, COMPANY) == quote_candidates(candidate, body)


def test_single_company_sentence_remains_available():
    candidate = candidate_pair()[0]
    raw = selected_response(candidate, BODY, (0, len(BODY)))
    restored = restore_quote_response(raw, articles=[(candidate, BODY)], company=COMPANY)
    assert restored['items'][0]['excerpts'][0]['text'] == BODY
