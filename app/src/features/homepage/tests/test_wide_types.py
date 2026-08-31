"""넓은 공식 웹 수집 계약(자료형)의 검증 규칙 회귀시험."""

from __future__ import annotations

import hashlib

import pytest

from src.features.homepage.wide_types import (
    WideCollectionAttempt,
    WideCollectionResult,
    WideDocumentIdentity,
    WideFragment,
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
        source_tier="TIER_1_OFFICIAL",
    )
    fields.update(overrides)
    return WideDocumentIdentity(**fields)


def _fragment(**overrides: object) -> WideFragment:
    text = "우리는 좋은 회사입니다."
    fields = dict(
        company_id="c1",
        fragment_id="frag-1",
        document_id="d1",
        location="https://company.example/about#0",
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        text=text,
        section_id="identity",
        slot_id="identity:corporate_identity",
        score_millis=700,
        reason_codes=("page_type_signal",),
    )
    fields.update(overrides)
    return WideFragment(**fields)


def _attempt(**overrides: object) -> WideCollectionAttempt:
    fields = dict(
        company_id="c1",
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


def test_source_tier가_허용값이_아니면_ValueError():
    with pytest.raises(ValueError):
        _document(source_tier="TIER_0_UNKNOWN")


def test_source_tier_TIER_1_OFFICIAL은_허용된다():
    document = _document(source_tier="TIER_1_OFFICIAL")
    assert document.source_tier == "TIER_1_OFFICIAL"


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
    assert attempt.company_id == "c1"


def test_attempt_빈_company_id는_ValueError():
    """계약 generation=8: company_id는 필수 — 비우면 즉시 거절한다."""
    with pytest.raises(ValueError):
        _attempt(company_id="")


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


def test_attempt_slot_ids가_빈_튜플이면_ValueError():
    """P0-2: 앱 계약(CollectionAttempt)이 빈 slot_ids를 생성 즉시 거절하므로,
    이 로컬 계약도 같은 지점에서 먼저 막아야 잘못된 데이터가 다음 모듈까지
    조용히 흘러가지 않는다."""
    with pytest.raises(ValueError):
        _attempt(slot_ids=())


def test_정상_fragment는_생성된다():
    fragment = _fragment()
    assert fragment.slot_id == "identity:corporate_identity"
    assert fragment.company_id == "c1"


def test_fragment_빈_company_id는_ValueError():
    """계약 generation=8: company_id는 필수 — 비우면 즉시 거절한다."""
    with pytest.raises(ValueError):
        _fragment(company_id="")


def test_fragment_text_sha256이_text와_불일치하면_ValueError():
    with pytest.raises(ValueError):
        _fragment(text_sha256="a" * 64)


def test_fragment_slot_id가_허용_어휘_밖이면_ValueError():
    with pytest.raises(ValueError):
        _fragment(slot_id="identity:unknown_field", section_id="identity")


def test_fragment_comparison_슬롯은_허용되지_않는다():
    with pytest.raises(ValueError):
        _fragment(slot_id="comparison_position:market_share", section_id="comparison_position")


def test_fragment_limitation_슬롯은_허용되지_않는다():
    with pytest.raises(ValueError):
        _fragment(slot_id="limitation:disclaimer", section_id="limitation")


def test_fragment_historical_performance_슬롯은_허용되지_않는다():
    with pytest.raises(ValueError):
        _fragment(slot_id="historical_performance:trend", section_id="historical_performance")


def test_fragment_section_id가_slot_id_장과_다르면_ValueError():
    with pytest.raises(ValueError):
        _fragment(section_id="culture")  # slot_id는 identity:corporate_identity 그대로


def test_fragment_score_millis_범위_밖이면_ValueError():
    with pytest.raises(ValueError):
        _fragment(score_millis=1001)
    with pytest.raises(ValueError):
        _fragment(score_millis=-1)


def test_fragment_score_millis_경계값은_허용된다():
    assert _fragment(score_millis=0).score_millis == 0
    assert _fragment(score_millis=1000).score_millis == 1000


def test_fragment_reason_codes가_비어있으면_ValueError():
    with pytest.raises(ValueError):
        _fragment(reason_codes=())


def test_fragment_reason_codes_형식이_잘못되면_ValueError():
    with pytest.raises(ValueError):
        _fragment(reason_codes=("한글 사유",))


def test_fragment_location_형식이_잘못되면_ValueError():
    with pytest.raises(ValueError):
        _fragment(location="https://company.example/about")  # '#index' 없음
    with pytest.raises(ValueError):
        _fragment(location="https://company.example/about#끝")  # 숫자 아님


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
