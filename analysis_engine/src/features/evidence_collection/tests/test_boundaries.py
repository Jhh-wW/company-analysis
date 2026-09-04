"""엔진 경계 시험 — app import 0건, 유료 AI 전환 경로 부재(요구사항 8·9)."""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

from features.evidence_collection import constants as c

_FEATURE_ROOT = Path(__file__).resolve().parent.parent
_APP_IMPORT_LINE_PATTERN = re.compile(r"^\s*(?:from|import)\s+app(?:\.|\s|$)")
#: 유료 AI provider 호출 흔적 — 이 목록에 걸리면 이 feature가 DART가 아닌
#: 다른 곳으로 «자동 전환»될 수 있다는 뜻이다.
_FORBIDDEN_PAID_API_SUBSTRINGS = (
    "anthropic", "openai", "claude-", "gpt-", "api.anthropic.com",
    "generativelanguage", "chat.completions", "api.openai.com",
)


def _feature_python_files(*, include_tests: bool) -> list[Path]:
    files = []
    for path in _FEATURE_ROOT.rglob("*.py"):
        if not include_tests and "tests" in path.relative_to(_FEATURE_ROOT).parts:
            continue
        files.append(path)
    return files


def test_생산_코드는_app을_import하지_않는다() -> None:
    """정적 스캔 — «from app」·「import app」으로 시작하는 줄이 없어야 한다."""
    violations = []
    for path in _feature_python_files(include_tests=False):
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if _APP_IMPORT_LINE_PATTERN.match(line):
                violations.append(f"{path}:{line_no}: {line.strip()}")
    assert violations == [], f"app import 발견:\n" + "\n".join(violations)


def test_collect_모듈을_실제로_import해도_app이_sys_modules에_없다() -> None:
    """정적 스캔이 못 잡는 동적 import까지 닫기 위한 실행시간 확인."""
    module_name = "features.evidence_collection.collect"
    sys.modules.pop(module_name, None)
    importlib.import_module(module_name)

    loaded_app_modules = [name for name in sys.modules if name == "app" or name.startswith("app.")]
    assert loaded_app_modules == [], f"app 계열 모듈이 로드됨: {loaded_app_modules}"


def test_생산_코드에_유료_AI_provider_문자열이_없다() -> None:
    violations = []
    for path in _feature_python_files(include_tests=False):
        text_lower = path.read_text(encoding="utf-8").lower()
        for forbidden in _FORBIDDEN_PAID_API_SUBSTRINGS:
            if forbidden in text_lower:
                violations.append(f"{path}: {forbidden!r}")
    assert violations == [], f"유료 provider 흔적 발견:\n" + "\n".join(violations)


def test_허용_host_allowlist는_DART_계열뿐이다() -> None:
    assert c.ALLOWED_HOST_ALLOWLIST == frozenset({"opendart.fss.or.kr", "dart.fss.or.kr"})


def test_zip_해제_상한은_core_dart_client_값을_그대로_재사용한다() -> None:
    from core.dart_client import (
        DOCUMENT_MEMBER_MAX_BYTES,
        DOCUMENT_ZIP_MAX_MEMBERS,
        DOCUMENT_ZIP_TOTAL_UNCOMPRESSED_MAX_BYTES,
        ZIP_MEMBER_MAX_COMPRESSION_RATIO,
    )

    assert c.ZIP_BOMB_MAX_TOTAL_UNCOMPRESSED_BYTES == DOCUMENT_ZIP_TOTAL_UNCOMPRESSED_MAX_BYTES
    assert c.ZIP_BOMB_MAX_MEMBER_BYTES == DOCUMENT_MEMBER_MAX_BYTES
    assert c.ZIP_BOMB_MAX_MEMBERS == DOCUMENT_ZIP_MAX_MEMBERS
    assert c.ZIP_BOMB_MAX_COMPRESSION_RATIO == ZIP_MEMBER_MAX_COMPRESSION_RATIO
