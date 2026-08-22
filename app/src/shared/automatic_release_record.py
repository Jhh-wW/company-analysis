"""자동출고 레코드의 기능 간 공통 무결성 계약."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Final


AUTOMATIC_CHECKER_VERSION: Final[str] = "automatic-release-v1"
REQUIRED_AUTOMATIC_CHECKS: Final[tuple[str, ...]] = (
    "canonical_fact_citation_numeric_structure_forbidden",
    "pdf_all_pages_rendered",
    "web_pdf_notion_channel_equivalence",
    "final_hash_binding",
)
_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")


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


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def valid_automatic_release_timestamp(value: str) -> bool:
    if type(value) is not str or value != value.strip():
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


def automatic_release_json(record: AutomaticReleaseRecord) -> str:
    return _canonical_json(asdict(record)).decode("utf-8")


def validate_automatic_release_record(
    record: AutomaticReleaseRecord,
) -> tuple[str, ...]:
    """손상된 런타임 값에도 예외 대신 무결성 문제 목록을 반환한다."""

    if not isinstance(record, AutomaticReleaseRecord):
        return ("자동출고 레코드 형식이 올바르지 않습니다",)

    problems: list[str] = []
    checker_version_valid = type(record.checker_version) is str
    report_sha256_valid = type(record.report_sha256) is str
    pdf_sha256_valid = type(record.pdf_sha256) is str
    page_count_valid = type(record.page_count) is int
    page_hashes_valid = type(record.page_png_sha256s) is tuple and all(
        type(value) is str for value in record.page_png_sha256s
    )
    fact_ids_valid = type(record.expected_fact_ids) is tuple and all(
        type(value) is str for value in record.expected_fact_ids
    )
    checks_valid = type(record.checks) is tuple and all(
        type(check) is AutomaticCheckResult for check in record.checks
    )
    check_scalars_valid = checks_valid and all(
        type(check.name) is str
        and type(check.passed) is bool
        and type(check.evidence_sha256) is str
        for check in record.checks
    )
    released_at_valid = type(record.released_at) is str
    record_sha256_valid = type(record.record_sha256) is str

    if (
        not checker_version_valid
        or record.checker_version != AUTOMATIC_CHECKER_VERSION
    ):
        problems.append("자동검사 버전이 현재 출고 계약과 다릅니다")
    if (
        not report_sha256_valid
        or _SHA256_RE.fullmatch(record.report_sha256) is None
    ):
        problems.append("보고서 SHA-256이 올바르지 않습니다")
    if not pdf_sha256_valid or _SHA256_RE.fullmatch(record.pdf_sha256) is None:
        problems.append("PDF SHA-256이 올바르지 않습니다")
    if (
        not page_count_valid
        or record.page_count <= 0
        or not page_hashes_valid
        or record.page_count != len(record.page_png_sha256s)
    ):
        problems.append("PDF 페이지 수와 페이지 지문 수가 다릅니다")
    if not page_hashes_valid or not record.page_png_sha256s or any(
        _SHA256_RE.fullmatch(value) is None for value in record.page_png_sha256s
    ):
        problems.append("전 페이지 PNG SHA-256이 올바르지 않습니다")
    if (
        not fact_ids_valid
        or not record.expected_fact_ids
        or len(record.expected_fact_ids) != len(set(record.expected_fact_ids))
    ):
        problems.append("자동검사 사실 장부가 비었거나 중복됐습니다")
    if (
        not check_scalars_valid
        or tuple(check.name for check in record.checks) != REQUIRED_AUTOMATIC_CHECKS
    ):
        problems.append("필수 자동검사 목록이 완전하지 않습니다")
    if not check_scalars_valid or any(
        check.passed is not True
        or _SHA256_RE.fullmatch(check.evidence_sha256) is None
        for check in record.checks
    ):
        problems.append("통과하지 못했거나 증거 지문이 없는 자동검사가 있습니다")
    if not released_at_valid or not valid_automatic_release_timestamp(
        record.released_at
    ):
        problems.append("자동출고 시각에 시간대가 없습니다")
    if (
        not record_sha256_valid
        or _SHA256_RE.fullmatch(record.record_sha256) is None
    ):
        problems.append("자동출고 레코드 SHA-256이 올바르지 않습니다")
    elif (
        checker_version_valid
        and report_sha256_valid
        and pdf_sha256_valid
        and page_count_valid
        and page_hashes_valid
        and fact_ids_valid
        and check_scalars_valid
        and released_at_valid
        and automatic_release_record_sha256(record) != record.record_sha256
    ):
        problems.append("자동출고 레코드 지문이 내용과 일치하지 않습니다")
    return tuple(problems)


def parse_automatic_release_json(raw: str) -> AutomaticReleaseRecord:
    """허용 필드만 있는 정규 JSON을 자동출고 레코드로 복원한다."""

    try:
        if type(raw) is not str:
            raise TypeError
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != set(
            AutomaticReleaseRecord.__dataclass_fields__
        ):
            raise TypeError
        for name in (
            "checker_version",
            "report_sha256",
            "pdf_sha256",
            "released_at",
            "record_sha256",
        ):
            if type(payload[name]) is not str:
                raise TypeError
        if type(payload["page_count"]) is not int:
            raise TypeError
        for name in ("page_png_sha256s", "expected_fact_ids"):
            values = payload[name]
            if not isinstance(values, list) or any(
                type(value) is not str for value in values
            ):
                raise TypeError
            payload[name] = tuple(values)
        checks = payload["checks"]
        if not isinstance(checks, list) or any(
            not isinstance(check, dict)
            or set(check) != set(AutomaticCheckResult.__dataclass_fields__)
            or type(check["name"]) is not str
            or type(check["passed"]) is not bool
            or type(check["evidence_sha256"]) is not str
            for check in checks
        ):
            raise TypeError
        payload["checks"] = tuple(AutomaticCheckResult(**check) for check in checks)
        record = AutomaticReleaseRecord(**payload)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("자동출고 기록 JSON을 안전하게 읽을 수 없습니다") from exc
    if validate_automatic_release_record(record):
        raise ValueError("자동출고 기록이 현재 무결성 계약을 통과하지 못했습니다")
    if automatic_release_json(record) != raw:
        raise ValueError("자동출고 기록 JSON이 정규 형식과 다릅니다")
    return record


def validate_persisted_automatic_release(
    *,
    report_sha256: str,
    pdf_sha256: str,
    checker_version: str,
    release_json: str,
    release_sha256: str,
    released_at: str,
) -> AutomaticReleaseRecord:
    """SQLite 열과 정규 JSON·내용 지문을 함께 검증한다."""

    values = (
        report_sha256,
        pdf_sha256,
        checker_version,
        release_json,
        release_sha256,
        released_at,
    )
    if any(type(value) is not str for value in values):
        raise ValueError("자동출고 DB 형식이 손상됐습니다")
    record = parse_automatic_release_json(release_json)
    if (
        record.report_sha256 != report_sha256
        or record.pdf_sha256 != pdf_sha256
        or record.checker_version != checker_version
        or record.released_at != released_at
        or record.record_sha256 != release_sha256
        or automatic_release_record_sha256(record) != release_sha256
    ):
        raise ValueError("자동출고 기록과 DB 지문이 일치하지 않습니다")
    return record
