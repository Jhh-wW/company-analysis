"""공식 해당 장 근거에 한정한 묶음 작성 한 번과 동일 검수 한 번."""
from __future__ import annotations

import json
import hashlib
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import replace

from src.features.composer.constants import GRADE_CONFIRMED, SECTION_IDS, SECTION_GUIDES
from src.features.composer.culture_constants import SOURCE_CLAUSE_SPLIT_RE
from src.features.composer.culture_guard import _clause_carries_section_subject
from src.features.composer.dedupe import drop_cross_section_duplicates
from src.features.composer.empty_section_recovery_constants import (
    EMPTY_RECOVERY_GUIDE, EMPTY_RECOVERY_STEP, MAX_EMPTY_RECOVERY_EVIDENCE_CHARS,
    MAX_EMPTY_RECOVERY_FRAGMENTS, MAX_EMPTY_RECOVERY_SENTENCES, MAX_EMPTY_RECOVERY_SECTIONS,
)
from src.features.composer.logic import AskFn, extract_json_payload, parse_section_response, _strip_inline_citation_markers
from src.features.composer.port import AskFatalError, CollectedFragment, ComposedReport, ComposedSection
from src.features.composer.verify import verify_report
from src.features.composer.structured_claims import enforce_public_numeric_safety
from src.shared.report_evidence.constants import FORMAL_DOCUMENT_SOURCE_KINDS


def rejected_sentence_fingerprint(text: str) -> str:
    """공백·표기 정규화·인용 표식만 바꾼 재승인을 막는 내부 지문."""
    normalized = unicodedata.normalize("NFKC", _strip_inline_citation_markers(text))
    return hashlib.sha256("".join(normalized.split()).encode("utf-8")).hexdigest()


def recovery_evidence(fragments: Sequence[CollectedFragment]) -> dict[str, tuple[CollectedFragment, ...]]:
    """이미 신원이 결속된 공식 조각의 해당 장 slot만 사용한다."""
    result = {}
    for section_id in SECTION_IDS:
        selected = []
        chars = 0
        for fragment in fragments:
            if (fragment.formal_source_kind not in FORMAL_DOCUMENT_SOURCE_KINDS
                    or not fragment.document_identity or not fragment.document_content_sha256
                    or not fragment.identity_binding or not fragment.text.strip()
                    or not any(slot.startswith(section_id + ":") for slot in fragment.supported_claim_slots)):
                continue
            if section_id == "culture" and not any(
                _clause_carries_section_subject(clause)
                for clause in SOURCE_CLAUSE_SPLIT_RE.split(fragment.text)
            ):
                continue
            if chars + len(fragment.text) > MAX_EMPTY_RECOVERY_EVIDENCE_CHARS:
                continue
            selected.append(fragment)
            chars += len(fragment.text)
            if len(selected) >= MAX_EMPTY_RECOVERY_FRAGMENTS:
                break
        if selected:
            result[section_id] = tuple(selected)
    return result


def recover_empty_sections(
    company_name: str, report: ComposedReport, *, targets: tuple[str, ...],
    evidence: Mapping[str, tuple[CollectedFragment, ...]], writer: AskFn, reviewer: AskFn,
    performance_table=None, baseline_date=None, diagnostics=None, protocol_diagnostics=None,
    comparison_fragments: Sequence[CollectedFragment] = (),
    rejected_fingerprints: Mapping[str, frozenset[str]] | None = None,
) -> ComposedReport:
    """비대상 장은 그대로 두고 확인·verified 문장만 빈 본문에 반영한다."""
    empty_ids = {section.section_id for section in report.sections if not section.sentences}
    targets = tuple(section_id for section_id in SECTION_IDS
                    if section_id in targets and section_id in empty_ids and evidence.get(section_id))
    targets = targets[:MAX_EMPTY_RECOVERY_SECTIONS]
    if not targets:
        return report
    payload = {
        section_id: {
            "작성범위": SECTION_GUIDES[section_id],
            "공식근거": [{"id": f.fragment_id, "원문": f.text} for f in evidence[section_id]],
        }
        for section_id in targets
    }
    raw = extract_json_payload(writer(
        f"분석 회사: {company_name}\n{EMPTY_RECOVERY_GUIDE}" + json.dumps(payload, ensure_ascii=False)
    ))
    by_section = raw.get("장들") if isinstance(raw, Mapping) else None
    if not isinstance(by_section, Mapping) or set(by_section) != set(targets):
        if protocol_diagnostics is not None:
            protocol_diagnostics.append({"step": EMPTY_RECOVERY_STEP, "상태": "작성형식실패", "대상장": list(targets)})
        return report
    sections = []
    for section_id in targets:
        parsed = parse_section_response(json.dumps(by_section[section_id], ensure_ascii=False), section_id,
                                        reject_inline_citation_markers=True)
        allowed = {f.fragment_id for f in evidence[section_id]}
        sentences = tuple(
            sentence for sentence in (parsed or ())
            if sentence.grade == GRADE_CONFIRMED and sentence.citations
            and set(sentence.citations) <= allowed
            and rejected_sentence_fingerprint(sentence.text)
            not in (rejected_fingerprints or {}).get(section_id, frozenset())
        )[:MAX_EMPTY_RECOVERY_SENTENCES]
        sections.append(ComposedSection(section_id, sentences))
    if not any(section.sentences for section in sections):
        if protocol_diagnostics is not None:
            protocol_diagnostics.append({"step": EMPTY_RECOVERY_STEP, "상태": "확인후보없음", "대상장": list(targets)})
        return report
    if protocol_diagnostics is not None:
        protocol_diagnostics.append({"step": EMPTY_RECOVERY_STEP, "상태": "작성완료",
                                     "대상장": list(targets),
                                     "작성문장수": sum(len(section.sentences) for section in sections)})
    called = False

    def review_once(prompt: str) -> str:
        nonlocal called
        if called:
            raise AskFatalError(ValueError("빈 장 복구 검수는 한 번만 허용합니다"), call_limit=True)
        called = True
        return reviewer(prompt)

    fragments = tuple({f.fragment_id: f for section_id in targets for f in evidence[section_id]}.values())
    verified = verify_report(
        ComposedReport(tuple(sections)), fragments, performance_table, review_once,
        diagnostics=diagnostics, protocol_diagnostics=protocol_diagnostics,
        baseline_date=baseline_date, allow_sentence_rewrite=False,
    )
    verified, _ = drop_cross_section_duplicates(verified, fragments=fragments)
    # 본문 출고와 같은 수치 안전 검사까지 살아남아야 복구한 장으로 센다.
    verified, _ = enforce_public_numeric_safety(verified)
    replacements = {
        section.section_id: tuple(sentence for sentence in section.sentences
                                  if sentence.grade == GRADE_CONFIRMED and sentence.verification_state == "verified")
        for section in verified.sections
    }
    recovered = []
    merged = []
    for section in report.sections:
        sentences = replacements.get(section.section_id, ())
        # 복구 문장이 기존 장의 사실을 다시 가져오면 복구 쪽만 버린다.
        # 기존 중복 판정과 같은 문서 결속을 사용하되 기존 본문은 수정하지 않는다.
        unique = []
        for sentence in sentences:
            candidate_report = replace(report, sections=tuple(
                replace(original, sentences=(sentence,))
                if original.section_id == section.section_id else original
                for original in report.sections
            ))
            _, duplicate_count = drop_cross_section_duplicates(
                candidate_report, fragments=comparison_fragments or fragments,
            )
            if not duplicate_count:
                unique.append(sentence)
        sentences = tuple(unique)
        if section.section_id in targets and not section.sentences and sentences:
            merged.append(replace(section, sentences=sentences, notice=""))
            recovered.append(section.section_id)
        else:
            merged.append(section)
    if protocol_diagnostics is not None:
        protocol_diagnostics.append({"step": EMPTY_RECOVERY_STEP, "상태": "검수완료",
                                     "대상장": list(targets), "복구장": recovered})
    return replace(report, sections=tuple(merged))
