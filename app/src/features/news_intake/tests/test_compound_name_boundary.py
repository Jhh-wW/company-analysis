"""전체 법인명 뒤의 정확 복합조사만 인식하고 사건 귀속 정책은 유지한다."""

from collections import Counter
import datetime as dt

import pytest

from src.features.news_intake.grounded import _bound_subject, _excerpt
from src.features.news_intake.identity_names import mentions_target
from src.features.news_intake.models import NewsCandidate, NewsCompanyContext


COMPANY = NewsCompanyContext('가나다전자 주식회사', aliases=('가나다전자', 'ABC Electronics'))


@pytest.mark.parametrize('text', [
    '지난달 가나다전자에서는 가맹점이 외부에 노출한 인증정보를 제3자가 이용하는 사고가 발생했다.',
    'ABC Electronics에서는 보안 사고가 발생했다고 밝혔다.',
    '가나다전자에서는 “점검을 진행했다”고 말했다.',
])
def test_정확한_전체회사명뒤_에서는를_인식한다(text):
    assert mentions_target(text, COMPANY)
    assert _bound_subject({'text': text, 'subject': '', 'subject_evidence': ''},
                          text, COMPANY, 0) == (text, 0)


@pytest.mark.parametrize('text', [
    '한빛가나다전자에서는 사고를 확인했다.',
    '가나다전자에서는컴퍼니가 사고를 확인했다.',
    '가나다전자에서는도 사고를 확인했다.',
    '가나다전자상사에서는 사고를 확인했다.',
    '가나다에서는 사고를 확인했다.',
    '다른회사에서는 사고를 확인했다.',
])
def test_부분이름이나_조사뒤_다른법인접미사는_인식하지않는다(text):
    assert not mentions_target(text, COMPANY)


def test_이름인식이_기자서술을_회사발언으로_승격하지않는다():
    text = '지난달 가나다전자에서는 가맹점이 외부에 노출한 인증정보를 제3자가 이용하는 사고가 발생했다.'
    candidate = NewsCandidate('unit-incident', '보안 사고', '', '', '', '2026-10-06',
                             'example.com', 4, 'https://example.com/news', source_category='news_report')
    raw = dict(text=text, subject='', subject_evidence='', time_evidence='',
        section_id='current_challenges', claim_slot='current_challenges:issue',
        claim_kind='reported_fact', temporal_status='ongoing', topic='risk', event_key='사고', event_on='')
    rejected = Counter()
    assert _excerpt(raw, candidate, text, COMPANY, dt.date(2026, 10, 21), rejected) is None
    assert rejected == {'grounded_attribution_required': 1}
