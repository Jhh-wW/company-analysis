"""본문에 박힌 인용 번호를 «모양만» 바꾸는 분해기를 못 박는다.

★ 왜 필요한가 (사용자 신고) — v2 본문은 `[1]`이 문자열 안에 박힌 채 템플릿이
  그대로 인쇄해, 본문과 «같은 크기»의 대괄호 숫자가 문장마다 나왔다. v1은 같은
  번호를 `.ref` 작은 위첨자 링크로 낸다. 두 화면이 갈려 있었던 것이라
  「가독성이 떨어진다」는 신고의 큰 부분이 여기였다.
★ 이 분해기는 번호를 새로 매기거나 없애지 않는다. 부록과 1:1로 이미 맞춰진
  값이므로 «모양만» 바꾼다.
"""

from __future__ import annotations

from src.core.citations import split_citation_markers


def _rebuilt(text: str) -> str:
    """분해한 조각을 다시 이어 붙이면 원문과 같아야 한다(글자 손실 없음)."""
    return "".join(
        part.text if not part.number else f"[{part.number}]"
        for part in split_citation_markers(text)
    )


def test_글과_번호를_나눈다():
    parts = split_citation_markers("회사는 시트를 가공한다. [1] 끝.")

    assert [(p.number, p.text) for p in parts] == [
        (0, "회사는 시트를 가공한다. "),
        (1, ""),
        (0, " 끝."),
    ]


def test_번호가_붙어_있어도_각각_나눈다():
    parts = split_citation_markers("문장이다. [1][8]")

    assert [p.number for p in parts if p.number] == [1, 8]


def test_번호가_없으면_글_한_덩어리다():
    parts = split_citation_markers("번호 없는 문장이다. — 해석")

    assert len(parts) == 1
    assert parts[0].number == 0


def test_숫자가_아닌_대괄호는_건드리지_않는다():
    """회사명·제품명에 들어간 대괄호를 인용으로 오인하면 글이 사라진다."""
    원문 = "제품명은 [프리미엄]이다. [3]"
    parts = split_citation_markers(원문)

    assert [p.number for p in parts if p.number] == [3]
    assert "[프리미엄]" in "".join(p.text for p in parts)


def test_글자를_잃지_않는다():
    for 원문 in (
        "회사는 시트를 가공한다. [1][8] 이는 성장으로 읽힌다. — 해석",
        "번호 없는 문장.",
        "[12] 문장이 번호로 시작한다.",
        "문장이 번호로 끝난다. [7]",
        "",
    ):
        assert _rebuilt(원문) == 원문


def test_빈_문자열도_안전하다():
    assert split_citation_markers("") == ()
    assert split_citation_markers(None) == ()
