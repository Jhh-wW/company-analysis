"""'.env' 로더 — 키는 프로그램만 읽는다 (사람·AI가 파일을 열지 않기 위한 장치).

KEY=VALUE 줄만 해석해 환경변수로 넣는다. 값을 출력·로그에 남기는 것은 금지.
"""
from __future__ import annotations

import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
ENV_DISABLE_DOTENV = "ANALYSIS_ENGINE_DISABLE_DOTENV"


def load_env(path: Path = ENV_PATH) -> list[str]:
    """'.env'를 환경변수로 로드하고 **키 이름 목록만** 돌려준다 (값은 절대 반환·출력 금지)."""
    # 실시간 성능시험은 실행기가 명시적으로 전달한 allowlist만 써야 한다.
    # 파일이 실제로 존재하는지조차 확인하기 전에 반환해 key 자동 유입을 막는다.
    if os.environ.get(ENV_DISABLE_DOTENV, "").strip() == "1":
        return []
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
