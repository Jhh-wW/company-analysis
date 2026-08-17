"""단계별 입출력 로거 — 「로그 없는 실행은 검증이 아니다」 (핸드오프 계획 ③).

모든 파이프라인 단계는 log_step으로 입력·출력을 남긴다. 이 로그가 v2 기획서의 실측 자료다.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from core.runtime_paths import ENV_DATA_ROOT, LOCAL_LOG_DIR, runtime_log_dir

# 예전 코드가 가져다 쓸 수 있도록 남겨 둔 로컬 기본값이다.
LOG_DIR = LOCAL_LOG_DIR

# 파일명에 못 쓰는 문자 — run_id가 경로 문자를 담아도 logs/ 밖으로 못 나가게 치환
_UNSAFE_ID_RE = re.compile(r"[^\w가-힣\-]")


def log_step(run_id: str, stage: str, payload_in: Any, payload_out: Any) -> None:
    """한 단계의 입출력을 JSONL 한 줄로 남긴다 (요청 run_id별 파일)."""
    run_id = _UNSAFE_ID_RE.sub("_", run_id)
    log_dir = runtime_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "stage": stage,
        "in": payload_in,
        "out": payload_out,
    }
    with (log_dir / f"{run_id}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
