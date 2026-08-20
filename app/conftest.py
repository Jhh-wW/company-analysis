# -*- coding: utf-8 -*-
"""시험 전체에 공통으로 거는 준비.

★ 시험이 «진짜 저장소»를 건드리면 안 된다.
  안 그러면 시험을 돌릴 때마다 실제 보고서·로그인 기록이 더러워지고,
  시험 결과도 「지난번 기록」에 따라 달라진다.
"""

from __future__ import annotations

import datetime as dt
import os
import tempfile

import pytest

from src.features.observability import constants as obs_constants
from src.features.storage import constants as storage_constants


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    """모든 시험이 «임시 폴더»의 저장소와 이력을 쓰게 한다.

    ★ 이력 격리는 나중에 채웠다 (문제로그 P-85). 그전에는 DB만 격리돼 있어
      **시험을 돌릴 때마다 진짜 이력 파일(`data/observability/runs.jsonl`)에
      기록이 쌓였다.** 실측 — 813건 중 대부분이 시험 찌꺼기였고, 관리 화면의
      「전체 처리 건수」가 사용자가 한 적 없는 조사를 세고 있었다.
      ⚠️ 이 파일이 `obs_constants`를 import해 놓고 «쓰지 않고» 있던 것이 단서였다.
    """
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(tmp_path / "test.db"))
    monkeypatch.setenv(
        obs_constants.ENV_RECORDS_PATH, str(tmp_path / "runs.jsonl")
    )
    yield


@pytest.fixture(autouse=True)
def _fresh_guards():
    """시험마다 «갓 켠 서버»처럼 돈·횟수 상태를 비운다 (문제로그 P-92).

    ★ 왜 필요한가 — 횟수 제한(10분에 5건)은 «서버 하나»를 기준으로 센다.
      비우지 않으면 앞 시험이 쓴 횟수가 뒷 시험에 넘어가서,
      **아무 잘못 없는 시험이 「너무 많이 요청했다」로 실패**한다.
      실제로 그렇게 6개가 깨졌다.

    ⚠️ 상한 자체를 «풀지» 않는다 — 값은 그대로 두고 «기록»만 비운다.
      풀어 버리면 제한이 실제로 도는지를 시험이 확인할 수 없게 된다.
    """
    from src.features.budget import logic as budget_logic   # noqa: PLC0415
    from src.features.sharelink import logic as share_logic  # noqa: PLC0415
    from src.web import job_runtime, paid_runtime, public_ids  # noqa: PLC0415

    job_runtime._JOBS.clear()
    job_runtime._PAID_ATTEMPTS.clear()
    with public_ids._RESERVATION_LOCK:
        public_ids._RESERVED_IDS.clear()
    paid_runtime._RATE_HISTORY = budget_logic.RateHistory()
    paid_runtime._LEDGER = budget_logic.Ledger(day=dt.date.today())
    # ★ 예산은 «링크별»로 센다 (P-94) — 이것도 같이 비워야 한다.
    paid_runtime._LINK_SPEND = share_logic.DailySpend(day=dt.date.today())
    paid_runtime._RUNNING = 0
    paid_runtime._RUNNING_BY_BUCKET.clear()
    paid_runtime._BUDGET_STORE_HEALTHY = True
    paid_runtime._UNRESOLVED_BUCKETS.clear()
    paid_runtime._ACTIVE_PAID_PHASES.clear()
    yield
