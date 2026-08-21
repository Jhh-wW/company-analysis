"""Hash-bound automatic checks and fail-closed report release.

The legacy three-person PDF approval records remain available as audit data,
but they are not an authorization input here.  A release is created only from
the automatic checks in this module and is bound to the exact canonical
report, PDF bytes, and every rendered page image.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Final

from src.features.export_pdf.release import (
    PDFReleaseBlockedError,
    PdfReleaseCandidate,
    _candidate_integrity_problems,
    is_valid_sha256,
)
from src.features.pipeline.port import Report
from src.features.report_standard import build_published_report, validate_publishable
from src.features.storage.reports import report_to_dict


AUTOMATIC_CHECKER_VERSION: Final[str] = "automatic-release-v1"
REQUIRED_AUTOMATIC_CHECKS: Final[tuple[str, ...]] = (
    "canonical_fact_citation_numeric_structure_forbidden",
    "pdf_all_pages_rendered",
    "web_pdf_notion_channel_equivalence",
    "final_hash_binding",
)


@dataclass(frozen=True)
class AutomaticCheckResult:
    name: str
    passed: bool
    evidence_sha256: str


@dataclass(frozen=True)
class AutomaticReleaseRecord:
    checker_version: str
    report_sha256: str
    pdf_sha256: str
    page_count: int
    page_png_sha256s: tuple[str, ...]
    expected_fact_ids: tuple[str, ...]
    checks: tuple[AutomaticCheckResult, ...]
    released_at: str
    record_sha256: str


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


def _valid_timestamp(value: str) -> bool:
    if not isinstance(value, str) or value != value.strip():
        return False
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def automatic_release_record_sha256(record: AutomaticReleaseRecord) -> str:
    payload = asdict(record)
    payload.pop("record_sha256", None)
    return _sha256(_canonical_json(payload))


def validate_automatic_release_record(
    record: AutomaticReleaseRecord,
) -> tuple[str, ...]:
    problems: list[str] = []
    if record.checker_version != AUTOMATIC_CHECKER_VERSION:
        problems.append("자동검사 버전이 현재 출고 계약과 다릅니다")
    if not is_valid_sha256(record.report_sha256):
        problems.append("보고서 SHA-256이 올바르지 않습니다")
    if not is_valid_sha256(record.pdf_sha256):
        problems.append("PDF SHA-256이 올바르지 않습니다")
    if record.page_count <= 0 or record.page_count != len(record.page_png_sha256s):
        problems.append("PDF 페이지 수와 페이지 지문 수가 다릅니다")
    if not record.page_png_sha256s or any(
        not is_valid_sha256(value) for value in record.page_png_sha256s
    ):
        problems.append("전 페이지 PNG SHA-256이 올바르지 않습니다")
    if not record.expected_fact_ids or len(record.expected_fact_ids) != len(
        set(record.expected_fact_ids)
    ):
        problems.append("자동검사 사실 장부가 비었거나 중복됐습니다")
    if tuple(check.name for check in record.checks) != REQUIRED_AUTOMATIC_CHECKS:
        problems.append("필수 자동검사 목록이 완전하지 않습니다")
    if any(
        check.passed is not True or not is_valid_sha256(check.evidence_sha256)
        for check in record.checks
    ):
        problems.append("통과하지 못했거나 증거 지문이 없는 자동검사가 있습니다")
    if not _valid_timestamp(record.released_at):
        problems.append("자동출고 시각에 시간대가 없습니다")
    if not is_valid_sha256(record.record_sha256):
        problems.append("자동출고 레코드 SHA-256이 올바르지 않습니다")
    elif automatic_release_record_sha256(record) != record.record_sha256:
        problems.append("자동출고 레코드 지문이 내용과 일치하지 않습니다")
    return tuple(problems)


def run_automatic_checks(
    report: Report,
    candidate: PdfReleaseCandidate,
) -> tuple[tuple[AutomaticCheckResult, ...], tuple[str, ...]]:
    """Run every mandatory check and return non-sensitive hash evidence."""

    reasons: list[str] = []
    initial_report_hash = report_sha256(report)
    validation = validate_publishable(report)
    canonical_ok = bool(validation)
    published: Report | None = None
    if canonical_ok:
        try:
            published = build_published_report(report)
            canonical_ok = report_sha256(published) == initial_report_hash
        except Exception:  # fail closed; raw report details must not escape
            canonical_ok = False
    if not canonical_ok:
        reasons.append("사실·인용·수치·목차·금지 문구 정본 검사를 통과하지 못했습니다")
    canonical_check = AutomaticCheckResult(
        name=REQUIRED_AUTOMATIC_CHECKS[0],
        passed=canonical_ok,
        evidence_sha256=_evidence_sha256(
            REQUIRED_AUTOMATIC_CHECKS[0],
            initial_report_hash,
            tuple(validation.reasons),
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
    if canonical_ok and published is not None:
        try:
            # Import lazily so PDF generation and Notion adapters remain separate.
            from src.features.export_notion.logic import build_blocks  # noqa: PLC0415

            notion_blocks = build_blocks(published)
            published_fact_ids = tuple(
                fact.fact_id for fact in published.fact_records
            )
            channel_ok = bool(notion_blocks) and (
                candidate.expected_fact_ids == published_fact_ids
            )
        except Exception:  # fail closed at a public-channel boundary
            channel_ok = False
    if not channel_ok:
        reasons.append("웹·PDF·Notion 채널 동등성 검사를 통과하지 못했습니다")
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
) -> AutomaticallyReleasedPdf:
    """Release only when all mandatory automatic checks pass together."""

    checks, reasons = run_automatic_checks(report, candidate)
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
