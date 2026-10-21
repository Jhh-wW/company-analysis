"""실제 선택된 현재·미래 혼합 발언의 원문 결속과 기존 거절 경계를 재생한다."""

from collections import Counter
from copy import deepcopy
from dataclasses import replace
import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from src.features.news_intake.claim_role import sentence_ranges
from src.features.news_intake.grounded import validate_grounded_response, EXCERPT_SCHEMA, _excerpt
from src.features.news_intake.grounded_mapping import select_diverse_excerpts, to_evidence_fragment
from src.features.news_intake.industry_context import split_response
from src.features.news_intake.models import NewsCandidate, NewsCollectionPolicy, NewsCompanyContext
from src.features.news_intake.quote_selection import quote_candidates
from src.features.news_intake.statement_plan_role import statement_plan_parts, normalize_statement_plan_kind
from src.shared.business_challenge_context import BusinessActivityAnchor


SAVED = json.loads((Path(__file__).parent / 'fixtures/mixed_current_plan_statement.json').read_text(encoding='utf-8'))
COMPANY = NewsCompanyContext(**{**SAVED['company'], 'aliases': tuple(SAVED['company']['aliases']),
    'executive_names': tuple(SAVED['company']['executive_names']),
    'business_anchors': tuple(BusinessActivityAnchor(**a) for a in SAVED['business_anchors'])})
AS_OF = dt.date.fromisoformat(SAVED['as_of'])


def _candidate(article):
    return NewsCandidate(id=article['id'], title=article['title'], description='',
        originallink=article['url'], link=article['url'], published_on=article['indexed_published_on'],
        publisher=article['publisher'], priority=4, source_url=article['url'],
        source_category=article['source_category'])


def _validate(case):
    article = case['article']
    candidate = _candidate(article)
    articles = [(candidate, article['body'])]
    as_of = dt.date.fromisoformat(case['as_of']) if 'as_of' in case else AS_OF
    direct, _, _ = split_response(case['saved_response'], articles=articles, company=COMPANY,
        as_of=as_of, full_body_hashes={candidate.source_url: hashlib.sha256(article['body'].encode()).hexdigest()})
    roles = Counter()
    excerpts, excluded = validate_grounded_response(direct, articles=articles, company=COMPANY,
        as_of=as_of, role_diagnostics=roles)
    return excerpts, excluded, roles


def test_6차에서_이미_선택한_직접미래발언의_kind만_정상화하고_원문은_보존한다():
    case = json.loads((Path(__file__).parent / 'fixtures/sixth_selected_direct_plan.json').read_text(encoding='utf-8'))
    before = deepcopy(case)
    excerpts, excluded, roles = _validate(case)
    assert len(excerpts) == 1
    excerpt = excerpts[0]
    assert excerpt.claim_kind == 'company_plan' and excerpt.temporal_status == 'planned'
    assert excerpt.claim_slot == 'future_strategy:stated_plan'
    assert (excerpt.span_start, excerpt.span_end) == (555, 692)
    assert excerpt.text == case['article']['body'][555:692]
    assert excerpt.text.endswith('자리를 이어가겠다”고 말했다.')
    assert not excerpt.event_on and excerpt.split_from is None
    assert not excluded and roles == {'role_explicit_statement_plan_kind': 1}
    fragment = to_evidence_fragment(excerpt)
    assert fragment.text_sha256 == hashlib.sha256(excerpt.text.encode()).hexdigest()
    assert case == before


@pytest.mark.parametrize('text', [
    '다른회사 관계자는 “사업자를 위한 상담 지원을 앞으로도 이어가겠다”고 말했다.',
    '토스페이먼츠 기자는 “사업자를 위한 상담 지원을 앞으로도 이어가겠다”고 말했다.',
    '토스페이먼츠 관계자는 “조건이 맞으면 사업자를 위한 상담 지원을 이어가겠다”고 말했다.',
    '토스페이먼츠 관계자는 “한빛회사는 사업자를 위한 상담 지원을 이어가겠다”고 말했다.',
    '토스페이먼츠 관계자는 “사업자를 위한 상담 지원을 확대할 것으로 보인다”고 말했다.',
    '토스페이먼츠 관계자는 “이번 행사를 성황리에 마쳤다”고 말했다.',
    '토스페이먼츠 관계자는 “2026년 4월 14일 사업자 상담 서비스를 제공하겠다”고 말했다.',
    '토스페이먼츠 관계자는 “오는 10월 14일 행사에서 사업자 상담을 지원하겠다”고 말했다.',
    '토스페이먼츠는 사업자 상담 서비스를 확대할 예정이다.',
    '토스페이먼츠 관계자는 “한빛회사가 밝힌 계획을 전하자면”이라며 “사업자를 위한 상담 지원을 앞으로도 확대하겠다”고 말했다.',
    '토스페이먼츠 관계자는 “삼성전자가 발표한 계획을 인용하면”이라며 “사업자를 위한 상담 지원을 앞으로도 확대하겠다”고 말했다.',
])
def test_kind정리가_타사_기자_조건부_전망_완료_날짜있는계획을_승격하지_않는다(text):
    assert normalize_statement_plan_kind('company_statement', 'planned', 'future_strategy',
        'future_strategy:stated_plan', text, COMPANY) == 'company_statement'
    case = json.loads((Path(__file__).parent / 'fixtures/sixth_selected_direct_plan.json').read_text(encoding='utf-8'))
    raw = {key: '' for key in EXCERPT_SCHEMA['required']}
    raw.update(text=text, section_id='future_strategy', claim_slot='future_strategy:stated_plan',
        claim_kind='company_statement', temporal_status='planned', topic='strategy', event_key='사업자 지원 계획')
    excluded = Counter()
    assert _excerpt(raw, _candidate(case['article']), text, COMPANY, dt.date(2026, 10, 22), excluded) is None


@pytest.mark.parametrize('kind,temporal,section,slot', [
    ('reported_fact', 'planned', 'future_strategy', 'future_strategy:stated_plan'),
    ('company_statement', 'ongoing', 'future_strategy', 'future_strategy:stated_plan'),
    ('company_statement', 'completed', 'future_strategy', 'future_strategy:stated_plan'),
    ('company_statement', 'planned', 'future_strategy', 'future_strategy:plan_status'),
    ('company_statement', 'planned', 'portfolio', 'portfolio:product_role'),
])
def test_선택칸과_미래판정_둘다가_맞은_경우외에는_kind를_바꾸지_않는다(kind, temporal, section, slot):
    text = '토스페이먼츠 관계자는 “사업자를 위한 상담 지원을 앞으로도 이어가겠다”고 말했다.'
    assert normalize_statement_plan_kind(kind, temporal, section, slot, text, COMPANY) == kind


def test_실제_선택인용에서_현재원후보와_동일대표의_명시미래발언을_정확히_보존한다():
    before = deepcopy(SAVED)
    case = SAVED['cases'][0]
    current, future = _validate(case)[0]
    body = case['article']['body']
    regenerated = {q['id']: q for q in quote_candidates(_candidate(case['article']), body, COMPANY)}
    assert all(regenerated[q['id']] == q for q in case['selected_quotes'])
    selected_id = case['saved_response']['items'][0]['excerpts'][1]['text_quote_id']
    selected = regenerated[selected_id]
    assert (current.span_start, current.span_end) == (selected['start'], selected['end'])
    assert current.text == body[selected['start']:selected['end']]
    assert current.claim_kind == 'company_statement' and current.temporal_status == 'ongoing'
    assert current.claim_slot == 'operations_partners:operating_role'
    assert future.claim_slot == 'future_strategy:stated_plan'
    assert future.claim_kind == 'company_plan' and future.temporal_status == 'planned'
    assert future.text.startswith('토스페이먼츠 관계자는')
    assert future.text.endswith('자리를 이어가겠다”고 말했다.')
    assert not future.event_on
    for excerpt in (current, future):
        assert body[excerpt.span_start:excerpt.span_end] == excerpt.text
        fragment = to_evidence_fragment(excerpt)
        assert fragment.text_sha256 == hashlib.sha256(body[fragment.span_start:fragment.span_end].encode()).hexdigest()
        assert fragment.url == case['article']['url']
        assert excerpt.split_from == current.span_start
    assert SAVED == before
    assert _validate(case)[1:] == ({'grounded_attribution_required': 1}, {'role_explicit_statement_plan_split': 1})


def test_원선택_한개_예산과_실제글자상한을_그대로_적용한다():
    excerpts, _, _ = _validate(SAVED['cases'][0])
    selected, excluded = select_diverse_excerpts(list(excerpts), NewsCollectionPolicy(max_fragments=1))
    assert selected == excerpts and not excluded
    limited, excluded = select_diverse_excerpts(list(excerpts),
        NewsCollectionPolicy(max_fragments=1, max_fragment_chars=len(excerpts[0].text)))
    assert limited == (excerpts[0],) and excluded == {'fragment_budget': 1}


@pytest.mark.parametrize('replacement', [
    '다른회사 관계자는 “사업자를 위한 상담 지원을 앞으로도 이어가겠다”고 말했다.',
    '토스페이먼츠 기자는 “사업자를 위한 상담 지원을 앞으로도 이어가겠다”고 말했다.',
    '토스페이먼츠 관계자는 “조건이 맞으면 사업자를 위한 상담 지원을 이어가겠다”고 말했다.',
    '토스페이먼츠 관계자는 “좋으면 사업자를 위한 상담 지원을 이어가겠다”고 말했다.',
    '토스페이먼츠 관계자는 “가능해지면 사업자를 위한 상담 지원을 이어가겠다”고 말했다.',
    '토스페이먼츠 관계자는 “한빛회사는 사업자를 위한 상담 지원을 이어가겠다”고 말했다.',
    '토스페이먼츠 관계자는 “사업자를 위한 상담 지원을 확대할 것으로 보인다”고 말했다.',
    '토스페이먼츠 관계자는 “이번 행사를 성황리에 마쳤다”고 말했다.',
    '토스페이먼츠 관계자는 “내년 행사를 개최하겠다”고 다른 회사의 계획을 설명했다.',
])
def test_타사_기자_조건부_예측_완료는_새미래후보로_승격하지않는다(replacement):
    original = _validate(SAVED['cases'][0])[0][0]
    first_start, first_end = sentence_ranges(original.text)[0]
    text = original.text[first_start:first_end] + ' ' + replacement
    assert statement_plan_parts(replace(original, text=text, span_end=original.span_start + len(text)), COMPANY) is None


def test_선택인용_밖의_발언이나_과거공지에는_분리승격이_없다():
    original = _validate(SAVED['cases'][0])[0][0]
    start, end = sentence_ranges(original.text)[0]
    selected_only = replace(original, text=original.text[start:end], span_end=original.span_start + end)
    assert statement_plan_parts(selected_only, COMPANY) is None
    assert statement_plan_parts(replace(original, temporal_status='completed'), COMPANY) is None
    assert statement_plan_parts(replace(original, claim_kind='reported_fact'), COMPANY) is None


@pytest.mark.parametrize('case_index,reason', [(1, 'grounded_attribution_required'), (2, 'grounded_plan_mismatch')])
def test_실제_웹케시와_보안점검_계획의_기존거절은_완화하지않는다(case_index, reason):
    excerpts, excluded, roles = _validate(SAVED['cases'][case_index])
    assert len(excerpts) == 1 and excerpts[0].temporal_status == 'completed'
    assert not any(e.section_id == 'future_strategy' for e in excerpts)
    assert excluded == {reason: 1} and not roles


def test_새실제_제품배정_혼합발언은_운영노력과_향후의지를_정확범위로_분리한다():
    case = json.loads((Path(__file__).parent / 'fixtures/portfolio_mixed_statement.json').read_text(encoding='utf-8'))
    original = deepcopy(case)
    excerpts, excluded, roles = _validate(case)
    assert len(excerpts) == 2
    current, future = excerpts
    assert current.claim_slot == 'operations_partners:operating_role'
    assert current.claim_kind == 'reported_fact'
    assert current.text.endswith('인사이트로 바꾸는 데 힘쓰고 있다.')
    assert future.claim_slot == 'future_strategy:stated_plan'
    assert future.claim_kind == 'company_plan' and future.temporal_status == 'planned'
    assert not any(e.claim_slot == 'portfolio:product_role' for e in excerpts)
    assert '이어가겠다' not in current.text and '이어가겠다' in future.text
    for excerpt in excerpts:
        assert case['article']['body'][excerpt.span_start:excerpt.span_end] == excerpt.text
        assert to_evidence_fragment(excerpt).text_sha256 == hashlib.sha256(excerpt.text.encode()).hexdigest()
    selected, _ = select_diverse_excerpts(list(excerpts), NewsCollectionPolicy(max_fragments=1))
    assert selected == excerpts
    assert excluded == {'grounded_attribution_required': 1}
    assert roles == {'role_explicit_statement_plan_split': 1}
    assert case == original


@pytest.mark.parametrize('current_text,slot', [
    ('토스페이먼츠는 고객의 온라인 결제 편의를 높이는 데 힘쓰고 있다.', 'operations_partners:operating_role'),
    ('토스페이먼츠는 고객의 온라인 결제 편의를 높이는 데 주력하고 있다.', 'operations_partners:operating_role'),
    ('토스페이먼츠는 사업자를 위한 무료 세미나를 개최했다.', 'operations_partners:operating_role'),
    ('토스페이먼츠는 온라인 가맹점에 결제 인증과 대금 정산 서비스를 제공하고 있다.', 'portfolio:product_role'),
])
def test_기업지향점은_제품기능이아니며_현재서비스_설명은_보존한다(current_text, slot):
    original = _validate(SAVED['cases'][0])[0][0]
    start, end = sentence_ranges(original.text)[-1]
    text = current_text + ' ' + original.text[start:end]
    current, future = statement_plan_parts(replace(original, text=text,
        section_id='portfolio', claim_slot='portfolio:product_role', span_end=original.span_start+len(text)), COMPANY)
    assert current.claim_slot == slot and current.text == current_text
    assert future.claim_slot == 'future_strategy:stated_plan'
    if '세미나를 개최했다' in current_text:
        assert current.temporal_status == 'completed'
