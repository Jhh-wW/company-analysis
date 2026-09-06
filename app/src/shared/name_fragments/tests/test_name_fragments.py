"""이름 조각 표기 정본 — 조립과 해체가 서로를 되돌린다.

★ 이 표기가 깨지면 하류가 조용히 실패한다. 3장 판정은 이름을 하나도 못 찾고도
  「쓸 이름이 없었다」로 보여 초록불이 되고, 작가는 어느 칸이 이름인지 모른 채
  추측한다. 그래서 왕복(compose → parse)을 값마다 못 박는다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.shared.name_fragments.constants import (
    NAME_KIND_LABELS,
    NAME_KINDS,
    NAME_LABEL_SEPARATOR,
    NAME_VALUE_SEPARATOR,
    REPRESENTATIVE_NAME_KINDS,
    REPRESENTATIVE_NAME_LABELS,
    compose_name_location,
    parse_name_location,
)


_LOCATION = "가. 주요 제품 및 서비스의 현황 · 3행"

#: 시험용 «가공» 이름. 실존 회사·그룹·상품 이름을 쓰지 않는다.
_NAMES = (
    "가람이름",
    "나래 이름 D9",
    "다솜(주석)",
    "라온 · 마루",
    "바람: 사슴",
    "AbC 이름",
)


@pytest.mark.parametrize("kind", sorted(NAME_KINDS))
@pytest.mark.parametrize("name", _NAMES)
def test_조립한_표기는_그대로_해체된다(kind: str, name: str) -> None:
    location = compose_name_location(_LOCATION, kind, name)

    assert parse_name_location(location) == (NAME_KIND_LABELS[kind], name)


@pytest.mark.parametrize("kind", sorted(NAME_KINDS))
def test_조립한_표기는_원래_위치를_앞에_그대로_둔다(kind: str) -> None:
    location = compose_name_location(_LOCATION, kind, _NAMES[0])

    assert location.startswith(_LOCATION + NAME_LABEL_SEPARATOR)
    assert location.endswith(NAME_VALUE_SEPARATOR + _NAMES[0])


def test_조립한_표기는_글자_그대로_이_모양이다() -> None:
    """★ 왕복 시험만으로는 부족하다 — 조립기와 해체기가 «같은 상수»를 쓰므로
    구분자를 통째로 바꿔도 왕복은 그대로 성립한다. 실측으로 확인했다:
    값 구분자를 「: 」에서 「 = 」로 바꿔도 왕복·동일성 시험이 전부 초록이었다.

    작가 안내문이 「… · 종류: 이름 모양이고 : 뒤가 그 조각의 이름」이라고
    «글자로» 약속하므로, 그 글자를 여기서 리터럴로 못 박는다.
    """

    assert NAME_LABEL_SEPARATOR == " · "
    assert NAME_VALUE_SEPARATOR == ": "
    assert compose_name_location("가. 표 · 3행", "ip", "가람이름") == (
        "가. 표 · 3행 · 대표 IP: 가람이름"
    )
    assert compose_name_location("가. 표 · 3행", "product", "가람이름") == (
        "가. 표 · 3행 · 제품: 가람이름"
    )
    assert compose_name_location("가. 표 · 3행", "brand", "가람이름") == (
        "가. 표 · 3행 · 브랜드: 가람이름"
    )
    assert compose_name_location("가. 표 · 3행", "segment", "가람이름") == (
        "가. 표 · 3행 · 사업부문: 가람이름"
    )
    assert compose_name_location("가. 표 · 3행", "subsidiary", "가람이름") == (
        "가. 표 · 3행 · 종속회사: 가람이름"
    )
    assert compose_name_location("가. 표 · 3행", "contract", "가람이름") == (
        "가. 표 · 3행 · 주요 계약: 가람이름"
    )


def test_해체기는_글자로_적은_표기를_읽는다() -> None:
    """조립기를 안 거친 «손으로 적은» 표기도 읽혀야 한다 — 순환을 끊는다."""

    assert parse_name_location("가. 표 · 3행 · 대표 IP: 가람이름") == (
        "대표 IP",
        "가람이름",
    )
    assert parse_name_location("가. 표 · 3행 · 제품: 나래이름") == ("제품", "나래이름")


def test_이름_조각이_아닌_위치는_None이다() -> None:
    assert parse_name_location("사업의 내용") is None
    assert parse_name_location("") is None
    assert parse_name_location("가. 표 · 3행") is None
    # 라벨은 있지만 값 구분자가 없으면 이름 조각 표기가 아니다.
    assert parse_name_location("가. 표 · 3행 · 제품") is None


def test_표기가_두_번_보이면_뒤에_있는_것이_이_조각의_표기다() -> None:
    """앞선 위치 글자에 우연히 같은 모양이 있어도 뒤엣것을 쓴다."""

    inner = compose_name_location(_LOCATION, "segment", "가람부문")
    outer = compose_name_location(inner, "product", "나래품목")

    assert parse_name_location(outer) == (NAME_KIND_LABELS["product"], "나래품목")


def test_등록되지_않은_종류는_조용히_넘어가지_않는다() -> None:
    with pytest.raises(KeyError):
        compose_name_location(_LOCATION, "없는종류", _NAMES[0])


def test_대표_이름_라벨은_정본_표에서_만들어진다() -> None:
    """손으로 적으면 종류 표를 고칠 때 한쪽만 바뀐다."""

    assert REPRESENTATIVE_NAME_LABELS == tuple(
        NAME_KIND_LABELS[kind] for kind in REPRESENTATIVE_NAME_KINDS
    )
    assert set(REPRESENTATIVE_NAME_KINDS) < NAME_KINDS
    assert "segment" not in REPRESENTATIVE_NAME_KINDS


def test_공용_정본은_core나_feature를_역으로_import하지_않는다() -> None:
    package = Path(__file__).resolve().parents[1]
    modules: list[str] = []
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(f"{path.name}:{a.name}" for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(f"{path.name}:{node.module}")

    assert [
        item
        for item in modules
        if item.partition(":")[2].startswith(("src.core.", "src.features."))
    ] == []
