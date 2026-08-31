"""넓은 공식 웹 수집 계약(자료형)의 검증 규칙 회귀시험."""

from __future__ import annotations

import pytest

from src.features.homepage.wide_types import (
    WideCollectionAttempt,
    WideCollectionResult,
    WideDocumentIdentity,
)

_SHA = "a" * 64


def _document(**overrides: object) -> WideDocumentIdentity:
    fields = dict(
        company_id="c1",
        document_id="d1",
        canonical_url="https://company.example/about",
        source_kind="official_web_page",
        publisher="company.example",
        title="회사소개",
        published_on="",
        collected_at="2026-08-31T00:00:00+00:00",
        content_sha256=_SHA,
        identity_binding="root",
        usable_ranges=("우리는 좋은 회사입니다.",),
        collector_version="v1",
        parser_version="v1",
        requirement="REQUIRED",
    )
    fields.update(overrides)
    return WideDocumentIdentity(**fields)


def _attempt(**overrides: object) -> WideCollectionAttempt:
    fields = dict(
        attempt_id="page-0001",
        source_kind="official_web_page",
        requirement="REQUIRED",
        state="OK",
        slot_ids=("identity:corporate_identity",),
        reason_code="page_ok",
        elapsed_ms=10,
        bytes_downloaded=100,
        documents_seen=1,
    )
    fields.update(overrides)
    return WideCollectionAttempt(**fields)


def test_정상_document는_생성된다():
    document = _document()
    assert document.canonical_url == "https://company.example/about"


def test_빈_company_id는_ValueError():
    with pytest.raises(ValueError):
        _document(company_id="")


def test_잘못된_sha256_형식은_ValueError():
    with pytest.raises(ValueError):
        _document(content_sha256="not-a-hash")


def test_대문자_sha256도_ValueError():
    with pytest.raises(ValueError):
        _document(content_sha256="A" * 64)


def test_requirement이_REQUIRED_OPTIONAL이_아니면_ValueError():
    with pytest.raises(ValueError):
        _document(requirement="MAYBE")


def test_usable_ranges가_비어있으면_ValueError():
    with pytest.raises(ValueError):
        _document(usable_ranges=())


def test_usable_ranges_안에_빈_문자열이_있으면_ValueError():
    with pytest.raises(ValueError):
        _document(usable_ranges=("본문", "   "))


def test_published_on은_빈_문자열을_허용한다():
    document = _document(published_on="")
    assert document.published_on == ""


def test_정상_attempt는_생성된다():
    attempt = _attempt()
    assert attempt.state == "OK"


def test_attempt_state가_잘못되면_ValueError():
    with pytest.raises(ValueError):
        _attempt(state="UNKNOWN")


def test_attempt_reason_code_형식이_잘못되면_ValueError():
    with pytest.raises(ValueError):
        _attempt(reason_code="한글 사유")


def test_attempt_reason_code는_영문숫자_구두점만_허용한다():
    attempt = _attempt(reason_code="page_missing_404")
    assert attempt.reason_code == "page_missing_404"


def test_attempt_음수_elapsed_ms는_ValueError():
    with pytest.raises(ValueError):
        _attempt(elapsed_ms=-1)


def test_attempt_slot_ids_안에_빈_문자열이_있으면_ValueError():
    with pytest.raises(ValueError):
        _attempt(slot_ids=("",))


def test_attempt_slot_ids는_빈_튜플을_허용한다():
    attempt = _attempt(slot_ids=())
    assert attempt.slot_ids == ()


def test_result은_documents와_attempts를_묶는다():
    result = WideCollectionResult(documents=(_document(),), attempts=(_attempt(),))
    assert len(result.documents) == 1
    assert len(result.attempts) == 1


def test_document_id_중복이면_ValueError():
    with pytest.raises(ValueError):
        WideCollectionResult(
            documents=(_document(document_id="dup"), _document(document_id="dup")),
            attempts=(),
        )


def test_attempt_id_중복이면_ValueError():
    with pytest.raises(ValueError):
        WideCollectionResult(
            documents=(),
            attempts=(_attempt(attempt_id="a1"), _attempt(attempt_id="a1")),
        )
