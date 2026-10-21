"""실제 영문 사건의 기존 실패와 새 독립 검수 계약을 분리해 검증한다."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from src.features.composer.news_translation import prepare_translation_requests, bind_translation_response
from src.features.composer.port import CollectedFragment, ComposedSentence
from src.features.composer.prose_facts import ProseEvidence, evaluate_verified_prose_fact
from src.features.composer.verify import _ReviewItem, _ask_verdicts, _recheck_rewritten
from src.features.provenance.sources import Source, SourceKind
from src.features.report_standard.publish import _evidence_support_problems
from src.shared.report_quality.fact_binding import fact_evidence_binding
from src.shared.report_quality.news_translation import text_sha256, fact_translation_supported
from src.shared.report_quality.evidence_support import fact_prose_evidence_support_ready
from src.shared.report_quality.source_identity import document_identity


SAVED = json.loads((Path(__file__).parent / 'fixtures/english_news_translation.json').read_text(encoding='utf-8'))
ARTICLE = SAVED['article']
QUOTE = SAVED['selected_text']
PREFIX = f"{ARTICLE['indexed_published_on']} {ARTICLE['publisher']} 보도에 따르면, "


def _source():
    return Source(number=83, kind=SourceKind.NEWS, source_type=SourceKind.NEWS.value,
                  label=ARTICLE['title'], title=ARTICLE['title'], publisher=ARTICLE['publisher'],
                  source_id='news-source-83', url=ARTICLE['url'], published_at=ARTICLE['indexed_published_on'],
                  location='원문 3339:3574', document_content_sha256=SAVED['body_sha256'],
                  exact_evidence_hashes=[SAVED['quote_sha256']])


def _fragment():
    source = _source()
    return CollectedFragment('83', '뉴스', QUOTE, source_url=ARTICLE['url'],
        location=source.location, document_date=ARTICLE['indexed_published_on'],
        document_identity=document_identity(source), document_content_sha256=SAVED['body_sha256'],
        counts_toward_document_floor=False, formal_source_kind='news',
        supported_claim_slots=('current_challenges:issue',), source_publisher=ARTICLE['publisher'],
        news_claim_kind='company_statement', news_temporal_status='ongoing', news_grounded=True)


def _item(claim=None):
    return _ReviewItem(34, ComposedSentence(claim or SAVED['claim'], ('83',), '확인',
                       planned_claim_slot='current_challenges:issue'), 'current_challenges')


def _test_response(request, *, claim=None):
    """새 계약용 가공 검수 응답. 저장된 모델 승인의 재생·수정본이 아니다."""
    body = (claim or SAVED['claim'])[len(PREFIX):]
    return {'번호': request['request']['number'], '장': 'current_challenges', '근거': ['83'],
        '근거대조': '83: 사건과 회사 부인 발언의 주체·부정·범위 대조', '결과': '참',
        '검증근거': {'번역': {'요청SHA': request['request_sha256'],
            '문장SHA': request['request']['candidate_sha256'], '원문SHA': request['request']['quote_sha256'],
            '근거': '83', '대응': [{'원문': QUOTE, '주장': body}]}}}


def _reviewer(mutator=None, capture=None):
    def ask(prompt):
        line = next(line for line in str(prompt).splitlines() if line.startswith('  번역 요청(JSON 자료): '))
        request = json.loads(line.split(': ', 1)[1])
        entry = _test_response(request)
        if mutator:
            mutator(entry)
        if capture is not None:
            capture.append((request, deepcopy(entry)))
        return json.dumps({'판정': [entry]}, ensure_ascii=False)
    return ask


def _reviewed():
    final = {}
    _recheck_rewritten(_reviewer(), [_item()], {'83': _fragment()}, '', '', final)
    return final[34]


def _fact(sentence=None, source=None):
    return evaluate_verified_prose_fact(sentence or _reviewed(), section_id='current_challenges',
        company_name='토스페이먼츠', as_of_date='2026-10-21',
        evidence=(ProseEvidence('83', source or _source(), QUOTE),))


def test_실제원문과_기존참판정에는_번역증명을_소급부여하지않는다():
    assert text_sha256(QUOTE) == SAVED['quote_sha256']
    assert len(QUOTE) == SAVED['quote']['end'] - SAVED['quote']['start']
    entry = SAVED['original_review_entry']
    assert entry['결과'] == '참' and entry['검증근거'] == {}
    reasons = {}
    verdicts = _ask_verdicts(lambda _: json.dumps({'판정': [entry]}, ensure_ascii=False),
        [_item()], {'83': _fragment()}, '', grounding_problems=reasons)
    assert verdicts[34] != '참'
    assert reasons[34] == 'news_translation_proof_invalid'
    assert not verdicts.translation_receipts


def test_새독립검수증명은_공개한국어불변으로_원문과_정본출고까지_결속된다():
    original = deepcopy(SAVED)
    sentence = _reviewed()
    assert sentence.text == SAVED['claim'] and sentence.verification_state == 'verified'
    assert sentence.news_translation_receipt
    result = _fact(sentence)
    assert not result.reason_code
    fact = result.fact
    assert fact_translation_supported(fact)
    assert fact_prose_evidence_support_ready(fact)
    assert not _evidence_support_problems(fact)
    assert fact.evidence_binding == fact_evidence_binding(fact)
    manifest = json.loads(fact.state_evidence)
    assert manifest[0]['news_exact_text'] == QUOTE
    assert not fact.evidence_support_terms  # 영어를 공개 문장에 끼워 넣지 않았다.
    assert SAVED == original


@pytest.mark.parametrize('mutation', [
    lambda e: e.update(결과='거짓'),
    lambda e: e.update(결과='애매'),
    lambda e: e.update(검증근거=None),
    lambda e: e.update(검증근거=[]),
    lambda e: e.update(장='portfolio'),
    lambda e: e.update(근거=['80']),
    lambda e: e['검증근거']['번역'].update(요청SHA='0'*64),
    lambda e: e['검증근거']['번역'].update(문장SHA='0'*64),
    lambda e: e['검증근거']['번역'].update(원문SHA='0'*64),
    lambda e: e['검증근거']['번역'].update(근거='80'),
    lambda e: e['검증근거']['번역']['대응'][0].update(원문=QUOTE[:-1]),
    lambda e: e['검증근거']['번역']['대응'][0].update(주장=SAVED['claim'][len(PREFIX):-1]),
])
def test_거짓불명확_다른요청_다른근거_불완전범위는_승인되지않는다(mutation):
    verdicts = _ask_verdicts(_reviewer(mutation), [_item()], {'83': _fragment()}, '')
    assert verdicts[34] != '참' and not verdicts.translation_receipts


def test_다른검수_문장_출처_미검수로의_영수증복사는_실패한다():
    item, fragment = _item(), _fragment()
    first = prepare_translation_requests([item], {'83': fragment})[34]
    second = prepare_translation_requests([item], {'83': fragment})[34]
    assert first['request_sha256'] != second['request_sha256']
    assert not bind_translation_response(_test_response(first), second, claim=item.sentence.text, quote=QUOTE)
    sentence = _reviewed()
    for changed in [replace(sentence, text=sentence.text.replace('증거는 없다', '증거가 있다')),
                    replace(sentence, verification_state='unverified'),
                    replace(sentence, planned_claim_slot='current_challenges:response')]:
        assert _fact(changed).fact is None
    assert _fact(sentence, replace(_source(), url='https://other.example/article')).fact is None
    assert _fact(sentence, replace(_source(), document_content_sha256='0'*64)).fact is None


def test_정본봉인과_응답증명이_손상되면_재사용되지않는다():
    fact = _fact().fact
    manifest = json.loads(fact.state_evidence)
    manifest[0]['news_translation_review']['response']['결과'] = '거짓'
    changed = replace(fact, state_evidence=json.dumps(manifest, ensure_ascii=False))
    assert changed.evidence_binding != fact_evidence_binding(changed)
    assert not fact_translation_supported(changed)
    assert _evidence_support_problems(changed)
    assert not fact_prose_evidence_support_ready(changed)


@pytest.mark.parametrize('field,value', [
    ('document_content_sha256', '0'*64), ('exact_evidence_hashes', ['0'*64]),
    ('url', 'https://other.example/article'), ('location', '다른 원문 범위'),
    ('published_at', '2026-09-12'), ('publisher', '다른 매체'),
])
def test_최종출고는_실제Source의_본문지문과_메타를_다시대조한다(field, value):
    fact = _fact().fact
    source = replace(_source(), **{field: value})
    assert fact_translation_supported(fact, source=_source())
    assert not fact_translation_supported(fact, source=source)
    assert _evidence_support_problems(fact, source=source)


def test_공통영문명칭두개가있어도_손상된번역영수증을_구제하지않는다():
    fact = _fact().fact
    changed = replace(fact, claim=fact.claim + ' Toss Payments',
                      evidence_support_terms=['Toss', 'Payments'])
    changed = replace(changed, evidence_binding=fact_evidence_binding(changed))
    assert not fact_translation_supported(changed, source=_source())
    assert not fact_prose_evidence_support_ready(changed)
    assert _evidence_support_problems(changed, source=_source())


def test_부정반전과_숫자추가는_번역증명으로_구제하지않는다():
    from src.shared.report_quality.news_translation import translation_pairs
    assert translation_pairs(SAVED['claim'].replace('증거는 없다', '증거가 있다'), QUOTE, PREFIX,
        [{'원문': QUOTE, '주장': SAVED['claim'][len(PREFIX):].replace('증거는 없다', '증거가 있다')}]) is None
    # 수치 검증은 번역 증명과 독립이며 같은 원문에 없는 인원수를 면제하지 않는다.
    from src.shared.report_quality.supplementary_prose import news_number_tokens
    assert not news_number_tokens('제3자 3명이 결제 내역을 열람했다.', source_text=QUOTE).issubset(
        news_number_tokens(QUOTE, source_text=QUOTE))


def test_같은언어_혼합출처_다중인용_해석은_새번역계약대상이아니다():
    item = _item()
    fragment = _fragment()
    assert not prepare_translation_requests([item], {'83': replace(fragment, text='회사는 사고 원인을 설명했다.')})
    assert not prepare_translation_requests([replace(item, sentence=replace(item.sentence, grade='해석'))], {'83': fragment})
    assert not prepare_translation_requests([replace(item, sentence=replace(item.sentence, citations=('83', '80')))], {'83': fragment})
    assert not prepare_translation_requests([item], {'83': replace(fragment, formal_source_kind='official_web_page')})


@pytest.mark.parametrize('grouped', [False, True])
def test_최초본문_두검수경로와_품질투영_요약에서도_같은증명을_보존한다(grouped):
    from src.features.composer.verify import _semantic_review, _semantic_review_grouped
    from src.features.composer.extractive_summary import _verified_fact_registry
    from src.features.composer.quality_projection import _claim_fact
    from src.shared.report_quality.dto import SourceDocument
    from src.shared.report_quality.supplementary_prose import reviewed_news_prose_problems
    fragments = {'83': _fragment()}
    if grouped:
        groups, _ = _semantic_review_grouped([[_item().sentence]], ['current_challenges'], {},
            {'current_challenges': frozenset({'83'})}, fragments, None, _reviewer())
    else:
        groups = _semantic_review([[_item().sentence]], fragments, (), None, _reviewer(),
                                  group_ids=['current_challenges'])
    assert len(groups[0]) == 1
    sentence = groups[0][0]
    assert sentence.text == SAVED['claim'] and sentence.news_translation_receipt
    fact = _fact(sentence).fact
    assert _verified_fact_registry([fact])[0][fact.fact_id] == fact
    source = SourceDocument(source_id=_source().source_id, document_identity=document_identity(_source()),
        exact_evidence_hashes=(SAVED['quote_sha256'],), document_content_sha256=SAVED['body_sha256'],
        publisher=ARTICLE['publisher'], counts_toward_document_floor=False, source_kind='news',
        published_on=ARTICLE['indexed_published_on'])
    assert reviewed_news_prose_problems(_claim_fact(fact), {source.source_id: source}) == ()
    assert reviewed_news_prose_problems(_claim_fact(fact),
        {source.source_id: replace(source, document_content_sha256='0'*64)})
