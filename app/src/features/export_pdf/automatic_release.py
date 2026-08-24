"""Hash-bound automatic checks and fail-closed report release.

The legacy three-person PDF approval records remain available as audit data,
but they are not an authorization input here.  A release is created only from
the automatic checks in this module and is bound to the exact canonical
report, PDF bytes, and every rendered page image.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Callable, Optional

from src.features.composer.render import ENGINE_V2_SCHEMA_VERSION
from src.features.export_pdf.release import (
    PDFReleaseBlockedError,
    PdfReleaseCandidate,
    _candidate_integrity_problems,
    report_fact_id_ledger,
)
from src.shared.automatic_release_record import (
    AUTOMATIC_CHECKER_VERSION,
    REQUIRED_AUTOMATIC_CHECKS,
    AutomaticCheckResult,
    AutomaticReleaseRecord,
    automatic_release_record_sha256,
    valid_automatic_release_timestamp as _valid_timestamp,
    validate_automatic_release_record,
)
from src.features.pipeline.port import Report
from src.features.report_standard import build_published_report, validate_publishable
from src.features.storage.reports import report_to_dict


#: 주입식 내용 검증기 (엔진 v2용) — 보고서를 받아 «검증 실패 사유» 튜플을
#: 돌려준다. 빈 튜플이면 통과다. None(기본)이면 기존 canonical 검사 그대로다.
ContentValidator = Callable[[Report], tuple[str, ...]]

#: 주입 검증기 자체가 죽었을 때의 fail-closed 사유 (통과로 위장하지 않는다)
_INJECTED_VALIDATOR_FAILED_REASON = "주입된 내용 검증기가 실패했습니다"


@dataclass(frozen=True)
class AutomaticallyReleasedPdf:
    content: bytes
    record: AutomaticReleaseRecord


class AutomaticGateStopped(PDFReleaseBlockedError):
    """A mandatory automatic release check failed."""

    def __init__(self, reasons: tuple[str, ...]):
        self.reasons = reasons
        super().__init__("GATE_STOPPED: " + "; ".join(reasons))


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def report_sha256(report: Report) -> str:
    """Digest the exact canonical report without recording its contents."""

    return _sha256(_canonical_json(report_to_dict(report)))


def _evidence_sha256(name: str, *values: object) -> str:
    return _sha256(_canonical_json({"check": name, "values": values}))


def run_automatic_checks(
    report: Report,
    candidate: PdfReleaseCandidate,
    *,
    content_validator: Optional[ContentValidator] = None,
) -> tuple[tuple[AutomaticCheckResult, ...], tuple[str, ...]]:
    """Run every mandatory check and return non-sensitive hash evidence.

    ``content_validator``가 주어지면(엔진 v2) 4검사 중 첫 번째 «내용 검증»만
    그 함수로 대체한다 — v1 기본(None)의 동작은 그대로다. 렌더 무결성·채널
    동등성·해시 재검사는 어느 경로든 똑같이 태운다 (04장 3-4절 3항).
    """

    reasons: list[str] = []
    initial_report_hash = report_sha256(report)
    published: Report | None = None
    if content_validator is None:
        validation = validate_publishable(report)
        canonical_ok = bool(validation)
        if canonical_ok:
            try:
                published = build_published_report(report)
                canonical_ok = report_sha256(published) == initial_report_hash
            except Exception:  # fail closed; raw report details must not escape
                canonical_ok = False
        validation_reasons = tuple(validation.reasons)
    else:
        # 주입 검증(v2): 내용 검증만 대체한다. v2는 별도 공개본 투영이 없으므로
        # 통과 시 정본 그대로를 published로 삼아 뒤 채널 동등성 검사를 태운다.
        try:
            validation_reasons = tuple(content_validator(report))
        except Exception:  # fail closed — 검증기 오류를 통과로 위장하지 않는다
            validation_reasons = (_INJECTED_VALIDATOR_FAILED_REASON,)
        canonical_ok = not validation_reasons
        if canonical_ok:
            published = report
    if not canonical_ok:
        reasons.append("사실·인용·수치·목차·금지 문구 정본 검사를 통과하지 못했습니다")
    canonical_check = AutomaticCheckResult(
        name=REQUIRED_AUTOMATIC_CHECKS[0],
        passed=canonical_ok,
        evidence_sha256=_evidence_sha256(
            REQUIRED_AUTOMATIC_CHECKS[0],
            initial_report_hash,
            validation_reasons,
        ),
    )

    candidate_problems, page_hashes = _candidate_integrity_problems(candidate)
    pdf_ok = not candidate_problems
    if not pdf_ok:
        reasons.append("PDF 전 페이지 생성·렌더 검사를 통과하지 못했습니다")
    pdf_check = AutomaticCheckResult(
        name=REQUIRED_AUTOMATIC_CHECKS[1],
        passed=pdf_ok,
        evidence_sha256=_evidence_sha256(
            REQUIRED_AUTOMATIC_CHECKS[1],
            candidate.pdf_sha256,
            page_hashes,
            tuple(candidate_problems),
        ),
    )

    channel_ok = False
    # ★ 채널 검사가 막을 때 사유를 정직하게 남기려고 기본값(동등성 불일치)에서
    #   출발해, 원인이 «인용 0건»으로 밝혀지면 이 변수만 그 사유로 바꾼다
    #   (차단은 그대로 유지 — v2 인용 장부가 비면 web·PDF·Notion 동등성을
    #   증거로 확인할 방법이 없다는 사실은 안 바뀐다).
    channel_reason = "웹·PDF·Notion 채널 동등성 검사를 통과하지 못했습니다"
    if canonical_ok and published is not None:
        if published.schema_version == ENGINE_V2_SCHEMA_VERSION:
            # v2(엔진 v2): Notion 채널은 04장 3-4절 4항 정책대로 후속 과제다
            # (PDF·웹 우선). export_notion.build_blocks는 내부에서 v1
            # build_published_report를 다시 호출해 v2 Report를 구조적으로
            # 거부한다(실측 — PublishBlockedError, "company-report-v2-composer
            # 보고서만..." 사유가 아니라 "canonical 보고서만..." 사유로 막힘).
            # 그래서 v2는 Notion 렌더 성공을 채널 동등성 증거로 쓰지 않고,
            # PDF 후보와 실제 보고서 내용(인용 장부)이 같은 재료로 만들어졌는지만
            # 확인한다 — web·PDF는 같은 report 객체를 쓰고 그 결속은 ④(해시
            # 재검사)가 report_sha256으로 이미 강제한다.
            try:
                published_fact_ids = report_fact_id_ledger(published)
                if not published_fact_ids:
                    # 인용이 0건인 v2 보고서(해석 문장만·실적표 없음)는 합법적
                    # 결과물이다 — «채널이 안 맞다»가 아니라 «비교할 인용 장부
                    # 자체가 없다»가 실제 사유이므로 무관한 채널 동등성 사유로
                    # 오표기하지 않는다.
                    channel_reason = "인용된 출처가 없어 PDF 자동 출고를 보류했습니다"
                    channel_ok = False
                else:
                    channel_ok = candidate.expected_fact_ids == published_fact_ids
            except Exception:  # fail closed at a public-channel boundary
                channel_ok = False
        else:
            try:
                # Import lazily so PDF generation and Notion adapters remain separate.
                from src.features.export_notion.logic import build_blocks  # noqa: PLC0415

                notion_blocks = build_blocks(published)
                published_fact_ids = report_fact_id_ledger(published)
                channel_ok = bool(notion_blocks) and (
                    candidate.expected_fact_ids == published_fact_ids
                )
            except Exception:  # fail closed at a public-channel boundary
                channel_ok = False
    if not channel_ok:
        reasons.append(channel_reason)
    channel_check = AutomaticCheckResult(
        name=REQUIRED_AUTOMATIC_CHECKS[2],
        passed=channel_ok,
        evidence_sha256=_evidence_sha256(
            REQUIRED_AUTOMATIC_CHECKS[2],
            initial_report_hash,
            candidate.pdf_sha256,
            candidate.expected_fact_ids,
        ),
    )

    final_report_hash = report_sha256(report)
    final_pdf_hash = (
        _sha256(candidate.pdf_bytes)
        if isinstance(candidate.pdf_bytes, bytes)
        else ""
    )
    final_page_hashes = tuple(
        _sha256(page.png_bytes) if isinstance(page.png_bytes, bytes) else ""
        for page in candidate.pages
    )
    hash_ok = (
        final_report_hash == initial_report_hash
        and final_pdf_hash == candidate.pdf_sha256
        and final_page_hashes == page_hashes
    )
    if not hash_ok:
        reasons.append("자동검사 뒤 보고서·PDF·페이지 지문이 변경되었습니다")
    hash_check = AutomaticCheckResult(
        name=REQUIRED_AUTOMATIC_CHECKS[3],
        passed=hash_ok,
        evidence_sha256=_evidence_sha256(
            REQUIRED_AUTOMATIC_CHECKS[3],
            final_report_hash,
            final_pdf_hash,
            final_page_hashes,
        ),
    )
    return (
        (canonical_check, pdf_check, channel_check, hash_check),
        tuple(reasons),
    )


def automatic_release_pdf(
    report: Report,
    candidate: PdfReleaseCandidate,
    *,
    released_at: str,
    content_validator: Optional[ContentValidator] = None,
) -> AutomaticallyReleasedPdf:
    """Release only when all mandatory automatic checks pass together.

    ``content_validator``는 ``run_automatic_checks``에 그대로 전달된다 —
    None(기본)이면 기존 v1 동작과 완전히 같다.
    """

    checks, reasons = run_automatic_checks(
        report, candidate, content_validator=content_validator
    )
    if not _valid_timestamp(released_at):
        reasons = (*reasons, "자동출고 시각에 시간대가 없습니다")
    if reasons or any(check.passed is not True for check in checks):
        raise AutomaticGateStopped(tuple(dict.fromkeys(reasons)))
    payload = {
        "checker_version": AUTOMATIC_CHECKER_VERSION,
        "report_sha256": report_sha256(report),
        "pdf_sha256": candidate.pdf_sha256,
        "page_count": candidate.page_count,
        "page_png_sha256s": tuple(page.png_sha256 for page in candidate.pages),
        "expected_fact_ids": candidate.expected_fact_ids,
        "checks": checks,
        "released_at": released_at,
    }
    unsigned = AutomaticReleaseRecord(**payload, record_sha256="")
    record = AutomaticReleaseRecord(
        **payload,
        record_sha256=automatic_release_record_sha256(unsigned),
    )
    problems = validate_automatic_release_record(record)
    if problems:
        raise AutomaticGateStopped(problems)
    return AutomaticallyReleasedPdf(content=candidate.pdf_bytes, record=record)

def restore_automatic_release(
    report: Report,
    candidate: PdfReleaseCandidate,
    record: AutomaticReleaseRecord,
) -> AutomaticallyReleasedPdf:
    """Rebind a stored release without repeating expensive page rendering."""

    problems = list(validate_automatic_release_record(record))
    if report_sha256(report) != record.report_sha256:
        problems.append("자동검사 뒤 보고서 지문이 변경되었습니다")
    if _sha256(candidate.pdf_bytes) != record.pdf_sha256:
        problems.append("자동검사 뒤 PDF 지문이 변경되었습니다")
    if tuple(_sha256(page.png_bytes) for page in candidate.pages) != (
        record.page_png_sha256s
    ):
        problems.append("자동검사 뒤 PDF 페이지 지문이 변경되었습니다")
    if candidate.expected_fact_ids != record.expected_fact_ids:
        problems.append("자동검사 뒤 사실 장부가 변경되었습니다")
    if problems:
        raise AutomaticGateStopped(tuple(dict.fromkeys(problems)))
    return AutomaticallyReleasedPdf(content=candidate.pdf_bytes, record=record)
