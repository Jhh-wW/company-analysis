"""첫 화면이 «지금 어느 모드인지»를 사실대로 말하는지 못 박는다 (문제로그 P-79).

★ 이 시험이 잡는 것 — **진짜 조사 모드인데 「지금은 데모입니다」라고 말하는 것.**
  예전에는 데모 «파일»이 있는지만 보고 상자를 띄웠다. 그래서 `PIPELINE=real`로
  켜도 파일이 남아 있으면 상자가 그대로 떠서, 사용자는 **돈이 나가는 줄 모르고**
  조사를 눌렀다. 머리의 배지는 실제 모드를 따라가는데(P-37) 이 상자만 안 따라갔다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.features.pipeline.demo import DemoPipeline
from src.web import main
from src.web.tests._visible_text import class_count


class _가짜진짜알맹이:
    """`DemoPipeline`이 아니기만 하면 된다 — `is_real` 판정이 그것으로 갈린다."""


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


def _첫화면(client: TestClient) -> str:
    response = client.get("/")
    assert response.status_code == 200
    return response.text


def test_데모_모드에서는_데모라고_말한다(client: TestClient, monkeypatch):
    monkeypatch.setattr(main, "_PIPELINE", DemoPipeline())

    html = _첫화면(client)

    assert class_count(html, "demo-note") == 1


def test_진짜_조사_모드에서는_데모라고_말하지_않는다(client: TestClient, monkeypatch):
    """★ P-79 그 자체. 돈이 나가는데 「데모」라고 하면 안 된다."""
    monkeypatch.setattr(main, "_PIPELINE", _가짜진짜알맹이())

    html = _첫화면(client)

    assert class_count(html, "demo-note") == 0
