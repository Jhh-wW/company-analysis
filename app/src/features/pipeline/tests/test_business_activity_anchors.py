"""공식 사업 앵커가 회계·미래·타사 문구를 실제 사업으로 오인하지 않는지 확인한다."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from src.features.pipeline.business_activity_anchors import build_business_activity_anchors
from src.shared.name_fragments.constants import compose_name_location
from src.shared.report_evidence.constants import (
    EvidenceReadiness, SourceRequirement, SourceTier, SOURCE_KIND_DART_BUSINESS_REPORT,
)
from src.shared.report_evidence.models import (
    ChapterEvidenceCandidates, CollectedEvidenceDocument, DocumentTextRange, EvidenceFragment,
)
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult


PROFILE = {"corp_code": "00123456", "corp_name": "가온기업"}
RECEIPT = "20260331000001"


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _evidence(rows, *, section="portfolio", label=None, identity_binding=None):
    rows = tuple(rows)
    ranges, offset = [], 0
    for text in rows:
        ranges.append(DocumentTextRange(offset, offset + len(text)))
        offset += len(text) + 1
    document = CollectedEvidenceDocument(
        company_id=PROFILE["corp_code"], document_id=f"dart:{RECEIPT}",
        canonical_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={RECEIPT}",
        source_tier=SourceTier.TIER_1_OFFICIAL, source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
        publisher="금융감독원", title="공식 사업보고서", published_on="2026-03-31",
        collected_at="2026-09-30T00:00:00Z", content_sha256=_sha("\n".join(rows)),
        exact_evidence_hashes=tuple(dict.fromkeys(_sha(text) for text in rows)),
        identity_binding=identity_binding or (
            f"corp_code={PROFILE['corp_code']};rcept_no={RECEIPT};"
            f"source_kind={SOURCE_KIND_DART_BUSINESS_REPORT};identity_check=verified_match"
        ),
        usable_ranges=tuple(ranges), collector_version="시험수집기", parser_version="시험파서",
        requirement=SourceRequirement.REQUIRED,
    )
    slot = {"portfolio": "portfolio:product_role", "identity": "identity:business_definition",
            "business_model": "business_model:value_exchange",
            "operations_partners": "operations_partners:operating_role"}[section]
    fragments = tuple(EvidenceFragment(
        company_id=PROFILE["corp_code"], fragment_id=f"anchor-{section}-{index}",
        document_id=document.document_id,
        location=(compose_name_location(f"{span.start}-{span.end}", label[0], label[1])
                  if label else f"{span.start}-{span.end}"),
        text_sha256=_sha(text), text=text, section_id=section, slot_id=slot,
        score_millis=750, reason_codes=("test_exact_text",),
    ) for index, (text, span) in enumerate(zip(rows, ranges)))
    return OfficialEvidenceCollectionResult(
        company_id=PROFILE["corp_code"], candidates=tuple(ChapterEvidenceCandidates(
            company_id=PROFILE["corp_code"], section_id=current,
            documents=(document,) if current == section else (),
            fragments=fragments if current == section else (), attempts=(),
            candidate_readiness=EvidenceReadiness.READY if current == section else EvidenceReadiness.UNKNOWN,
            reason_codes=(), estimated_tokens=0, max_chars=10000, max_estimated_tokens=10000,
        ) for current in REQUIRED_EVIDENCE_SECTION_IDS),
    )


@pytest.mark.parametrize("text,item", (
    ("당사는 정밀부품을 제조·판매합니다.", "정밀부품"),
    ("회사는 고객에게 생성형 AI 서비스를 제공합니다.", "생성형 AI 서비스"),
    ("가온기업은 산업용 센서를 생산합니다.", "산업용 센서"),
    ("당사는 온라인 광고 사업을 영위합니다.", "온라인 광고"),
    ("회사의 주요 사업은 산업용 센서 제조 및 판매입니다.", "산업용 센서"),
    ("회사는 2020년에 설립되어 본점을 두고 있으며, 인공지능 소프트웨어 개발 및 공급을 주요 사업으로 영위하고 있습니다.",
     "인공지능 소프트웨어 개발 및 공급"),
    ("1. 회사의 개요 가온기업(이하 \"회사\")은 2020년에 설립되어 본점을 두고 있으며, 분석 서비스 개발 및 공급을 주요 사업으로 영위하고 있습니다.",
     "분석 서비스 개발 및 공급"),
    ("(1) 투자 계획을 수립했습니다.(2) 공시대상 사업부문의 구분 당사는 산업용 센서의 제조 및 판매를 단일사업으로 영위하는 기업입니다.(3) 회계 정책을 적용합니다.",
     "산업용 센서"),
    ("(2) 공시대상 사업부문의 구분 당사는 정밀부품의 제조  및 판매를 단일사업으로 영위 하는 기업입니다.",
     "정밀부품"),
    ("당사는 정밀부품의 제조/판매 등을 하는 부품 부문과 분석 장비의 제조/판매 등을 하는 장비 사업부문으로 구성되어 있는 글로벌 장비 기업입니다.",
     "정밀부품"),
    ("당사는 정밀부품의 제조/판매 등을 하는 부품 부문과 분석 장비의 제조/판매 등을 하는 장비 부문, 금형, 시작품의 제조/판매 등을 하는 기타 사업부문으로 구성되어 있는 글로벌 장비 기업입니다.",
     "정밀부품"),
))
def test_actual_business_preserves_whole_source_and_provenance(text, item):
    evidence = _evidence((text,))
    anchor, = build_business_activity_anchors(evidence, profile=PROFILE)
    document = evidence.candidates[2].documents[0]
    fragment = evidence.candidates[2].fragments[0]
    assert anchor.business_item == item
    assert anchor.exact_text == text
    assert anchor.text_sha256 == _sha(text)
    assert anchor.anchor_id == fragment.fragment_id
    assert anchor.location == fragment.location
    assert anchor.document_content_sha256 == document.content_sha256
    assert anchor.identity_binding == document.identity_binding
    assert anchor.source_id == ""


def test_운영칸의_명시적_현재생산도_공식원문앵커로_조사한다():
    text = ("콘텐츠 사업이란 콘텐츠의 기획, 제작, 유통, 판매, 소비와 관련된 사업을 통칭하는 것으로 "
            "당사는 기사를 중심으로 한 뉴스콘텐츠를 직접 생산 가공하고 있으며, "
            "생산 가공된 콘텐츠를 다시 당사 매체 및 타 매체를 통해 유통판매하고 있습니다.")
    evidence = _evidence((text,), section="operations_partners")
    diagnostics = {}
    anchor, = build_business_activity_anchors(evidence, profile=PROFILE, diagnostics=diagnostics)
    assert anchor.business_item == "뉴스콘텐츠"
    assert anchor.exact_text == text
    assert anchor.text_sha256 == _sha(text)
    assert anchor.location == f"0-{len(text)}"
    assert diagnostics["verified_fragments"] == diagnostics["current_business_matches"] == 1


@pytest.mark.parametrize("text", (
    "미디어사업은 광고사업 및 다양한 창구의 콘텐츠 판매 사업으로 나뉩니다.",
    "회사는 임직원에게 회계관리 서비스를 제공합니다.",
    "콘텐츠 사업이란 상품 유통을 통칭하는 것으로 고객사는 뉴스콘텐츠를 생산 가공합니다.",
    "콘텐츠 사업이란 상품 유통을 통칭하는 것으로 당사의 자회사는 뉴스콘텐츠를 생산 가공합니다.",
    "당사는 산업용 센서를 생산 가공할 계획입니다.",
    "당사는 내부 업무시스템을 운영합니다.",
))
def test_운영칸_자체는_회사사업_앵커의_양성근거가_아니다(text):
    assert build_business_activity_anchors(_evidence((text,), section="operations_partners"), profile=PROFILE) == ()


@pytest.mark.parametrize("text", (
    "회사는 고객에게 재화를 제공할 때 대가를 수익으로 인식합니다.",
    "감사인은 고객에게 제공된 서비스를 검토합니다.",
    "당사는 정관 사업목적으로 정밀부품 제조를 정했습니다.",
    "당사는 정밀부품을 생산할 계획입니다.",
    "당사의 자회사는 정밀부품을 제조합니다.",
    "회사는 경쟁사가 정밀부품을 제조한다고 설명합니다.",
    "전세계 산업은 정밀부품을 제조합니다.",
    "당사는 제품을 제조합니다.",
    "당사는 다양한 서비스를 제공합니다.",
    "당사는 사업을 영위하기 위해 준비 중입니다.",
    "당사는 정밀부품 사업을 매각하여 종료했습니다.",
    "회사는 2020년에 설립되어 정관 사업목적에 분석 서비스 개발 및 공급을 주요 사업으로 정했습니다.",
    "회사의 소프트웨어 개발 및 공급 사업은 계열사가 영위하고 있습니다.",
    "회사는 분석 서비스 개발 및 공급을 주요 사업으로 영위할 계획입니다.",
    "[부품부문] (주)다온제조 (1) 일반사항입니다.(2) 공시대상 사업부문의 구분 당사는 정밀부품의 제조 및 판매를 단일사업으로 영위하는 기업입니다.",
    "[부품부문] 다온제조(주) (1) 일반사항입니다.(2) 공시대상 사업부문의 구분 당사는 정밀부품의 제조 및 판매를 단일사업으로 영위하는 기업입니다.",
    "다온제조의 사업개요입니다. 당사는 산업용 센서를 제조합니다.",
    "다온제조(이하 회사)은 설립되었습니다. 회사는 산업용 센서를 제조합니다.",
    "종속회사 다온제조의 사업개요입니다. 당사는 산업용 센서를 제조합니다.",
    "당사는 산업용 센서를 생산할 것입니다.",
    "당사는 산업용 센서를 제조하고자 합니다.",
    "당사는 산업용 센서를 제조하지 않습니다.",
    "당사는 산업용 센서를 공급하지 않는다.",
    "당사는 향후 산업용 센서 제조 사업을 영위합니다.",
    "당사는 산업용 센서 제조 사업을 영위하지 않습니다.",
    "당사는 산업용 센서를 제조한 바 있습니다.",
    "당사는 산업용 센서 제조 사업을 영위하였습니다.",
    "당사는 산업용 센서를 제조하는 기업이었습니다.",
    "당사는 산업용 센서를 공급하는 회사였다.",
    "당사는 정밀부품의 제조/판매 등을 하는 부품 사업부문으로 구성될 기업입니다.",
    "당사는 정밀부품의 제조/판매 등을 하는 부품 사업부문으로 구성되어 있는 기업이 될 예정입니다.",
    "다온제조의 사업개요입니다. 당사는 정밀부품의 제조/판매 등을 하는 부품 사업부문으로 구성되어 있는 기업입니다.",
))
def test_accounting_future_other_company_and_generic_items_are_not_anchors(text):
    assert build_business_activity_anchors(_evidence((text,)), profile=PROFILE) == ()


def test_only_named_product_brand_segment_and_ip_labels_are_accepted():
    for kind in ("product", "brand", "segment", "ip"):
        anchor, = build_business_activity_anchors(
            _evidence(("가온센서 | 산업용 센서 | 판매",), label=(kind, "가온센서")), profile=PROFILE,
        )
        assert anchor.business_item == "가온센서"
    for kind in ("subsidiary", "contract"):
        assert build_business_activity_anchors(
            _evidence(("가온센서 | 산업용 센서 | 판매",), label=(kind, "가온센서")), profile=PROFILE,
        ) == ()
    assert build_business_activity_anchors(
        _evidence(("가온센서 | 산업용 센서 | 출시 예정",), label=("product", "가온센서")), profile=PROFILE,
    ) == ()
    assert build_business_activity_anchors(
        _evidence(("가온센서 | 산업용 센서 | 출시를 준비하고 있습니다",), label=("product", "가온센서")), profile=PROFILE,
    ) == ()
    assert build_business_activity_anchors(
        _evidence(("실제센서 | 산업용 센서 | 판매",), label=("product", "다른센서")), profile=PROFILE,
    ) == ()


def test_no_or_wrong_identity_returns_no_anchor():
    text = "당사는 정밀부품을 제조합니다."
    diagnostics = {}
    assert build_business_activity_anchors(None, profile=PROFILE, diagnostics=diagnostics) == ()
    assert diagnostics["status"] == "company_binding_unavailable"
    assert diagnostics["selected_fragments"] == 0
    assert build_business_activity_anchors(_evidence((text,)), profile={**PROFILE, "corp_code": "00888888"}) == ()
    assert build_business_activity_anchors(_evidence((text,), identity_binding="확인하지 않은 문서"), profile=PROFILE, diagnostics=diagnostics) == ()
    assert diagnostics["selected_fragments"] == 1
    assert diagnostics["verified_fragments"] == 0
    assert diagnostics["accepted_anchors"] == 0


def test_current_company_heading_and_filing_list_identity_preserve_whole_text():
    text = "(주)가온기업 (1) 일반사항입니다.(2) 공시대상 사업부문의 구분 당사는 정밀부품의 제조 및 판매를 단일사업으로 영위하는 기업입니다."
    binding = (f"corp_code={PROFILE['corp_code']};rcept_no={RECEIPT};"
               "source_kind=dart_business_report;identity_check=verified_filing_list_match")
    anchor, = build_business_activity_anchors(_evidence((text,), identity_binding=binding), profile=PROFILE)
    assert anchor.business_item == "정밀부품"
    assert anchor.exact_text == text
    assert anchor.identity_binding == binding


def test_dedup_cap_and_corrupted_source_hash_fail_closed():
    rows = ("당사는 정밀부품을 제조합니다.", "당사는 정밀부품을 판매합니다.",
            "당사는 의료기기를 판매합니다.", "당사는 분석 서비스를 제공합니다.",
            "당사는 온라인 광고 사업을 영위합니다.")
    evidence = _evidence(rows)
    diagnostics = {}
    anchors = build_business_activity_anchors(evidence, profile=PROFILE, diagnostics=diagnostics)
    assert [anchor.business_item for anchor in anchors] == ["정밀부품", "의료기기", "분석 서비스"]
    assert diagnostics["current_business_matches"] == 4
    assert diagnostics["accepted_anchors"] == 3
    corrupt = _evidence((rows[0],))
    object.__setattr__(corrupt.candidates[2].fragments[0], "text", "당사는 다른 상품을 판매합니다.")
    assert build_business_activity_anchors(corrupt, profile=PROFILE, diagnostics=diagnostics) == ()
    assert diagnostics["status"] == "collection_contract_invalid"


@pytest.mark.parametrize("actor,owner,status,accepted", (
    ("가온기업", "가온기업", "", True),
    ("다온제조", "가온기업", "", False),
    ("가온기업", "다온제조", "", False),
    ("가온기업", "가온기업", "양산 적용 예정", False),
))
def test_source_row_actor_and_status_constrain_industry_anchor(actor, owner, status, accepted):
    text = "당사는 정밀부품을 제조합니다."
    evidence = _evidence((text,))
    candidate = evidence.candidates[2]
    row = " | ".join(filter(None, ("정밀부품", actor, text, status)))
    context = json.dumps({
        "version": "source-context-v1", "origin": "table_row", "text": row,
        "location": f"0-{len(row)}", "text_sha256": _sha(row),
        "actor": actor, "document_actor": owner, "status": status,
        "document_actor_location": f"0-{len(owner)}", "document_actor_sha256": _sha(owner),
        "item": "정밀부품",
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fragment = replace(candidate.fragments[0], source_context_json=context)
    evidence = replace(evidence, candidates=tuple(
        replace(current, fragments=(fragment,)) if current is candidate else current
        for current in evidence.candidates
    ))
    anchors = build_business_activity_anchors(evidence, profile=PROFILE)
    assert len(anchors) == int(accepted)
    if accepted:
        assert anchors[0].exact_text == text
        assert anchors[0].text_sha256 == _sha(text)


@pytest.mark.parametrize("actor,accepted", (("(주)가온기업의", True), ("(주)다온제조의", False)))
def test_heading_possessive_keeps_company_identity_without_altering_original(actor, accepted):
    text = "당사는 정밀부품을 제조합니다."
    evidence = _evidence((text,))
    candidate = evidence.candidates[2]
    heading = "[부품부문]\n(1) 영업개황\n" + actor + " 매출은 공시에 기재되어 있습니다."
    owner = "가온기업"
    context = json.dumps({
        "version": "source-context-v1", "origin": "company_heading", "text": heading,
        "location": f"0-{len(heading)}", "text_sha256": _sha(heading),
        "actor": actor, "document_actor": owner, "status": "",
        "document_actor_location": f"0-{len(owner)}", "document_actor_sha256": _sha(owner),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fragment = replace(candidate.fragments[0], source_context_json=context)
    evidence = replace(evidence, candidates=tuple(
        replace(current, fragments=(fragment,)) if current is candidate else current
        for current in evidence.candidates
    ))
    anchors = build_business_activity_anchors(evidence, profile=PROFILE)
    assert len(anchors) == int(accepted)
    assert fragment.source_context_json == context
