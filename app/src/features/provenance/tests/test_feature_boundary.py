"""출처 계층이 «출력 채널» feature를 거꾸로 import하지 못하게 막는다.

★ 왜 이 시험이 있나 (2026-09-23 독립 검토 N6)
  `provenance.sources.external_news_notice`가 「이 보고서에는 외부 언론 보도
  출처가 없습니다.」라는 화면 문구를 `export_pdf.constants`에서 가져왔다.
  자료·출처 계층이 PDF 내보내기 기능에 기대는 방향이라 feature-atomic §2-2에
  어긋난다. 게다가 그 문구는 PDF·Notion·웹이 다 쓰므로 shared가 제자리다(§2-4).

★ 함수 «안»의 import도 잡는다 — `ast.walk`는 들여쓴 import까지 본다.
  함수 안으로 숨기면 순환 오류는 안 나지만 방향은 그대로 뒤집혀 있다.
"""

from __future__ import annotations

import ast
from pathlib import Path

from src.features.export_pdf import constants as export_pdf_constants
from src.features.provenance.sources import external_news_notice
from src.shared.report_generation.citation_constants import (
    CITATIONS_NO_EXTERNAL_NEWS_NOTE,
)

#: `pipeline.port`는 이 결함보다 먼저 있던 «자료형» 의존이라 이번 범위 밖이다
#: (`freshness.py`가 `ReportSection`을 받는다). 새 위반을 막는 것이 목적이므로
#: 지금 있는 것만 이름으로 적어 두고, 여기에 줄이 늘어나면 그때 다시 본다.
_ALLOWED_FEATURE_MODULES: frozenset[str] = frozenset({"src.features.pipeline.port"})


def _imported_feature_modules(path: Path) -> list[str]:
    """그 파일이 import하는 «다른 feature» 모듈 이름을 줄번호와 함께 모은다."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        for module in modules:
            if (
                module.startswith("src.features.")
                and not module.startswith("src.features.provenance")
                and module not in _ALLOWED_FEATURE_MODULES
            ):
                found.append(f"{path.name}:{node.lineno}:{module}")
    return found


def test_provenance는_다른_feature를_직접_import하지_않는다() -> None:
    feature_dir = Path(__file__).resolve().parents[1]
    modules = sorted(feature_dir.glob("*.py"))
    assert modules, "provenance 안에 검사할 모듈이 하나도 없으면 이 시험은 허수다"

    violations = [
        violation for path in modules for violation in _imported_feature_modules(path)
    ]

    assert violations == [], (
        f"출처 계층이 다른 feature를 직접 가져온다: {violations}. "
        "공유가 필요하면 shared/를 경유한다."
    )


def test_언론_0건_안내_글자는_shared_한_벌이다() -> None:
    """★ `is` 로 본다 — 같은 글자를 두 곳에 «따로 적은» 사본은 `==`로 안 잡힌다."""

    assert export_pdf_constants.CITATIONS_NO_EXTERNAL_NEWS_NOTE is (
        CITATIONS_NO_EXTERNAL_NEWS_NOTE
    )
    assert external_news_notice(()) is CITATIONS_NO_EXTERNAL_NEWS_NOTE
