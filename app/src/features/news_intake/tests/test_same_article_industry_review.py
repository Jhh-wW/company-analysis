"""같은 원기사의 새 사업앵커가 기존 분석 상한 안에서만 기회를 여는지 확인한다."""
from dataclasses import replace
from datetime import date
import json

import pytest

from src.features.news_intake.collection import collect_from_snapshot
from src.features.news_intake.models import NewsCandidate, NewsCompanyContext, NewsCollectionPolicy, NewsSearchSnapshot
from src.features.news_intake.search_snapshot import company_digest, policy_digest, snapshot_digest
from src.features.news_intake.tests.test_collection import accepted

BUSINESS = '가람회사는 계측 장비를 생산한다. 계측 장비는 검사 현장에 쓰인다.'
PROBLEM = '한국의 계측 장비 산업은 부품 공급 지연 문제를 겪고 있다.'


def collect_case(*, calls_limit=2, fragment_chars=1000, propose=True, business=BUSINESS,
                 tail=PROBLEM, inject_unknown=False):
    company = NewsCompanyContext('가람회사', company_id='12345678')
    policy = NewsCollectionPolicy(batch_size=1, max_body_articles=1, max_analysis_calls=calls_limit,
                                  max_fragment_chars=fragment_chars,
                                  trusted_publisher_domains=('media.example',))
    candidate = NewsCandidate(id='article-own', title='가람회사 사업 소식', description='',
                              originallink='', link='', published_on='2026-10-08',
                              publisher='media.example', priority=4,
                              source_url='https://media.example/own', source_category='news_report',
                              metadata_name_match=True, topics=('industry:계측 장비',))
    snapshot = NewsSearchSnapshot(candidates=(candidate,), query_attempts=(),
                                 company_digest=company_digest(company), policy_digest=policy_digest(policy),
                                 as_of='2026-10-09', digest='', status='success', reason_codes=(),
                                 cache_eligible=True)
    snapshot = replace(snapshot, digest=snapshot_digest(snapshot))
    body = business + '\n' + tail
    calls, fetches = [], []

    def fetch(url):
        fetches.append(url)
        return body

    def analyze(prompt, schema, max_tokens):
        data = next(json.loads(line) for line in prompt.splitlines()
                    if line.startswith('{') and '"articles"' in line)
        article, = data['articles']
        calls.append((prompt, schema, article['body']))
        assert article['body'] == body
        row = accepted(article, text=business, event_key='계측 장비 생산')
        properties = schema['properties']['items']['items']['properties']
        field = next((name for name in ('industry_assessments', 'industry_problems') if name in properties), '')
        if field:
            option = properties[field]['items']
            anchor_id, = (option['anyOf'][0] if field == 'industry_assessments' else option)['properties']['anchor_id']['enum']
            if propose:
                row[field] = [{
                    'anchor_id': 'unrequested-anchor' if inject_unknown else anchor_id,
                    'status': 'proposed', 'text': PROBLEM, 'industry': '계측 장비',
                    'problem': '부품 공급 지연', 'geography': 'domestic', 'geography_detail': '한국',
                    'geography_evidence': '한국의', 'applicability_quote': '계측 장비 산업',
                    'problem_present': True, 'same_business': True, 'geography_supported': True,
                }]
                if field == 'industry_problems':
                    row[field][0].pop('status')
            else:
                row[field] = [{'anchor_id': anchor_id, 'status': 'no_current_problem'}] if field == 'industry_assessments' else []
        return {'items': [row]}

    result = collect_from_snapshot(snapshot, company=company, as_of=date(2026, 10, 9), policy=policy,
                                   fetch_text=fetch, analyze_grounded=analyze)
    return result, calls, fetches


def test_new_verified_anchor_reuses_exact_body_with_fresh_request_and_no_duplicate_facts():
    result, calls, fetches = collect_case()
    assert len(calls) == 2 and len(fetches) == 1
    assert calls[0][0] != calls[1][0] and calls[0][1] != calls[1][1]
    assert len(result.fragments) == len(result.articles) == len(result.business_anchors) == 1
    assert len(result.industry_problems) == 1
    assert result.diagnostics['뉴스사업앵커본문재검수'] == {'기회기사': 1, '분석호출': 1, '입력기사': 1}
    assert result.diagnostics['본문읽기'] == result.diagnostics['본문시도기사'] == 1
    assert result.diagnostics['분석AI호출'] == 2


def test_existing_english_industry_search_contract_keeps_followup_opportunity():
    result, calls, fetches = collect_case(tail='The equipment industry faces a component shortage.', propose=False)
    assert len(calls) == 2 and len(fetches) == 1
    assert not result.industry_problems


@pytest.mark.parametrize('change', ['budget', 'no_problem', 'selection_drop'])
def test_unavailable_opportunity_does_not_spend_another_call(change):
    settings = {'budget': {'calls_limit': 1}, 'no_problem': {'tail': '계측 장비 산업의 소개다.'},
                'selection_drop': {'fragment_chars': 1}}[change]
    result, calls, fetches = collect_case(**settings)
    assert len(calls) == len(fetches) == 1
    assert not result.industry_problems


@pytest.mark.parametrize('change', ['nonproposal', 'unrequested_anchor'])
def test_followup_keeps_proposal_and_anchor_validation(change):
    result, calls, fetches = collect_case(propose=change != 'nonproposal',
                                         inject_unknown=change == 'unrequested_anchor')
    assert len(calls) == 2 and len(fetches) == 1
    assert not result.industry_problems
    assert len(result.fragments) == len(result.business_anchors) == 1


@pytest.mark.parametrize('business', [
    '고객사는 계측 장비를 생산하고 가람회사는 이를 사용한다.',
    '가람회사는 계측 장비를 생산할 계획이다.',
])
def test_unverified_customer_or_future_activity_cannot_mint_reuse_anchor(business):
    result, calls, fetches = collect_case(business=business)
    assert len(calls) == len(fetches) == 1
    assert not result.business_anchors and not result.industry_problems
