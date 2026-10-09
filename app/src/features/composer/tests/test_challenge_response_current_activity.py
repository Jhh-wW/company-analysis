"""대응 내용과 동일 활동의 현재진행 상태를 따로 검증한다."""
import pytest
from src.features.composer.challenge_event_scope import challenge_event_scope_problem


@pytest.mark.parametrize('source,claim', [
    ('시장 확대에 대응하여 압축기 라인업 확보', '회사는 압축기 라인업 확보가 진행 중이다.'),
    ('프로세스 보완 및 관리 강화', '회사는 프로세스 보완 및 관리 강화를 추진하고 있다.'),
    ('압축기 라인업 확보 | 양산 적용 중', '회사는 압축기 라인업 확보가 진행 중이다.'),
    ('냉매 라인업 확보. 전동 라인업 확보가 진행 중이다.', '회사는 냉매 라인업 확보가 진행 중이다.'),
    ('프로세스 보완; 협력사는 프로세스 보완을 추진하고 있다.', '회사는 프로세스 보완을 추진하고 있다.'),
    ('프로세스 보완 예정', '회사는 프로세스 보완이 진행 중이다.'),
    ('프로세스 보완 완료', '회사는 프로세스 보완이 진행 중이다.'),
    ('회사는 과거에 프로세스 보완을 추진하고 있었다.', '회사는 프로세스 보완을 추진하고 있다.'),
    ('향후 프로세스 보완을 추진하고 있다.', '회사는 프로세스 보완을 추진하고 있다.'),
    ('냉매 라인업 확보 성과는 양산 적용이 진행 중이다.', '회사는 냉매 라인업 확보가 진행 중이다.'),
])
def test_명사형_다른활동_다른주체_다른상태를_진행으로_빌리지않는다(source,claim):
    assert challenge_event_scope_problem(claim, {'1':source}) == 'time_invalid'


@pytest.mark.parametrize('source,claim', [
    ('회사는 압축기 라인업 확보를 진행 중이다.', '회사는 압축기 라인업 확보가 진행 중이다.'),
    ('회사는 프로세스 보완 및 관리 강화를 추진하고 있다.', '회사는 프로세스 보완 및 관리 강화를 추진하고 있다.'),
    ('프로세스 보완 완료', '회사는 프로세스 보완을 완료했다.'),
    ('프로세스 보완 예정', '회사는 프로세스 보완을 계획하고 있다.'),
    ('프로세스 보완 및 관리 강화', '회사의 대책에 프로세스 보완 및 관리 강화가 기재되어 있다.'),
    ('압축기 라인업 확보 | 양산 적용 중', '회사는 압축기 양산 적용이 진행 중이다.'),
    ('회사는 과거에 프로세스 보완을 추진하고 있었다.', '회사는 과거에 프로세스 보완을 추진하고 있었다.'),
    ('신제품 개발 성과를 양산에 적용하는 절차를 진행 중이다.', '신제품 개발 성과를 양산에 적용하는 절차를 진행 중이다.'),
    ('설비 개선 후 직원 교육을 추진하고 있다.', '설비 개선 후 직원 교육을 추진하고 있다.'),
])
def test_명시진행과_원상태_중립대응은_보존한다(source,claim):
    assert challenge_event_scope_problem(claim, {'1':source}) == ''


def _table(first, second=''):
    return '제재조치일 | 조치대상자 | 이행 및 재발방지대책 ; 2026.02.20 | 가람법인 | '+first+second


def test_행의_다른법인과_다른시점_진행대여를_막는다():
    text='가람법인은 프로세스 보완을 추진하고 있다.'
    assert challenge_event_scope_problem(text, {'1':_table('프로세스 보완', '; 2026.03.20 | 다른법인 | 프로세스 보완 추진 중')}) == 'time_invalid'
    assert challenge_event_scope_problem(text, {'1':_table('프로세스 보완', '; 2026.03.20 | 가람법인 | 프로세스 보완 추진 중')}) == 'time_invalid'
    assert challenge_event_scope_problem(text, {'1':_table('프로세스 보완 추진 중')}) == ''
    assert challenge_event_scope_problem('가람법인은 프로세스 보완을 이행 대책으로 추진하고 있다.', {'1':_table('프로세스 보완 추진 중')}) == ''


def test_조치내용과_전용대책열은_서로의_상태를_빌리지않는다():
    header='제재조치일 | 조치대상자 | 조치내용 | 이행 및 재발방지대책 ; '
    source=header+'2026.02.20 | 가람법인㈜ | 시정조치 이행 중 | 프로세스 보완 및 관리 강화'
    claim='가람법인은 법률 위반에 대해 프로세스 보완 및 관리 강화를 추진하고 있다.'
    assert challenge_event_scope_problem(claim, {'1':source}) == 'time_invalid'
    positive=source.replace('프로세스 보완 및 관리 강화','프로세스 보완 및 관리 강화 추진 중')
    assert challenge_event_scope_problem(claim, {'1':positive}) == ''
