"""전역 경로 상수 — 배포에 포함되는 읽기 전용 데이터 파일 위치."""
from __future__ import annotations

from pathlib import Path

# 이 파일 기준: prototype_v1/src/core/paths.py → 워크스페이스 루트
WORKSPACE_ROOT: Path = Path(__file__).resolve().parents[3]

# 알리오 「2026년 공공기관 일반현황」에서 필요한 두 열만 옮긴 조건 0 제외 명단.
PUBLIC_ORG_REGISTRY: Path = (
    WORKSPACE_ROOT
    / "prototype_v1" / "src" / "features" / "public_org" / "data"
    / "public_org_registry_2026.json"
)
