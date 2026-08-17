"""'.env' 로더 — 키는 프로그램만 읽는다 (사람·AI가 파일을 열지 않기 위한 장치).

KEY=VALUE 줄만 해석해 환경변수로 넣는다. 값을 출력·로그에 남기는 것은 금지.
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def load_env(path: Path = ENV_PATH) -> list[str]:
    """'.env'를 환경변수로 로드하고 **키 이름 목록만** 돌려준다 (값은 절대 반환·출력 금지)."""
    if not path.exists():
        return []
    loaded: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value:
            os.environ.setdefault(key, value)
            loaded.append(key)
    return loaded
