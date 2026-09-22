"""회계정책 주석 상용구가 «모든 장»의 본문을 채우는 것을 막는다.

8장(culture)에는 `culture_guard.culture_accounting_policy_problem` 전용 경로가
이미 있었지만 나머지 8개 장에는 아무 검사가 없었다. 그 결과 실제 산출 PDF에서
9장 「회사가 밝힌 차별점」이 금융자산 측정·정부보조금·현금성자산 정의·대손충당금
4문장으로, 5장 「당면 과제」가 이연법인세 미적용 문장으로, 1·2·5장이 수익인식
기준·재고자산 총평균법·유동성 관리 상용구로 채워졌다.

★ 판단 단위는 «절»이다 — `SOURCE_CLAUSE_SPLIT_RE` 로 나눈 한 문장. 항목의
  «모든» 절이 회계정책 상용구일 때만 차단한다. 일부 절만 상용구인 혼합 항목을
  통째로 지우면 같은 항목에 실린 회사 고유 사실까지 함께 사라지기 때문이다.
  혼합 항목은 `accounting_policy_mixed` 로 관측만 한다(차단 아님).

★ 손실충당금·주식기준보상 «순수 회계 측정» 절은 규칙을 두 벌로 만들지 않고
  `culture_accounting_policy_problem` 을 «그대로 호출해» 쓴다. 같은 규칙을
  복사해 두면 한쪽만 고쳐져 두 잣대가 생긴다(쌍둥이 규칙은 대조 시험으로
  묶는다 — `tests/test_accounting_policy_guard.py`).

⚠️ 빈 문자열은 그 문장이 옳다는 뜻이 아니다. 주어·시점·수치 결속은 기존 의미
   검수가 그대로 판정한다. 이 모듈은 «장 계약과의 부적합»만 본다.
"""

from __future__ import annotations

import unicodedata

from src.features.composer.accounting_policy_constants import (
    ACCOUNTING_POLICY_BOILERPLATE,
    ACCOUNTING_POLICY_RULES,
)
from src.features.composer.culture_constants import SOURCE_CLAUSE_SPLIT_RE
from src.features.composer.culture_guard import culture_accounting_policy_problem


def _surface(text: str) -> str:
    """공백을 지운 정규화 표면형 — 다른 가드와 같은 잣대를 쓴다.

    띄어쓰기는 회사마다·문장마다 달라서 낱말 경계로 쓸 수 없다. 「현금 및
    현금성자산」과 「현금및현금성자산」이 같은 말로 읽혀야 한다.
    """

    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def _matched_rule(clause: str) -> str:
    """그 절이 회계정책 상용구면 걸린 범주 이름, 아니면 빈 문자열.

    범주 이름은 로그·시험에서 «어느 규칙이 걸렸는지»를 되짚는 데만 쓴다.
    공개 사유 코드는 언제나 하나다.
    """

    surface_clause = _surface(clause)
    if not surface_clause:
        return ""
    # 손실충당금·주식기준보상 순수 측정 절은 기존 규칙을 그대로 재사용한다.
    # (culture 장 전용이 아니라 «순수 회계 측정»을 가리는 규칙이라 장과 무관하다.)
    if culture_accounting_policy_problem(clause):
        return "순수회계측정"
    for name, subject_pattern, treatment_pattern in ACCOUNTING_POLICY_RULES:
        if (subject_pattern.search(surface_clause)
                and treatment_pattern.search(surface_clause)):
            return name
    return ""


def _clause_verdicts(text: str) -> tuple[bool, ...]:
    """비어 있지 않은 절마다 «회계정책 상용구인가»를 차례대로 돌려준다.

    빈 절(문장부호 뒤 꼬리)은 아예 세지 않는다 — 세면 「모든 절이 상용구」가
    빈 꼬리 하나 때문에 거짓이 된다.
    """

    return tuple(
        bool(_matched_rule(clause))
        for clause in SOURCE_CLAUSE_SPLIT_RE.split(text)
        if _surface(clause)
    )


def accounting_policy_problem(text: str) -> str:
    """항목 전체가 회계정책 주석 상용구면 고정 사유를 돌려준다.

    Args:
        text: 검수 후보 항목 하나의 본문(여러 문장일 수 있다).

    Returns:
        차단이면 `ACCOUNTING_POLICY_BOILERPLATE`, 아니면 빈 문자열.

    ⚠️ 절이 하나도 없으면(빈 문자열·공백) 차단하지 않는다. `all(())` 은 참이라
       그대로 두면 빈 항목이 «전부 상용구»로 읽힌다.
    """

    verdicts = _clause_verdicts(text)
    if not verdicts:
        return ""
    return ACCOUNTING_POLICY_BOILERPLATE if all(verdicts) else ""


def accounting_policy_mixed(text: str) -> bool:
    """일부 절만 회계정책 상용구인 «혼합» 항목인가.

    차단 판단이 아니다 — 진단·로그 집계에만 쓴다. 회사 고유 사실과 상용구가
    한 항목에 섞여 있다는 뜻이므로, 그 항목을 지우면 사실까지 함께 사라진다.
    """

    verdicts = _clause_verdicts(text)
    return any(verdicts) and not all(verdicts)


def accounting_policy_matched_rules(text: str) -> tuple[str, ...]:
    """절마다 걸린 범주 이름(안 걸린 절은 빈 문자열) — 관측·시험용.

    어느 규칙이 어느 절을 잡았는지 확인할 수 없으면, 차단 시험이 초록이어도
    «의도한 이유로» 걸린 것인지 알 수 없다.
    """

    return tuple(
        _matched_rule(clause)
        for clause in SOURCE_CLAUSE_SPLIT_RE.split(text)
        if _surface(clause)
    )
