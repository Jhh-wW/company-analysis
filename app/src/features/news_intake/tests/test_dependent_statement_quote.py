"""단독 역참조 발언은 실제 제공된 같은 기사 문맥 후보가 있을 때만 제외한다."""

from copy import deepcopy
from dataclasses import replace
import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from src.features.news_intake import quote_selection_constants as qc
from src.features.news_intake.grounded import validate_grounded_response
from src.features.news_intake.models import NewsCandidate, NewsCompanyContext
from src.features.news_intake.quote_selection import (
    _self_contained_statement_choices, quote_candidates, restore_quote_response,
)


SAVED = json.loads((Path(__file__).parent / 'fixtures/dependent_statement_context.json')
                   .read_text(encoding='utf-8'))
COMPANY = NewsCompanyContext('토스페이먼츠 주식회사', aliases=('토스페이먼츠',),
                            executive_names=('임한욱',))
CANDIDATE = NewsCandidate('unit-context-article', '사건과 보상 발언', '',
    SAVED['source_url'], SAVED['source_url'], '2026-10-06', 'it.chosun.com', 4,
    SAVED['source_url'], source_category='news_report')
CONTEXT, STATEMENT = SAVED['body'].split(' 이에 대해 ', 1)
STATEMENT = '이에 대해 ' + STATEMENT


def _unfiltered(candidate, body, monkeypatch):
    # 후보 생성 자체는 그대로 실행하고 마지막 선택지 필터만 관측한다.
    with monkeypatch.context() as patch:
        patch.setattr('src.features.news_intake.quote_selection._self_contained_statement_choices',
                      lambda rows, *_: rows)
        return quote_candidates(candidate, body, COMPANY)


def _selection(candidate, identity_id, selected_id):
    # 새 원문 선택 계약을 시험하는 합성 응답이며 실제 모델 승인 재사용이 아니다.
    return {'items': [{'id': candidate.id, 'same_company': True, 'material': True,
        'source_type': 'news_report', 'entity_evidence_quote_id': identity_id,
        'excerpts': [{
            'section_id': 'current_challenges', 'claim_slot': 'current_challenges:issue',
            'claim_kind': 'company_statement', 'temporal_status': 'ongoing',
            'event_key': '보안 체계에 대한 지적과 대표 발언', 'event_on': '',
            'subject': '', 'subject_is_target': True, 'subject_evidence_quote_id': '',
            'time_evidence_quote_id': '', 'text_quote_id': selected_id, 'topic': 'risk',
        }]}]}


def test_실제연속본문_축약시험은_짧은선택만제외하고_원문좌표해시를보존한다(monkeypatch):
    body = SAVED['body']
    before = deepcopy(SAVED)
    old = _unfiltered(CANDIDATE, body, monkeypatch)
    short = next(row for row in old if body[row['start']:row['end']] == STATEMENT)
    current = quote_candidates(CANDIDATE, body, COMPANY)
    assert current == [row for row in old if row != short]
    assert any(row['start'] == 0 and row['end'] == len(body) for row in current)
    body_sha = hashlib.sha256(body.encode()).hexdigest()
    for row in current:
        exact = body[row['start']:row['end']]
        assert 0 < len(exact) <= qc.QUOTE_MAX_CHARS
        digest = hashlib.sha256(f"{CANDIDATE.id}\n{body_sha}\n{row['start']}:{row['end']}".encode()).hexdigest()
        assert row['id'] == qc.QUOTE_ID_PREFIX + digest[:qc.QUOTE_ID_HASH_CHARS]
        assert row['begin_text'] == exact[:qc.QUOTE_BOUNDARY_PREVIEW_CHARS]
        assert row['end_text'] == exact[-qc.QUOTE_BOUNDARY_PREVIEW_CHARS:]
    assert len(current) <= len(old) <= qc.QUOTE_MAX_CANDIDATES_PER_ARTICLE
    assert SAVED == before


def test_짧은과거선택은거절하고_문맥후보도_별도선택과기존검증을요구한다(monkeypatch):
    body = SAVED['body']
    old = _unfiltered(CANDIDATE, body, monkeypatch)
    short = next(row for row in old if body[row['start']:row['end']] == STATEMENT)
    longer = next(row for row in old if row['start'] == 0 and row['end'] == len(body))
    rejected = _selection(CANDIDATE, longer['id'], short['id'])
    original = deepcopy(rejected)
    result, reasons = validate_grounded_response(rejected, articles=[(CANDIDATE, body)],
        company=COMPANY, as_of=dt.date(2026, 10, 21))
    assert not result and reasons == {'grounded_invalid_excerpt': 1}
    failed = restore_quote_response(rejected, articles=[(CANDIDATE, body)], company=COMPANY)
    assert failed['items'][0]['excerpts'][0]['invalid_quote_selection'] is True
    assert rejected == original
    selected = _selection(CANDIDATE, longer['id'], longer['id'])
    restored = restore_quote_response(selected, articles=[(CANDIDATE, body)], company=COMPANY)
    assert restored['items'][0]['excerpts'][0]['text'] == body
    # 명시 주어 등 기존 검사가 여전히 적용된다. 선택지 제공은 검수 통과가 아니다.
    selected['items'][0]['excerpts'][0]['subject_is_target'] = False
    assert not validate_grounded_response(selected, articles=[(CANDIDATE, body)],
        company=COMPANY, as_of=dt.date(2026, 10, 21))[0]


@pytest.mark.parametrize('text', [
    '이에 대해서 임한욱 대표는 "보상을 진행하고 있다"고 말했다.',
    '이에 대한 임한욱 대표는 "보상을 진행하고 있다"고 말했다.',
    '이에 대해선 임한욱 대표는 "보상을 진행하고 있다"고 말했다.',
    '“이에 대해 임한욱 대표는 보상을 진행하고 있다”고 말했다.',
    '이에 대해 임한욱 대표는 보상을 진행하고 있다고 말했다.',
    '이에 대해 임한욱 대표는 "보상을 진행하고 있다"고 답했다.',
    '토스페이먼츠는 "환불과 개선을 진행하고 있다"고 말했다.',
    '임한욱 대표는 "환불과 개선을 진행하고 있다"고 말했다.',
])
def test_정확접두부와_단독직접발언이아니면_환불개선후보도보존한다(text, monkeypatch):
    body = CONTEXT + ' ' + text
    assert quote_candidates(CANDIDATE, body, COMPANY) == _unfiltered(CANDIDATE, body, monkeypatch)


@pytest.mark.parametrize('prefix', ['이에 대해', '이와 관련해'])
@pytest.mark.parametrize('quotes', [('"', '"'), ('“', '”')])
def test_정확접두부와_일반닫는인용부호의_직접발언만제외한다(prefix, quotes, monkeypatch):
    text = f'{prefix} 임한욱 대표는 {quotes[0]}보상을 진행하고 있다{quotes[1]}고 말했다.'
    body = CONTEXT + ' ' + text
    old = _unfiltered(CANDIDATE, body, monkeypatch)
    short = next(row for row in old if body[row['start']:row['end']] == text)
    assert quote_candidates(CANDIDATE, body, COMPANY) == [row for row in old if row != short]


def test_같은대상_같은본문의_반환표안_포함후보가없으면_짧은인용을유지한다(monkeypatch):
    body = SAVED['body']
    old = _unfiltered(CANDIDATE, body, monkeypatch)
    short = next(row for row in old if body[row['start']:row['end']] == STATEMENT)
    units = [(short['start'], short['end'])]
    # 긴 범위가 원문에 존재해도 실제 반환 표에서 빠졌으면 사용하지 않는다.
    assert _self_contained_statement_choices([short], body, units, COMPANY) == [short]
    assert _self_contained_statement_choices(old, body, units, None) == old
    assert _self_contained_statement_choices(old, body, units,
        NewsCompanyContext('다른회사')) == old
    assert _self_contained_statement_choices(old, body, [], COMPANY) == old
    # 기사 앞부분이 다른 회사이면 다른 기사에서 본 토스 문맥을 빌릴 수 없다.
    foreign_body = body.replace('토스페이먼츠', '다른회사')
    assert quote_candidates(CANDIDATE, foreign_body, COMPANY) == _unfiltered(CANDIDATE, foreign_body, monkeypatch)


def test_상한밖_문맥은짧은후보를없애지않는다(monkeypatch):
    context = '토스페이먼츠는 ' + '보안 체계의 점검 범위를 설명하며 ' * 80 + '입장을 설명했다.'
    body = context + ' ' + STATEMENT
    old = _unfiltered(CANDIDATE, body, monkeypatch)
    assert any(body[row['start']:row['end']] == STATEMENT for row in old)
    assert quote_candidates(CANDIDATE, body, COMPANY) == old


def test_다른기사_ID나본문으로_문맥선택의결속을재사용할수없다():
    body = SAVED['body']
    longer = next(row for row in quote_candidates(CANDIDATE, body, COMPANY)
                  if row['start'] == 0 and row['end'] == len(body))
    selected = _selection(CANDIDATE, longer['id'], longer['id'])
    other = replace(CANDIDATE, id='unit-other-article', source_url='https://example.com/other')
    selected['items'][0]['id'] = other.id
    assert not validate_grounded_response(selected, articles=[(other, body)],
        company=COMPANY, as_of=dt.date(2026, 10, 21))[0]
    selected['items'][0]['id'] = CANDIDATE.id
    assert not validate_grounded_response(selected, articles=[(CANDIDATE, body + ' 추가 내용.')],
        company=COMPANY, as_of=dt.date(2026, 10, 21))[0]
