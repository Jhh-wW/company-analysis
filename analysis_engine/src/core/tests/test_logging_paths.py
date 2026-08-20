"""단계 로그도 Render 영속 디스크에 남는지 시험."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import dart_client, logging_util, naver_client


def test_로컬_기본_로그_경로는_예전과_같다(monkeypatch):
    monkeypatch.delenv(logging_util.ENV_DATA_ROOT, raising=False)

    assert logging_util.runtime_log_dir() == logging_util.LOG_DIR


def test_배포_단계로그와_API계수기는_같은_폴더에서_이름이_겹치지_않는다(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv(logging_util.ENV_DATA_ROOT, str(tmp_path))

    logging_util.log_step("요청-1", "시험", {"개수": 1}, {"결과": "정상"})

    log_path = tmp_path / "logs" / "요청-1.jsonl"
    row = json.loads(log_path.read_text(encoding="utf-8"))
    assert row["stage"] == "시험"
    assert log_path.name not in {
        dart_client.COUNTER_FILENAME,
        naver_client.COUNTER_FILENAME,
    }
