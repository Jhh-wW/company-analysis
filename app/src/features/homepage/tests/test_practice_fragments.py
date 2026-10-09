"""교육 예시 문맥을 원문 손실 없이 공식 웹 조각에 보존한다."""

import hashlib
import json

from src.features.homepage.wide_evidence_mapping import to_evidence_mappings
from src.features.homepage.wide_fragments import build_fragments
from src.features.homepage.wide_types import WideCollectionResult, WideDocumentIdentity
from src.shared.report_evidence.practice_context import practice_context_fingerprint


def document(ranges):
    return WideDocumentIdentity(
        company_id="c1",document_id="d1",canonical_url="https://example.org/blog/demo",
        source_kind="official_web_page",publisher="새봄소프트 주식회사",title="AI 실무 방법",
        published_on="",collected_at="2026-10-10",content_sha256=hashlib.sha256("\n".join(ranges).encode()).hexdigest(),
        identity_binding="root",usable_ranges=ranges,collector_version="homepage-wide-collector/5",
        parser_version="homepage-wide-parser/2",requirement="REQUIRED",source_tier="TIER_1_OFFICIAL",
    )


def test_예시_원문과_hash는_유지하고_별도_문맥을_운송한다():
    quote="현재 온보딩 플로우를 기준으로 두 안을 구현한다. 조건: 기본·오류·로딩·완료 상태를 포함한다."
    doc=document(("예를 들어 이렇게 요청합니다.",quote))
    original=doc.usable_ranges
    fragment,=build_fragments(doc,company_id="c1")
    assert fragment.text==quote and fragment.text_sha256==hashlib.sha256(quote.encode()).hexdigest()
    assert doc.usable_ranges==original
    context=json.loads(fragment.practice_context_json)
    assert context["text"]==original[0] and context["document_sha256"]==doc.content_sha256
    mapped=to_evidence_mappings(result=WideCollectionResult(company_id="c1",documents=(doc,),attempts=()),fragments=(fragment,))
    assert mapped["fragments"][0]["practice_context_json"]==fragment.practice_context_json
    assert mapped["documents"][0]["exact_practice_context_bindings"]==[{
        "location":fragment.location,"text_sha256":fragment.text_sha256,
        "practice_context_sha256":practice_context_fingerprint(fragment.practice_context_json),
    }]


def test_같은_교육글의_실제_회사_완료_사례는_원래_지원슬롯을_보존한다():
    actual="새봄소프트는 지난해 온보딩 검수 서비스를 출시했습니다."
    doc=document(("예를 들어 이렇게 요청합니다.","현재 완료 상태를 검수한다.",actual))
    fragments=build_fragments(doc,company_id="c1")
    actual_fragment=next(f for f in fragments if f.text==actual)
    assert "past_changes:completed_execution" in actual_fragment.covered_slot_ids
    assert actual_fragment.practice_context_json==""


def test_문맥없는_구형_문서의_운송_키를_추가하지_않는다():
    doc=document(("회사는 AI 검수 서비스를 출시했습니다.",))
    fragments=build_fragments(doc,company_id="c1")
    mapped=to_evidence_mappings(result=WideCollectionResult(company_id="c1",documents=(doc,),attempts=()),fragments=fragments)
    assert "practice_context_json" not in mapped["fragments"][0]
    assert "exact_practice_context_bindings" not in mapped["documents"][0]
