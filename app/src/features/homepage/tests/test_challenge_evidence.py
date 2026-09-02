"""당면과제 issue·response 관계 판정을 잠근다.

원본 branch(claude/evidence-8da84a36)에는 challenge_evidence.py 전용
단위 시험이 없었다(wide_fragments를 통한 간접 노출만 있었다). 이 조각을
독립 모듈로 먼저 들여오면서 그 계약을 직접 잠그는 시험을 새로 쓴다.
"""

from __future__ import annotations

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


def test_같은_범위에_문제와_회사행동이_함께_있으면_직접_관계다() -> None:
    evidence = classify_challenge_evidence(
        ("원가 부담을 줄이기 위해 공급처를 다변화했습니다.",)
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
