"""실제9 수집 응답→typed→작성 입력→새 모의 검수→Fact를 확인한다.

9차에 미래 작가·검수 성공 응답은 없었다. 아래 모의 응답은 연결 시험일 뿐이며
이전 실행의 참 판정·봉인·키를 복사하지 않는다.
"""

from copy import deepcopy
import hashlib
import json

import pytest

from src.core import news_intake_switch
from src.features.composer.evidence_pair_selection import writer_visible_fragments
from src.features.composer.logic import build_section_prompt, parse_section_response
from src.features.composer.port import ComposedReport, ComposedSection
from src.features.composer.render import render_report
from src.features.composer.verify import _ReviewItem, _recheck_rewritten
from src.features.news_intake.grounded_mapping import to_evidence_fragment
from src.features.news_intake.tests.test_culture_direct_plan import SAVED
from src.features.news_intake.tests.test_statement_plan_role import _validate
from src.features.pipeline.evidence_transport import typed_fragments_from_raw
from src.features.pipeline.real import _news_raw_fragment
from src.features.provenance.sources import has_valid_provenance_seal
from src.features.report_standard.publish import _evidence_support_problems
from src.shared.report_quality.fact_binding import fact_evidence_binding


@pytest.fixture
def _news_on(monkeypatch):
    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, '1')
    news_intake_switch._reset_process_news_intake_switch_for_tests()
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()


@pytest.mark.parametrize('verdict,proof_valid,expected', [('참', True, True), ('거짓', True, False), ('참', False, False)])
def test_문화분리_후보는_새로운_작성과_독립검수를_통과해야만_fact가_된다(_news_on, verdict, proof_valid, expected):
    before = deepcopy(SAVED)
    current, future = _validate(SAVED)[0]
    raws = {number: _news_raw_fragment(to_evidence_fragment(excerpt), corp_id='01492697',
        collected_on=SAVED['as_of'], document_content_sha256=SAVED['body_sha256'], grounded=True)
        for number, excerpt in ((1, current), (2, future))}
    converted = typed_fragments_from_raw(corp_id='01492697', frags=raws, filing_meta=None)
    assert converted.rejected_count == 0
    visible = writer_visible_fragments('future_strategy', converted.fragments)
    assert len(visible) == 1 and visible[0].fragment_id == '2'
    fragment = visible[0]
    assert fragment.text == SAVED['article']['body'][555:692]
    assert fragment.document_date == '2026-10-21' and fragment.source_collected_on == '2026-10-23'
    assert fragment.news_claim_kind == 'company_plan' and fragment.news_temporal_status == 'planned'
    prompt = build_section_prompt(SAVED['company']['company_name'], 'future_strategy', converted.fragments,
        None, show_supported_claim_slots=True)
    assert fragment.text in prompt and 'future_strategy:stated_plan' in prompt

    # 새 작가와 새 검수의 모의 출력이다. 실제9 원응답이나 기존 승인으로 표시하지 않는다.
    writer = json.dumps({'문장들': [{'글': fragment.text, '인용': ['2'], '등급': '확인',
        '주장슬롯': 'future_strategy:stated_plan'}]}, ensure_ascii=False)
    sentence, = parse_section_response(writer, 'future_strategy')
    assert sentence.verification_state == 'unverified'
    item = _ReviewItem(1, sentence, 'future_strategy')
    evidence = {'근거': '2', '대상': '이번 세미나를 시작으로 사업자에게 필요한 마케팅 과제를 함께 풀어가는 자리',
        '활동': '이어가겠다', '원문': '이번 세미나를 시작으로 사업자에게 필요한 마케팅 과제를 함께 풀어가는 자리를 이어가겠다', '양태': '계획'}
    if not proof_valid:
        evidence['활동'] = '투자하겠다'
    response = {'판정': [{'번호': 1, '근거대조': '2의 회사 관계자 직접 발언과 활동을 대조한 모의 검수',
        '결과': verdict, '검증근거': {'미래근거': [evidence]}}]}
    calls, final = [], {}

    def new_review(review_prompt):
        calls.append(review_prompt)
        return json.dumps(response, ensure_ascii=False)

    results = _recheck_rewritten(new_review, [item], {'2': fragment}, '', '', final, baseline_date=SAVED['as_of'])
    assert len(calls) == 1 and fragment.text in calls[0]
    accepted = results[1] == '참'
    assert accepted is expected
    sentences = (final[1],) if accepted else ()
    report = render_report(SAVED['company']['company_name'],
        ComposedReport((ComposedSection('future_strategy', sentences),)), visible, None,
        generated_at=SAVED['as_of'], as_of_date=SAVED['as_of'], company_id='01492697')
    if expected:
        fact, = report.fact_records
        assert fact.claim == fragment.text and fact.time_state == 'future'
        assert fact.claim_slot == 'future_strategy:stated_plan' and fact.verification_status == 'verified'
        assert fact.evidence_binding == fact_evidence_binding(fact)
        assert not _evidence_support_problems(fact)
        source, = report.citations
        assert source.published_at == '2026-10-21' and source.collected_at == '2026-10-23'
        assert source.document_content_sha256 == SAVED['body_sha256']
        assert source.exact_evidence_hashes == [hashlib.sha256(fragment.text.encode()).hexdigest()]
        assert has_valid_provenance_seal(source)
    else:
        assert not report.fact_records
    assert SAVED == before
