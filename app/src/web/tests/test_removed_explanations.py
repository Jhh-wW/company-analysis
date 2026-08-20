"""사용자가 삭제한 입력·회사 확인 설명 문구가 다시 나타나지 않게 한다."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.core.constants import (
    REMOVED_CONFIRM_COPY_MARKERS,
    REMOVED_INPUT_COPY_MARKERS,
)
from src.features.pipeline.demo import DemoPipeline
from src.web import main
from src.web import runtime
from src.web.tests._visible_text import visible_text


class _가짜진짜알맹이:
    """`DemoPipeline`이 아니면 첫 화면이 진짜 조사 모드로 렌더링된다."""


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


def test_첫화면에서_삭제한_설명이_모두_사라졌다(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(runtime, "_PIPELINE", _가짜진짜알맹이())

    response = client.get("/")

    assert response.status_code == 200
    shown = visible_text(response.text)
    for removed in REMOVED_INPUT_COPY_MARKERS:
        assert removed not in shown


def test_회사_확인화면에서_삭제한_설명이_사라졌다(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())

    response = client.post(
        "/confirm",
        data={
            "company": "파마리서치",
            "job": "의료기기 개발",
            "region": "강원 강릉시",
            "posting_text": "x",
        },
    )

    assert response.status_code == 200
    shown = visible_text(response.text)
    for removed in REMOVED_CONFIRM_COPY_MARKERS:
        assert removed not in shown
