"""공고 지문 — 캐시 1층 키의 세 번째 성분 (회사 × 직무 × 공고지문).

정본: 확정/기획서/03_수집/07_캐시와저장.md §1
요구역량 목록(원문 문장 그대로)을 정규화(정렬·공백 제거)한 뒤 해시한다.
같은 공고의 다른 캡처가 같은 지문이 되는 비율은 실측 미결(같은 문서 §미결 1).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

FINGERPRINT_HEX_LEN = 16  # 충돌 걱정 없는 선에서 짧게 (64bit)
_WS_RE = re.compile(r"\s+")


def _normalize_line(line: str) -> str:
    text = unicodedata.normalize("NFKC", line).lower()
    return _WS_RE.sub("", text).strip("·•-–—*[]().,")


def posting_fingerprint(requirement_lines: list[str]) -> str:
    """요구역량 목록 → 지문. 줄 순서·공백·글머리표 차이에 흔들리지 않는다."""
    normalized = sorted(_normalize_line(ln) for ln in requirement_lines if ln.strip())
    joined = "\n".join(normalized)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:FINGERPRINT_HEX_LEN]
