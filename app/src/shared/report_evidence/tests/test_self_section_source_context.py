"""신규 자기부문 문맥의 실제 운송 핀과 부분문장 범위를 구분한다."""

import hashlib
import json

import pytest

from src.shared.report_evidence.source_context import parse_source_context


def _fixture():
    digest = lambda text: hashlib.sha256(text.encode()).hexdigest()
    owner = '푸른주식회사'
    declaration = '당사는 계측기를 제조/판매 등을 하는 계측 부문으로 구성되어 있는 기업입니다.'
    heading = '[계측 부문]'
    text = '매출유형 | 품목 | 구체적용도 ; 제품 | 감지모듈 | 설비 계측용'
    document = owner + '\n\n' + declaration + '\n\n' + heading + '\n\n' + text
    start, declaration_start, heading_start = map(document.index, (text, declaration, heading))
    section = dict(version='source-section-context-v1', document_id='d1',
                   document_sha256=digest(document), text=heading,
                   location=f'{heading_start}-{heading_start + len(heading)}',
                   text_sha256=digest(heading), scope_location=f'{heading_start}-{len(document)}',
                   fragment_location=f'{start}-{len(document)}', fragment_sha256=digest(text))
    canonical = lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    section_json = canonical(section)
    context = dict(version='source-context-v1', origin='self_section_declaration',
                   text=declaration, location=f'{declaration_start}-{declaration_start + len(declaration)}',
                   text_sha256=digest(declaration), actor=owner, document_actor=owner,
                   document_actor_location=f'0-{len(owner)}', document_actor_sha256=digest(owner),
                   status='', declaration_part='계측부문', section_context_json=section_json)
    pins = dict(document_id='d1', document_sha256=digest(document),
                fragment_location=section['fragment_location'], fragment_sha256=digest(text),
                section_context_json=section_json, binding_scope='fragment')
    return canonical(context), pins


def test_문서_조각_사업범위_핀은_모두_실제운송대상과_같아야한다():
    raw, pins = _fixture()
    assert parse_source_context(raw, **pins)['origin'] == 'self_section_declaration'
    for name, value in [('document_id', 'other'), ('document_sha256', '0' * 64),
                        ('fragment_location', '1-2'), ('fragment_sha256', '0' * 64),
                        ('section_context_json', '')]:
        with pytest.raises(ValueError):
            parse_source_context(raw, **{**pins, name: value})


def test_신규origin의_적격입구는_핀누락을_허용하지않는다():
    raw, pins = _fixture()
    for name in ('document_id', 'document_sha256', 'fragment_location', 'fragment_sha256'):
        with pytest.raises(ValueError):
            parse_source_context(raw, **{**pins, name: ''})
    with pytest.raises(ValueError):
        parse_source_context(raw, **{**pins, 'section_context_json': None})


def test_부분비교문장은_문서핀과_원사업범위로만_재검증한다():
    raw, pins = _fixture()
    partial = {name: value for name, value in pins.items()
               if name not in ('fragment_location', 'fragment_sha256')}
    partial['binding_scope'] = 'document'
    assert parse_source_context(raw, **partial)['declaration_part'] == '계측부문'
    with pytest.raises(ValueError):
        parse_source_context(raw, **{**partial, 'document_id': 'other'})


def test_레거시빈문맥은_새결속인자를_요구하지않는다():
    assert parse_source_context('', binding_scope='fragment') == {}


def test_공개접수번호는_공식종류와_URL로_수집문서ID에_결속한다():
    from dataclasses import replace
    from src.features.company_comparison.official_sources import OfficialCandidateSentence
    from src.features.provenance.sources import Source, SourceKind
    raw, pins = _fixture()
    context = json.loads(raw)
    section = json.loads(pins['section_context_json'])
    receipt = '20260318000959'
    section['document_id'] = f'dart_business_report:{receipt}'
    canonical = lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    context['section_context_json'] = canonical(section)
    source = Source(number=1, kind=SourceKind.FILING, label='공식 원문',
                    document_id=receipt, formal_source_kind='dart_business_report',
                    url=f'https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}')
    candidate = OfficialCandidateSentence(source, '감지모듈',
        document_content_sha256=pins['document_sha256'],
        source_context_json=canonical(context), section_context_json=canonical(section))
    assert candidate.source.document_id == receipt
    for changed in (replace(source, document_id='20250318001355'),
                    replace(source, formal_source_kind='dart_semiannual_report'),
                    replace(source, url='https://example.org/')):
        with pytest.raises(ValueError):
            replace(candidate, source=changed)
