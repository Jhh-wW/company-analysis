"""프로젝트 안에서 쓰는 경로를 한곳에 모은다.

경로를 코드 여기저기에 흩어 쓰면 폴더를 옮길 때 전부 찾아 고쳐야 한다.
"""

from __future__ import annotations

from pathlib import Path

# 이 파일 기준으로 거슬러 올라가 app/ 폴더를 찾는다.
# paths.py → core/ → src/ → app/
APP_ROOT: Path = Path(__file__).resolve().parent.parent.parent

# app/ 의 부모가 프로젝트 루트(기업분석2/)다.
PROJECT_ROOT: Path = APP_ROOT.parent

# ── 웹 자원 ──────────────────────────────────────────────
WEB_DIR: Path = APP_ROOT / "src" / "web"
TEMPLATES_DIR: Path = WEB_DIR / "templates"
STATIC_DIR: Path = WEB_DIR / "static"

# ── 데모 데이터 (1단계 전용) ────────────────────────────
# 초기 조사 엔진이 실제로 돌려 남긴 기록이다. 진짜 파이프라인을 붙이면 안 쓴다.
PILOT_DIR: Path = PROJECT_ROOT / "analysis_engine" / "data" / "pilot"
PILOT_RUNS_FILE: Path = PILOT_DIR / "runs.jsonl"
PILOT_REPORTS_DIR: Path = PILOT_DIR / "reports"


def demo_data_available() -> bool:
    """데모 데이터가 제자리에 있는지 확인한다.

    Returns:
        기록 파일과 보고서 폴더가 둘 다 있으면 True.
    """
    return PILOT_RUNS_FILE.exists() and PILOT_REPORTS_DIR.is_dir()
