"""네이버 사용량 계수기의 영속 경로 시험."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import naver_client


def test_로컬_기본_경로는_예전과_같다(monkeypatch):
    monkeypatch.delenv(naver_client.ENV_DATA_ROOT, raising=False)

    assert naver_client.default_counter_path() == naver_client.COUNTER_PATH


def test_배포에서는_영속_데이터_루트를_쓴다(tmp_path, monkeypatch):
    monkeypatch.setenv(naver_client.ENV_DATA_ROOT, str(tmp_path))

    naver_client._tick(today="2026-08-17")

    expected = tmp_path / "logs" / naver_client.COUNTER_FILENAME
    assert json.loads(expected.read_text(encoding="utf-8")) == {"2026-08-17": 1}
