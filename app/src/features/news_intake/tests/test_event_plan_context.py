"""명시 행사일이 지난 실제 예정 응답을 원문 변경 없이 제한한다."""

from copy import deepcopy
from dataclasses import replace
import datetime as dt
import hashlib
import json
from pathlib import Path

import pytest

from src.features.news_intake.event_plan_context import event_plan_context_problem
from src.features.news_intake.grounded import validate_grounded_response
from src.features.news_intake.industry_context import split_response
from src.features.news_intake.models import NewsCandidate, NewsCompanyContext
from src.shared.business_challenge_context import BusinessActivityAnchor


SAVED = json.loads((Path(__file__).parent / 'fixtures/expired_event_plan.json').read_text(encoding='utf-8'))
COMPANY = NewsCompanyContext(**{**SAVED['company'], 'aliases': tuple(SAVED['company']['aliases']),
    'executive_names': tuple(SAVED['company']['executive_names']),
    'business_anchors': tuple(BusinessActivityAnchor(**a) for a in SAVED['business_anchors'])})
ARTICLE = SAVED['article']
BODY = ARTICLE['body']
CANDIDATE = NewsCandidate(id=ARTICLE['id'], title=ARTICLE['title'], description='',
    originallink=ARTICLE['url'], link=ARTICLE['url'], published_on=ARTICLE['indexed_published_on'],
    publisher=ARTICLE['publisher'], priority=4, source_url=ARTICLE['url'], source_category=ARTICLE['source_category'])


def _validate(as_of):
    articles = [(CANDIDATE, BODY)]
    direct, _, _ = split_response(SAVED['saved_response'], articles=articles, company=COMPANY,
        as_of=as_of, full_body_hashes={CANDIDATE.source_url: hashlib.sha256(BODY.encode()).hexdigest()})
    return validate_grounded_response(direct, articles=articles, company=COMPANY, as_of=as_of)


def test_실제_완료일지난_행사개최와_행사안내_계획은_현재후보가_아니다():
    before = deepcopy(SAVED)
    excerpts, rejected = _validate(dt.date(2026, 10, 21))
    assert not excerpts
    assert rejected == {'grounded_event_plan_expired': 2}
    assert SAVED == before


def test_같은_실제_응답도_행사일_전에는_두_정확원문을_보존한다():
    excerpts, rejected = _validate(dt.date(2026, 10, 1))
    assert len(excerpts) == 2 and not rejected
    assert all(BODY[e.span_start:e.span_end] == e.text for e in excerpts)


def _problem(body, text, *, as_of=dt.date(2026, 10, 21), published='2026-09-23', temporal='planned'):
    return event_plan_context_problem(text, body, replace(CANDIDATE, published_on=published),
        COMPANY, as_of, temporal_status=temporal)


OWNER = "토스페이먼츠는 사업자 세미나 '새로운 사업 전략'을 개최한다고 밝혔다. "
SCHEDULE = '세미나는 오는 10월 14일 서울에서 열린다. '
PLANNED = '특히 토스페이먼츠는 이번 세미나에서 결제 데이터 활용법을 소개할 예정이다.'


@pytest.mark.parametrize('body,text', [
    (OWNER + SCHEDULE + '세미나에서는 데이터 활용을 다룬다. ' + PLANNED, PLANNED),
    (OWNER + SCHEDULE, OWNER.strip()),
])
def test_회사_단일행사와_명시_예정행동이_결속될때만_제한한다(body, text):
    assert _problem(body, text) == 'grounded_event_plan_expired'


@pytest.mark.parametrize('body,text', [
    (OWNER.replace('토스페이먼츠', '다른회사') + SCHEDULE + PLANNED, PLANNED),
    (OWNER + SCHEDULE.replace('10월 14일', '12월 14일') + PLANNED, PLANNED),
    (OWNER + SCHEDULE + '행사 일정이 연기됐다. ' + PLANNED, PLANNED),
    (OWNER + SCHEDULE + '설명회는 오는 12월 1일 열린다. ' + PLANNED, PLANNED),
    (OWNER + SCHEDULE + '토스페이먼츠는 상시 결제 분석 서비스를 제공한다.', '토스페이먼츠는 상시 결제 분석 서비스를 제공한다.'),
    (OWNER + SCHEDULE + "토스페이먼츠는 '새로운 제품'을 소개할 예정이다.", "토스페이먼츠는 '새로운 제품'을 소개할 예정이다."),
    (OWNER + SCHEDULE + "토스페이먼츠는 내년 '새로운 사업 전략' 세미나를 다시 개최할 예정이다.", "토스페이먼츠는 내년 '새로운 사업 전략' 세미나를 다시 개최할 예정이다."),
])
def test_타사_다른미래일정_상시사업_별도제품은_이경계로_지우지않는다(body, text):
    assert not _problem(body, text)


def test_오는_월일의_연도넘김과_당일을_과거로_오판하지않는다():
    text = PLANNED
    body = OWNER + SCHEDULE.replace('10월 14일', '1월 14일') + text
    assert not _problem(body, text, published='2026-12-20', as_of=dt.date(2026, 12, 21))
    assert not _problem(OWNER + SCHEDULE + text, text, as_of=dt.date(2026, 10, 14))
