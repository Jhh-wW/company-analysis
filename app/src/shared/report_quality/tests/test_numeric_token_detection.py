"""한글 일반어를 수치 claim으로 오인해 정상 문장을 버리지 않는 경계."""

from __future__ import annotations

import pytest

from src.shared.report_quality.assessment import has_public_numeric_token


@pytest.mark.parametrize(
    "text",
    (
        "계약 기간과 수익 배분 같은 세부 조건은 공개되지 않았다.",
        "음악 판매 한 번으로 끝나지 않는 반복 수익 구조를 지향한다.",
        "회사는 한 개념을 여러 서비스에 적용했다.",
        "두 번째 이익축을 준비하고 있다.",
        "회사는 사원을 새로 채용했다.",
        "조직의 일원으로 프로젝트에 참여했다.",
        "프로젝트 조원으로 역할을 나눴다.",
        "대표는 임직원에게 세배를 했다.",
        "협력사와 한배를 탔다.",
    ),
)
def test_일반단어와_관용횟수는_구조화수치로_오인하지않는다(text: str) -> None:
    assert has_public_numeric_token(text) is False


@pytest.mark.parametrize(
    "text",
    (
        "매출은 이십오 퍼센트 증가했다.",
        "매출은 두 배로 늘었다.",
        "신규 계약은 십 건으로 집계됐다.",
        "임직원은 삼백 명이다.",
        "투자액은 십억원이다.",
        "투자액은 일조원이다.",
        "수수료는 일 원이다.",
        "2025년 매출은 24.28% 증가했다.",
    ),
)
def test_명시적인_한글수량과_아라비아숫자는_결속대상이다(text: str) -> None:
    assert has_public_numeric_token(text) is True
