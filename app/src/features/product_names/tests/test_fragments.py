from pathlib import Path

from src.features.product_names.constants import MAX_NAME_FRAGMENTS_PER_FILING
from src.features.product_names.fragments import (
    NAME_FRAGMENT_SECTION_ID,
    NAME_FRAGMENT_SLOT_ID,
    formal_source_kind_for_filing,
    name_candidate_fragments,
)
from src.features.product_names.logic import collect_name_candidates
from src.features.product_names.models import NameCandidate
from src.shared.report_evidence.constants import (
    SOURCE_KIND_DART_AUDIT_REPORT,
    SOURCE_KIND_DART_BUSINESS_REPORT,
)
from src.shared.report_generation.models import exact_text_sha256


FIXTURES = Path(__file__).parent / "fixtures"
CORP_ID = "00126380"
RCEPT_NO = "20260315000123"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _typed_anchor(
    *,
    receipt_no: str = RCEPT_NO,
    source_kind: str = SOURCE_KIND_DART_BUSINESS_REPORT,
) -> dict[str, object]:
    source_url = (
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + receipt_no
    )
    return {
        "종류": source_kind,
        "원문": "공시에서 이미 검증된 원문 조각이다.",
        "출처": source_url,
        "문서ID": f"{source_kind}:{receipt_no}",
        "문서명": "사업보고서 (2025.12)",
        "문서일": "2026-03-15",
        "원문위치": "사업의 내용",
        "company_id": CORP_ID,
        "_evidence_section_ids": ("identity",),
        "_evidence_slot_ids": ("identity:legal_entity",),
        "_evidence_origin_fragment_ids": ("dart:anchor",),
        "_evidence_document_identity": (
            f"document:dart.fss.or.kr:{receipt_no}"
        ),
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


def _filing() -> dict[str, str]:
    return {
        "rcept_no": RCEPT_NO,
        "report_nm": "사업보고서 (2025.12)",
        "rcept_dt": "20260315",
    }


def test_카카오_이름후보는_원문해시와_3장슬롯을_그대로_가진다() -> None:
    anchor = _typed_anchor()
    source_kind = formal_source_kind_for_filing(
        filing_meta=_filing(), corp_id=CORP_ID, typed_fragments=(anchor,)
    )
    candidates = collect_name_candidates(
        _fixture("kakao_product_services.txt"), source_kind=source_kind
    )

    made = name_candidate_fragments(
        candidates,
        filing_meta=_filing(),
        corp_id=CORP_ID,
        typed_fragments=(anchor,),
    )

    assert made
    assert len(made) == min(len(candidates), MAX_NAME_FRAGMENTS_PER_FILING)
    for candidate, raw in zip(candidates, made, strict=True):
        assert raw["원문"] == candidate.excerpt
        assert exact_text_sha256(str(raw["원문"])) == candidate.excerpt_sha256
        assert raw["_evidence_section_ids"] == (NAME_FRAGMENT_SECTION_ID,)
        assert raw["_evidence_slot_ids"] == (NAME_FRAGMENT_SLOT_ID,)
        assert raw["_evidence_document_identity"] == anchor[
            "_evidence_document_identity"
        ]
        assert raw["_evidence_document_content_sha256"] == anchor[
            "_evidence_document_content_sha256"
        ]
        assert raw["_evidence_identity_binding"] == anchor[
            "_evidence_identity_binding"
        ]
        assert raw["문서ID"] == anchor["문서ID"]


def test_이름조각은_후보순서를_유지하며_열두개에서_자른다() -> None:
    candidates = tuple(
        NameCandidate(
            name=f"제품{i}",
            subject_kind="product",
            description="",
            source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
            location="주요 제품 및 서비스",
            excerpt=f"제품{i} 서비스 설명",
            excerpt_sha256=exact_text_sha256(f"제품{i} 서비스 설명"),
        )
        for i in range(MAX_NAME_FRAGMENTS_PER_FILING + 5)
    )

    made = name_candidate_fragments(
        candidates,
        filing_meta=_filing(),
        corp_id=CORP_ID,
        typed_fragments=(_typed_anchor(),),
    )

    assert len(made) == MAX_NAME_FRAGMENTS_PER_FILING
    assert [raw["원문"] for raw in made] == [
        candidate.excerpt
        for candidate in candidates[:MAX_NAME_FRAGMENTS_PER_FILING]
    ]
    origins = [raw["_evidence_origin_fragment_ids"] for raw in made]
    assert len(origins) == len(set(origins))


def test_같은공시의_typed신원이_없으면_이름조각을_만들지_않는다() -> None:
    candidates = collect_name_candidates(
        _fixture("kakao_product_services.txt"),
        source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
    )

    assert name_candidate_fragments(
        candidates,
        filing_meta=_filing(),
        corp_id=CORP_ID,
        typed_fragments=(_typed_anchor(receipt_no="20250315000999"),),
    ) == []


def test_인이지_감사보고서의_주요계약도_이름조각이_된다() -> None:
    candidates = collect_name_candidates(
        _fixture("ineeji_contracts.txt"),
        source_kind=SOURCE_KIND_DART_AUDIT_REPORT,
    )
    contract = next(item for item in candidates if item.subject_kind == "contract")

    made = name_candidate_fragments(
        (contract,),
        filing_meta=_filing(),
        corp_id=CORP_ID,
        typed_fragments=(
            _typed_anchor(source_kind=SOURCE_KIND_DART_AUDIT_REPORT),
        ),
    )

    assert len(made) == 1
    assert made[0]["원문"] == contract.excerpt
    assert str(made[0]["원문위치"]).endswith("주요 계약")
