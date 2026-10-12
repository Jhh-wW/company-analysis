"""예시 원문은 보존하되 비교·산업의 실제 회사 증거로 재사용하지 않는다."""

import copy
import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.features.pipeline.comparison_transport import build_typed_comparison_candidate_inputs
from src.features.pipeline.official_evidence_transport_adapter import merge_official_evidence_fragments
from src.features.pipeline.tests.test_comparison_transport_dates import (
    _formal_result, _PROFILE, _CORP_CODE, _COMPANY_NAME, _COLLECTED_ON,
)
from src.features.pipeline.tests.test_section_context_wiring import _packets
from src.features.pipeline.tests.test_official_industry_context import prepare, bound_input
from src.shared.report_evidence.industry_candidates import OfficialIndustryCandidateEvidence
from src.shared.report_evidence.models import DocumentTextRange
from src.shared.report_evidence.practice_context import build_practice_context
from src.features.pipeline.business_activity_anchors import build_business_activity_anchors


def _result(text='현재 온보딩 플로우를 기준으로 두 안을 구현한다. 조건: 모바일 375px.'):
    result = _formal_result('dart_business_report')
    document = next(doc for group in result.candidates for doc in group.documents)
    ranges = ('예를 들어 이렇게 요청합니다.', text)
    full = '\n'.join(ranges)
    digest = hashlib.sha256(full.encode()).hexdigest()
    text = ranges[1]
    text_digest = hashlib.sha256(text.encode()).hexdigest()
    start = len(ranges[0]) + 1
    location = f'{start}-{len(full)}'
    context = build_practice_context(ranges=ranges, document_id=document.document_id,
        document_sha256=digest, fragment_index=1, fragment_location=location)
    assert context
    changed = replace(result, candidates=tuple(replace(group,
        documents=tuple(replace(doc, content_sha256=digest,
            exact_evidence_hashes=(text_digest,),
            usable_ranges=(DocumentTextRange(start, len(full)),)) for doc in group.documents),
        fragments=tuple(replace(fragment, text=text, text_sha256=text_digest,
            location=location, practice_context_json=context) for fragment in group.fragments),
    ) for group in result.candidates))
    return changed, context


def _comparison(raw, result):
    return build_typed_comparison_candidate_inputs(raw, result=result, profile=_PROFILE,
        corp_code=_CORP_CODE, company_name=_COMPANY_NAME, collected_on=_COLLECTED_ON)


def test_bound_practice_survives_raw_packet_but_is_not_comparison_evidence():
    result, context = _result()
    raw = merge_official_evidence_fragments({}, result)[0]
    assert all(row['practice_context_json'] == context for row in raw.values())
    packet = _packets(raw, result.source_snapshot_sha256)
    typed = [f for p in packet.packets for f in p.fragments if f.formal_source_kind]
    assert typed and all(f.practice_context_json == context for f in typed)
    sources, candidates = _comparison(raw, result)
    assert sources and not candidates
    plain = replace(result, candidates=tuple(replace(group,
        fragments=tuple(replace(f, practice_context_json='') for f in group.fragments),
    ) for group in result.candidates))
    assert result.source_snapshot_sha256 != plain.source_snapshot_sha256
    assert packet.packet_sha256s != _packets(
        merge_official_evidence_fragments({}, plain)[0], plain.source_snapshot_sha256,
    ).packet_sha256s


@pytest.mark.parametrize('change', ['delete', 'replace'])
def test_comparison_rejects_lost_or_replaced_practice(change):
    result, _ = _result()
    raw = copy.deepcopy(merge_official_evidence_fragments({}, result)[0])
    row = next(iter(raw.values()))
    if change == 'delete':
        row.pop('practice_context_json')
    else:
        row['practice_context_json'] = '{}'
    with pytest.raises(ValueError, match='예시 문맥'):
        _comparison(raw, result)


def test_industry_selected_cannot_drop_or_add_practice_context():
    _, fallback, selected, calls, _ = prepare()
    altered = SimpleNamespace(**{field: getattr(selected, field)
                                 for field in selected.__dataclass_fields__})
    altered.practice_context_json = '{}'
    assert fallback((altered,)) == () and not calls


def test_industry_example_only_has_no_analysis_callback():
    result, _ = _result()
    _, fallback, _, calls, _ = prepare(evidence=result, profile=_PROFILE)
    assert fallback is None and not calls


def test_business_anchor_does_not_reinterpret_bound_practice():
    result, _ = _result('제조·판매하는 기업의 사업을 다음과 같이 작성한다.')
    assert build_business_activity_anchors(result, profile=_PROFILE) == ()


def test_real_company_execution_in_same_range_keeps_no_practice_marker():
    ranges = ('예를 들어 이렇게 요청합니다. 당사는 현재 정밀부품을 제조·판매합니다.',)
    assert build_practice_context(ranges=ranges, document_id='d1',
        document_sha256=hashlib.sha256(ranges[0].encode()).hexdigest(),
        fragment_index=0, fragment_location=f'0-{len(ranges[0])}') == ''


def test_industry_practice_lane_is_excluded_with_real_business_anchor_present():
    evidence, selected = bound_input()
    document = next(doc for group in evidence.candidates for doc in group.documents)
    ranges = ('예를 들어 이렇게 요청합니다.', '국내 시장의 수요 감소 문제를 다음과 같이 작성한다.')
    full = '\n'.join(ranges)
    text = ranges[1]
    digest = hashlib.sha256(full.encode()).hexdigest()
    text_digest = hashlib.sha256(text.encode()).hexdigest()
    start = len(ranges[0]) + 1
    location = f'{start}-{len(full)}'
    receipt = document.document_id.rpartition(':')[2]
    new_receipt = '20260315009999'
    doc = replace(document, document_id=document.document_id.replace(receipt, new_receipt),
        canonical_url=document.canonical_url.replace(receipt, new_receipt),
        identity_binding=document.identity_binding.replace(receipt, new_receipt),
        content_sha256=digest, exact_evidence_hashes=(text_digest,),
        usable_ranges=(DocumentTextRange(start, len(full)),))
    context = build_practice_context(ranges=ranges, document_id=doc.document_id,
        document_sha256=digest, fragment_index=1, fragment_location=location)
    item = OfficialIndustryCandidateEvidence(company_id=doc.company_id,
        fragment_id='example-industry-fragment', document=doc, location=location,
        text_sha256=text_digest, text=text, practice_context_json=context)
    evidence = replace(evidence, industry_candidates=(item,))
    _, fallback, _, calls, _ = prepare(evidence=evidence)
    assert fallback is not None
    assert fallback((selected,)) == ()
    assert len(calls) == 1 and len(calls[0]['candidates']) == 1
    assert all(fragment.text != text for fragment, _ in calls[0]['candidates'])
    assert evidence.industry_candidates == (item,)
