# -*- coding: utf-8 -*-
"""손님이 «막혔을 때» 보는 화면이 사실을 말하고 길을 남기는지 못 박는다.

★ 왜 이 파일이 따로 있나 — 막는 이유는 넷(자리 없음·횟수·하루 상한·한도 소진)인데
  화면은 하나였다. 그래서 「내일이면 열린다」가 참인 하루 상한의 틀에, 내일도 안
  열리는 한도 소진과 초대가 있어야 열리는 차단까지 실려 화면이 사실과 다른 말을 했다.
  상태 코드만 보는 시험은 이 증상을 한 건도 잡지 못한다.

★ 보고서 접근 거절(404·403·409)도 같이 본다. 가장 자주 공유되는 주소가 남에게
  열릴 때 서체·상단바·돌아갈 길이 없으면 손님에게는 그냥 고장 난 페이지다.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from src.features.budget.constants import BUSY_MESSAGE, RATE_LIMITED_MESSAGE
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.report_access import logic as report_access_logic
from src.features.sharelink import store as share_store
from src.features.sharelink import tracks as share_tracks
from src.features.sharelink.constants import (
    KEY_COOKIE_NAME,
    LINK_BUDGET_EXHAUSTED_MESSAGE,
    LINK_TOTAL_BUDGET_EXHAUSTED_CONTACT,
    LINK_TOTAL_BUDGET_EXHAUSTED_DETAIL,
    LINK_TOTAL_BUDGET_EXHAUSTED_MESSAGE,
    LINK_TOTAL_BUDGET_EXHAUSTED_TITLE,
    PUBLIC_NOT_ALLOWED_MESSAGE,
)
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.web import job_runtime, main, request_helpers
from src.web.tests._visible_text import visible_text

#: 32자리 16진수여야 열쇠로 인정된다 — 아무 글자나 통장이 되면 상한이 무의미해진다.
_열쇠 = "b7c6d5e4f3a2910bb7c6d5e4f3a2910b"

#: 「기다리면 열린다」로 읽히는 말. 기다려도 안 열리는 화면에 있으면 거짓 안내다.
_기다림_문구 = "잠시 기다려 주세요"
#: 하루 상한에 걸렸을 때만 참인 설명.
_하루상한_설명 = "하루에 돌릴 수 있는 양"


def _synthetic_request(*, cookies: dict[str, str] | None = None) -> Request:
    """라우팅 없이 안내 화면 함수만 직접 그려 보기 위한 최소 요청."""
    headers: list[tuple[bytes, bytes]] = []
    if cookies:
        raw = "; ".join(f"{name}={value}" for name, value in cookies.items())
        headers.append((b"cookie", raw.encode("utf-8")))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/run",
            "raw_path": b"/run",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 8000),
        }
    )


def _throttled_html(message: str, kind: str, *, cookies=None) -> str:
    response = request_helpers._throttled(
        _synthetic_request(cookies=cookies), message, kind
    )
    assert response.status_code == 429
    return response.body.decode("utf-8")


def test_누적_한도_소진_화면은_기다리라고_말하지_않는다():
    """★ 하루 소진은 내일 열리지만 누적 소진은 내일도 안 열린다.

    같은 틀에 실으면 제목·설명이 본문을 뒤집어 손님을 헛되이 기다리게 한다.
    """
    html = _throttled_html(
        LINK_TOTAL_BUDGET_EXHAUSTED_MESSAGE,
        f"budget-total:{share_tracks.Track.LINK.value}",
    )
    text = visible_text(html)

    assert LINK_TOTAL_BUDGET_EXHAUSTED_TITLE in text
    assert _기다림_문구 not in text
    assert _하루상한_설명 not in text
    # 기다려도 안 열리므로 «사람에게 닿는 길»을 반드시 같이 준다.
    assert LINK_TOTAL_BUDGET_EXHAUSTED_CONTACT in text
    # 「그래도 볼 수 있는 것」은 그대로 남아야 한다.
    assert "미리 준비된 회사 보고서는 계속 볼 수 있습니다" in text
    assert html.lstrip().lower().startswith("<!doctype")


@pytest.mark.parametrize(
    ("message", "kind"),
    (
        (RATE_LIMITED_MESSAGE, "rate"),
        (BUSY_MESSAGE, "busy"),
    ),
)
def test_자리없음과_횟수제한_화면은_하루_상한을_설명하지_않는다(message, kind):
    """★ 이 둘은 하루 상한과 무관하다. 그 설명을 붙이면 틀린 이유를 알려주는 셈이다."""
    text = visible_text(_throttled_html(message, kind))

    assert _하루상한_설명 not in text
    # 이 둘은 «정말로» 기다리면 풀린다 — 제목은 그대로 남아야 한다.
    assert _기다림_문구 in text


def test_초대없는_손님_화면은_없는_회사_목록을_가리키지_않는다():
    """★ 이 화면에는 회사 목록이 없다. 「아래 회사들」이라고 하면 손님이 헤맨다."""
    text = visible_text(
        _throttled_html(
            PUBLIC_NOT_ALLOWED_MESSAGE,
            f"budget:{share_tracks.Track.PUBLIC.value}",
        )
    )

    assert "아래 회사들" not in text
    assert _기다림_문구 not in text
    assert _하루상한_설명 not in text
    assert request_helpers.THROTTLE_NOT_INVITED_TITLE in text


def test_하루_상한_화면에는_그_설명이_그대로_남는다():
    """★ 반대 경우 — 갈래를 나누다가 «사실인 설명»까지 지우지 않았는지 본다."""
    text = visible_text(
        _throttled_html(
            LINK_BUDGET_EXHAUSTED_MESSAGE,
            f"budget:{share_tracks.Track.LINK.value}",
        )
    )

    assert _하루상한_설명 in text
    assert _기다림_문구 in text


def test_한도가_막혀도_연결된_회사_보고서로_가는_버튼을_준다(monkeypatch):
    """★ 저장본 열람은 0원이라 막힌 뒤에도 열린다 — 그 길을 화면이 알려야 한다."""
    report_id = uuid.uuid4().hex
    report = build_demo_report()
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, "demo-corp", report.job, report)
        share_store.insert_new(
            conn,
            key=_열쇠,
            company=report.company,
            job="마케팅",
            report_id=report_id,
            now_iso="2026-08-16T10:00:00",
        )

    html = _throttled_html(
        LINK_TOTAL_BUDGET_EXHAUSTED_MESSAGE,
        f"budget-total:{share_tracks.Track.LINK.value}",
        cookies={KEY_COOKIE_NAME: _열쇠},
    )

    assert f'href="/result/{report_id}"' in html
    assert f"{report.company} 보고서 보기" in visible_text(html)


def test_연결된_보고서가_없으면_없는_버튼을_그리지_않는다():
    """★ 반대 경우 — 열쇠가 없는 손님에게 빈 버튼을 그리면 막다른 길이 는다."""
    html = _throttled_html(
        LINK_TOTAL_BUDGET_EXHAUSTED_MESSAGE,
        f"budget-total:{share_tracks.Track.LINK.value}",
    )

    assert "보고서 보기" not in visible_text(html)
    assert "/result/" not in html


@pytest.mark.parametrize(
    ("reason", "expected_status", "expected_hint"),
    (
        ("not_owner", 404, request_helpers.REPORT_ACCESS_REOPEN_HINT),
        ("member_revoked", 403, request_helpers.REPORT_ACCESS_REOPEN_HINT),
        ("resource_revoked", 409, request_helpers.REPORT_ACCESS_REVOKED_HINT),
    ),
)
def test_보고서를_열_수_없는_화면은_틀과_상단바를_갖춘다(
    monkeypatch, reason, expected_status, expected_hint
):
    """★ 앞서는 본문 전체가 `<h1><p>` 두 줄뿐이라 서체·상단바·돌아갈 길이 없었다."""

    def deny(_request, locator, *, now=None):
        del locator, now
        return report_access_logic.AccessDecision(False, None, reason)

    monkeypatch.setattr(report_access_logic, "authorize_report_access", deny)
    with TestClient(main.app, base_url="https://testserver") as client:
        response = client.get(f"/result/{uuid.uuid4().hex}", follow_redirects=False)

    assert response.status_code == expected_status
    # ① 틀이 있다 ② 상단바가 있다 — 두 가지가 「조각이 아니다」의 증거다.
    assert response.text.lstrip().lower().startswith("<!doctype")
    assert 'class="topbar"' in response.text
    text = visible_text(response.text)
    assert request_helpers.REPORT_ACCESS_DENIED_TITLE in text
    assert expected_hint in text
    # 거절 응답은 캐시에 남기지 않는다 (기존 계약).
    assert response.headers["Cache-Control"] == "private, no-store"


def test_보고서_접근_거절_화면에도_돌아갈_길이_있다(monkeypatch):
    """★ 같은 주소를 다시 열어도 결과가 같다 — 버튼은 «다른 곳»으로 가야 한다."""

    def deny(_request, locator, *, now=None):
        del locator, now
        return report_access_logic.AccessDecision(False, None, "not_owner")

    monkeypatch.setattr(report_access_logic, "authorize_report_access", deny)
    with TestClient(main.app, base_url="https://testserver") as client:
        response = client.get(f"/result/{uuid.uuid4().hex}", follow_redirects=False)

    assert f'href="{job_runtime.DEFAULT_EXIT_URL}"' in response.text
    assert job_runtime.DEFAULT_EXIT_LABEL in visible_text(response.text)
    # ↻는 «다시 하면 된다»는 뜻이라 결과가 달라지지 않는 이 화면에는 쓰지 않는다.
    assert "↻" not in response.text


def test_누적_한도_소진_화면은_같은_문장을_두_번_보여주지_않는다():
    """★ 제목이 본문 첫 문장을 그대로 앞세우는 갈래다.

    제목과 본문을 그대로 이어 그리면 손님은 같은 문장을 위아래로 두 번 읽는다.
    제목으로 이미 말한 문장은 본문에서 빼고, 「그래도 볼 수 있는 것」만 남긴다.
    """
    text = visible_text(
        _throttled_html(
            LINK_TOTAL_BUDGET_EXHAUSTED_MESSAGE,
            f"budget-total:{share_tracks.Track.LINK.value}",
        )
    )

    assert text.count(LINK_TOTAL_BUDGET_EXHAUSTED_TITLE) == 1, text
    # 제목만 남기고 나머지 안내까지 지우면 손님은 「그럼 뭘 볼 수 있나」를 모른다.
    assert LINK_TOTAL_BUDGET_EXHAUSTED_DETAIL in text
    assert LINK_TOTAL_BUDGET_EXHAUSTED_CONTACT in text
