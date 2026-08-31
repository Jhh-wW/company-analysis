"""공개 API — 회사 한 곳의 수집 결과를 아홉 장 근거 후보로 바꾼다."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from src.features.chapter_evidence.constants import (
    CompanyType,
    DEFAULT_MAX_CHARS_PER_SECTION,
    DEFAULT_MAX_ESTIMATED_TOKENS_PER_SECTION,
)
from src.features.chapter_evidence.diagnose import diagnose_candidate_readiness
from src.features.chapter_evidence.normalize import (
    normalize_attempts,
    normalize_company_type,
    normalize_documents,
    normalize_fragments,
)
from src.features.chapter_evidence.select import select_section_fragments
from src.shared.report_evidence.models import (
    ChapterEvidenceCandidates,
    CollectedEvidenceDocument,
    CollectionAttempt,
    EvidenceFragment,
)
from src.shared.report_evidence.policy import (
    REQUIRED_EVIDENCE_SECTION_IDS,
    required_slots_for,
)


def _require_company_id(value: str) -> str:
    clean = str(value).strip()
    if not clean:
        raise ValueError("회사 식별자는 비워 둘 수 없습니다")
    return clean


def produce_chapter_evidence_candidates(
    *,
    company_id: str,
    company_type: CompanyType | str,
    documents: Iterable[CollectedEvidenceDocument | Mapping[str, object]],
    fragments: Iterable[EvidenceFragment | Mapping[str, object]],
    attempts: Iterable[CollectionAttempt | Mapping[str, object]],
) -> tuple[ChapterEvidenceCandidates, ...]:
    """회사 한 곳의 수집 결과에서 아홉 장 근거 후보를 정책 순서로 만든다.

    Args:
        company_id: 대상 회사 식별자. 문서는 ``company_id``로 직접 걸러진다.
            조각(fragment)은 그 자체로 회사 식별자를 갖지 않는다 —
            ``document_id``로 자신이 속한 문서를 찾고, 그 문서가 실제로
            내보내는 ``exact_evidence_hashes`` 허용 목록에 자기
            ``text_sha256``이 있어야만(select.py의 결속 확인) 후보에 남는다.
            ``document_id``가 다른 회사와 우연히 겹쳐도(수집기 버그) 이
            결속 확인이 남의 원문이 조용히 섞이는 것을 막는 방어선이다 —
            다만 서로 다른 회사에 같은 ``document_id``를 발급하지 않는 것이
            수집기 쪽의 원칙이고, 이 결속 확인은 그 원칙이 깨졌을 때만 작동한다.
        company_type: ``"listed"``·``"audit_only"``·``"financial"``·
            ``"undecided"`` 중 하나(또는 같은 값의 ``CompanyType``). 슬롯
            요구 자체는 바꾸지 않고, 그 슬롯을 정상 확인하는 조회 경로
            기대값만 바꾼다. ``"undecided"``는 기대 경로를 «모름»으로 다뤄
            진단이 계약과 완전히 같은 판정만 내도록 한다.
        documents: 계약 ``CollectedEvidenceDocument`` 인스턴스이거나 같은
            필드 이름의 매핑 시퀀스.
        fragments: 계약 ``EvidenceFragment`` 인스턴스이거나 같은 필드 이름의
            매핑 시퀀스.
        attempts: 계약 ``CollectionAttempt`` 인스턴스이거나 같은 필드 이름의
            매핑 시퀀스.

    Returns:
        ``REQUIRED_EVIDENCE_SECTION_IDS`` 순서로 정렬된 9개
        ``ChapterEvidenceCandidates``.

    Raises:
        ValueError: 회사 식별자·회사 유형이 비었거나, 문서·조각·시도 입력이
            계약 형식과 맞지 않을 때. 원문이나 비밀은 담기지 않는다.
    """

    clean_company_id = _require_company_id(company_id)
    resolved_company_type = normalize_company_type(company_type)

    normalized_documents = normalize_documents(documents)
    normalized_fragments = normalize_fragments(fragments)
    normalized_attempts = normalize_attempts(attempts)

    own_documents = tuple(
        document
        for document in normalized_documents
        if document.company_id == clean_company_id
    )

    candidates: list[ChapterEvidenceCandidates] = []
    for section_id in REQUIRED_EVIDENCE_SECTION_IDS:
        selection = select_section_fragments(
            section_id=section_id,
            company_id=clean_company_id,
            documents=own_documents,
            fragments=normalized_fragments,
            max_chars=DEFAULT_MAX_CHARS_PER_SECTION,
            max_estimated_tokens=DEFAULT_MAX_ESTIMATED_TOKENS_PER_SECTION,
        )
        used_document_ids = {
            fragment.document_id for fragment in selection.fragments
        }
        section_documents = tuple(
            document
            for document in own_documents
            if document.document_id in used_document_ids
        )
        filled_slot_ids = frozenset(
            fragment.slot_id for fragment in selection.fragments
        )

        # 이 장의 필수 슬롯(수집 슬롯 + 주입 슬롯) 전체와 조회 대상이 겹치는
        # 시도만 넘긴다 — 최종 게이트가 주입 슬롯의 required_path_unobserved
        # 여부도 판단할 수 있게 하기 위함이다(생산부는 주입 슬롯을 채우지
        # 않지만, 그 슬롯을 «조회»한 기록까지 숨길 이유는 없다).
        section_required_slots = set(required_slots_for(section_id))
        section_attempts = tuple(
            attempt
            for attempt in normalized_attempts
            if set(attempt.slot_ids) & section_required_slots
        )

        readiness, diagnosis_reasons = diagnose_candidate_readiness(
            section_id=section_id,
            company_type=resolved_company_type,
            filled_slot_ids=filled_slot_ids,
            attempts=section_attempts,
        )

        candidates.append(
            ChapterEvidenceCandidates(
                company_id=clean_company_id,
                section_id=section_id,
                documents=section_documents,
                fragments=selection.fragments,
                attempts=section_attempts,
                candidate_readiness=readiness,
                reason_codes=(*selection.reason_codes, *diagnosis_reasons),
                estimated_tokens=selection.estimated_tokens,
                max_chars=DEFAULT_MAX_CHARS_PER_SECTION,
                max_estimated_tokens=DEFAULT_MAX_ESTIMATED_TOKENS_PER_SECTION,
            )
        )

    return tuple(candidates)
