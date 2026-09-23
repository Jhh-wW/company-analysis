"""감사인 표준 문구 표지의 세 사본(수집 엔진·근거 선별·composer)이 갈라지지 않았는지.

세 feature는 서로 import하지 않는다(엔진은 app을, app의 두 feature는 서로를). 그래서
값을 복사해 두고 여기서 파일을 ast로 읽어 대조한다 — import하지 않으므로 경계를
넘지 않는다. 엔진 소스가 없는 배치에서만 소리 나게 건너뛴다. 파일은 있는데 이름을
못 찾거나 값이 다르면 반드시 실패한다(test_vocabulary_equivalence.py와 같은 방침).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

#: 세 사본이 «같은 글자»여야 하는 이름.
SHARED_NAMES = (
    "AUDITOR_BOILERPLATE_MARKERS",
    "AUDITOR_BOILERPLATE_EXCLUDED_COMPOUNDS",
    "AUDITOR_BOILERPLATE_EXEMPTION_PATTERN",
    "AUDITOR_CLAUSE_SPLIT_PATTERN",
    "AUDITOR_CONTENT_CHAR_PATTERN",
    "AUDITOR_TRIVIAL_CLAUSE_MAX_CONTENT_CHARS",
    "AUDITOR_STRUCTURAL_MARKERS",
)

# 이 파일 위치: <저장소>/app/src/features/chapter_evidence/tests/이 파일
_REPO_ROOT = Path(__file__).resolve().parents[5]
_FEATURES = _REPO_ROOT / "app" / "src" / "features"
_CHAPTER_EVIDENCE = _FEATURES / "chapter_evidence" / "constants.py"
_COMPOSER = _FEATURES / "composer" / "audit_boilerplate_constants.py"
_ENGINE_DIR = _REPO_ROOT / "analysis_engine" / "src" / "features" / "evidence_collection"


def _literals(path: Path) -> dict[str, object]:
    """모듈 최상위의 일반·주석 붙은 대입 중 SHARED_NAMES 값을 리터럴로 꺼낸다."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names, value = [node.target.id], node.value
        elif isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            value = node.value
        else:
            continue
        for name in names:
            if name in SHARED_NAMES and value is not None:
                found[name] = ast.literal_eval(value)
    missing = [name for name in SHARED_NAMES if name not in found]
    assert not missing, f"{path.name}에 사본 이름이 없습니다: {missing}"
    return found


def test_근거_선별과_composer_사본이_같은_글자다() -> None:
    assert _literals(_CHAPTER_EVIDENCE) == _literals(_COMPOSER)


def test_수집_엔진_사본이_근거_선별_사본과_같은_글자다() -> None:
    if not _ENGINE_DIR.is_dir():
        pytest.skip("엔진 소스가 함께 배치되지 않았다 — 엔진 사본 대조를 건너뛴다")
    assert _literals(_ENGINE_DIR / "constants.py") == _literals(_CHAPTER_EVIDENCE)


def test_표지는_공백_없는_표면형이고_중복이_없다() -> None:
    markers = _literals(_CHAPTER_EVIDENCE)["AUDITOR_BOILERPLATE_MARKERS"]
    assert all(marker == "".join(marker.split()) for marker in markers)
    assert len(markers) == len(set(markers))


def test_구조_표지는_원문_쪽_표지의_부분집합이고_공백이_없다() -> None:
    """구조 표지는 «감사보고서에만 나오는 긴 문형»을 고른 것이다(2026-09-23 독립 검토 F1)."""

    literals = _literals(_CHAPTER_EVIDENCE)
    structural = literals["AUDITOR_STRUCTURAL_MARKERS"]
    assert set(structural) <= set(literals["AUDITOR_BOILERPLATE_MARKERS"])
    assert all(marker == "".join(marker.split()) for marker in structural)
    assert len(structural) == len(set(structural))


# ── 판정 함수 본문 대조(2026-09-23 독립 검토 F10) ─────────────────────────────
# 상수만 같아도 한 벌의 로직이 바뀌면 판정이 갈라진다. 세 사본이 공유하는 판정 함수는
# 본문(설명문 제외)을 ast로 정규화해 같은 모양인지 본다. 엔진은 상수를 `c.이름`으로
# 읽으므로 `이름`으로 바꿔 맞춘다. 문단 판정은 엔진만 채점용 분리 결과를 돌려주므로
# 근거 선별과 composer 두 벌만 대조한다.
SHARED_FUNCTIONS = (
    "_surface",
    "_occurrence_starts",
    "_marker_hit",
    "is_auditor_clause",
    "has_audit_report_structure",
)
APP_SHARED_FUNCTIONS = ("is_auditor_boilerplate",)
_CHAPTER_MODULE = _FEATURES / "chapter_evidence" / "auditor_boilerplate.py"
_COMPOSER_MODULE = _FEATURES / "composer" / "audit_boilerplate_guard.py"
_ENGINE_MODULE = _ENGINE_DIR / "auditor_boilerplate.py"


class _EngineConstantsToNames(ast.NodeTransformer):
    """엔진의 `c.이름` 참조를 app 사본과 같은 `이름`으로 바꾼다."""

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        self.generic_visit(node)
        if isinstance(node.value, ast.Name) and node.value.id == "c":
            return ast.copy_location(ast.Name(id=node.attr, ctx=node.ctx), node)
        return node


def _function_dumps(path: Path, names: tuple[str, ...]) -> dict[str, str]:
    """모듈 최상위 함수 중 이름이 맞는 것의 본문을 설명문을 뺀 ast 덤프로 돌려준다."""

    tree = _EngineConstantsToNames().visit(ast.parse(path.read_text(encoding="utf-8")))
    found: dict[str, str] = {}
    for node in tree.body:
        if not (isinstance(node, ast.FunctionDef) and node.name in names):
            continue
        first = node.body[0] if node.body else None
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            node.body = node.body[1:]
        found[node.name] = ast.dump(node, include_attributes=False)
    missing = [name for name in names if name not in found]
    assert not missing, f"{path.name}에 판정 함수가 없습니다: {missing}"
    return found


def test_세_사본의_공통_판정_함수는_본문이_같다() -> None:
    chapter = _function_dumps(_CHAPTER_MODULE, SHARED_FUNCTIONS)
    assert chapter == _function_dumps(_COMPOSER_MODULE, SHARED_FUNCTIONS)
    if not _ENGINE_DIR.is_dir():
        pytest.skip("엔진 소스가 함께 배치되지 않았다 — 엔진 함수 대조를 건너뛴다")
    assert chapter == _function_dumps(_ENGINE_MODULE, SHARED_FUNCTIONS)


def test_근거_선별과_composer의_조각_판정_함수는_본문이_같다() -> None:
    assert (_function_dumps(_CHAPTER_MODULE, APP_SHARED_FUNCTIONS)
            == _function_dumps(_COMPOSER_MODULE, APP_SHARED_FUNCTIONS))


def test_감사보고서일_단독은_원문_표지에도_구조_표지에도_두지_않는다() -> None:
    """2026-09-23 B 수정 재검토 R2 — 「감사보고서일」 단독은 회사 주석의 기준일(「감사보고서일
    현재 소송 결과를 예측할 수 없습니다」)에도 쓰여 문단째 버렸다. 감사인 문형만 남긴다."""

    literals = _literals(_CHAPTER_EVIDENCE)
    for name in ("AUDITOR_BOILERPLATE_MARKERS", "AUDITOR_STRUCTURAL_MARKERS"):
        assert "감사보고서일" not in literals[name]
        assert {"감사보고서일까지입수", "감사보고서일현재로유효", "감사보고서일후"} <= set(literals[name])
