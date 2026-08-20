"""층1 이름 대조 테스트 — 기획서 실측 사례를 정답으로 쓴다."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from features.name_match.logic import (
    build_index,
    convert_digit_runs,
    digits_to_sino,
    match_layer1,
    normalize_name,
)

CORPS = [
    ("00001", "십일번가"),
    ("00002", "엘지전자"),
    ("00003", "디와이오토(주)"),
    ("00010", "에스엠"),        # 동명 3곳 — 층3이 좁힌다
    ("00011", "(주)에스엠"),
    ("00012", "에스엠 "),
    ("00020", "카카오"),
    ("00021", "카카오페이"),
]
INDEX = build_index(CORPS)


def test_숫자_한글_읽기():
    assert digits_to_sino(11) == "십일"
    assert digits_to_sino(3) == "삼"
    assert digits_to_sino(24) == "이십사"
    assert digits_to_sino(111) == "백십일"


def test_11번가는_십일번가로_걸린다():
    stage, hits = match_layer1("11번가", INDEX)
    assert stage == "숫자변환" and hits == ["00001"]


def test_법인격과_공백_제거():
    assert normalize_name("(주)디와이오토") == normalize_name("디와이오토")
    assert normalize_name("엘지 전자") == "엘지전자"


def test_동명은_전부_반환한다():
    stage, hits = match_layer1("에스엠", INDEX)
    assert stage == "그대로" and sorted(hits) == ["00010", "00011", "00012"]


def test_카카오는_카카오페이를_잡지_않는다():
    _, hits = match_layer1("카카오", INDEX)
    assert hits == ["00020"]  # 부분 일치 금지 — 정확 일치만


def test_못_찾으면_빈_결과():
    stage, hits = match_layer1("배달의민족", INDEX)
    assert stage is None and hits == []


def test_숫자변환은_숫자_없으면_변형_안_만든다():
    assert convert_digit_runs("에스엠") == "에스엠"
