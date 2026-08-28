from __future__ import annotations

import json
from dataclasses import replace

import pytest

from src.shared import automatic_release_record as release_contract
from src.shared.automatic_release_record import (
    AUTOMATIC_CHECKER_V1,
    AUTOMATIC_CHECKER_VERSION,
    REQUIRED_AUTOMATIC_CHECKS,
    AutomaticCheckResult,
    AutomaticReleaseRecord,
    automatic_release_json,
    automatic_release_record_sha256,
    parse_automatic_release_json,
    validate_automatic_release_record,
)


_AT = "2026-08-21T12:00:00+09:00"


def _valid_record() -> AutomaticReleaseRecord:
    draft = AutomaticReleaseRecord(
        checker_version=AUTOMATIC_CHECKER_VERSION,
        report_sha256="a" * 64,
        pdf_sha256="b" * 64,
        page_count=1,
        page_png_sha256s=("c" * 64,),
        expected_fact_ids=("fact-1",),
        checks=tuple(
            AutomaticCheckResult(
                name=name,
                passed=True,
                evidence_sha256="d" * 64,
            )
            for name in REQUIRED_AUTOMATIC_CHECKS
        ),
        released_at=_AT,
        record_sha256="0" * 64,
    )
    return replace(
        draft,
        record_sha256=automatic_release_record_sha256(draft),
    )


def test_검증기는_임의의_손상된_dataclass값에도_예외를_내지않는다():
    record = _valid_record()
    first_check = record.checks[0]
    broken_records = (
        replace(record, checker_version=object()),
        replace(record, report_sha256=object()),
        replace(record, pdf_sha256=object()),
        replace(record, page_count=True),
        replace(record, page_png_sha256s=([],)),
        replace(record, expected_fact_ids=([],)),
        replace(record, checks=(object(),)),
        replace(
            record,
            checks=(replace(first_check, name=object()), *record.checks[1:]),
        ),
        replace(
            record,
            checks=(replace(first_check, passed=1), *record.checks[1:]),
        ),
        replace(
            record,
            checks=(
                replace(first_check, evidence_sha256=object()),
                *record.checks[1:],
            ),
        ),
        replace(record, released_at=object()),
        replace(record, record_sha256=object()),
    )

    for broken in broken_records:
        assert validate_automatic_release_record(broken)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("checker_version", 1),
        ("report_sha256", True),
        ("pdf_sha256", None),
        ("page_count", True),
        ("released_at", 1),
        ("record_sha256", False),
    ),
)
def test_파서는_상위_scalar의_exact_type을_요구한다(field: str, value: object):
    payload = json.loads(automatic_release_json(_valid_record()))
    payload[field] = value
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    with pytest.raises(ValueError, match="안전하게 읽을 수 없습니다"):
        parse_automatic_release_json(raw)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("name", 1),
        ("passed", 1),
        ("evidence_sha256", None),
    ),
)
def test_파서는_자동검사_scalar의_exact_type을_요구한다(
    field: str,
    value: object,
):
    payload = json.loads(automatic_release_json(_valid_record()))
    payload["checks"][0][field] = value
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    with pytest.raises(ValueError, match="안전하게 읽을 수 없습니다"):
        parse_automatic_release_json(raw)


def test_지원버전목록은_실행중_수정할수없는_코드계약이다():
    assert release_contract.SUPPORTED_AUTOMATIC_CHECKS_BY_VERSION[
        AUTOMATIC_CHECKER_V1
    ] == REQUIRED_AUTOMATIC_CHECKS
    with pytest.raises(TypeError):
        release_contract.SUPPORTED_AUTOMATIC_CHECKS_BY_VERSION[
            "automatic-release-forged"
        ] = REQUIRED_AUTOMATIC_CHECKS


def test_모르는버전은_자체지문이맞아도_승인으로_복원하지않는다():
    unknown_version = "automatic-release-v999"
    draft = replace(
        _valid_record(),
        checker_version=unknown_version,
        record_sha256="",
    )
    unknown = replace(
        draft,
        record_sha256=automatic_release_record_sha256(draft),
    )

    assert any(
        "지원하는 출고 계약" in problem
        for problem in validate_automatic_release_record(
            unknown,
            expected_checker_version=unknown_version,
        )
    )
    with pytest.raises(ValueError, match="지정한 무결성 계약"):
        parse_automatic_release_json(
            automatic_release_json(unknown),
            expected_checker_version=unknown_version,
        )


def test_현재버전이바뀌어도_명시한_지원역사버전은_독립적으로검증한다(
    monkeypatch,
):
    record = _valid_record()
    monkeypatch.setattr(
        release_contract,
        "AUTOMATIC_CHECKER_VERSION",
        "automatic-release-v2",
    )

    # 현재 alias와 분리된 역사 키라서 v2 전환 자체가 v1 계약을 지우지 않는다.
    assert release_contract.SUPPORTED_AUTOMATIC_CHECKS_BY_VERSION[
        AUTOMATIC_CHECKER_V1
    ] == REQUIRED_AUTOMATIC_CHECKS

    # 버전을 생략한 새 출고 검증은 새 현재 버전을 따르므로 v1을 거부한다.
    assert validate_automatic_release_record(record)
    # 이미 발급된 artifact 소비자는 저장된 v1을 명시해 그대로 검증한다.
    assert not validate_automatic_release_record(
        record,
        expected_checker_version="automatic-release-v1",
    )
