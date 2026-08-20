"""사용량 계수기 테스트 — 한도·경보·날짜 전환."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import dart_client
from core.dart_client import DartLimitReached, UsageCounter


def test_tick이_세고_한도에서_멈춘다(tmp_path):
    counter = UsageCounter(path=tmp_path / "usage.json", limit=3)
    assert counter.tick("2026-08-14") == 1
    assert counter.tick("2026-08-14") == 2
    assert counter.tick("2026-08-14") == 3
    with pytest.raises(DartLimitReached):
        counter.tick("2026-08-14")


def test_날짜가_바뀌면_0부터(tmp_path):
    counter = UsageCounter(path=tmp_path / "usage.json", limit=3)
    counter.tick("2026-08-14")
    counter.tick("2026-08-14")
    assert counter.tick("2026-08-15") == 1


def test_경보_문턱_경고_출력(tmp_path, capsys):
    counter = UsageCounter(path=tmp_path / "usage.json", limit=10)
    for _ in range(8):
        counter.tick("2026-08-14")
    assert "경보" in capsys.readouterr().out


def test_로컬_기본_경로는_예전과_같다(monkeypatch):
    monkeypatch.delenv(dart_client.ENV_DATA_ROOT, raising=False)

    assert dart_client.default_counter_path() == dart_client.COUNTER_PATH


def test_배포에서는_영속_데이터_루트를_쓴다(tmp_path, monkeypatch):
    monkeypatch.setenv(dart_client.ENV_DATA_ROOT, str(tmp_path))

    counter = UsageCounter(limit=3)
    counter.tick("2026-08-17")

    expected = tmp_path / "logs" / dart_client.COUNTER_FILENAME
    assert counter.path == expected
    assert expected.exists()
