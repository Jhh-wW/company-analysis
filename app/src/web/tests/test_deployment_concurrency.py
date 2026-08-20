"""HTTP 요청 여유와 유료 조사 슬롯을 서로 다른 계약으로 고정한다."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from src.features.budget.constants import MAX_CONCURRENT_RUNS
from src.web import main, paid_runtime


APP_ROOT = Path(__file__).resolve().parents[3]


def _docker_http_concurrency_limit() -> int:
    dockerfile = (APP_ROOT / "Dockerfile").read_text(encoding="utf-8")
    matches = re.findall(r"--limit-concurrency\s+(\d+)", dockerfile)
    assert len(matches) == 1, "Docker HTTP 동시 요청 한도는 한 곳에서만 정한다"
    return int(matches[0])


def test_docker_http_요청_여유와_조사_예산_한도를_분리한다() -> None:
    http_limit = _docker_http_concurrency_limit()

    assert http_limit == 20
    assert MAX_CONCURRENT_RUNS == 5
    assert http_limit > MAX_CONCURRENT_RUNS


def test_조사_슬롯_다섯_개가_차도_healthz는_응답한다(monkeypatch) -> None:
    monkeypatch.setattr(paid_runtime, "_RUNNING", MAX_CONCURRENT_RUNS)

    with TestClient(main.app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
