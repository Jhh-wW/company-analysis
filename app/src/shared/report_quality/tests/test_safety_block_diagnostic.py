"""FULL 공개 안전 차단 한 줄 — 정화기와 유형 분류기의 닫힌 계약.

★ 기대값은 리터럴이다. 생산 상수를 import해 기대값으로 쓰면 이름이 바뀌어도
  시험이 함께 바뀌어 아무것도 지키지 못한다.
"""

from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path

import pytest

from src.shared.report_quality.composition_diagnostics import (
    observed_composition_steps,
)
from src.shared.report_quality.safety_problem_kinds import safety_problem_kind

_STEP = "8_공개안전_차단유형"
_VALID = {
    "step": _STEP,
    "회차": "1차",
    "문제수": 30,
    "유형별": {"numeric_labels_missing": 15, "numeric_binding_missing": 15},
    "장별": {
        "identity": 14,
        "operations_partners": 2,
        "culture": 6,
        "competitive_position": 6,
        "past_changes": 2,
    },
}


def _sanitized(record: dict) -> list[dict]:
    return [
        item
        for item in observed_composition_steps([record])
        if item.get("step") == _STEP
    ]


def test_닫힌_유형_코드와_장별_개수는_정화기를_그대로_지난다():
    assert _sanitized(deepcopy(_VALID)) == [_VALID]


def test_정화기는_장별을_보고서_장_순서로_유형별을_코드_목록_순서로_싣는다():
    (line,) = _sanitized(deepcopy(_VALID))
    assert list(line["장별"]) == [
        "identity",
        "past_changes",
        "operations_partners",
        "culture",
        "competitive_position",
    ]
    assert list(line["유형별"]) == ["numeric_labels_missing", "numeric_binding_missing"]


def test_보충_회차와_요약_장도_받는다():
    record = {
        "step": _STEP,
        "회차": "보충",
        "문제수": 3,
        "유형별": {"summary_binding": 1, "unbound_public_content": 1, "other": 1},
        "장별": {"summary": 1, "culture": 1},
    }
    assert _sanitized(deepcopy(record)) == [record]


def test_열린_글자_칸은_실리지_않는다():
    record = deepcopy(_VALID)
    record["문제"] = "v2-prose-0의 구조화 수치 이름표가 비었습니다"
    (line,) = _sanitized(record)
    assert "문제" not in line
    assert line == _VALID


def _mutated(path: tuple[str, ...], value: object) -> dict:
    record = deepcopy(_VALID)
    target = record
    for key in path[:-1]:
        target = target[key]
    if value is _DELETE:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return record


_DELETE = object()


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("회차",), "2차"),
        (("회차",), ["1차"]),
        (("회차",), _DELETE),
        (("문제수",), 0),
        (("문제수",), True),
        (("문제수",), "30"),
        (("문제수",), 31),
        (("유형별",), {}),
        (("유형별",), "numeric_labels_missing"),
        (("유형별", "numeric_labels_missing"), 0),
        (("유형별", "numeric_labels_missing"), 15.0),
        (("유형별", "공개 문장 원문"), 1),
        (("장별",), _DELETE),
        (("장별",), ["identity"]),
        (("장별", "identity"), 0),
        (("장별", "identity"), 24),
        (("장별", "가나다전자"), 1),
    ],
    ids=lambda value: repr(value)[:30],
)
def test_어긋난_기록은_통째로_버린다(path, value):
    assert _sanitized(_mutated(path, value)) == []


def test_장을_가리지_못한_문제는_장별에_없어도_된다():
    record = deepcopy(_VALID)
    record["장별"] = {}
    assert _sanitized(record) == [record]


@pytest.mark.parametrize(
    ("problem", "kind"),
    [
        ("v2-prose-*의 구조화 수치 이름표가 비었습니다", "numeric_labels_missing"),
        ("v2-prose-*의 수치에 versioned NumericBinding이 없습니다", "numeric_binding_missing"),
        ("fact-9의 과거 실적 종류에 versioned NumericBinding이 없습니다", "numeric_binding_missing"),
        ("<장>장에 fact_id와 결속되지 않은 공개 내용이 있습니다", "unbound_public_content"),
        ("culture장에 fact_id와 결속되지 않은 공개 내용이 있습니다", "unbound_public_content"),
        ("요약에 본문 fact_id와 결속되지 않은 공개 내용이 있습니다", "summary_binding"),
        ("v2-prose-*: 뉴스 산문의 숫자·단위가 정확 원문과 다릅니다", "claim_detail"),
        ("비교 프로그램: 두 비교 사실의 기준 기간이 다릅니다", "comparison_program"),
        ("fact-1의 공개 claim_type을 알 수 없습니다: 'x'", "claim_type_unknown"),
        ("fact_id fact-1가 identity장과 culture장에 중복 공개됐습니다", "duplicate_public_fact"),
        ("source_id s-1가 중복됐습니다", "source_registry"),
        ("검증하지 못한 공개 claim이 있습니다", "unverified_claim"),
        ("처음 보는 문제 문장", "other"),
    ],
)
def test_대표_문장은_정해진_유형으로_접힌다(problem: str, kind: str):
    assert safety_problem_kind(problem) == kind


#: 안전 판정 문구가 만들어지는 함수들 — 품질 판정(assess_quality)의 문구는 대상이 아니다.
_SAFETY_FUNCTIONS = (
    "_fact_registry", "_source_registry", "_public_fact_ids", "assess_safety",
)


def _render(node: ast.AST, counter: list[int]) -> str | None:
    """문구 틀을 한 줄로 편다. 건마다 달라지는 자리는 빈칸 없는 «X숫자»로 채운다."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            rendered = _render(value, counter)
            if rendered is None:
                return None
            parts.append(rendered)
        return "".join(parts)
    if isinstance(node, (ast.FormattedValue, ast.Name, ast.Attribute, ast.Subscript)):
        counter[0] += 1
        return f"X{counter[0]}"
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _render(node.left, counter), _render(node.right, counter)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.GeneratorExp):
        return _render(node.elt, counter)
    return None


def _safety_problem_templates() -> list[str]:
    source = Path(__file__).resolve().parents[1] / "assessment.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    templates: list[str] = []
    for function in tree.body:
        if (
            not isinstance(function, ast.FunctionDef)
            or function.name not in _SAFETY_FUNCTIONS
        ):
            continue
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"append", "extend"}
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "problems"
                and node.args
            ):
                rendered = _render(node.args[0], [0])
                if rendered is not None:
                    templates.append(rendered)
    return templates


def test_안전_판정의_모든_문구_틀이_기타가_아닌_유형으로_접힌다():
    """판정 문구가 바뀌거나 새 문구가 생기면 여기서 «other»로 드러난다."""

    templates = _safety_problem_templates()
    # 틀을 하나도 못 찾으면 이 시험은 아무것도 지키지 않는다 — 실측 개수의 하한.
    assert len(templates) >= 40
    unclassified = [
        template for template in templates
        if safety_problem_kind(template) == "other"
    ]
    assert unclassified == []
