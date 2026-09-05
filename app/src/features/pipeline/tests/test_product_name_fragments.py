import json
from pathlib import Path

from src.features.composer.constants import SECTION_IDS
from src.features.composer.logic import compose_sections
from src.features.composer.port import filing_meta_from_raw
from src.features.pipeline import real
from src.features.product_names.logic import collect_name_candidates
from src.shared.report_evidence.constants import SOURCE_KIND_DART_BUSINESS_REPORT
from src.shared.report_evidence.legacy_fragment_kinds import LEGACY_FRAGMENT_KINDS
from src.shared.report_generation.models import exact_text_sha256


FIXTURES = (
    Path(__file__).resolve().parents[2]
    / "product_names"
    / "tests"
    / "fixtures"
)
CORP_ID = "00126380"
RCEPT_NO = "20260315000123"
GENERATION_SHA256 = "b" * 64


def _typed_anchor() -> dict[str, object]:
    source_url = (
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + RCEPT_NO
    )
    return {
        "종류": SOURCE_KIND_DART_BUSINESS_REPORT,
        "원문": "공시에서 이미 검증된 원문 조각이다.",
        "출처": source_url,
        "문서ID": f"{SOURCE_KIND_DART_BUSINESS_REPORT}:{RCEPT_NO}",
        "문서명": "사업보고서 (2025.12)",
        "문서일": "2026-03-15",
        "원문위치": "사업의 내용",
        "company_id": CORP_ID,
        "_evidence_section_ids": ("identity",),
        "_evidence_slot_ids": ("identity:legal_entity",),
        "_evidence_origin_fragment_ids": ("dart:anchor",),
        "_evidence_document_identity": f"document:dart.fss.or.kr:{RCEPT_NO}",
        "_evidence_document_content_sha256": "a" * 64,
        "_evidence_identity_binding": "fixture_identity_binding",
        "_evidence_publisher": "금융감독원 전자공시시스템",
        "_evidence_collected_on": "2026-03-15",
        "_evidence_domain_attestation_source_id": "",
        "_evidence_domain_attestation_evidence": "",
        "_evidence_reporting_period": "",
        "_evidence_attachment_url": "",
        "_evidence_ir_metadata_verification": "",
        "_evidence_domain_redirect_verification": "",
        "_evidence_domain_redirect_from_host": "",
        "_evidence_domain_redirect_to_host": "",
    }


def _legacy_fragments() -> dict[int, dict[str, object]]:
    return {
        number: {
            "종류": kind,
            "원문": f"테스트 회사의 {kind} 공식 근거다.",
        }
        for number, kind in enumerate(sorted(LEGACY_FRAGMENT_KINDS), start=1)
    }


def _filing() -> dict[str, str]:
    return {
        "rcept_no": RCEPT_NO,
        "report_nm": "사업보고서 (2025.12)",
        "rcept_dt": "20260315",
    }


def _kakao_text() -> str:
    return (FIXTURES / "kakao_product_services.txt").read_text(encoding="utf-8")


def test_이름조각은_packet과_가짜작가의_3장카드를_통과한다() -> None:
    before = _legacy_fragments()
    steps: list[dict[str, object]] = []
    frags, added = real._attach_name_candidate_fragments(  # noqa: SLF001
        before,
        filing_text=_kakao_text(),
        filing_meta=_filing(),
        corp_id=CORP_ID,
        typed_fragments=(_typed_anchor(),),
        steps=steps,
    )

    assert added > 0
    assert set(before) < set(frags)
    assert min(set(frags) - set(before)) > max(before)
    name_number = next(
        number
        for number, raw in frags.items()
        if raw.get("_evidence_slot_ids") == ("portfolio:product_role",)
        and "카카오톡" in str(raw.get("원문") or "")
    )
    packets = real._full_section_evidence_packets(  # noqa: SLF001
        corp_id=CORP_ID,
        source_identity_digest=GENERATION_SHA256,
        frags=frags,
        filing_meta=filing_meta_from_raw(_filing()),
    )
    portfolio_packet = next(
        packet for packet in packets.packets if packet.section_id == "portfolio"
    )
    name_fragment = next(
        fragment
        for fragment in portfolio_packet.fragments
        if fragment.fragment_id == str(name_number)
    )
    assert name_fragment.supported_claim_slots == ("portfolio:product_role",)
    candidate = next(
        item
        for item in collect_name_candidates(
            _kakao_text(), source_kind=SOURCE_KIND_DART_BUSINESS_REPORT
        )
        if item.name == "카카오톡"
    )
    assert name_fragment.text == candidate.excerpt
    assert exact_text_sha256(name_fragment.text) == candidate.excerpt_sha256

    calls = 0

    def fake_writer(_prompt: str) -> str:
        nonlocal calls
        section_id = SECTION_IDS[calls]
        calls += 1
        flow_rows = []
        if section_id == "portfolio":
            flow_rows = [
                {
                    "칸": ["카카오톡", "", "", ""],
                    "인용": [str(name_number)],
                }
            ]
        return json.dumps(
            {"문장들": [], "경로표": flow_rows}, ensure_ascii=False
        )

    composed = compose_sections(
        "테스트 회사",
        {},
        None,
        fake_writer,
        section_evidence_packets=packets,
    )
    portfolio = next(
        section for section in composed.sections if section.section_id == "portfolio"
    )

    assert calls == len(SECTION_IDS)
    assert len(portfolio.flow_rows) == 1
    assert portfolio.flow_rows[0].cells[0] == "카카오톡"
    assert portfolio.flow_rows[0].citations == (str(name_number),)


def test_typed신원이_없어도_후보수와_단계는_남고_조각은_없다() -> None:
    before = _legacy_fragments()
    steps: list[dict[str, object]] = []

    frags, added = real._attach_name_candidate_fragments(  # noqa: SLF001
        before,
        filing_text=_kakao_text(),
        filing_meta=_filing(),
        corp_id=CORP_ID,
        typed_fragments=(),
        steps=steps,
    )

    assert added == 0
    assert frags == before
    assert steps == [
        {
            "step": "7_이름후보",
            "후보": 8,
            "조각": 0,
            "종류별": {"segment": 2, "product": 6},
            "상한적용": False,
        }
    ]
