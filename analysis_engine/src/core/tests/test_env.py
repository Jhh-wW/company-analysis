"""실시간 평가 모드에서는 `.env`를 자동으로 읽지 않는다."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import env


def test_explicit_disable_returns_before_reading_dotenv(tmp_path, monkeypatch) -> None:
    marker = "UNIT_TEST_DOTENV_VALUE_THAT_MUST_NOT_LOAD"
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"{marker}=forbidden-sentinel\n", encoding="utf-8")
    monkeypatch.delenv(marker, raising=False)
    monkeypatch.setenv(env.ENV_DISABLE_DOTENV, "1")

    loaded = env.load_env(dotenv)

    assert loaded == []
    assert marker not in os.environ


def test_normal_loader_still_reads_an_explicit_temporary_file(
    tmp_path, monkeypatch
) -> None:
    marker = "UNIT_TEST_EXPLICIT_DOTENV_VALUE"
    dotenv = tmp_path / ".env"
    dotenv.write_text(f"{marker}=temporary-test-only\n", encoding="utf-8")
    monkeypatch.delenv(marker, raising=False)
    monkeypatch.delenv(env.ENV_DISABLE_DOTENV, raising=False)

    loaded = env.load_env(dotenv)

    assert loaded == [marker]
    assert os.environ[marker] == "temporary-test-only"
