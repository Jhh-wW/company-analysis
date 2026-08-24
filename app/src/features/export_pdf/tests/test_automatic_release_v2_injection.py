"""자동출고 4검사의 «내용 검증 주입»을 못 박는다 (엔진 v2 소단계 3-4b).

★ 여기서 지키는 것 (04장 3-4절 3항):
  ① 기본값(None)이면 기존 canonical 내용 검증이 그대로 돈다 — v1 무변.
  ② 검증기를 주입하면 «내용 검증만» 그 함수로 대체된다 — canonical 검사는
     호출되지 않고, 렌더 무결성·채널 동등성·해시 재검사는 그대로 태운다.
  ③ 주입 검증기의 실패 사유·예외는 출고를 막는다 (fail-closed, 통과 위장 없음).
"""

from __future__ import annotations

import pytest

from src.features.composer.constants import GRADE_INTERPRETED, SECTION_IDS
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.render import render_report
from src.features.export_pdf import automatic_release
from src.features.export_pdf.automatic_release import (
    AutomaticGateStopped,
    automatic_release_pdf,
    run_automatic_checks,
)
from src.features.export_pdf.release import prepare_pdf_release
from src.features.pipeline.demo import DemoPipeline, available_companies
from src.features.pipeline.port import Outcome, UserInput

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
