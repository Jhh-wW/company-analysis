"""9차 문화 배정은 원후보를 보존하고 직접 미래 발언만 미검수 후보로 나눈다."""

from copy import deepcopy
from dataclasses import replace
import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from src.features.news_intake.grounded_mapping import select_diverse_excerpts, to_evidence_fragment
from src.features.news_intake.models import GroundedNewsExcerpt, NewsCandidate, NewsCollectionPolicy, NewsCompanyContext
from src.features.news_intake.quote_selection import quote_candidates
from src.features.news_intake.statement_plan_role import statement_plan_parts
from src.features.news_intake.tests.test_statement_plan_role import COMPANY, _candidate, _validate


FIXTURES = Path(__file__).parent / 'fixtures'
SAVED = json.loads((FIXTURES / 'ninth_culture_direct_plan.json').read_text(encoding='utf-8'))
COMPANY_GENERIC = NewsCompanyContext('가온페이')
AS_OF = dt.date(2026, 10, 23)
CURRENT = '가온페이는 사업자가 결제 데이터를 활용하도록 지원하는 데 힘쓰고 있다.'
PLAN = '가온페이 관계자는 “사업자를 위한 상담 지원을 앞으로도 이어가겠다”고 말했다.'


def _parts(plan=PLAN, *, current=CURRENT, after='', before='', **changes):
    text = current + ' ' + plan
    body = before + text + after
    candidate = NewsCandidate('test', '사업자 상담 지원', '', 'https://media.example/article',
        'https://media.example/article', '2026-10-21', '가상매체', 4, source_url='https://media.example/article',
        source_category='news_report')
    excerpt = GroundedNewsExcerpt(candidate, text, 'culture', 'culture:work_principle',
        'company_statement', 'ongoing', 'strategy', '사업자 상담 지원 계획', '', len(before), len(before) + len(text))
    return statement_plan_parts(replace(excerpt, **changes), COMPANY_GENERIC, body=body, as_of=AS_OF)


def test_실제9차_원응답과_선택인용은_불변이며_미래발언만_정확범위로_운반한다():
    before = deepcopy(SAVED)
    current, future = _validate(SAVED)[0]
    assert current.claim_slot == 'culture:work_principle'
    assert (current.span_start, current.span_end) == (493, 692)
    assert current.claim_kind == 'company_statement' and current.temporal_status == 'ongoing'
    assert future.claim_slot == 'future_strategy:stated_plan'
    assert future.claim_kind == 'company_plan' and future.temporal_status == 'planned'
    assert (future.span_start, future.span_end) == (555, 692)
    assert future.text == SAVED['article']['body'][555:692]
    assert future.candidate.published_on == '2026-10-21' and not future.event_on
    regenerated = {q['id']: q for q in quote_candidates(_candidate(SAVED['article']), SAVED['article']['body'], COMPANY)}
    assert all(regenerated[q['id']] == q for q in SAVED['selected_quotes'])
    for e in (current, future):
        assert SAVED['article']['body'][e.span_start:e.span_end] == e.text
        f = to_evidence_fragment(e)
        assert f.text_sha256 == hashlib.sha256(e.text.encode()).hexdigest()
        assert f.published_on == '2026-10-21' and not f.statement_on and not f.event_on
        assert e.split_from == 493
    assert SAVED == before


def test_8차의_미래배정과9차의_문화배정_차이를_승인재사용없이_처리한다():
    eighth = json.loads((FIXTURES / 'eighth_direct_plan_assignment.json').read_text(encoding='utf-8'))
    eighth_before = deepcopy(eighth)
    old, = _validate(eighth)[0]
    current, future = _validate(SAVED)[0]
    assert old.section_id == 'future_strategy' and old.claim_kind == 'company_statement'
    assert old.temporal_status == 'ongoing' and old.text == current.text
    assert future.text in old.text and future.claim_kind == 'company_plan'
    assert eighth == eighth_before


def test_분리후에도_원선택1개예산과_실제글자상한을_유지한다():
    excerpts, _, _ = _validate(SAVED)
    selected, excluded = select_diverse_excerpts(list(excerpts), NewsCollectionPolicy(max_fragments=1))
    assert selected == excerpts and not excluded
    limited, excluded = select_diverse_excerpts(list(excerpts), NewsCollectionPolicy(
        max_fragments=1, max_fragment_chars=len(excerpts[0].text)))
    assert limited == (excerpts[0],) and excluded == {'fragment_budget': 1}


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
def test_타사_기자_조건_전망_완료_날짜있는계획_전달은_새후보가_되지않는다(plan):
    assert _parts(plan) is None


@pytest.mark.parametrize('changes', [
    {'claim_kind': 'reported_fact'}, {'temporal_status': 'completed'},
    {'claim_slot': 'culture:verified_case'}, {'span_start': 1},
])
def test_실제선택역할과_원문좌표가_일치해야한다(changes):
    assert _parts(**changes) is None


@pytest.mark.parametrize('after', [
    ' 하지만 이 계획은 철회했다.', ' 그러나 가온페이는 이 계획을 철회했다.',
    ' 당사는 해당 방침을 취소했다.',
    ' 하지만 회사는 이 계획을 철회했다고 밝혔다.', ' 다만 이 계획은 취소되었다.',
])
def test_같은기사_바로다음의_동일계획철회는_새미래후보를_만들지않는다(after):
    assert _parts(after=after) is None


@pytest.mark.parametrize('after', [
    ' 다른회사는 이 계획을 철회했다.', ' 하지만 다른 계획은 철회했다.',
    ' 가온페이는 고객 상담 행사를 마쳤다.',
    ' 다만 다른 계획은 취소되었다.', ' 다른회사는 이 계획을 철회했다고 밝혔다.',
])
def test_다른회사_다른계획_다른행위완료는_직접미래발언을_지우지않는다(after):
    assert _parts(after=after)[1].text == PLAN


def test_다른기사_문맥을_빌리거나_원문없는_미래승격을_하지않는다():
    current = _validate(SAVED)[0][0]
    assert statement_plan_parts(current, COMPANY, as_of=AS_OF) is None
    assert statement_plan_parts(current, COMPANY, body='다른 원문', as_of=AS_OF) is None
    assert _parts(current=CURRENT.replace('가온페이는', '다른회사는')) is None
    assert _parts(current='가온페이는 지난해 사업자 상담 계획을 소개했다.') is None
