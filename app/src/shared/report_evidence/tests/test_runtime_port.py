from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import replace

import pytest

from src.shared.report_evidence.constants import (
    CollectionState,
    EvidenceReadiness,
    SOURCE_KIND_OFFICIAL_IR_PDF,
    SourceRequirement,
    SourceTier,
)
from src.shared.report_evidence.models import (
    ChapterEvidenceCandidates,
    CollectedEvidenceDocument,
    CollectionAttempt,
    DocumentTextRange,
    EvidenceFragment,
)
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.report_evidence.runtime_port import (
    OfficialEvidenceCollectionRequest,
    OfficialEvidenceCollectionResult,
    OfficialProvenanceDocument,
    UnclassifiedEvidenceObservation,
)


COMPANY_ID = "00126380"
DOCUMENT_ID = "dart:00126380:20260331000123"
DOCUMENT_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260331000123"
FIRST_TEXT = "예시전자는 산업용 센서를 개발하고 판매하는 법인입니다."
SECOND_TEXT = "예시전자는 제조사 고객에게 센서 판매 대가를 받습니다."


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _provenance_document(**overrides: object) -> OfficialProvenanceDocument:
    fields = dict(
        company_id=COMPANY_ID,
        document_id="external-ir-1",
        canonical_url="https://cdn.example.com/ir.pdf",
        source_tier=SourceTier.TIER_3_TRUSTED,
        source_kind=SOURCE_KIND_OFFICIAL_IR_PDF,
        publisher="example.com",
        title="IR 자료",
        published_on="",
        collected_at="2026-09-04",
        content_sha256="c" * 64,
        identity_binding="official exact-link attachment",
        collector_version="wide-v1",
        parser_version="wide-parser-v1",
        requirement=SourceRequirement.OPTIONAL,
        exclusion_reason="writer_ineligible:official_ir_writer_metadata_incomplete",
        attachment_url="https://cdn.example.com/ir.pdf",
    )
    fields.update(overrides)
    return OfficialProvenanceDocument(**fields)


def _document(
    *,
    text_hashes: tuple[str, ...],
    content_sha256: str | None = None,
    document_id: str = DOCUMENT_ID,
    canonical_url: str = DOCUMENT_URL,
) -> CollectedEvidenceDocument:
    return CollectedEvidenceDocument(
        company_id=COMPANY_ID,
        document_id=document_id,
        canonical_url=canonical_url,
        source_tier=SourceTier.TIER_1_OFFICIAL,
        source_kind="dart_business_report",
        publisher="금융감독원 전자공시",
        title="사업보고서",
        published_on="2026-03-31",
        collected_at="2026-09-04",
        content_sha256=content_sha256 or _sha256(FIRST_TEXT + SECOND_TEXT),
        exact_evidence_hashes=text_hashes,
        identity_binding="corp_code_and_receipt_verified",
        usable_ranges=(DocumentTextRange(0, len(FIRST_TEXT + SECOND_TEXT)),),
        collector_version="typed-dart-v1",
        parser_version="typed-dart-parser-v1",
        requirement=SourceRequirement.REQUIRED,
    )


def _fragment(
    *,
    fragment_id: str,
    text: str,
    slot_id: str,
    document_id: str = DOCUMENT_ID,
    canonical_url: str = DOCUMENT_URL,
) -> EvidenceFragment:
    return EvidenceFragment(
        company_id=COMPANY_ID,
        fragment_id=fragment_id,
        document_id=document_id,
        location=f"{canonical_url}#본문",
        text_sha256=_sha256(text),
        text=text,
        section_id="identity",
        slot_id=slot_id,
        covered_slot_ids=(slot_id,),
        score_millis=900,
        reason_codes=("official_direct_statement",),
    )


def _candidates(
    *,
    first_text: str = FIRST_TEXT,
    content_sha256: str | None = None,
    document_id: str = DOCUMENT_ID,
    canonical_url: str = DOCUMENT_URL,
    attempt_reason: str = "dart_document_ok",
    attempt_state: CollectionState = CollectionState.OK,
    reverse_identity_items: bool = False,
    company_id: str = COMPANY_ID,
) -> tuple[ChapterEvidenceCandidates, ...]:
    first = _fragment(
        fragment_id="identity-corporate",
        text=first_text,
        slot_id="identity:corporate_identity",
        document_id=document_id,
        canonical_url=canonical_url,
    )
    second = _fragment(
        fragment_id="identity-business",
        text=SECOND_TEXT,
        slot_id="identity:business_definition",
        document_id=document_id,
        canonical_url=canonical_url,
    )
    fragments = (first, second)
    hashes = (first.text_sha256, second.text_sha256)
    slots = ("identity:corporate_identity", "identity:business_definition")
    if reverse_identity_items:
        fragments = tuple(reversed(fragments))
        hashes = tuple(reversed(hashes))
        slots = tuple(reversed(slots))
    document = _document(
        text_hashes=hashes,
        content_sha256=content_sha256,
        document_id=document_id,
        canonical_url=canonical_url,
    )
    attempt = CollectionAttempt(
        company_id=COMPANY_ID,
        attempt_id="dart-business-report",
        source_kind="dart_business_report",
        requirement=SourceRequirement.REQUIRED,
        state=attempt_state,
        slot_ids=slots,
        reason_code=attempt_reason,
        documents_seen=1,
    )

    candidates: list[ChapterEvidenceCandidates] = []
    for section_id in REQUIRED_EVIDENCE_SECTION_IDS:
        identity = section_id == "identity"
        candidates.append(
            ChapterEvidenceCandidates(
                company_id=company_id,
                section_id=section_id,
                documents=(document,) if identity else (),
                fragments=fragments if identity else (),
                attempts=(attempt,) if identity else (),
                candidate_readiness=(
                    EvidenceReadiness.READY
                    if identity
                    else EvidenceReadiness.UNKNOWN
                ),
                reason_codes=() if identity else ("required_path_unobserved",),
                estimated_tokens=30 if identity else 0,
                max_chars=10_000,
                max_estimated_tokens=2_500,
            )
        )
    return tuple(candidates)


def test_요청은_기존_DART자원과_공식회사정보를_정규화한다(tmp_path) -> None:
    counter = object()

    def get_json(_path, _params, _counter):
        return {}

    def download(_receipt, _directory, _counter):
        return tmp_path / "document.xml"

    request = OfficialEvidenceCollectionRequest(
        company_id=f" {COMPANY_ID} ",
        company_name=" 예시전자 ",
        company_aliases=(" EXAMPLE ", "EXAMPLE", ""),
        root_homepage_url=" https://example.com/about ",
        company_registration_numbers=(" 123-45-67890 ", "1234567890"),
        official_candidate_urls=(" https://ir.example.com/ ", "https://ir.example.com/", ""),
        as_of_date=dt.date(2026, 9, 4),
        dart_document_cache_dir=tmp_path,
        dart_counter=counter,
        dart_get_json=get_json,
        dart_download_document=download,
    )

    assert request.company_id == COMPANY_ID
    assert request.company_name == "예시전자"
    assert request.company_aliases == ("EXAMPLE",)
    assert request.root_homepage_url == "https://example.com/about"
    assert request.company_registration_numbers == ("1234567890",)
    assert request.official_candidate_urls == ("https://ir.example.com/",)
    assert request.collected_at == "2026-09-04"
    assert request.dart_counter is counter


def test_공식등록번호는_글자를_지워_우연히_10자리로_만들지_않는다(tmp_path) -> None:
    with pytest.raises(ValueError, match="공식 등록번호"):
        OfficialEvidenceCollectionRequest(
            company_id=COMPANY_ID,
            company_name="예시전자",
            company_aliases=(),
            root_homepage_url="",
            company_registration_numbers=("corp123-45-67890",),
            official_candidate_urls=(),
            as_of_date=dt.date(2026, 9, 4),
            dart_document_cache_dir=tmp_path,
            dart_counter=object(),
            dart_get_json=lambda *_args: {},
            dart_download_document=lambda *_args: tmp_path / "document.xml",
        )


def test_결과는_정책순서_아홉장을_강제한다() -> None:
    with pytest.raises(ValueError, match="정책 순서"):
        OfficialEvidenceCollectionResult(
            company_id=COMPANY_ID,
            candidates=tuple(reversed(_candidates())),
        )


def test_결과는_다른회사_후보를_거절한다() -> None:
    with pytest.raises(ValueError, match="다른 회사"):
        OfficialEvidenceCollectionResult(
            company_id="other-company",
            candidates=_candidates(),
        )


def test_결과는_등록되지않은_공식문서종류를_거절한다() -> None:
    candidates = list(_candidates())
    identity = candidates[0]
    candidates[0] = replace(
        identity,
        documents=(replace(identity.documents[0], source_kind="official_shop"),),
    )

    with pytest.raises(ValueError, match="등록되지 않은 공식 문서"):
        OfficialEvidenceCollectionResult(COMPANY_ID, tuple(candidates))


def test_채용페이지가_문화밖_의미칸을_주장하면_거절한다() -> None:
    candidates = list(_candidates())
    identity = candidates[0]
    candidates[0] = replace(
        identity,
        documents=(
            replace(identity.documents[0], source_kind="official_recruit_page"),
        ),
    )

    with pytest.raises(ValueError, match="소유하지 않은 의미 칸"):
        OfficialEvidenceCollectionResult(COMPANY_ID, tuple(candidates))


def test_외부IR을_공식host_TIER1으로_승격하면_거절한다() -> None:
    candidates = list(_candidates())
    identity = candidates[0]
    candidates[0] = replace(
        identity,
        documents=(
            replace(
                identity.documents[0],
                source_kind=SOURCE_KIND_OFFICIAL_IR_PDF,
                canonical_url="https://cdn.example.com/ir.pdf",
                publisher="ir.company.example",
            ),
        ),
    )

    with pytest.raises(ValueError, match="외부 IR 첨부"):
        OfficialEvidenceCollectionResult(COMPANY_ID, tuple(candidates))


def test_TIER3_외부IR은_필수의미칸_조각으로_승격할수없다() -> None:
    candidates = list(_candidates())
    identity = candidates[0]
    candidates[0] = replace(
        identity,
        documents=(
            replace(
                identity.documents[0],
                source_kind=SOURCE_KIND_OFFICIAL_IR_PDF,
                canonical_url="https://cdn.example.com/ir.pdf",
                publisher="ir.company.example",
                source_tier=SourceTier.TIER_3_TRUSTED,
                requirement=SourceRequirement.OPTIONAL,
            ),
        ),
    )

    with pytest.raises(ValueError, match="필수 의미 칸 조각"):
        OfficialEvidenceCollectionResult(COMPANY_ID, tuple(candidates))


def test_결과는_접두어만_닮은_공식조회종류를_거절한다() -> None:
    candidates = list(_candidates())
    identity = candidates[0]
    candidates[0] = replace(
        identity,
        attempts=(replace(identity.attempts[0], source_kind="dart_typo"),),
    )

    with pytest.raises(ValueError, match="등록되지 않은 공식 조회"):
        OfficialEvidenceCollectionResult(COMPANY_ID, tuple(candidates))


def test_같은문서의_서로다른조각은_독립문서_한건이다() -> None:
    result = OfficialEvidenceCollectionResult(
        company_id=COMPANY_ID,
        candidates=_candidates(),
    )

    assert result.independent_document_count == 1
    assert len(result.source_snapshot_sha256) == 64
    assert set(result.source_snapshot_sha256) <= set("0123456789abcdef")


def test_provenance_only문서는_generation지문과_독립문서수에_영향없다() -> None:
    baseline = OfficialEvidenceCollectionResult(COMPANY_ID, _candidates())
    observed = OfficialEvidenceCollectionResult(
        COMPANY_ID,
        _candidates(),
        provenance_documents=(_provenance_document(),),
    )

    assert observed.source_snapshot_sha256 == baseline.source_snapshot_sha256
    assert observed.independent_document_count == baseline.independent_document_count
    assert observed.provenance_snapshot_sha256 != baseline.provenance_snapshot_sha256


def test_provenance_only_exact메타변경은_감사지문만_바꾼다() -> None:
    first = OfficialEvidenceCollectionResult(
        COMPANY_ID,
        _candidates(),
        provenance_documents=(_provenance_document(),),
    )
    second = OfficialEvidenceCollectionResult(
        COMPANY_ID,
        _candidates(),
        provenance_documents=(
            _provenance_document(title="정정 IR 자료", content_sha256="d" * 64),
        ),
    )

    assert second.source_snapshot_sha256 == first.source_snapshot_sha256
    assert second.independent_document_count == first.independent_document_count
    assert second.provenance_snapshot_sha256 != first.provenance_snapshot_sha256


def test_같은원문을_URL과_ID만바꿔복제해도_독립문서가_늘지않는다() -> None:
    candidates = list(_candidates())
    identity = candidates[0]
    original_document = identity.documents[0]
    cloned_document_id = "official_web:cloned-copy"
    cloned_url = "https://example.com/cloned-copy"
    cloned_document = replace(
        original_document,
        document_id=cloned_document_id,
        canonical_url=cloned_url,
    )
    cloned_fragment = replace(
        identity.fragments[0],
        fragment_id="identity-corporate-cloned-copy",
        document_id=cloned_document_id,
        location=f"{cloned_url}#본문",
    )
    candidates[0] = replace(
        identity,
        documents=(*identity.documents, cloned_document),
        fragments=(*identity.fragments, cloned_fragment),
    )

    result = OfficialEvidenceCollectionResult(
        company_id=COMPANY_ID,
        candidates=tuple(candidates),
    )

    assert result.independent_document_count == 1


def test_snapshot은_입력나열순서와_무관하게_결정론적이다() -> None:
    normal = OfficialEvidenceCollectionResult(COMPANY_ID, _candidates())
    reversed_items = OfficialEvidenceCollectionResult(
        COMPANY_ID,
        _candidates(reverse_identity_items=True),
    )

    assert reversed_items.source_snapshot_sha256 == normal.source_snapshot_sha256
    assert reversed_items.independent_document_count == normal.independent_document_count


@pytest.mark.parametrize(
    "changed_part",
    [
        "document_content",
        "document_url",
        "document_id",
        "fragment",
        "attempt_reason",
        "attempt_state",
    ],
)
def test_문서_조각_시도_상태가_바뀌면_snapshot도_바뀐다(changed_part: str) -> None:
    baseline = OfficialEvidenceCollectionResult(COMPANY_ID, _candidates())
    if changed_part == "document_content":
        changed_candidates = _candidates(content_sha256="b" * 64)
    elif changed_part == "document_url":
        changed_candidates = _candidates(canonical_url=DOCUMENT_URL + "&view=full")
    elif changed_part == "document_id":
        changed_candidates = _candidates(document_id=DOCUMENT_ID + ":revision")
    elif changed_part == "fragment":
        changed_candidates = _candidates(first_text=FIRST_TEXT + " 변경")
    elif changed_part == "attempt_reason":
        changed_candidates = _candidates(attempt_reason="dart_document_missing")
    else:
        changed_candidates = _candidates(attempt_state=CollectionState.TRUNCATED)

    changed = OfficialEvidenceCollectionResult(COMPANY_ID, changed_candidates)

    assert changed.source_snapshot_sha256 != baseline.source_snapshot_sha256


def test_snapshot은_원문_payload를_별도필드로_노출하지않는다() -> None:
    result = OfficialEvidenceCollectionResult(COMPANY_ID, _candidates())

    assert not hasattr(result, "source_snapshot_payload")
    assert FIRST_TEXT not in result.source_snapshot_sha256


def test_무분류관측은_원문없이_snapshot을_바꾸고_회사에_결속된다() -> None:
    baseline = OfficialEvidenceCollectionResult(COMPANY_ID, _candidates())
    observation = UnclassifiedEvidenceObservation(
        company_id=COMPANY_ID,
        document_count=1,
        fragment_count=2,
        observation_sha256="a" * 64,
    )

    observed = OfficialEvidenceCollectionResult(
        COMPANY_ID,
        _candidates(),
        unclassified_evidence=observation,
    )

    assert observed.source_snapshot_sha256 != baseline.source_snapshot_sha256
    assert observed.unclassified_evidence is observation
    assert not hasattr(observation, "text")
    assert not hasattr(observation, "documents")
    assert FIRST_TEXT not in repr(observation)

    with pytest.raises(ValueError, match="다른 회사"):
        OfficialEvidenceCollectionResult(
            COMPANY_ID,
            _candidates(),
            unclassified_evidence=replace(observation, company_id="other-company"),
        )


@pytest.mark.parametrize(
    ("document_count", "fragment_count", "digest"),
    [(0, 1, "a" * 64), (1, 0, "a" * 64), (1, 1, "A" * 64)],
)
def test_무분류관측의_개수와_지문은_닫힌형식이다(
    document_count: int,
    fragment_count: int,
    digest: str,
) -> None:
    with pytest.raises(ValueError):
        UnclassifiedEvidenceObservation(
            company_id=COMPANY_ID,
            document_count=document_count,
            fragment_count=fragment_count,
            observation_sha256=digest,
        )


def test_선택_게이트_공개출처에_영향주는_메타가_바뀌면_snapshot도_바뀐다() -> None:
    baseline_candidates = _candidates()
    baseline = OfficialEvidenceCollectionResult(COMPANY_ID, baseline_candidates)
    identity = baseline_candidates[0]
    document = identity.documents[0]
    fragment = identity.fragments[0]
    attempt = identity.attempts[0]

    changed_documents = (
        replace(document, publisher="다른 발행자"),
        replace(document, title="정정 사업보고서"),
        replace(document, published_on="2026-04-01"),
        replace(document, identity_binding="corp_code_and_receipt_reverified"),
        replace(document, collector_version="typed-dart-v2"),
        replace(document, parser_version="typed-dart-parser-v2"),
    )
    changed_fragments = (
        replace(fragment, fragment_id="identity-corporate-v2"),
        replace(fragment, location=DOCUMENT_URL + "#다른위치"),
        replace(fragment, score_millis=899),
        replace(fragment, reason_codes=("official_direct_statement_v2",)),
        replace(fragment, period_start="2026-01-01"),
        replace(fragment, unit="원"),
        replace(fragment, company_scope="연결"),
    )
    changed_attempts = (
        replace(attempt, attempt_id="dart-business-report-v2"),
        replace(attempt, requirement=SourceRequirement.OPTIONAL),
    )

    for changed_document in changed_documents:
        candidates = list(baseline_candidates)
        candidates[0] = replace(identity, documents=(changed_document,))
        changed = OfficialEvidenceCollectionResult(COMPANY_ID, tuple(candidates))
        assert changed.source_snapshot_sha256 != baseline.source_snapshot_sha256

    for changed_fragment in changed_fragments:
        candidates = list(baseline_candidates)
        candidates[0] = replace(identity, fragments=(changed_fragment, identity.fragments[1]))
        changed = OfficialEvidenceCollectionResult(COMPANY_ID, tuple(candidates))
        assert changed.source_snapshot_sha256 != baseline.source_snapshot_sha256

    for changed_attempt in changed_attempts:
        candidates = list(baseline_candidates)
        candidates[0] = replace(identity, attempts=(changed_attempt,))
        changed = OfficialEvidenceCollectionResult(COMPANY_ID, tuple(candidates))
        assert changed.source_snapshot_sha256 != baseline.source_snapshot_sha256


def test_수집시각과_관측계수만_바뀌면_snapshot은_같다() -> None:
    baseline_candidates = _candidates()
    baseline = OfficialEvidenceCollectionResult(COMPANY_ID, baseline_candidates)
    candidates = list(baseline_candidates)
    identity = candidates[0]
    candidates[0] = replace(
        identity,
        documents=(replace(identity.documents[0], collected_at="2026-09-05"),),
        attempts=(
            replace(
                identity.attempts[0],
                elapsed_ms=999,
                bytes_downloaded=12345,
                documents_seen=7,
            ),
        ),
    )

    changed = OfficialEvidenceCollectionResult(COMPANY_ID, tuple(candidates))

    assert changed.source_snapshot_sha256 == baseline.source_snapshot_sha256


def test_장별_판정과_선택예산이_바뀌면_snapshot도_바뀐다() -> None:
    baseline_candidates = _candidates()
    baseline = OfficialEvidenceCollectionResult(COMPANY_ID, baseline_candidates)
    identity = baseline_candidates[0]
    changed_candidates = (
        replace(
            identity,
            candidate_readiness=EvidenceReadiness.UNKNOWN,
            reason_codes=("producer_readiness_changed",),
            estimated_tokens=identity.estimated_tokens + 1,
            max_chars=identity.max_chars + 1,
            max_estimated_tokens=identity.max_estimated_tokens + 1,
        ),
        *baseline_candidates[1:],
    )

    changed = OfficialEvidenceCollectionResult(COMPANY_ID, changed_candidates)

    assert changed.source_snapshot_sha256 != baseline.source_snapshot_sha256
