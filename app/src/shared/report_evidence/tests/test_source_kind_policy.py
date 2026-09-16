"""실제 공식 수집 생산자와 닫힌 source_kind 정본의 완전성 검사."""

from __future__ import annotations

import ast
from pathlib import Path

from src.features.chapter_evidence.constants import (
    REQUIRED_SOURCE_KINDS_BY_COMPANY_TYPE,
)
from src.features.homepage.constants import (
    WIDE_SOURCE_KIND_IR_PDF,
    WIDE_SOURCE_KIND_IDENTITY_VERIFIED_WEB_PAGE,
    WIDE_SOURCE_KIND_RECRUIT_PAGE,
    WIDE_SOURCE_KIND_WEB_PAGE,
)
from src.shared.official_ir import (
    IR_METADATA_VERIFICATION_VALUE,
    IR_METADATA_VERIFICATION_VALUE_COVER,
)
from src.shared.report_evidence.constants import (
    FORMAL_ATTEMPT_SOURCE_KINDS,
    FORMAL_DOCUMENT_SOURCE_KINDS,
    OFFICIAL_WEB_SOURCE_KINDS,
    SOURCE_KIND_DART_AUDIT_REPORT,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT,
    SOURCE_KIND_DART_QUARTERLY_REPORT,
    SOURCE_KIND_DART_SEMIANNUAL_REPORT,
    SOURCE_KIND_NEWS,
    SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE,
    SOURCE_KIND_ROBOTS_TXT,
    SUPPLEMENTARY_DOCUMENT_SOURCE_KINDS,
    SourceRequirement,
    SourceTier,
)
from src.shared.report_evidence.models import _REASON_CODE as REASON_CODE_PATTERN
from src.shared.report_evidence.source_kind_policy import (
    FORMAL_ATTEMPT_SLOT_IDS_BY_SOURCE_KIND,
    FORMAL_WRITER_HONEST_REQUIREMENT_DOWNGRADE_SOURCE_KINDS,
    FORMAL_DOCUMENT_SLOT_IDS_BY_SOURCE_KIND,
    FORMAL_DOCUMENT_TRUST_BY_SOURCE_KIND,
    FORMAL_DOCUMENT_WRITER_TRUST_BY_SOURCE_KIND,
    SUPPLEMENTARY_SLOT_IDS_BY_SOURCE_KIND,
    SUPPLEMENTARY_TRUST_BY_SOURCE_KIND,
    SUPPLEMENTARY_WRITER_TRUST,
    formal_source_writer_ineligibility_reason,
)


_DART_SOURCE_CONSTANT_NAMES = {
    "SOURCE_KIND_BUSINESS_REPORT",
    "SOURCE_KIND_AUDIT_REPORT",
    "SOURCE_KIND_CONSOLIDATED_AUDIT_REPORT",
    "SOURCE_KIND_SEMIANNUAL_REPORT",
    "SOURCE_KIND_QUARTERLY_REPORT",
}


def _engine_constants_path() -> Path:
    return (
        Path(__file__).resolve().parents[5]
        / "analysis_engine"
        / "src"
        / "features"
        / "evidence_collection"
        / "constants.py"
    )


def _engine_dart_source_kinds(path: Path) -> frozenset[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        if node.target.id not in _DART_SOURCE_CONSTANT_NAMES:
            continue
        if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
            raise AssertionError(f"{node.target.id}는 문자열 리터럴이어야 합니다")
        values[node.target.id] = node.value.value
    assert set(values) == _DART_SOURCE_CONSTANT_NAMES
    return frozenset(values.values())


def test_analysis_engine_DART종류가_앱정본과_정확히같다() -> None:
    expected = frozenset(
        {
            SOURCE_KIND_DART_BUSINESS_REPORT,
            SOURCE_KIND_DART_AUDIT_REPORT,
            SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT,
            SOURCE_KIND_DART_SEMIANNUAL_REPORT,
            SOURCE_KIND_DART_QUARTERLY_REPORT,
        }
    )

    assert _engine_dart_source_kinds(_engine_constants_path()) == expected


def test_공식웹생산종류가_앱정본과_정확히같다() -> None:
    assert frozenset(
        {
            WIDE_SOURCE_KIND_WEB_PAGE,
            WIDE_SOURCE_KIND_RECRUIT_PAGE,
            WIDE_SOURCE_KIND_IR_PDF,
            WIDE_SOURCE_KIND_IDENTITY_VERIFIED_WEB_PAGE,
        }
    ) == OFFICIAL_WEB_SOURCE_KINDS


def test_닫힌종류목록과_슬롯소유권표가_빠짐없이_같다() -> None:
    assert frozenset(FORMAL_DOCUMENT_SLOT_IDS_BY_SOURCE_KIND) == (
        FORMAL_DOCUMENT_SOURCE_KINDS
    )
    assert frozenset(FORMAL_ATTEMPT_SLOT_IDS_BY_SOURCE_KIND) == (
        FORMAL_ATTEMPT_SOURCE_KINDS
    )
    assert frozenset(FORMAL_DOCUMENT_TRUST_BY_SOURCE_KIND) == (
        FORMAL_DOCUMENT_SOURCE_KINDS
    )
    assert frozenset(FORMAL_DOCUMENT_WRITER_TRUST_BY_SOURCE_KIND) == (
        FORMAL_DOCUMENT_SOURCE_KINDS
    )
    assert FORMAL_ATTEMPT_SOURCE_KINDS - FORMAL_DOCUMENT_SOURCE_KINDS == {
        SOURCE_KIND_ROBOTS_TXT
    }


def test_보조종류표_세개는_formal과_겹치지_않고_뉴스한종류로_닫힌다() -> None:
    assert SUPPLEMENTARY_DOCUMENT_SOURCE_KINDS == {SOURCE_KIND_NEWS}
    assert frozenset(SUPPLEMENTARY_SLOT_IDS_BY_SOURCE_KIND) == (
        SUPPLEMENTARY_DOCUMENT_SOURCE_KINDS
    )
    assert frozenset(SUPPLEMENTARY_TRUST_BY_SOURCE_KIND) == (
        SUPPLEMENTARY_DOCUMENT_SOURCE_KINDS
    )
    assert frozenset(SUPPLEMENTARY_WRITER_TRUST) == (
        SUPPLEMENTARY_DOCUMENT_SOURCE_KINDS
    )
    assert not FORMAL_DOCUMENT_SOURCE_KINDS & SUPPLEMENTARY_DOCUMENT_SOURCE_KINDS


def test_뉴스보조슬롯은_산문만_허용하고_정체성_수치_구장전체를_제외한다() -> None:
    slots = SUPPLEMENTARY_SLOT_IDS_BY_SOURCE_KIND[SOURCE_KIND_NEWS]

    assert {
        "identity:business_definition",
        "portfolio:product_role",
        "past_changes:completed_execution",
        "operations_partners:partnership",
        "culture:leadership",
    } <= slots
    assert {
        "identity:corporate_identity",
        "business_model:regional_mix",
        "past_changes:historical_performance",
        # 9장은 회사가 밝힌 차별점만 싣는 장이라 산문 칸까지 통째로 닫는다.
        "competitive_position:self_context",
        "competitive_position:stated_differentiator",
        "competitive_position:limitation",
        "competitive_position:comparison_target",
        "competitive_position:comparison_metric",
        "competitive_position:comparison_basis",
        "competitive_position:comparison_judgment",
    }.isdisjoint(slots)
    # 「비교 칸만 뺐다」로 되돌아가지 않게 9장 접두 칸이 하나도 없음을 잠근다.
    assert not any(slot.startswith("competitive_position:") for slot in slots)
    assert SUPPLEMENTARY_TRUST_BY_SOURCE_KIND[SOURCE_KIND_NEWS] == frozenset(
        {(SourceTier.TIER_SUPPLEMENTARY, SourceRequirement.OPTIONAL)}
    )
    assert SUPPLEMENTARY_WRITER_TRUST[SOURCE_KIND_NEWS] == (
        SourceTier.TIER_SUPPLEMENTARY,
        SourceRequirement.OPTIONAL,
    )


def test_연결감사보고서는_감사보고서와_같은_슬롯과_등급의_OPTIONAL문서다() -> None:
    assert FORMAL_DOCUMENT_SLOT_IDS_BY_SOURCE_KIND[
        SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT
    ] == FORMAL_DOCUMENT_SLOT_IDS_BY_SOURCE_KIND[SOURCE_KIND_DART_AUDIT_REPORT]
    assert FORMAL_DOCUMENT_TRUST_BY_SOURCE_KIND[
        SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT
    ] == frozenset(
        {(SourceTier.TIER_1_OFFICIAL, SourceRequirement.OPTIONAL)}
    )
    assert FORMAL_DOCUMENT_WRITER_TRUST_BY_SOURCE_KIND[
        SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT
    ] == (SourceTier.TIER_1_OFFICIAL, SourceRequirement.OPTIONAL)


def test_연결감사보고서는_회사유형별_REQUIRED종류에_들어가지_않는다() -> None:
    assert all(
        SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT not in source_kinds
        for by_section in REQUIRED_SOURCE_KINDS_BY_COMPANY_TYPE.values()
        for source_kinds in by_section.values()
    )


def test_신원검증웹은_완전한_DARTproof만_TIER1이_될수_있는_두조합이다() -> None:
    assert FORMAL_DOCUMENT_TRUST_BY_SOURCE_KIND[
        SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE
    ] == frozenset(
        {
            (SourceTier.TIER_1_OFFICIAL, SourceRequirement.REQUIRED),
            (SourceTier.TIER_3_TRUSTED, SourceRequirement.OPTIONAL),
        }
    )


def _official_ir_writer_reason(
    *, published_on: str, reporting_period: str, verification: str
) -> str:
    pdf_url = "https://company.example/ir/report.pdf"
    return formal_source_writer_ineligibility_reason(
        source_kind=WIDE_SOURCE_KIND_IR_PDF,
        source_tier=SourceTier.TIER_1_OFFICIAL,
        requirement=SourceRequirement.REQUIRED,
        canonical_url=pdf_url,
        publisher="company.example",
        published_on=published_on,
        collected_at="2026-09-05",
        identity_binding="DART 기업개황과 같은 공식 host",
        reporting_period=reporting_period,
        attachment_url=pdf_url,
        ir_metadata_verification=verification,
    )


def test_IR_writer관문은_옛anchor와_새표지_표식을_모두_허용한다() -> None:
    for verification in (
        IR_METADATA_VERIFICATION_VALUE,
        IR_METADATA_VERIFICATION_VALUE_COVER,
    ):
        assert not _official_ir_writer_reason(
            published_on="2026-03-12",
            reporting_period="2025-Q4",
            verification=verification,
        )


def test_IR_writer관문은_표지메타도_기존시간상한으로_거른다() -> None:
    invalid_dates = (
        ("2025-12-30", "2025-FY"),
        ("2026-07-10", "2025-FY"),
        ("2025-07-31", "2025-Q2"),
    )
    for published_on, reporting_period in invalid_dates:
        assert _official_ir_writer_reason(
            published_on=published_on,
            reporting_period=reporting_period,
            verification=IR_METADATA_VERIFICATION_VALUE_COVER,
        ) == "official_ir_writer_metadata_incomplete"


def _dart_writer_reason(
    *,
    source_kind: str,
    source_tier: SourceTier,
    requirement: SourceRequirement,
) -> str:
    receipt = "20250311000123"
    return formal_source_writer_ineligibility_reason(
        source_kind=source_kind,
        source_tier=source_tier,
        requirement=requirement,
        canonical_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
        publisher="금융감독원 전자공시시스템(DART)",
        published_on="2025-03-11",
        collected_at="2026-09-16T22:12:00+09:00",
        identity_binding=(
            f"corp_code=00000000;rcept_no={receipt};"
            f"source_kind={source_kind};identity_check=verified_match"
        ),
    )


def test_직전사업연도_연차공시는_필수여부를_낮춰말해도_Writer자격을_지킨다() -> None:
    """수집 엔진이 직전 사업연도 연차 공시를 OPTIONAL로 내보내는 것을 인정한다.

    ``analysis_engine``의 공시 선택기는 직전 사업연도 문서를
    ``REQUIREMENT_OPTIONAL``로 등록한다 — 「그 공시가 없어도 수집 실패가
    아니다」는 뜻이지 「신뢰가 낮다」가 아니다. 등급(TIER_1)과 발행처는 당기
    문서와 완전히 같다.
    """

    for source_kind in (
        SOURCE_KIND_DART_BUSINESS_REPORT,
        SOURCE_KIND_DART_AUDIT_REPORT,
    ):
        assert not _dart_writer_reason(
            source_kind=source_kind,
            source_tier=SourceTier.TIER_1_OFFICIAL,
            requirement=SourceRequirement.OPTIONAL,
        )
        assert not _dart_writer_reason(
            source_kind=source_kind,
            source_tier=SourceTier.TIER_1_OFFICIAL,
            requirement=SourceRequirement.REQUIRED,
        )


def test_필수여부_하향을_인정해도_등급_하향은_인정하지_않는다() -> None:
    """음성 대조 — 완화는 필수 여부 한 칸뿐이고 신뢰 등급은 그대로 정확히 본다."""

    for requirement in (SourceRequirement.REQUIRED, SourceRequirement.OPTIONAL):
        assert _dart_writer_reason(
            source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
            source_tier=SourceTier.TIER_3_TRUSTED,
            requirement=requirement,
        ) == "formal_writer_trust_not_eligible"


def test_OPTIONAL종류가_REQUIRED라고_올려말하면_Writer자격을_잃는다() -> None:
    """음성 대조 — 낮춰 말하는 것만 정직하다. 올려 말하면 필수 자료 수를 위조한다."""

    for source_kind in (
        SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT,
        SOURCE_KIND_DART_SEMIANNUAL_REPORT,
        SOURCE_KIND_DART_QUARTERLY_REPORT,
    ):
        assert _dart_writer_reason(
            source_kind=source_kind,
            source_tier=SourceTier.TIER_1_OFFICIAL,
            requirement=SourceRequirement.REQUIRED,
        ) == "formal_writer_trust_not_eligible"


def test_공식웹과_IR은_필수여부_하향을_인정하지_않는다() -> None:
    """음성 대조 — 웹·IR은 낮은 신뢰를 TIER_3+OPTIONAL «쌍»으로 표시한다.

    이 종류들에서 필수 여부만 낮춘 문서는 생산자가 만들 수 없는 조합이다.
    여기까지 열어 주면 「등급은 TIER_1인데 필수는 아니다」라고 주장하는
    외부 첨부가 Writer 입력이 될 수 있다.
    """

    for source_kind in (
        WIDE_SOURCE_KIND_WEB_PAGE,
        WIDE_SOURCE_KIND_RECRUIT_PAGE,
        WIDE_SOURCE_KIND_IDENTITY_VERIFIED_WEB_PAGE,
    ):
        assert formal_source_writer_ineligibility_reason(
            source_kind=source_kind,
            source_tier=SourceTier.TIER_1_OFFICIAL,
            requirement=SourceRequirement.OPTIONAL,
            canonical_url="https://company.example/about",
            publisher="company.example",
            published_on="",
            collected_at="2026-09-16T22:12:00+09:00",
            identity_binding="DART 기업개황과 같은 공식 host",
        ) == "formal_writer_trust_not_eligible"
    assert _official_ir_writer_reason_with_requirement(
        SourceRequirement.OPTIONAL
    ) == "formal_writer_trust_not_eligible"


def _official_ir_writer_reason_with_requirement(
    requirement: SourceRequirement,
) -> str:
    pdf_url = "https://company.example/ir/report.pdf"
    return formal_source_writer_ineligibility_reason(
        source_kind=WIDE_SOURCE_KIND_IR_PDF,
        source_tier=SourceTier.TIER_1_OFFICIAL,
        requirement=requirement,
        canonical_url=pdf_url,
        publisher="company.example",
        published_on="2026-03-12",
        collected_at="2026-09-05",
        identity_binding="DART 기업개황과 같은 공식 host",
        reporting_period="2025-Q4",
        attachment_url=pdf_url,
        ir_metadata_verification=IR_METADATA_VERIFICATION_VALUE,
    )


def test_필수여부_하향을_인정하는_종류는_생산자계약도_같은_조합을_허용한다() -> None:
    """두 표가 갈라지면 Writer가 받아들인 문서를 생산자 계약이 거절해 회사 전체가 죽는다.

    ``validate_formal_candidate_sources``는 Writer 자격을 통과한 문서에
    ``_validate_document_trust``를 다시 돌린다. 그래서 Writer가 인정하는
    (등급, 필수 여부)는 생산자가 만들 수 있는 조합 안에 있어야 한다.
    """

    for source_kind in FORMAL_DOCUMENT_SOURCE_KINDS:
        writer_tier, writer_requirement = (
            FORMAL_DOCUMENT_WRITER_TRUST_BY_SOURCE_KIND[source_kind]
        )
        producible = FORMAL_DOCUMENT_TRUST_BY_SOURCE_KIND[source_kind]
        assert (writer_tier, writer_requirement) in producible, source_kind

    for source_kind in FORMAL_WRITER_HONEST_REQUIREMENT_DOWNGRADE_SOURCE_KINDS:
        writer_tier, _writer_requirement = (
            FORMAL_DOCUMENT_WRITER_TRUST_BY_SOURCE_KIND[source_kind]
        )
        assert (
            writer_tier,
            SourceRequirement.OPTIONAL,
        ) in FORMAL_DOCUMENT_TRUST_BY_SOURCE_KIND[source_kind], source_kind


def test_필수여부_하향_허용목록은_연차공시_두_종류로_닫힌다() -> None:
    """목록이 조용히 넓어지면 완화가 정책 전체로 번진다."""

    assert FORMAL_WRITER_HONEST_REQUIREMENT_DOWNGRADE_SOURCE_KINDS == frozenset(
        {SOURCE_KIND_DART_BUSINESS_REPORT, SOURCE_KIND_DART_AUDIT_REPORT}
    )


def _writer_ineligibility_reason_names() -> frozenset[str]:
    """Writer 판정 함수가 실제로 돌려주는 닫힌 사유 이름을 소스에서 읽는다.

    손으로 적은 목록은 새 사유가 생겨도 따라 늘지 않는다. 사유 이름이 길어지면
    장 선택이 만드는 사유 코드가 계약의 120자 상한을 넘어 그 회사 생산 전체가
    예외로 죽으므로, 목록 자체를 구현에서 가져와 잰다.
    """

    source = (
        Path(__file__).resolve().parents[1] / "source_kind_policy.py"
    ).read_text(encoding="utf-8")
    function = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef)
        and node.name == "formal_source_writer_ineligibility_reason"
    )
    return frozenset(
        node.value.value
        for node in ast.walk(function)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        and node.value.value
    )


def test_닫힌사유를_덧붙인_장선택_사유코드가_계약형식을_넘지_않는다() -> None:
    """장 선택은 「무시한 개수」 뒤에 이 사유 이름을 붙여 다음 실행 진단에 남긴다."""

    reason_names = _writer_ineligibility_reason_names()
    assert len(reason_names) >= 5
    for prefix in (
        "low_trust_ir_fragment_ignored",
        "low_trust_external_page_fragment_ignored",
    ):
        for reason in reason_names:
            code = f"{prefix}:9999:{reason}"
            assert REASON_CODE_PATTERN.fullmatch(code) is not None, code
