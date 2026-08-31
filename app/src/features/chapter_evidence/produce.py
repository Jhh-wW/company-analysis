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


def produce_from_collection_envelopes(
    *,
    company_id: str,
    company_type: CompanyType | str,
    collection_envelopes: Iterable[Mapping[str, object]],
) -> tuple[ChapterEvidenceCandidates, ...]:
    """회사 소유권이 봉인된 수집 envelope들을 합쳐 아홉 장 후보를 만든다.

    DART·공식 웹 mapper는 모두 최상위 ``company_id``와 그 회사의
    documents/fragments/attempts를 함께 돌려준다. 이 최상위 식별자를 버리고
    내부 배열만 이어 붙이면, 잘못 라우팅된 수집 결과도 중첩 값이 우연히
    맞는 순간 조용히 통과한다. 운영 결합부는 이 함수를 유일한 merge
    경계로 사용하고, 저수준 ``produce_chapter_evidence_candidates``는 이미
    검증된 배열을 장별로 나누는 일만 맡는다.
    """

    clean_company_id = _require_company_id(company_id)
    envelopes = tuple(collection_envelopes)
    if not envelopes:
        raise ValueError("합칠 수집 결과가 하나도 없습니다")

    documents: list[object] = []
    fragments: list[object] = []
    attempts: list[object] = []
    for index, envelope in enumerate(envelopes):
        if not isinstance(envelope, Mapping):
            raise ValueError(f"수집 결과 {index}가 Mapping이 아닙니다")
        envelope_company_id = _require_company_id(
            str(envelope.get("company_id") or "")
        )
        if envelope_company_id != clean_company_id:
            raise ValueError(
                "수집 결과의 최상위 company_id가 대상 회사와 다릅니다: "
                f"index={index} expected={clean_company_id!r} "
                f"actual={envelope_company_id!r}"
            )
        for key, destination in (
            ("documents", documents),
            ("fragments", fragments),
            ("attempts", attempts),
        ):
            values = envelope.get(key)
            if not isinstance(values, (list, tuple)):
                raise ValueError(f"수집 결과 {index}의 {key}가 list/tuple이 아닙니다")
            destination.extend(values)

    return produce_chapter_evidence_candidates(
        company_id=clean_company_id,
        company_type=company_type,
        documents=documents,
        fragments=fragments,
        attempts=attempts,
    )


def produce_chapter_evidence_candidates(
    *,
    company_id: str,
    company_type: CompanyType | str,
    documents: Iterable[CollectedEvidenceDocument | Mapping[str, object]],
    fragments: Iterable[EvidenceFragment | Mapping[str, object]],
    attempts: Iterable[CollectionAttempt | Mapping[str, object]],
) -> tuple[ChapterEvidenceCandidates, ...]:
    """회사 한 곳의 수집 결과에서 아홉 장 근거 후보를 정책 순서로 만든다.

    혼합 회사 방어 정책(quiet-filter, API 전체에 일관 적용): 이 API는 자격
    없는 문서·조각·시도를 만나면 **예외로 전체를 거절하지 않고 조용히
    걸러낸 뒤 전용 사유 코드를 남긴다.** 수집기 한 건의 실수(엉뚱한
    company_id·해시·document_id 충돌)가 그 회사의 아홉 장 생산 전체를
    죽이면 안 되기 때문이다(가용성 우선). 반대로 «명시 거절»(전체
    ValueError)을 골랐다면 이런 실수 하나로 회사 전체가 실패했을 것이다 —
    이 API 안에서는 세 입력(documents·fragments·attempts) 모두 같은
    quiet-filter 정책을 쓴다:

    - documents: ``company_id``가 다르면 여기서 바로 걸러진다(조용히,
      사유 코드 없음 — 문서는 애초에 이 API 최상단에서 회사별로 나뉘어
      들어오는 것이 정상 경로라 이례적인 일로 보지 않는다).
    - fragments: select.py가 두 겹으로 확인한다 — 1층 ``company_id``
      결속(다르면 ``fragment_company_mismatch:N``), 2층
      ``exact_evidence_hashes`` 결속(다르면
      ``fragment_not_bound_to_document:N``). ``document_id``가 다른
      회사와 우연히 겹쳐도(수집기 버그) 이 두 겹이 남의 원문이 섞이는
      것을 막는다.
    - attempts: 이 함수가 장별로 ``company_id``를 확인해 걸러낸다(다르면
      ``attempt_company_mismatch:N``). 안 걸러내면 남의 회사가 REQUIRED로
      정상 확인(OK/MISSING)했다는 기록이 이 회사의 «미확인»(UNKNOWN)을
      «정상 확인 후 부재»(INSUFFICIENT)로 위장시킬 수 있다 —
      diagnose.py는 attempt의 company_id를 스스로 확인하지 않는다.

    최종적으로 shared 계약(``ChapterEvidenceCandidates``)에는 대상
    회사 값만 전달되며, 계약 자체도 이를 다시 한번 검증한다(방어 심층화).

    Args:
        company_id: 대상 회사 식별자.
        company_type: ``"listed"``·``"audit_only"``·``"financial"``·
            ``"undecided"`` 중 하나(또는 같은 값의 ``CompanyType``). 슬롯
            요구 자체는 바꾸지 않고, 그 슬롯을 정상 확인하는 조회 경로
            기대값만 바꾼다. ``"undecided"``는 기대 경로를 «모름»으로 다뤄
            진단이 계약과 완전히 같은 판정만 내도록 한다.
        documents: 계약 ``CollectedEvidenceDocument`` 인스턴스이거나 같은
            필드 이름의 매핑 시퀀스.
        fragments: 계약 ``EvidenceFragment`` 인스턴스이거나 같은 필드 이름의
            매핑 시퀀스. ``company_id``가 필수다.
        attempts: 계약 ``CollectionAttempt`` 인스턴스이거나 같은 필드 이름의
            매핑 시퀀스. ``company_id``가 필수다.

    Returns:
        ``REQUIRED_EVIDENCE_SECTION_IDS`` 순서로 정렬된 9개
        ``ChapterEvidenceCandidates``.

    Raises:
        ValueError: 회사 식별자·회사 유형이 비었거나, 문서·조각·시도 입력이
            계약 형식과 맞지 않을 때(예: company_id 누락). 원문이나 비밀은
            담기지 않는다. 다른 회사 값이 섞인 것 자체는 예외가 아니라
            위에서 설명한 quiet-filter로 처리된다.
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
        #
        # generation=8 결속 방어(fail-closed, quiet-filter) — 다른 회사의
        # attempt는 여기서 먼저 걸러낸다. 안 걸러내면 두 가지로 위험하다.
        # ① ChapterEvidenceCandidates가 attempts의 company_id 결속을 검증해
        #    예외를 던져 이 회사 전체 생산이 죽는다. ② 설령 죽지 않더라도,
        #    남의 회사가 REQUIRED로 정상 확인(OK/MISSING)했다는 기록이 이
        #    회사의 «미확인»(UNKNOWN)을 «정상 확인 후 부재»(INSUFFICIENT)로
        #    위장시킬 수 있다 — 진단(diagnose.py)은 attempt의 출처만 보고
        #    company_id는 스스로 확인하지 않기 때문이다. select.py의 조각
        #    결속 확인과 같은 정책(조용히 필터링 + 전용 사유 코드)을 쓴다.
        section_required_slots = set(required_slots_for(section_id))
        section_attempts: list[CollectionAttempt] = []
        foreign_attempt_count = 0
        for attempt in normalized_attempts:
            if not set(attempt.slot_ids) & section_required_slots:
                continue
            if attempt.company_id != clean_company_id:
                foreign_attempt_count += 1
                continue
            section_attempts.append(attempt)

        readiness, diagnosis_reasons = diagnose_candidate_readiness(
            section_id=section_id,
            company_type=resolved_company_type,
            filled_slot_ids=filled_slot_ids,
            attempts=tuple(section_attempts),
        )

        binding_reasons = (
            (f"attempt_company_mismatch:{foreign_attempt_count}",)
            if foreign_attempt_count
            else ()
        )

        candidates.append(
            ChapterEvidenceCandidates(
                company_id=clean_company_id,
                section_id=section_id,
                documents=section_documents,
                fragments=selection.fragments,
                attempts=tuple(section_attempts),
                candidate_readiness=readiness,
                reason_codes=(
                    *selection.reason_codes,
                    *diagnosis_reasons,
                    *binding_reasons,
                ),
                estimated_tokens=selection.estimated_tokens,
                max_chars=DEFAULT_MAX_CHARS_PER_SECTION,
                max_estimated_tokens=DEFAULT_MAX_ESTIMATED_TOKENS_PER_SECTION,
            )
        )

    return tuple(candidates)
