"""근거 대조 검증을 못 박는다 (문제로그 P-110).

★★ **이 시험들이 지키는 것은 하나다 — 「검증되지 않은 문장은 절대 안 나간다」.**
  작가 AI를 붙일 수 있는 근거가 오직 이 장치이기 때문에,
  여기가 헐거우면 **지어낸 문장이 「검증됨」 표시를 달고 나간다.**
"""

from __future__ import annotations

from typing import Any

import pytest

from src.features.writer.logic import Evidence, Sentence
from src.features.writer.verify import (
    apply_verdicts,
    build_prompt,
    make_pairs,
    verify_with_ai,
)

근거 = {
    "4-3": [
        Evidence("4-3-1", "하이브가 추구하는 전략은 음악과 기술의 접목이다.", "조각 10"),
        Evidence("4-3-2", "위버스에 AI를 적용하는 작업이 진행 중이다.", "조각 14·뉴스"),
    ]
}
쓴것 = {
    "4-3": [
        Sentence("하이브는 음악과 기술을 접목하려 한다.", "4-3-1"),
        Sentence("위버스에 AI를 넣는 작업이 진행 중이다.", "4-3-2"),
    ]
}


# ══════════════════════════════════════════════════════════
# ① 대조표
# ══════════════════════════════════════════════════════════


def test_문장에_근거_원문을_붙인다():
    쌍들 = make_pairs(쓴것, 근거)

    assert len(쌍들) == 2
    assert 쌍들[0].evidence.text.startswith("하이브가 추구하는 전략은")
    assert 쌍들[0].sentence.text == "하이브는 음악과 기술을 접목하려 한다."


def test_근거를_못_찾은_문장은_대조표에_안_넣는다():
    """★ 대조할 수 없는 문장은 통과시킬 방법이 없다."""
    쌍들 = make_pairs({"4-3": [Sentence("가", "없는번호")]}, 근거)

    assert 쌍들 == []


def test_다른_칸의_근거는_대조표에_안_넣는다():
    """앞 단계가 실수해도 검증이 다른 칸의 근거로 문장을 통과시키지 않는다."""
    쌍들 = make_pairs({"1": [Sentence("전략을 잘못 놓았다.", "4-3-1")]}, 근거)

    assert 쌍들 == []


def test_지시문이_문장과_근거를_나란히_놓는다():
    """★★ 이것이 기존 검사와의 «차이의 전부»다.

    ①-b 알맹이 검사는 원본을 일부러 안 준다. 그래서 거짓말을 못 잡는다.
    """
    프롬프트 = build_prompt(make_pairs(쓴것, 근거))

    assert "근거 원문:" in 프롬프트
    assert "문장    :" in 프롬프트
    assert "하이브가 추구하는 전략은" in 프롬프트
    assert "하이브는 음악과 기술을 접목하려 한다." in 프롬프트


@pytest.mark.parametrize(
    "문구",
    [
        "없는 정보가 한 조각이라도",
        "숫자·연도·고유명사가 근거와 다르면 거짓",
        "원인·결과·전망을 덧붙였으면 거짓",
        "애매하면 거짓으로 판정하라",
        "오직 아래 근거 원문만",
    ],
)
def test_판정_규칙이_빠지지_않았다(문구: str):
    """★ 이 규칙 하나하나가 실제 지어내기 수법에 대응한다. 빠지면 그 수법이 통과한다."""
    assert 문구 in build_prompt(make_pairs(쓴것, 근거))


# ══════════════════════════════════════════════════════════
# ② 판정 적용 — ★★ 가장 중요
# ══════════════════════════════════════════════════════════


def test_통과한_문장만_남는다():
    쌍들 = make_pairs(쓴것, 근거)

    남은, 버린 = apply_verdicts(쌍들, {"판정": [
        {"번호": 1, "근거에있다": True},
        {"번호": 2, "근거에있다": False},
    ]})

    assert [s.text for s in 남은["4-3"]] == ["하이브는 음악과 기술을 접목하려 한다."]
    assert len(버린) == 1


def test_대조가_죽으면_전부_버린다():
    """★★ 검사가 죽었는데 통과시키면 **검사가 없는 것과 같다.**

    그때 나가는 것은 「검증됐다고 표시된 거짓말」이라 아예 없는 것보다 나쁘다.
    """
    쌍들 = make_pairs(쓴것, 근거)

    남은, 버린 = apply_verdicts(쌍들, None)

    assert 남은 == {}
    assert len(버린) == 2


def test_판정에_안_실린_번호도_버린다():
    """★ 답이 반쪽이면 그 반쪽은 «검사받지 않은» 문장이다."""
    쌍들 = make_pairs(쓴것, 근거)

    남은, 버린 = apply_verdicts(쌍들, {"판정": [{"번호": 1, "근거에있다": True}]})

    assert len(남은["4-3"]) == 1
    assert len(버린) == 1


def test_답이_깨져도_안_터진다():
    쌍들 = make_pairs(쓴것, 근거)

    남은, 버린 = apply_verdicts(쌍들, {"판정": [{"번호": "하나", "근거에있다": True}]})

    assert 남은 == {}
    assert len(버린) == 2


@pytest.mark.parametrize("깨진판정", ["false", "true", 1, 0, None, {}])
def test_불리언이_아닌_판정은_거짓으로_닫는다(깨진판정: Any):
    """P-121 — 글자가 `false`여도 문자열은 참인 값이라 명시적으로 막아야 한다."""
    쌍들 = make_pairs(쓴것, 근거)

    남은, 버린 = apply_verdicts(
        쌍들,
        {"판정": [{"번호": 1, "근거에있다": 깨진판정}]},
    )

    assert 남은 == {}
    assert len(버린) == 2


def test_중복된_판정번호는_거짓으로_닫는다():
    """같은 문장을 참·거짓 둘 다 답한 결과는 어느 쪽도 믿을 수 없다."""
    쌍들 = make_pairs(쓴것, 근거)

    남은, 버린 = apply_verdicts(
        쌍들,
        {"판정": [
            {"번호": 1, "근거에있다": True},
            {"번호": 1, "근거에있다": True},
        ]},
    )

    assert 남은 == {}
    assert len(버린) == 2


@pytest.mark.parametrize("payload", ["깨진 답", [], {"판정": "목록 아님"}])
def test_전체_답_모양이_깨지면_전부_버린다(payload: Any):
    쌍들 = make_pairs(쓴것, 근거)

    남은, 버린 = apply_verdicts(쌍들, payload)

    assert 남은 == {}
    assert len(버린) == 2


# ══════════════════════════════════════════════════════════
# ③ 통째로
# ══════════════════════════════════════════════════════════


def test_대조할_것이_없으면_AI를_안_부른다():
    불렀나 = []

    남은, 기록 = verify_with_ai(
        lambda p, s: (불렀나.append(1), ({}, {}))[1], written={}, evidence=근거
    )

    assert 남은 == {}
    assert 불렀나 == []


def test_통째로_돌린다():
    def ask(prompt, schema):
        return {"판정": [
            {"번호": 1, "근거에있다": True},
            {"번호": 2, "근거에있다": False},
        ]}, {"in": 500, "out": 40}

    남은, 기록 = verify_with_ai(ask, written=쓴것, evidence=근거)

    assert 기록["대조"] == 2
    assert 기록["통과"] == 1
    assert 기록["버림"] == 1


def test_버린_문장을_기록에_남긴다():
    """★ 정본 「조용한 누락 금지」 — 왜 빠졌는지 남지 않으면 고칠 수 없다."""
    남은, 기록 = verify_with_ai(
        lambda p, s: ({"판정": [{"번호": 1, "근거에있다": False},
                               {"번호": 2, "근거에있다": False}]}, {}),
        written=쓴것, evidence=근거,
    )

    assert len(기록["버린문장"]) == 2
    assert "하이브는 음악과 기술을" in 기록["버린문장"][0]


def test_AI가_죽으면_사유가_남는다():
    남은, 기록 = verify_with_ai(
        lambda p, s: (None, {"error": "APIError"}), written=쓴것, evidence=근거
    )

    assert 남은 == {}
    assert 기록["오류"] == "APIError"
    assert "전부" in 기록["비고"]
