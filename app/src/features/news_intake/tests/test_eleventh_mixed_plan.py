"""11차 원선택을 승인으로 재사용하지 않고 정확 미래 작성 후보로만 복구한다."""

from collections import Counter
from copy import deepcopy
from dataclasses import replace
import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from src.features.news_intake.grounded import _excerpt, validate_grounded_response, EXCERPT_SCHEMA
from src.features.news_intake.grounded_mapping import select_diverse_excerpts, to_evidence_fragment
from src.features.news_intake.models import GroundedNewsExcerpt, NewsCollectionPolicy, NewsCompanyContext
from src.features.news_intake.quote_selection import quote_candidates, restore_quote_response
from src.features.news_intake.statement_plan_role import selected_statement_plan_part
from src.features.news_intake.tests.test_statement_plan_role import _candidate


SAVED = json.loads((Path(__file__).parent / 'fixtures/eleventh_mixed_planned_statement.json')
                   .read_text(encoding='utf-8'))
COMPANY = NewsCompanyContext(**{**SAVED['company'], 'aliases': tuple(SAVED['company']['aliases']),
    'executive_names': tuple(SAVED['company']['executive_names'])})
AS_OF = dt.date.fromisoformat(SAVED['as_of'])
GENERIC = NewsCompanyContext('가온페이')
CURRENT = '가온페이는 사업자가 결제 데이터를 활용하도록 지원하는 데 힘쓰고 있다.'
PLAN = '가온페이 관계자는 “사업자를 위한 상담 지원을 앞으로도 이어가겠다”고 말했다.'


def _generic(plan=PLAN, *, current=CURRENT, after='', **changes):
    text = current + ' ' + plan
    raw = {key: '' for key in EXCERPT_SCHEMA['required']}
    raw.update(text=text, section_id='future_strategy', claim_slot='future_strategy:stated_plan',
               claim_kind='company_statement', temporal_status='planned', topic='strategy',
               event_key='사업자 상담 지원 계획')
    raw.update(changes)
    excluded = Counter()
    return _excerpt(raw, _candidate(SAVED['article']), text + after, GENERIC, AS_OF, excluded)


def test_실제11차_원선택은_불변이고_직접의지만_정확범위의_미검수_후보로_복구한다():
    original = deepcopy(SAVED)
    article = SAVED['article']
    candidate = _candidate(article)
    articles = [(candidate, article['body'])]
    regenerated = {q['id']: q for q in quote_candidates(candidate, article['body'], COMPANY)}
    assert all(regenerated[q['id']] == q for q in SAVED['selected_quotes'])
    assert hashlib.sha256(article['body'].encode()).hexdigest() == SAVED['body_sha256']
    restored = restore_quote_response(SAVED['saved_response'], articles=articles, company=COMPANY)
    chosen = restored['items'][0]['excerpts'][1]
    assert chosen['text'] == article['body'][493:692]
    assert chosen['claim_kind'] == 'company_statement' and chosen['temporal_status'] == 'planned'
    # 산업 확장 배열은 정상 split_response와 같은 투영으로만 분리한다. 원응답은 불변이다.
    direct = deepcopy(restored)
    assert direct['items'][0].pop('industry_problems') == []
    roles = Counter()
    excerpts, excluded = validate_grounded_response(direct, articles=articles, company=COMPANY,
                                                    as_of=AS_OF, role_diagnostics=roles)
    future, = [e for e in excerpts if e.section_id == 'future_strategy']
    assert (future.span_start, future.span_end, future.split_from) == (555, 692, 493)
    assert future.text == article['body'][555:692]
    assert future.claim_kind == 'company_plan' and future.temporal_status == 'planned'
    assert future.claim_slot == 'future_strategy:stated_plan' and not future.event_on
    assert roles == {'role_explicit_statement_plan_kind': 1}
    assert excluded == {'grounded_attribution_required': 1}  # 기존 완료 행사 후보는 그대로 거절.
    fragment = to_evidence_fragment(future)
    assert fragment.text_sha256 == hashlib.sha256(article['body'][555:692].encode()).hexdigest()
    assert fragment.published_on == '2026-10-21' and not fragment.statement_on and not fragment.event_on
    assert fragment.url == article['url'] and fragment.supported_claim_slots == ('future_strategy:stated_plan',)
    selected, rejected = select_diverse_excerpts([future], NewsCollectionPolicy(max_fragments=1))
    assert selected == (future,) and not rejected
    selected, rejected = select_diverse_excerpts([future], NewsCollectionPolicy(max_fragment_chars=136))
    assert not selected and rejected == {'fragment_budget': 1}
    assert SAVED == original


@pytest.mark.parametrize('plan', [
    PLAN.replace('가온페이 관계자', '다른회사 관계자'),
    PLAN.replace('관계자', '기자'),
    PLAN.replace('앞으로도 이어가겠다', '가능하면 이어가겠다'),
    PLAN.replace('앞으로도 이어가겠다', '이어갈 것으로 보인다'),
    PLAN.replace('앞으로도 이어가겠다', '이미 완료했다'),
    PLAN.replace('사업자를 위한', '다른회사는 사업자를 위한'),
    PLAN.replace('앞으로도', '2025년 12월 31일까지'),
    PLAN.replace('앞으로도', '지난해'),
    '가온페이 관계자는 “다른회사가 발표한 계획을 인용하면”이라며 “사업자 상담 지원을 이어가겠다”고 말했다.',
])
def test_타사_기자_조건_전망_완료_만료_과거_인용전달을_승격하지않는다(plan):
    assert _generic(plan) is None


@pytest.mark.parametrize('current', [
    CURRENT.replace('가온페이는', '다른회사는'),
    '가온페이는 지난해 사업자 상담 계획을 소개했다.',
    '가온페이는 사업자 상담 행사를 완료했다.',
    '가온페이는 여건이 되면 상담 서비스 제공에 힘쓰고 있다.',
])
def test_같은회사_현재설명_범위를_벗어난_앞문장을_떼어_구제하지않는다(current):
    assert _generic(current=current) is None


@pytest.mark.parametrize('current', [
    '가온페이는 이 계획을 철회한다.',
    '가온페이는 이 계획을 취소하고 있다.',
    '가온페이가 해당 방침을 중단하고 있다.',
])
def test_같은계획의_앞문장_철회를_잘라_의지만_남기지않는다(current):
    assert _generic(current=current) is None


@pytest.mark.parametrize('current', [
    '가온페이는 다른 계획을 철회한다.',
    '가온페이는 다른회사의 계획을 취소하고 있다.',
    '가온페이는 다른회사가 이 계획을 취소했다고 설명한다.',
])
def test_다른계획이나_타사철회_설명을_자사_계획철회로_추정하지않는다(current):
    assert _generic(current=current).text == PLAN


@pytest.mark.parametrize('after', [
    ' 하지만 이 계획은 철회했다.',
    ' 그러나 가온페이는 이 계획을 철회했다고 밝혔다.',
    ' 다만 이 계획은 취소되었다.',
])
def test_바로다음_동일계획_철회는_분리전에_확인한다(after):
    assert _generic(after=after) is None


@pytest.mark.parametrize('after', [
    ' 다른회사는 이 계획을 철회했다.',
    ' 하지만 다른 계획은 철회했다.',
    ' 가온페이는 고객 상담 행사를 마쳤다.',
])
def test_다른회사_다른계획_완료는_독립_미래의지를_지우지않는다(after):
    assert _generic(after=after).text == PLAN


@pytest.mark.parametrize('changes', [
    {'temporal_status': 'ongoing'}, {'temporal_status': 'completed'},
    {'claim_kind': 'reported_fact'}, {'claim_slot': 'future_strategy:plan_status'},
    {'section_id': 'culture', 'claim_slot': 'culture:work_principle'},
    {'event_on': '2026-10-21'},
])
def test_새분리는_기존시제와_역할판정을_다시쓰지않는다(changes):
    text = CURRENT + ' ' + PLAN
    original = GroundedNewsExcerpt(_candidate(SAVED['article']), text,
        'future_strategy', 'future_strategy:stated_plan', 'company_statement', 'planned',
        'strategy', '사업자 상담 계획', '', 0, len(text))
    assert selected_statement_plan_part(replace(original, **changes), GENERIC, body=text, as_of=AS_OF) is None


def test_다른원문이나_좌표_발표일_불일치로_분리범위를_만들지않는다():
    text = CURRENT + ' ' + PLAN
    original = GroundedNewsExcerpt(_candidate(SAVED['article']), text,
        'future_strategy', 'future_strategy:stated_plan', 'company_statement', 'planned',
        'strategy', '사업자 상담 계획', '', 0, len(text))
    assert selected_statement_plan_part(original, GENERIC, body='다른 기사', as_of=AS_OF) is None
    assert selected_statement_plan_part(replace(original, span_start=1), GENERIC, body=text, as_of=AS_OF) is None
    assert selected_statement_plan_part(original, GENERIC, body=text, as_of=dt.date(2026, 10, 20)) is None
