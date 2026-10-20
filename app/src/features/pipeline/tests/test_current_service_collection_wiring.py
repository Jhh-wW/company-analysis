"""실제 수집·정규화·작성 입력에서 현재 서비스와 거래 근거를 함께 보존한다."""

import hashlib
import importlib
from pathlib import Path

import pytest

from src.features.chapter_evidence.produce import produce_chapter_evidence_candidates
from src.features.composer.logic import _render_fragments
from src.features.composer.partial_evidence import build_partial_evidence_view
from src.features.composer.port import filing_meta_from_raw
from src.features.pipeline import real
from src.features.pipeline.evidence_transport import (
    build_section_evidence_packet_set, typed_fragments_from_raw,
)
from src.features.pipeline.official_evidence_transport_adapter import merge_official_evidence_fragments
from src.features.pipeline.tests.test_evidence_transport import _all_legacy_frags
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult


ENGINE_PATH = Path(__file__).resolve().parents[5] / 'analysis_engine' / 'src'
pytestmark = pytest.mark.skipif(not ENGINE_PATH.is_dir(), reason='수집 엔진이 없는 앱 전용 트리')
COMPANY_ID = '00126380'
COMPANY = '(주)가온기업'
RECEIPT = '20260315000001'
SERVICE = (
    '당사는 온라인 가맹점의 결제 승인과 정산을 연결하는 전자결제 서비스를 '
    '제공하고 있습니다. 당사는 온라인 가맹점에 결제 서비스를 제공하고 거래별 '
    '수수료를 받습니다.'
)
SLOT = 'portfolio:product_role'


def _collected(text):
    real._engine()  # 엔진 경로만 준비하며 실제 네트워크·AI를 호출하지 않는다.
    collect = importlib.import_module('features.evidence_collection.collect')
    serialize = importlib.import_module('features.evidence_collection.serialize')
    filing = importlib.import_module('features.evidence_collection.filing_select')
    body = COMPANY + '\nII. 사업의 내용\n가. 주요 제품 및 서비스\n\n' + text

    class SavedFetcher:
        def fetch_filing_list(self, company_id, pblntf_ty):
            rows = (filing.RawFilingRow(RECEIPT, '사업보고서 (2025.12)', '20260315'),) if pblntf_ty == 'A' else ()
            return filing.FilingListResult(state='OK', rows=rows)

        def fetch_document_text(self, rcept_no):
            return filing.DocumentFetchResult(state='OK', text=body,
                corp_code=COMPANY_ID, document_actor=COMPANY)

    harvest = collect.collect_dart_evidence(SavedFetcher(), COMPANY_ID, now='2026-10-19')
    envelope = serialize.harvest_to_mapping(harvest)
    candidates = produce_chapter_evidence_candidates(company_id=COMPANY_ID,
        company_name=COMPANY, company_type=envelope['company_type'],
        documents=envelope['documents'], fragments=envelope['fragments'], attempts=envelope['attempts'])
    return OfficialEvidenceCollectionResult(company_id=COMPANY_ID, candidates=candidates), body


def test_현재서비스와_가맹점거래가_수집에서_부분작성과_full_packet까지_도달한다():
    result, body = _collected(SERVICE)
    raw, _ = merge_official_evidence_fragments({}, result)
    filing_meta = filing_meta_from_raw({'rcept_no': RECEIPT, 'report_nm': '사업보고서', 'rcept_dt': '20260315'})
    conversion = typed_fragments_from_raw(corp_id=COMPANY_ID, frags=raw, filing_meta=filing_meta)
    assert conversion.rejected_count == 0
    partial = build_partial_evidence_view(conversion.fragments)
    role, = [fragment for fragment in partial.packets['portfolio'] if SLOT in fragment.supported_claim_slots]
    assert role.text.endswith(SERVICE)
    assert role.document_content_sha256 == hashlib.sha256(body.encode()).hexdigest()
    assert any('business_model:value_exchange' in fragment.supported_claim_slots
               and fragment.text == role.text for fragment in partial.packets['business_model'])
    prompt = _render_fragments(partial.packets['portfolio'], show_supported_claim_slots=True)
    assert SERVICE in prompt and SLOT in prompt

    # 기존 아홉 장 fixture는 packet 형태만 충족한다. 새 서비스 조각의 지원칸과
    # 원문은 실제 수집 결과에서 오며 다른 장의 빈 근거를 이 시험이 승격하지 않는다.
    combined = dict(raw)
    offset = max(combined)
    combined.update({number + offset: item for number, item in _all_legacy_frags().items()})
    packets = build_section_evidence_packet_set(corp_id=COMPANY_ID,
        source_generation_sha256=result.source_snapshot_sha256,
        frags=combined, filing_meta=filing_meta)
    portfolio = next(packet for packet in packets.packets if packet.section_id == 'portfolio')
    assert any(fragment == role for fragment in portfolio.fragments)


@pytest.mark.parametrize('text', [
    SERVICE.replace('당사', '외부기업㈜'),
    SERVICE.replace('당사는', '운영사는'),
    '다음은 가상의 회사입니다.\n\n' + SERVICE,
    '실습 프로젝트의 예시: 가상의 업무를 수행한다고 가정합니다.\n\n' + SERVICE,
    '실습 프로젝트의 예시:\n\n가상의 업무를 수행한다고 가정합니다.\n\n' + SERVICE,
    '실습 프로젝트의 예시: 가상의 회사입니다.\n\n' + SERVICE,
])
def test_타사_미상주체_교육예시는_현재서비스_제품작성근거로_추가하지_않는다(text):
    result, _ = _collected(text)
    assert all(SLOT not in fragment.covered_slot_ids
               for candidate in result.candidates for fragment in candidate.fragments)
