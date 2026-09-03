"""당면과제 issue·response 관계 판정을 잠근다.

이 판정은 예전에 wide_fragments를 통한 간접 노출로만 시험되고 있었다.
독립 모듈로 분리하면서 그 계약을 직접 잠그는 시험을 둔다.
"""

from __future__ import annotations

import pytest

from src.features.homepage.challenge_evidence import classify_challenge_evidence


def test_구체적_부정_신호가_있으면_issue로_판정한다() -> None:
    evidence = classify_challenge_evidence(
        ("원자재 가격 상승으로 원가 부담이 커졌습니다.",)
    )

    assert evidence.has_issue(0)


def test_고객_문제_해결_광고는_issue도_response도_아니다() -> None:
    evidence = classify_challenge_evidence(("우리 제품은 고객의 문제를 해결합니다.",))

    assert not evidence.has_issue(0)
    assert not evidence.has_response(0)


def test_같은_범위에_문제와_연결어로_이어진_회사행동이_있으면_직접_관계다() -> None:
    evidence = classify_challenge_evidence(
        ("원자재 가격 상승으로 원가 부담이 커졌고, 이를 해결하기 위해 공급처를 다변화했습니다.",)
    )

    assert evidence.has_issue(0)
    assert evidence.has_response(0)


def test_앞_범위_문제와_연결어로_이어진_행동만_response로_인정한다() -> None:
    evidence = classify_challenge_evidence(
        (
            "원자재 가격 상승으로 원가 부담이 커졌습니다.",
            "이에 대응해 공급처를 다변화했습니다.",
        )
    )

    assert evidence.has_issue(0)
    assert evidence.has_response(1)


def test_연결어_없이_먼_범위의_행동은_response로_인정하지_않는다() -> None:
    evidence = classify_challenge_evidence(
        (
            "원자재 가격 상승으로 원가 부담이 커졌습니다.",
            "회사 소개",
            "매년 신규 인력을 확대 채용합니다.",
        )
    )

    assert evidence.has_issue(0)
    assert not evidence.has_response(2)


def test_문제_신호_없이_행동만_있으면_response로_인정하지_않는다() -> None:
    evidence = classify_challenge_evidence(("생산 공정을 자동화했습니다.",))

    assert not evidence.has_issue(0)
    assert not evidence.has_response(0)


def test_고객사의_부담은_회사_자신의_issue가_아니다() -> None:
    evidence = classify_challenge_evidence(("고객사의 원가 부담이 커졌습니다.",))

    assert not evidence.has_issue(0)


def test_부담이_줄었다는_문장은_issue가_아니라_개선이다() -> None:
    evidence = classify_challenge_evidence(("원가 부담이 완화되었습니다.",))

    assert not evidence.has_issue(0)


def test_실적_지표와_함께_있는_하락만_issue다() -> None:
    evidence = classify_challenge_evidence(("영업이익이 하락했습니다.",))

    assert evidence.has_issue(0)


def test_실적_지표_없이_하락_한_단어만으로는_issue가_아니다() -> None:
    evidence = classify_challenge_evidence(("금리가 하락했습니다.",))

    assert not evidence.has_issue(0)


# ══════════════════════════════════════════════════════════
# 신호 낱말이 부정·해소 서술로 되돌려지면 issue가 아니다.
# ``_has_concrete_issue``는 부분 문자열 매칭이라 방향을 모른다 —
# 「차질이 발생하지 않았다」처럼 신호 뒤에 명시적 부정·해소가 있으면
# 회사가 실제로 겪는 당면과제가 아니라 «문제가 없다»는 선언이다.
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "text",
    [
        "차질이 발생하지 않았습니다.",
        "공급망 부족 문제는 없었습니다.",
        "원가 압박이 해소되었습니다.",
        "생산 지연은 미발생했습니다.",
        "자금 조달 어려움은 사실이 아닙니다.",
    ],
)
def test_신호_낱말이_부정되거나_해소되면_issue가_아니다(text: str) -> None:
    evidence = classify_challenge_evidence((text,))

    assert not evidence.has_issue(0)


def test_부정_마커가_다음_문장에_있으면_이_문장의_issue는_그대로_유지된다() -> None:
    evidence = classify_challenge_evidence(
        ("원가 부담이 커졌습니다. 다른 문제는 해소되었습니다.",)
    )

    assert evidence.has_issue(0)
