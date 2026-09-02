# -*- coding: utf-8 -*-
"""「PDF 만들기」가 실패했을 때 «그렇다고 말하는지» 못 박는다.

★ 왜 이 파일이 생겼나
  ─────────────────────────────────────────────────────────
  우리은행 보고서가 이 화면으로 막혔다:
    「보고서 자동검사가 중단되었습니다 / 자동 출고 승인을 확인하지 못했습니다」

  **그런데 자동검사(4종)는 돌지도 않았다.** 실제로 멈춘 곳은 그 «앞 단계»인
  「PDF 후보 만들기(전 페이지 그림 변환)」였다.

  원인: 그 단계가 던지는 맨 ``PDFReleaseBlockedError`` 에는 ``reasons`` 가 없어서
  ``reports._gate_reasons`` 가 기본 문구(``_GATE_UNKNOWN_REASON``)로 떨어졌다.
  서버 로그에도 원본 예외가 안 남아 **관리자가 원인을 찾을 수단이 없었다.**

  즉 화면이 «틀린 단계»를 가리켰다. 사용자도 관리자도 엉뚱한 곳을 뒤지게 된다.

★ 이 시험이 지키는 세 가지
  ① 렌더 실패는 «사유를 실어» 보낸다 (화면이 기본 문구로 안 떨어진다).
  ② 그 사유에 **보고서 값이 안 섞인다** — 공개 화면에 그려지기 때문이다.
  ③ ``AutomaticGateStopped`` 의 진짜 사유를 **덮어쓰지 않는다.**
     ← 부모 클래스에 ``__init__`` 을 만들면 여기가 빨간불이 된다.
"""

from __future__ import annotations

import pytest

from src.features.export_pdf import release
from src.features.export_pdf.automatic_release import AutomaticGateStopped
from src.web.routers import reports


# ══════════════════════════════════════════════════════════
# ① 렌더 실패가 사유를 실어 보내는가
# ══════════════════════════════════════════════════════════


def test_렌더_실패는_사유를_싣는다() -> None:
    """사유가 비면 화면이 「자동 출고 승인을 확인하지 못했습니다」로 떨어진다."""
    막힘 = release._render_blocked()

    assert isinstance(막힘, release.PdfRenderBlockedError)
    assert 막힘.reasons == (release.RENDER_BLOCKED_REASON,)
    assert 막힘.reasons, "★ 비면 화면이 «다른 단계» 문구를 말한다"


def test_화면이_틀린_단계를_말하지_않는다() -> None:
    """★ 이 시험이 수정의 «이유»다.

    렌더 실패인데 화면이 「자동 출고 승인을 확인하지 못했습니다」라고 하면 안 된다.
    """
    사유들 = reports._gate_reasons(release._render_blocked())

    assert reports._GATE_UNKNOWN_REASON not in 사유들, (
        "★ 렌더 실패가 다시 「승인 확인 못 함」으로 뭉개졌다"
    )
    assert 사유들 == (release.RENDER_BLOCKED_REASON,)


def test_진짜_렌더_실패도_사유를_싣는다() -> None:
    """가짜가 아니라 «실제 경로»로도 사유가 실리는지 본다 — PDF 가 아닌 bytes."""
    with pytest.raises(release.PDFReleaseBlockedError) as 잡힘:
        release.prepare_pdf_bytes(b"not-a-pdf")  # PDF 가 아닌 바이트

    assert 잡힘.value.reasons == (release.RENDER_BLOCKED_REASON,)


# ══════════════════════════════════════════════════════════
# ② 공개 화면에 보고서 값이 새지 않는가
# ══════════════════════════════════════════════════════════


def test_사유_문구는_고정값이라_보고서_값이_안_섞인다() -> None:
    """이 문구는 로그인 없이도 보이는 화면에 그대로 그려진다."""
    사유 = release.RENDER_BLOCKED_REASON

    assert isinstance(사유, str)
    assert "\n" not in 사유, "줄바꿈이 있으면 로그 한 줄이 쪼개진다"
    assert len(사유) <= reports._GATE_REASON_MAX_CHARS
    # 값을 끼워 넣는 자리표시자가 있으면 나중에 보고서 값이 박힐 수 있다.
    assert "%" not in 사유 and "{" not in 사유


# ══════════════════════════════════════════════════════════
# ③ 원래 있던 자동검사 사유를 덮어쓰지 않는가  ← 가장 중요
# ══════════════════════════════════════════════════════════


def test_자동검사_사유를_덮어쓰지_않는다() -> None:
    """★ ``PDFReleaseBlockedError`` 에 ``__init__`` 을 만들면 여기가 빨간불이 된다.

    ``AutomaticGateStopped`` 는 ``self.reasons`` 를 «먼저» 넣고
    ``super().__init__`` 을 부른다. 부모가 ``__init__`` 에서 ``reasons`` 를
    세팅하면 **방금 넣은 진짜 사유가 빈 값으로 덮인다** —
    그러면 자동검사가 막았을 때도 화면이 사유를 잃는다.
    """
    진짜사유 = ("인용된 출처가 없어 PDF 자동 출고를 보류했습니다",)

    멈춤 = AutomaticGateStopped(진짜사유)

    assert 멈춤.reasons == 진짜사유
    assert reports._gate_reasons(멈춤) == 진짜사유


def test_부모_예외의_기본값은_빈_튜플이다() -> None:
    """사유를 안 실은 예외도 안전하게 다뤄져야 한다(한 글자씩 쪼개지지 않게)."""
    assert release.PDFReleaseBlockedError.reasons == ()
    assert release.PDFReleaseBlockedError("아무 말").reasons == ()


# ══════════════════════════════════════════════════════════
# ④ 판정이 «너무 넓지» 않은가  ← 한 번 넓게 잡았다가 되돌린 자리
# ══════════════════════════════════════════════════════════


def test_그_밖의_출고_차단까지_렌더_실패로_몰지_않는다() -> None:
    """★ 맨 ``PDFReleaseBlockedError`` 를 던지는 자리가 release.py 에만 12곳이다.

    「출고 승인이 없습니다」·「장부 무결성」 같은 것까지 「만들다 실패」라고 하면
    **화면이 또 틀린 말을 한다.** 좁게 잡은 것을 되돌리면 여기가 빨간불이 된다.
    """
    그밖 = release.PDFReleaseBlockedError("PDF 출고 승인이 없습니다")

    assert not isinstance(그밖, release.PdfRenderBlockedError)
    assert 그밖.reasons == ()
    # 종전 그대로 「승인 확인 못 함」 문구로 떨어진다 — 동작을 안 바꾼다.
    assert reports._gate_reasons(그밖) == (reports._GATE_UNKNOWN_REASON,)


def test_이력에_남길_사유_코드가_세_갈래로_갈린다() -> None:
    """관리자가 로그에서 「무엇에 막혔는지」를 가를 수 있어야 한다."""
    렌더 = reports._pdf_gate_stop_codes(release._render_blocked())
    검사 = reports._pdf_gate_stop_codes(AutomaticGateStopped(("어떤 사유",)))
    그밖 = reports._pdf_gate_stop_codes(
        release.PDFReleaseBlockedError("PDF 출고 승인이 없습니다")
    )

    assert 렌더["stop_reason"] == "pdf_render_failed"
    assert 검사["stop_reason"] == "automatic_release_gate_stopped"
    assert 그밖["stop_reason"] == "pdf_release_blocked"
    assert len({렌더["stop_step"], 검사["stop_step"], 그밖["stop_step"]}) == 3


# ══════════════════════════════════════════════════════════
# ⑤ 구조 검사도 «어느 검사였는지» 남기는가
# ══════════════════════════════════════════════════════════


def test_구조_검사_실패도_렌더_차단으로_분류된다() -> None:
    """★ 「글자가 없는 PDF 페이지가 있습니다」 같은 검사가 여기 해당한다.

    이것들도 사유 없이 던져서 화면이 「자동 출고 승인을 확인하지 못했습니다」로
    떨어졌다 — 자동검사는 돌지도 않았는데.
    """
    with pytest.raises(release.PdfRenderBlockedError) as 잡힘:
        release.prepare_pdf_bytes(b"")

    assert 잡힘.value.reasons == (release.RENDER_BLOCKED_REASON,)


def test_내부_검사이름은_화면이_아니라_로그로_간다(caplog) -> None:
    """★ 사용자에게 「사실 장부의 fact_id」는 잡음이다. 관리자 로그에는 필요하다."""
    import logging

    with caplog.at_level(logging.WARNING, logger=release.__name__):
        with pytest.raises(release.PdfRenderBlockedError) as 잡힘:
            release.prepare_pdf_bytes(b"%PDF-1.4", expected_fact_ids=("a", "a"))

    # 화면에 나가는 값에는 내부 검사 이름이 없다.
    assert "fact_id" not in " ".join(잡힘.value.reasons)
    # 로그에는 어느 검사였는지 남는다.
    assert any("fact_id" in 기록.getMessage() for 기록 in caplog.records), (
        "★ 로그에 어느 검사였는지 안 남으면 관리자가 원인을 못 찾는다"
    )
