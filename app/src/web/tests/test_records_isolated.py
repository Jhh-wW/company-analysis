"""시험이 «진짜 이력 파일»을 더럽히지 않는지 못 박는다.

★ 이 시험이 잡는 것 — **시험을 돌릴 때마다 관리 화면의 건수가 늘어나는 것.**
  `conftest.py`가 저장소(DB)는 임시 폴더로 돌려놨는데 **이력만 빠져 있었다.**
  그래서 pytest를 한 번 돌릴 때마다 `data/observability/runs.jsonl`에 기록이 쌓였고,
  관리 화면의 「전체 처리 건수 813」은 **사용자가 한 적 없는 조사**를 세고 있었다.
  실측 — 시험 한 파일(`test_survives_restart.py`)만 돌려도 **1,944바이트**가 늘었다.

★ 단서는 `conftest.py`가 `obs_constants`를 **import해 놓고 쓰지 않던 것**이었다.
  「하려다 만 격리」는 아무도 눈치채지 못한다 — 시험이 통과하기 때문이다.

⚠️ 이 시험은 **오염을 직접 재현하지 않는다.** 재현하려면 진짜 파일에 써 봐야 하는데,
  그게 바로 막으려는 행동이다. 대신 **격리 «장치»가 붙어 있는지**를 본다.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.core import paths
from src.features.observability import constants as obs
from src.web.recording import records_path


# ══════════════════════════════════════════════════════════
# ① 시험이 도는 동안에는 임시 경로를 본다
# ══════════════════════════════════════════════════════════


def test_시험_중에는_진짜_이력_파일을_안_쓴다():
    """★ 그 자체. `conftest.py`의 autouse 픽스처가 걸려 있어야 통과한다."""
    진짜 = paths.APP_ROOT / obs.DEFAULT_RECORDS_RELATIVE_PATH

    지금 = records_path()

    assert 지금 != 진짜, (
        "시험이 진짜 이력 파일을 가리키고 있습니다 — "
        "conftest.py의 격리 픽스처가 빠졌거나 꺼졌습니다"
    )


def test_임시_경로가_실제로_환경변수에서_온다():
    """연결이 «진짜»인지 본다 — 우연히 경로가 달라서 통과할 수 있기 때문이다."""
    설정값 = os.environ.get(obs.ENV_RECORDS_PATH, "")

    assert 설정값, f"{obs.ENV_RECORDS_PATH}가 안 걸려 있습니다"
    assert records_path() == Path(설정값)


# ══════════════════════════════════════════════════════════
# ② 환경변수가 없으면 원래 자리로 돌아간다 (배포용)
# ══════════════════════════════════════════════════════════


def test_환경변수가_없으면_원래_자리를_쓴다(monkeypatch):
    """★ 반대 방향 — 격리한다고 «진짜 서비스»의 기록까지 막으면 안 된다."""
    monkeypatch.delenv(obs.ENV_RECORDS_PATH, raising=False)

    assert records_path() == paths.APP_ROOT / obs.DEFAULT_RECORDS_RELATIVE_PATH


def test_빈_값은_없는_것으로_친다(monkeypatch):
    """빈 문자열이 들어오면 「현재 폴더에 파일 하나」가 되어 조용히 엉뚱한 데 쓴다."""
    monkeypatch.setenv(obs.ENV_RECORDS_PATH, "   ")

    assert records_path() == paths.APP_ROOT / obs.DEFAULT_RECORDS_RELATIVE_PATH


# ══════════════════════════════════════════════════════════
# ③ 매번 다시 읽는다
# ══════════════════════════════════════════════════════════


def test_경로를_한_번_읽고_굳히지_않는다(monkeypatch, tmp_path):
    """★ 상수나 캐시로 만들면 시험이 못 바꾼다 — 격리가 조용히 풀린다."""
    첫번째 = tmp_path / "a.jsonl"
    monkeypatch.setenv(obs.ENV_RECORDS_PATH, str(첫번째))
    assert records_path() == 첫번째

    두번째 = tmp_path / "b.jsonl"
    monkeypatch.setenv(obs.ENV_RECORDS_PATH, str(두번째))

    assert records_path() == 두번째
