"""자동출고 4검사의 «내용 검증 주입»을 못 박는다 (엔진 v2 소단계 3-4b).

★ 여기서 지키는 것 (04장 3-4절 3항):
  ① 기본값(None)이면 기존 canonical 내용 검증이 그대로 돈다 — v1 무변.
  ② 검증기를 주입하면 «내용 검증만» 그 함수로 대체된다 — canonical 검사는
     호출되지 않고, 렌더 무결성·채널 동등성·해시 재검사는 그대로 태운다.
  ③ 주입 검증기의 실패 사유·예외는 출고를 막는다 (fail-closed, 통과 위장 없음).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.features.composer.constants import GRADE_INTERPRETED, SECTION_IDS
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.render import render_report
from src.features.export_pdf import automatic_release
from src.features.export_pdf.automatic_release import (
    AutomaticGateStopped,
    automatic_release_pdf,
    content_manifest_matches,
    run_automatic_checks,
)
from src.features.export_pdf.content_manifest import (
    CONTENT_MANIFEST_VERSION,
    public_content_manifest_sha256,
)
from src.features.export_pdf.release import PdfReleaseCandidate, prepare_pdf_release
from src.features.pipeline.demo import DemoPipeline, available_companies
from src.features.pipeline.port import Outcome, UserInput
from src.features.report_standard.public_projection import build_public_projection
from src.shared.report_generation.public_projection import (
    PUBLIC_PROJECTION_VERSION,
    build_report_digest,
)

_AT = "2026-08-24T12:00:00+09:00"


@pytest.fixture(scope="module")
def report():
    sample = next(item for item in available_companies() if item["is_report"])
    user_input = UserInput(
        company=sample["company"],
        job=sample["job"],
        region="",
        posting_text="",
    )
    pipeline = DemoPipeline()
    result = pipeline.run(user_input, pipeline.find_company(user_input))
    assert result.outcome is Outcome.REPORT and result.report is not None
    return result.report


@pytest.fixture(scope="module")
def candidate(report):
    # PDF 렌더는 비싸다 — 이 파일의 시험들이 같은 후보를 나눠 쓴다 (읽기 전용).
    return prepare_pdf_release(report)


def test_기본값이면_기존_canonical_검증이_그대로_돈다(
    report, candidate, monkeypatch
):
    calls = {"count": 0}
    original = automatic_release.validate_publishable

    def counting(target):
        calls["count"] += 1
        return original(target)

    monkeypatch.setattr(automatic_release, "validate_publishable", counting)

    released = automatic_release_pdf(report, candidate, released_at=_AT)

    assert calls["count"] >= 1  # v1 기본 경로는 canonical 검사를 실제로 부른다
    assert all(check.passed for check in released.record.checks)


def test_주입_검증기가_있으면_canonical_검사를_부르지_않는다(
    report, candidate, monkeypatch
):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("주입 경로에서 canonical 내용 검증이 호출되었습니다")

    monkeypatch.setattr(automatic_release, "validate_publishable", forbidden)
    monkeypatch.setattr(automatic_release, "build_published_report", forbidden)
    validated = {"count": 0}

    def injected(target):
        validated["count"] += 1
        assert target is report
        return ()

    released = automatic_release_pdf(
        report, candidate, released_at=_AT, content_validator=injected
    )

    assert validated["count"] == 1
    # 나머지 3검사(렌더 무결성·채널 동등성·해시)는 그대로 돌아 전부 통과한다
    assert [check.passed for check in released.record.checks] == [True] * 4
    assert released.content == candidate.pdf_bytes


def test_주입_검증기의_실패_사유는_출고를_막는다(report, candidate, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("주입 경로에서 canonical 내용 검증이 호출되었습니다")

    monkeypatch.setattr(automatic_release, "validate_publishable", forbidden)

    checks, reasons = run_automatic_checks(
        report,
        candidate,
        content_validator=lambda _report: ("v2 검증 사유",),
    )

    assert checks[0].passed is False  # 내용 검증만 실패로 판정된다
    assert any("정본 검사" in reason for reason in reasons)
    with pytest.raises(AutomaticGateStopped, match="GATE_STOPPED"):
        automatic_release_pdf(
            report,
            candidate,
            released_at=_AT,
            content_validator=lambda _report: ("v2 검증 사유",),
        )


def test_주입_검증기_자체가_죽어도_fail_closed다(report, candidate):
    def broken(_report):
        raise RuntimeError("검증기 내부 오류")

    checks, _reasons = run_automatic_checks(
        report, candidate, content_validator=broken
    )

    assert checks[0].passed is False  # 오류를 통과로 위장하지 않는다
    with pytest.raises(AutomaticGateStopped, match="GATE_STOPPED"):
        automatic_release_pdf(
            report, candidate, released_at=_AT, content_validator=broken
        )


# ══════════════════════════════════════════════════════════
# v2 인용 0건 — 차단은 유지하되 사유가 정직해야 한다 (실측 결함)
# ══════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def zero_citation_v2_report():
    """인용이 하나도 없는 v2 보고서 — 전 문장이 «해석»이고 실적표도 없다.

    validate_v2의 3검사(내부 키·인용-부록 1:1·요약 3~5문장)는 전부 통과하는
    «합법적인» v2 결과물이다 — 근거가 극히 빈약한 회사에서 실제로 나올 수
    있는 입력이다.
    """
    sections = tuple(
        ComposedSection(
            section_id=section_id,
            sentences=(
                ComposedSentence(
                    text=f"{section_id} 장은 해석만으로 서술됐다.",
                    citations=(),
                    grade=GRADE_INTERPRETED,
                ),
            ),
        )
        for section_id in SECTION_IDS
    )
    summary = tuple(
        ComposedSentence(
            text=f"핵심 요약 {order}번째 해석이다.", citations=(), grade=GRADE_INTERPRETED
        )
        for order in range(1, 4)
    )
    composed = ComposedReport(sections=sections, summary=summary)
    return render_report(
        "가나다전자",
        composed,
        {},
        None,
        corp_type="상장사",
        as_of_date="2026-08-24",
    )


@pytest.fixture(scope="module")
def zero_citation_v2_candidate(zero_citation_v2_report):
    return prepare_pdf_release(zero_citation_v2_report)


def test_v2_인용0건은_차단은_유지하되_사유가_정직하다(
    zero_citation_v2_report, zero_citation_v2_candidate
):
    checks, reasons = run_automatic_checks(
        zero_citation_v2_report,
        zero_citation_v2_candidate,
        content_validator=lambda _report: (),  # 내용 검증 자체는 통과(합법적 v2 결과물)
    )

    channel_check = checks[2]
    assert channel_check.passed is False  # 차단은 그대로 유지한다
    assert any("인용된 출처가 없어" in reason for reason in reasons)
    assert not any("채널 동등성" in reason for reason in reasons)
    with pytest.raises(AutomaticGateStopped, match="인용된 출처가 없어"):
        automatic_release_pdf(
            zero_citation_v2_report,
            zero_citation_v2_candidate,
            released_at=_AT,
            content_validator=lambda _report: (),
        )


# ══════════════════════════════════════════════════════════
# v2 PDF 공개 내용 결속 — 같은 인용 번호만으로 다른 PDF를 승인할 수 없다
# ══════════════════════════════════════════════════════════


def _cited_v2_report(company: str):
    sections = tuple(
        ComposedSection(
            section_id=section_id,
            sentences=(
                ComposedSentence(
                    text=f"{company}의 {section_id} 분석은 공식 사업 자료를 바탕으로 한다.",
                    citations=("1",),
                    grade=GRADE_INTERPRETED,
                ),
            ),
        )
        for section_id in SECTION_IDS
    )
    summary = tuple(
        ComposedSentence(
            text=f"{company} 핵심 요약 {order}이다.",
            citations=("1",),
            grade=GRADE_INTERPRETED,
        )
        for order in range(1, 4)
    )
    return render_report(
        company,
        ComposedReport(sections=sections, summary=summary),
        {1: {"종류": "사업내용", "원문": f"{company}의 공식 사업 자료다."}},
        None,
        corp_type="상장사",
        as_of_date="2026-08-24",
    )


@pytest.fixture(scope="module")
def cited_v2_report():
    return _cited_v2_report("가나다전자")


@pytest.fixture(scope="module")
def cited_v2_candidate(cited_v2_report):
    return prepare_pdf_release(cited_v2_report)


def test_v2_정상PDF는_문장_표_출처_지문까지_같아야_통과한다(
    cited_v2_report, cited_v2_candidate
):
    checks, reasons = run_automatic_checks(
        cited_v2_report,
        cited_v2_candidate,
        content_validator=lambda _report: (),
    )

    assert [check.passed for check in checks] == [True] * 4
    assert reasons == ()
    assert cited_v2_candidate.content_manifest_version
    assert len(cited_v2_candidate.content_manifest_sha256) == 64


def test_인용번호가_같아도_다른회사_PDF면_채널동등성에서_차단한다(
    cited_v2_report,
):
    other_candidate = prepare_pdf_release(_cited_v2_report("다른회사"))
    assert other_candidate.expected_fact_ids == ("v2-citation-1",)

    checks, reasons = run_automatic_checks(
        cited_v2_report,
        other_candidate,
        content_validator=lambda _report: (),
    )

    assert checks[0].passed is True
    assert checks[1].passed is True
    assert checks[2].passed is False
    assert any("PDF 공개 내용" in reason for reason in reasons)


def test_후보객체의_내용지문만_바꿔써도_PDF_bytes와_대조해_차단한다(
    cited_v2_report, cited_v2_candidate
):
    forged = replace(cited_v2_candidate, content_manifest_sha256="f" * 64)

    checks, _reasons = run_automatic_checks(
        cited_v2_report,
        forged,
        content_validator=lambda _report: (),
    )

    assert checks[1].passed is False
    assert checks[2].passed is False


# ══════════════════════════════════════════════════════════
# 봉인(public_projection)이 있는 보고서 — 지문 대조는 새 digest를 본다
# ══════════════════════════════════════════════════════════


def _manifest_candidate(version: str, sha256: str) -> PdfReleaseCandidate:
    """지문 두 칸만 채운 후보. ``content_manifest_matches``는 이 둘만 읽는다."""

    return PdfReleaseCandidate(
        pdf_bytes=b"",
        pdf_sha256="",
        pages=(),
        content_manifest_version=version,
        content_manifest_sha256=sha256,
    )


def test_봉인이_있으면_내용지문_대조가_projection_digest를_본다(cited_v2_report):
    """봉인이 있으면 새 digest를, 없으면 옛 지문을 본다 — 섞이면 거부한다.

    ★ 왜 여기서 갈라 보나 — 옛 지문(PDF 전용 별도 직렬화기)은 감사 장부를
      덮는 방식이 봉인과 다르다. 「아는 버전이니 통과」로 두면 규칙이 다른 두
      지문이 서로를 대신하게 된다(설계 017 §5).
    """

    sealed = replace(
        cited_v2_report, public_projection=build_public_projection(cited_v2_report)
    )
    assert sealed.public_projection is not None
    digest = build_report_digest(sealed.public_projection)
    legacy_sha256 = public_content_manifest_sha256(cited_v2_report)
    sealed_candidate = _manifest_candidate(
        PUBLIC_PROJECTION_VERSION, digest.content_sha256
    )
    legacy_candidate = _manifest_candidate(CONTENT_MANIFEST_VERSION, legacy_sha256)

    assert content_manifest_matches(sealed, sealed_candidate)
    assert content_manifest_matches(cited_v2_report, legacy_candidate)

    # ① 값이 다르면 거부 ② 옛 지문으로는 봉인 보고서를 통과시키지 못한다
    #    ③ 봉인 지문으로는 봉인 없는 보고서를 통과시키지 못한다
    assert not content_manifest_matches(
        sealed, _manifest_candidate(PUBLIC_PROJECTION_VERSION, "f" * 64)
    )
    assert not content_manifest_matches(sealed, legacy_candidate)
    assert not content_manifest_matches(cited_v2_report, sealed_candidate)
    # 버전만 갈아 끼운 값도 «아는 버전»이라는 이유로 통과하지 않는다.
    assert not content_manifest_matches(
        sealed, _manifest_candidate(CONTENT_MANIFEST_VERSION, digest.content_sha256)
    )


@pytest.fixture(scope="module")
def sealed_v2_report(cited_v2_report):
    """봉인이 붙은 v2 보고서 — PDF도 이 봉인에서 그려진다."""

    return replace(
        cited_v2_report, public_projection=build_public_projection(cited_v2_report)
    )


@pytest.fixture(scope="module")
def sealed_v2_candidate(sealed_v2_report):
    # PDF 렌더는 비싸다 — 아래 시험들이 같은 후보를 나눠 쓴다 (읽기 전용).
    return prepare_pdf_release(sealed_v2_report)


def test_자동출고는_후보_digest를_projection_digest와_대조한다(
    cited_v2_report, sealed_v2_report, sealed_v2_candidate
):
    """봉인된 보고서의 PDF는 봉인 digest를 싣고, 그 값으로 출고가 판정된다.

    ★ 음성 대조 — 같은 PDF를 «봉인이 없는» 보고서로 출고하려 하면 막혀야 한다.
      막히지 않으면 지문 대조가 실은 아무것도 안 보고 있다는 뜻이다.
    """

    assert sealed_v2_report.public_projection is not None
    digest = build_report_digest(sealed_v2_report.public_projection)

    assert sealed_v2_candidate.content_manifest_version == PUBLIC_PROJECTION_VERSION
    assert sealed_v2_candidate.content_manifest_sha256 == digest.content_sha256
    assert sealed_v2_candidate.content_manifest_sha256 != public_content_manifest_sha256(
        sealed_v2_report
    )

    checks, reasons = run_automatic_checks(
        sealed_v2_report,
        sealed_v2_candidate,
        content_validator=lambda _report: (),
    )
    assert [check.passed for check in checks] == [True] * 4
    assert reasons == ()

    blocked_checks, blocked_reasons = run_automatic_checks(
        cited_v2_report,
        sealed_v2_candidate,
        content_validator=lambda _report: (),
    )
    assert blocked_checks[2].passed is False
    assert any("PDF 공개 내용" in reason for reason in blocked_reasons)
    with pytest.raises(AutomaticGateStopped, match="PDF 공개 내용"):
        automatic_release_pdf(
            cited_v2_report,
            sealed_v2_candidate,
            released_at=_AT,
            content_validator=lambda _report: (),
        )
