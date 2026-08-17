"""배포 영속 디스크와 로컬 기본 경로를 한곳에서 결정한다.

``APP_DATA_ROOT``를 설정하지 않으면 기존 ``prototype_v1/data``와
``prototype_v1/logs``를 그대로 쓴다. Render에서는 ``/var/data``를 지정한다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

ENV_DATA_ROOT: Final[str] = "APP_DATA_ROOT"
PERSISTENT_PROTOTYPE_DIRNAME: Final[str] = "prototype_v1"

PROTOTYPE_ROOT = Path(__file__).resolve().parents[2]
LOCAL_DATA_DIR = PROTOTYPE_ROOT / "data"
LOCAL_LOG_DIR = PROTOTYPE_ROOT / "logs"


def configured_data_root() -> Path | None:
    """설정된 영속 디스크 루트, 없으면 ``None``을 돌려준다."""
    override = os.environ.get(ENV_DATA_ROOT, "").strip()
    return Path(override).expanduser() if override else None


def runtime_data_dir() -> Path:
    """수집 캐시·실행 산출물을 둘 디렉터리."""
    root = configured_data_root()
    if root is None:
        return LOCAL_DATA_DIR
    return root / PERSISTENT_PROTOTYPE_DIRNAME


def runtime_log_dir() -> Path:
    """API 계수기와 단계 로그를 둘 디렉터리."""
    root = configured_data_root()
    return root / "logs" if root is not None else LOCAL_LOG_DIR
