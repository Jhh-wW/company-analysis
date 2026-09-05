"""legacy·typed 근거가 한 계약으로 아홉 장에 전달되는지 검증한다."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json

import pytest

from src.features.composer.constants import (
    DART_FINANCIAL_API_HOST,
    SECTION_IDS,
)
from src.features.composer.port import filing_meta_from_raw
from src.features.pipeline.evidence_transport import (
    RAW_EVIDENCE_COMPANY_ID_KEY,
    RAW_EVIDENCE_ATTACHMENT_URL_KEY,
    RAW_EVIDENCE_COLLECTED_ON_KEY,
    RAW_EVIDENCE_DOCUMENT_CONTENT_SHA256_KEY,
    RAW_EVIDENCE_DOCUMENT_IDENTITY_KEY,
    RAW_EVIDENCE_IDENTITY_BINDING_KEY,
    RAW_EVIDENCE_DOMAIN_ATTESTATION_EVIDENCE_KEY,
    RAW_EVIDENCE_DOMAIN_ATTESTATION_SOURCE_ID_KEY,
    RAW_EVIDENCE_DOMAIN_REDIRECT_FROM_HOST_KEY,
    RAW_EVIDENCE_DOMAIN_REDIRECT_TO_HOST_KEY,
    RAW_EVIDENCE_DOMAIN_REDIRECT_VERIFICATION_KEY,
    RAW_EVIDENCE_IR_METADATA_VERIFICATION_KEY,
    RAW_EVIDENCE_ORIGIN_FRAGMENT_IDS_KEY,
    RAW_EVIDENCE_PUBLISHER_KEY,
    RAW_EVIDENCE_REPORTING_PERIOD_KEY,
    RAW_EVIDENCE_SECTION_IDS_KEY,
    RAW_EVIDENCE_SLOT_IDS_KEY,
    TYPED_TRANSPORT_KIND_PREFIX,
    EvidenceTransportError,
    build_section_evidence_packet_set,
)
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID,
    FINAL_GATE_DETAIL_PREFLIGHT_UNREGISTERED_FRAGMENT_KIND,
)
from src.shared.report_evidence.legacy_fragment_kinds import (
    LEGACY_FRAGMENT_KINDS,
    LEGACY_KIND_HOMEPAGE,
    LEGACY_KIND_NEW_BUSINESS_OUTLOOK,
    LEGACY_KIND_OFFICIAL_IR,
    LEGACY_KIND_RESEARCH_AND_DEVELOPMENT,
    LEGACY_KIND_REVENUE_AND_ORDERS,
    sections_for_legacy_fragment_kind,
)
from src.shared.report_evidence.constants import (
    FORMAL_DOCUMENT_SOURCE_KINDS,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT,
    SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE,
    SOURCE_KIND_OFFICIAL_IR_PDF,
    SOURCE_KIND_OFFICIAL_RECRUIT_PAGE,
)
from src.shared.report_evidence.identity_verified_web import (
    build_dart_filing_url_provenance,
    build_verified_dart_filing_official_web_binding,
)
from src.shared.report_evidence.profile_domain_attestation import (
    build_registered_subdomain_profile_attestation,
)
from src.shared.report_evidence.source_kind_policy import (
    document_slots_for_formal_source_kind,
)
from src.shared.report_quality.source_identity import (
    collected_document_identity,
    document_identity_from_parts,
)


_CORP_ID = "00126380"
_GENERATION = "a" * 64
_RCEPT_NO = "20260315000123"
_PROFILE_EVIDENCE = json.dumps(
    {"corp_code": _CORP_ID, "corp_name": "가나다전자", "hm_url": "https://example.com"},
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
)
_FILING_META = filing_meta_from_raw(
    {
        "rcept_no": _RCEPT_NO,
        "report_nm": "사업보고서 (2025.12)",
        "rcept_dt": "20260315",
    }
)


def _all_legacy_frags() -> dict[int, dict[str, object]]:
    return {
        number: {
            "종류": kind,
            "원문": f"가나다전자의 {kind} 공식 근거다.",
        }
        for number, kind in enumerate(sorted(LEGACY_FRAGMENT_KINDS), start=1)
    }


def _typed_raw(
    *,
    section_id: str = "portfolio",
    slot_id: str = "portfolio:product_role",
    origin_id: str = "dart:20260315000123:frag-opaque",
    company_id: str = _CORP_ID,
    document_identity: str = "url:https://example.com/products",
    source_kind: str = SOURCE_KIND_OFFICIAL_IR_PDF,
    source_url: str = "https://example.com/products",
    document_id: str = "official-document-products",
    document_content_sha256: str = "b" * 64,
) -> dict[str, object]:
    return {
        "종류": source_kind,
        "원문": "가나다전자의 공식 제품 근거다.",
        "출처": source_url,
        "문서ID": document_id,
        "문서일": "2026-09-04",
        RAW_EVIDENCE_COMPANY_ID_KEY: company_id,
        RAW_EVIDENCE_SECTION_IDS_KEY: (section_id,),
        RAW_EVIDENCE_SLOT_IDS_KEY: (slot_id,),
        RAW_EVIDENCE_ORIGIN_FRAGMENT_IDS_KEY: (origin_id,),
        RAW_EVIDENCE_DOCUMENT_IDENTITY_KEY: document_identity,
        RAW_EVIDENCE_DOCUMENT_CONTENT_SHA256_KEY: document_content_sha256,
        RAW_EVIDENCE_IDENTITY_BINDING_KEY: "fixture_identity_binding",
        RAW_EVIDENCE_PUBLISHER_KEY: "example.com",
        RAW_EVIDENCE_COLLECTED_ON_KEY: "2026-09-04",
        RAW_EVIDENCE_DOMAIN_ATTESTATION_SOURCE_ID_KEY: (
            f"dart-company-profile-{_CORP_ID}"
        ),
        RAW_EVIDENCE_DOMAIN_ATTESTATION_EVIDENCE_KEY: _PROFILE_EVIDENCE,
        RAW_EVIDENCE_REPORTING_PERIOD_KEY: "2026-Q2",
        RAW_EVIDENCE_ATTACHMENT_URL_KEY: source_url,
        RAW_EVIDENCE_IR_METADATA_VERIFICATION_KEY: (
            "official_anchor_exact_date_period"
        ),
        RAW_EVIDENCE_DOMAIN_REDIRECT_VERIFICATION_KEY: "",
        RAW_EVIDENCE_DOMAIN_REDIRECT_FROM_HOST_KEY: "",
        RAW_EVIDENCE_DOMAIN_REDIRECT_TO_HOST_KEY: "",
    }


def _without_web_provenance(raw: dict[str, object]) -> dict[str, object]:
    copied = dict(raw)
    for key in (
        RAW_EVIDENCE_DOMAIN_ATTESTATION_SOURCE_ID_KEY,
        RAW_EVIDENCE_DOMAIN_ATTESTATION_EVIDENCE_KEY,
        RAW_EVIDENCE_REPORTING_PERIOD_KEY,
        RAW_EVIDENCE_ATTACHMENT_URL_KEY,
        RAW_EVIDENCE_IR_METADATA_VERIFICATION_KEY,
        RAW_EVIDENCE_DOMAIN_REDIRECT_VERIFICATION_KEY,
        RAW_EVIDENCE_DOMAIN_REDIRECT_FROM_HOST_KEY,
        RAW_EVIDENCE_DOMAIN_REDIRECT_TO_HOST_KEY,
    ):
        copied[key] = ""
    return copied


def _verified_web_binding(url: str) -> str:
    provenance = build_dart_filing_url_provenance(
        company_id=_CORP_ID,
        url=url,
        source_document_id=f"dart_business_report:{_RCEPT_NO}",
        source_receipt_no=_RCEPT_NO,
        source_member_name="covers/homepage.xml",
        source_location="raw_xml_chars:10-40",
        source_document_sha256="d" * 64,
        source_payload_sha256="e" * 64,
    )
    name_hash = hashlib.sha256("가나다전자".encode("utf-8")).hexdigest()
    registration_hash = hashlib.sha256(b"1234567890").hexdigest()
    return build_verified_dart_filing_official_web_binding(
        provenance_value=provenance,
        company_id=_CORP_ID,
        company_name="가나다전자",
        company_registration_numbers=("123-45-67890",),
        candidate_url=url,
        effective_urls=(url,),
        scope_sha256="f" * 64,
        scope_allows=lambda candidate: candidate == url,
        identity_evidence_sha256="a" * 64,
        matched_name_sha256=name_hash,
        registration_number_sha256=registration_hash,
    )


def _typed_raw_for_formal_kind(source_kind: str) -> dict[str, object]:
    slot_id = sorted(document_slots_for_formal_source_kind(source_kind))[0]
    section_id = slot_id.split(":", 1)[0]
    if source_kind.startswith("dart_"):
        source_url = (
            "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + _RCEPT_NO
        )
        document_id = f"{source_kind}:{_RCEPT_NO}"
        raw = _typed_raw(
            section_id=section_id,
            slot_id=slot_id,
            source_kind=source_kind,
            source_url=source_url,
            document_id=document_id,
            document_identity=collected_document_identity(
                source_kind=source_kind,
                document_id=document_id,
                url=source_url,
            ),
        )
        return _without_web_provenance(raw)

    source_url = "https://example.com/"
    raw = _typed_raw(
        section_id=section_id,
        slot_id=slot_id,
        source_kind=source_kind,
        source_url=source_url,
        document_id=f"{source_kind}:example",
        document_identity=collected_document_identity(
            source_kind=source_kind,
            document_id=f"{source_kind}:example",
            url=source_url,
        ),
    )
    if source_kind != SOURCE_KIND_OFFICIAL_IR_PDF:
        for key in (
            RAW_EVIDENCE_REPORTING_PERIOD_KEY,
            RAW_EVIDENCE_ATTACHMENT_URL_KEY,
            RAW_EVIDENCE_IR_METADATA_VERIFICATION_KEY,
        ):
            raw[key] = ""
    if source_kind == SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE:
        raw[RAW_EVIDENCE_IDENTITY_BINDING_KEY] = _verified_web_binding(source_url)
        raw[RAW_EVIDENCE_DOMAIN_ATTESTATION_SOURCE_ID_KEY] = ""
        raw[RAW_EVIDENCE_DOMAIN_ATTESTATION_EVIDENCE_KEY] = ""
    return raw


def _build(frags: Mapping[int, Mapping[str, object]]):
    return build_section_evidence_packet_set(
        corp_id=_CORP_ID,
        source_generation_sha256=_GENERATION,
        frags=frags,
        filing_meta=_FILING_META,
    )


def _sections_containing(packet_set, fragment_id: int) -> frozenset[str]:
    target = str(fragment_id)
    return frozenset(
        packet.section_id
        for packet in packet_set.packets
        if any(fragment.fragment_id == target for fragment in packet.fragments)
    )


def _fragment_by_id(packet_set, fragment_id: int):
    target = str(fragment_id)
    matches = {
        fragment
        for packet in packet_set.packets
        for fragment in packet.fragments
        if fragment.fragment_id == target
    }
    assert len(matches) == 1
    return matches.pop()


def _packet_hash(packet_set, section_id: str) -> str:
    return next(
        packet.packet_sha256
        for packet in packet_set.packets
        if packet.section_id == section_id
    )


def test_legacy_17종은_정본의_정확한_장에만_배정된다() -> None:
    frags = _all_legacy_frags()
    packet_set = _build(frags)

    for fragment_id, raw in frags.items():
        assert _sections_containing(packet_set, fragment_id) == (
            sections_for_legacy_fragment_kind(str(raw["종류"]))
        )


def test_신규사업전망은_정체성과_과거에_새지_않는다() -> None:
    frags = _all_legacy_frags()
    fragment_id = next(
        number
        for number, raw in frags.items()
        if raw["종류"] == LEGACY_KIND_NEW_BUSINESS_OUTLOOK
    )

    assert _sections_containing(_build(frags), fragment_id) == frozenset(
        {"future_strategy"}
    )


def test_실제_9번칸의_매출수주와_연구개발은_운영파트너packet에_남는다() -> None:
    frags = _all_legacy_frags()
    expected_ids = {
        str(number)
        for number, raw in frags.items()
        if raw["종류"]
        in {
            LEGACY_KIND_REVENUE_AND_ORDERS,
            LEGACY_KIND_RESEARCH_AND_DEVELOPMENT,
        }
    }

    operations = next(
        packet
        for packet in _build(frags).packets
        if packet.section_id == "operations_partners"
    )

    operation_ids = {fragment.fragment_id for fragment in operations.fragments}
    assert expected_ids <= operation_ids


def test_typed는_공식IR_legacy_소유표가_아닌_봉인된_장에만_간다() -> None:
    frags = _all_legacy_frags()
    frags[99] = _typed_raw()

    packet_set = _build(frags)

    assert _sections_containing(packet_set, 99) == frozenset({"portfolio"})
    assert _fragment_by_id(packet_set, 99).kind.startswith(
        TYPED_TRANSPORT_KIND_PREFIX
    )


def test_typed_formal_source_kind는_허용하고_같은_legacy_kind는_거절한다() -> None:
    typed_frags = _all_legacy_frags()
    typed_frags[99] = _typed_raw(source_kind=SOURCE_KIND_OFFICIAL_IR_PDF)

    assert _sections_containing(_build(typed_frags), 99) == frozenset({"portfolio"})

    legacy_frags = _all_legacy_frags()
    legacy_frags[99] = {
        "종류": SOURCE_KIND_OFFICIAL_IR_PDF,
        "원문": "공식 공시 원문이다.",
    }
    with pytest.raises(EvidenceTransportError) as caught:
        _build(legacy_frags)
    assert caught.value.detail_code == (
        FINAL_GATE_DETAIL_PREFLIGHT_UNREGISTERED_FRAGMENT_KIND
    )


def test_typed_채용페이지는_제품칸을_근거라고_주장할수없다() -> None:
    frags = _all_legacy_frags()
    frags[99] = _typed_raw(source_kind=SOURCE_KIND_OFFICIAL_RECRUIT_PAGE)

    with pytest.raises(EvidenceTransportError) as caught:
        _build(frags)

    assert caught.value.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID


def test_typed_origin과_slot은_packet_hash에_결속된다() -> None:
    first = _all_legacy_frags()
    first[99] = _typed_raw(origin_id="origin-a")
    changed_origin = _all_legacy_frags()
    changed_origin[99] = _typed_raw(origin_id="origin-b")
    changed_slot = _all_legacy_frags()
    changed_slot[99] = _typed_raw(slot_id="portfolio:revenue_link")

    assert _packet_hash(_build(first), "portfolio") != _packet_hash(
        _build(changed_origin), "portfolio"
    )
    assert _packet_hash(_build(first), "portfolio") != _packet_hash(
        _build(changed_slot), "portfolio"
    )


def test_typed_문서원문hash는_packet까지_손실없이_결속된다() -> None:
    frags = _all_legacy_frags()
    frags[99] = _typed_raw(document_content_sha256="c" * 64)

    fragment = _fragment_by_id(_build(frags), 99)

    assert fragment.document_content_sha256 == "c" * 64


def test_typed_의미칸은_packet까지_손실없이_운반되고_legacy는_추측하지_않는다() -> None:
    frags = _all_legacy_frags()
    frags[99] = _typed_raw(slot_id="portfolio:revenue_link")

    packet_set = _build(frags)

    assert _fragment_by_id(packet_set, 99).supported_claim_slots == (
        "portfolio:revenue_link",
    )
    legacy_id = next(iter(_all_legacy_frags()))
    assert _fragment_by_id(packet_set, legacy_id).supported_claim_slots == ()


def test_출력은_정책순서의_비어있지_않은_아홉_packet이다() -> None:
    packet_set = _build(_all_legacy_frags())

    assert tuple(packet.section_id for packet in packet_set.packets) == SECTION_IDS
    assert len(packet_set.packets) == 9
    assert all(packet.fragments for packet in packet_set.packets)


def test_legacy_DART_재무API_웹_문서신원_해석을_보존한다() -> None:
    frags = _all_legacy_frags()
    ids_by_kind = {str(raw["종류"]): number for number, raw in frags.items()}
    financial_id = ids_by_kind["재무"]
    homepage_id = ids_by_kind[LEGACY_KIND_HOMEPAGE]
    dart_id = ids_by_kind["수익인식"]
    fallback_id = ids_by_kind["MD&A"]
    frags[financial_id]["원문"] = "주요계정(DART API): 매출액 100"
    frags[homepage_id]["출처"] = "https://example.com/about"
    frags[dart_id]["문서ID"] = "20260314000456"

    packet_set = _build(frags)

    assert _fragment_by_id(packet_set, financial_id).document_identity.startswith(
        f"document:{DART_FINANCIAL_API_HOST}:"
    )
    assert _fragment_by_id(packet_set, homepage_id).document_identity == (
        "url:https://example.com/about"
    )
    assert _fragment_by_id(packet_set, dart_id).document_identity == (
        "document:dart.fss.or.kr:20260314000456"
    )
    assert _fragment_by_id(packet_set, fallback_id).document_identity == (
        f"document:dart.fss.or.kr:{_RCEPT_NO}"
    )


def test_typed가_제공한_문서신원을_웹주소보다_우선한다() -> None:
    frags = _all_legacy_frags()
    expected = document_identity_from_parts(url="https://ir.example.com/document.pdf")
    raw = _typed_raw(
        document_identity=expected,
        source_url="https://ir.example.com/document.pdf",
    )
    raw[RAW_EVIDENCE_DOMAIN_ATTESTATION_EVIDENCE_KEY] = json.dumps(
        {
            "corp_code": _CORP_ID,
            "corp_name": "가나다전자",
            "hm_url": "https://ir.example.com",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    frags[99] = raw

    assert _fragment_by_id(_build(frags), 99).document_identity == expected


def test_typed_공식웹_URL이_봉인뒤_바뀌면_packet전에_거절한다() -> None:
    frags = _all_legacy_frags()
    frags[99] = _typed_raw()
    frags[99]["출처"] = "https://attacker.example/products"

    with pytest.raises(EvidenceTransportError) as caught:
        _build(frags)

    assert caught.value.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID


def test_typed_채용하위도메인은_DART_root_자손proof까지_packet에_보존한다() -> None:
    source_url = "https://recruit.example.com/jobs"
    document_id = f"{SOURCE_KIND_OFFICIAL_RECRUIT_PAGE}:jobs"
    raw = _typed_raw(
        section_id="culture",
        slot_id="culture:work_principle",
        source_kind=SOURCE_KIND_OFFICIAL_RECRUIT_PAGE,
        source_url=source_url,
        document_id=document_id,
        document_identity=collected_document_identity(
            source_kind=SOURCE_KIND_OFFICIAL_RECRUIT_PAGE,
            document_id=document_id,
            url=source_url,
        ),
    )
    raw[RAW_EVIDENCE_REPORTING_PERIOD_KEY] = ""
    raw[RAW_EVIDENCE_ATTACHMENT_URL_KEY] = ""
    raw[RAW_EVIDENCE_IR_METADATA_VERIFICATION_KEY] = ""
    proof = build_registered_subdomain_profile_attestation(
        _PROFILE_EVIDENCE,
        source_url=source_url,
    )
    assert proof
    raw[RAW_EVIDENCE_DOMAIN_ATTESTATION_EVIDENCE_KEY] = proof
    frags = _all_legacy_frags()
    frags[99] = raw

    fragment = _fragment_by_id(_build(frags), 99)

    assert fragment.domain_attestation_evidence == proof

    raw["출처"] = "https://jobs.example.com/jobs"
    raw[RAW_EVIDENCE_DOCUMENT_IDENTITY_KEY] = collected_document_identity(
        source_kind=SOURCE_KIND_OFFICIAL_RECRUIT_PAGE,
        document_id=document_id,
        url=raw["출처"],
    )
    with pytest.raises(EvidenceTransportError) as caught:
        _build({**_all_legacy_frags(), 99: raw})
    assert caught.value.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID


@pytest.mark.parametrize("source_kind", sorted(FORMAL_DOCUMENT_SOURCE_KINDS))
def test_formal_문서_전종류가_같은_typed_envelope로_packet까지_왕복한다(
    source_kind: str,
) -> None:
    frags = _all_legacy_frags()
    frags[99] = _typed_raw_for_formal_kind(source_kind)

    fragment = _fragment_by_id(_build(frags), 99)

    assert fragment.formal_source_kind == source_kind
    assert fragment.source_document_id == frags[99]["문서ID"]
    assert fragment.source_publisher == frags[99][RAW_EVIDENCE_PUBLISHER_KEY]
    assert fragment.identity_binding == frags[99][RAW_EVIDENCE_IDENTITY_BINDING_KEY]
    assert fragment.source_collected_on == frags[99][RAW_EVIDENCE_COLLECTED_ON_KEY]
    assert fragment.domain_attestation_source_id == frags[99][
        RAW_EVIDENCE_DOMAIN_ATTESTATION_SOURCE_ID_KEY
    ]
    assert fragment.domain_attestation_evidence == frags[99][
        RAW_EVIDENCE_DOMAIN_ATTESTATION_EVIDENCE_KEY
    ]
    assert fragment.reporting_period == frags[99][RAW_EVIDENCE_REPORTING_PERIOD_KEY]
    assert fragment.attachment_url == frags[99][RAW_EVIDENCE_ATTACHMENT_URL_KEY]
    assert fragment.ir_metadata_verification == frags[99][
        RAW_EVIDENCE_IR_METADATA_VERIFICATION_KEY
    ]


@pytest.mark.parametrize("source_kind", sorted(FORMAL_DOCUMENT_SOURCE_KINDS))
def test_formal_문서_전종류는_다른종류의_provenance를_섞으면_AI전에_거절한다(
    source_kind: str,
) -> None:
    raw = _typed_raw_for_formal_kind(source_kind)
    if source_kind.startswith("dart_"):
        raw[RAW_EVIDENCE_DOMAIN_ATTESTATION_SOURCE_ID_KEY] = "forged-attester"
        raw[RAW_EVIDENCE_DOMAIN_ATTESTATION_EVIDENCE_KEY] = _PROFILE_EVIDENCE
    elif source_kind == SOURCE_KIND_OFFICIAL_IR_PDF:
        raw[RAW_EVIDENCE_IR_METADATA_VERIFICATION_KEY] = "forged"
    else:
        raw[RAW_EVIDENCE_REPORTING_PERIOD_KEY] = "2026-Q2"
    frags = _all_legacy_frags()
    frags[99] = raw

    with pytest.raises(EvidenceTransportError) as caught:
        _build(frags)

    assert caught.value.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID


def test_typed_DART는_접수번호와_URL이_같은_문서일때만_받는다() -> None:
    receipt_number = "20260314000456"
    source_url = (
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + receipt_number
    )
    expected = document_identity_from_parts(
        document_id=receipt_number,
        host="dart.fss.or.kr",
        url=source_url,
    )
    frags = _all_legacy_frags()
    frags[99] = _without_web_provenance(_typed_raw(
        source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
        source_url=source_url,
        document_id=f"{SOURCE_KIND_DART_BUSINESS_REPORT}:{receipt_number}",
        document_identity=expected,
    ))

    assert _fragment_by_id(_build(frags), 99).document_identity == expected

    frags[99]["출처"] = (
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260314000999"
    )
    with pytest.raises(EvidenceTransportError) as caught:
        _build(frags)
    assert caught.value.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID


def test_typed_연결감사보고서는_DART접수번호_문서신원을_유지한다() -> None:
    raw = _typed_raw_for_formal_kind(
        SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT
    )
    frags = _all_legacy_frags()
    frags[99] = raw

    fragment = _fragment_by_id(_build(frags), 99)

    assert fragment.document_identity == f"document:dart.fss.or.kr:{_RCEPT_NO}"
    assert fragment.source_document_id == (
        f"{SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT}:{_RCEPT_NO}"
    )


def test_미등록_kind는_별도_닫힌_사유로_실패한다() -> None:
    frags = _all_legacy_frags()
    frags[99] = {"종류": "처음보는종류", "원문": "내용"}

    with pytest.raises(EvidenceTransportError) as caught:
        _build(frags)

    assert caught.value.detail_code == (
        FINAL_GATE_DETAIL_PREFLIGHT_UNREGISTERED_FRAGMENT_KIND
    )


@pytest.mark.parametrize(
    "broken",
    [
        _typed_raw(company_id="99999999"),
        _typed_raw(document_identity="embedded:forbidden"),
        _typed_raw(document_content_sha256="not-a-sha256"),
        _typed_raw(section_id="unknown", slot_id="portfolio:product_role"),
        _typed_raw(section_id="portfolio", slot_id="future_strategy:stated_plan"),
        {key: value for key, value in _typed_raw().items() if key != RAW_EVIDENCE_SLOT_IDS_KEY},
        {
            key: value
            for key, value in _typed_raw().items()
            if key != RAW_EVIDENCE_DOCUMENT_CONTENT_SHA256_KEY
        },
    ],
)
def test_다른회사_invalid_identity_unknown_section_불완전typed는_실패한다(
    broken: dict[str, object],
) -> None:
    frags = _all_legacy_frags()
    frags[99] = broken

    with pytest.raises(EvidenceTransportError) as caught:
        _build(frags)

    assert caught.value.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID


def test_빈원문과_비숫자_공개번호는_packet_invalid다() -> None:
    blank = _all_legacy_frags()
    blank[1]["원문"] = "  "
    with pytest.raises(EvidenceTransportError) as blank_error:
        _build(blank)
    assert blank_error.value.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID

    invalid_id: dict[object, Mapping[str, object]] = _all_legacy_frags()
    invalid_id["99"] = {"종류": "사업내용", "원문": "내용"}
    with pytest.raises(EvidenceTransportError) as id_error:
        _build(invalid_id)  # type: ignore[arg-type]
    assert id_error.value.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID


def test_같은_origin을_둘의_공개번호로_복제하면_실패한다() -> None:
    frags = _all_legacy_frags()
    frags[98] = _typed_raw(origin_id="same-origin")
    frags[99] = _typed_raw(origin_id="same-origin")

    with pytest.raises(EvidenceTransportError) as caught:
        _build(frags)

    assert caught.value.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID


def test_어느_장이라도_비면_packet_invalid로_실패한다() -> None:
    only_future = {
        1: {
            "종류": LEGACY_KIND_NEW_BUSINESS_OUTLOOK,
            "원문": "가나다전자는 새 사업을 추진할 계획이다.",
        }
    }

    with pytest.raises(EvidenceTransportError) as caught:
        _build(only_future)

    assert caught.value.detail_code == FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID
