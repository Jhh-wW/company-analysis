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

★ 절에 화폐 금액이나 회사에 일어난 사건(회계정책 «변경» 등)이 있으면 그 절은
  아예 세지 않는다(`ACCOUNTING_POLICY_EXEMPTIONS`). 규칙이 «대상어 + 처리
  표지»만 보다 보니 「…금융부채의 잔액은 1,234백만원이며…」처럼 회사 고유
  금액이 든 문장까지 막았기 때문이다. 이 면제는 아래 재사용 규칙보다 앞선다.

★ 손실충당금·주식기준보상 «순수 회계 측정» 절은 규칙을 두 벌로 만들지 않고
  `culture_accounting_policy_problem` 을 «그대로 호출해» 쓴다. 같은 규칙을
  복사해 두면 한쪽만 고쳐져 두 잣대가 생긴다(쌍둥이 규칙은 대조 시험으로
  묶는다 — `tests/test_accounting_policy_guard.py`).

⚠️ 빈 문자열은 그 문장이 옳다는 뜻이 아니다. 주어·시점·수치 결속은 기존 의미
   검수가 그대로 판정한다. 이 모듈은 «장 계약과의 부적합»만 본다.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence

from src.features.composer.accounting_policy_constants import (
    ACCOUNTING_POLICY_BOILERPLATE,
    ACCOUNTING_POLICY_EXEMPTIONS,
    ACCOUNTING_POLICY_RULES,
    REVENUE_COMPOSITION_SUBJECT_RE,
    REVENUE_COMPOSITION_VERB_RE,
    REVENUE_RECOGNITION_ACT_RE,
    REVENUE_RECOGNITION_CRITERION_RE,
    REVENUE_RECOGNITION_EXEMPTIBLE_RULES,
    REVENUE_STREAM_EXEMPTION_NAME,
    REVENUE_STREAM_NAME_RE,
)
from src.features.composer.culture_constants import SOURCE_CLAUSE_SPLIT_RE
from src.features.composer.culture_guard import culture_accounting_policy_problem
# 같은 feature 안의 기준·조건 문법을 그대로 빌린다 — 규칙을 두 벌로 만들면
# 한쪽만 고쳐져 두 잣대가 생긴다(진행·완료·기간 한정은 scope 가드와 같은 잣대).
from src.features.composer.scope_constants import (
    COMPLETION_RE, DURATION_LIMIT_RE, PROGRESS_RE,
    RECOGNITION_CLAUSE_RE, RECOGNITION_RE,
)

#: 호출자가 주는 자기 인용 원문의 허용 꼴 — id→원문 매핑 또는 원문 나열.
SourceTexts = Mapping[str, str] | Sequence[str] | None


def _surface(text: str) -> str:
    """공백을 지운 정규화 표면형 — 다른 가드와 같은 잣대를 쓴다.

    띄어쓰기는 회사마다·문장마다 달라서 낱말 경계로 쓸 수 없다. 「현금 및
    현금성자산」과 「현금및현금성자산」이 같은 말로 읽혀야 한다.
    """

    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def _exemption(clause: str) -> str:
    """그 절에 «회사 고유» 신호가 있으면 면제 이름, 없으면 빈 문자열.

    화폐 금액이나 회사에 실제로 일어난 사건이 절 안에 있으면, 회사 이름을
    바꿔도 그대로 말이 되는 문장이 아니다 — 상용구로 셀 수 없다.
    """

    surface_clause = _surface(clause)
    for name, marker_pattern in ACCOUNTING_POLICY_EXEMPTIONS:
        if marker_pattern.search(surface_clause):
            return name
    return ""


def _source_texts(sources: SourceTexts) -> tuple[str, ...]:
    """호출자가 매핑을 주든 나열을 주든 원문 문자열만 뽑는다."""

    if sources is None or isinstance(sources, (str, bytes)):
        return ()
    if isinstance(sources, Mapping):
        return tuple(str(value) for value in sources.values())
    return tuple(str(value) for value in sources)


def _stream_names(text: str) -> frozenset[str]:
    """「<이름> 매출」 꼴로 명명된 수익원 이름의 표면형 집합."""

    return frozenset(
        _surface(match["stream"])
        for match in REVENUE_STREAM_NAME_RE.finditer(text)
        if _surface(match["stream"])
    )


def _stream_recognition_exemption(clause: str, sources: SourceTexts) -> str:
    """자기 인용에 결속된 «실제 수익원의 제공·인식 조건»이면 면제 이름을 준다.

    세 가지가 모두 확인될 때만 면제한다 — 낱말 하나로는 절대 면제하지 않는다:
      ① 절이 「<이름> 매출」로 수익원을 명명하고, 같은 자기 인용의 «수익 구성
         나열 절»이 그 수익원을 실제로 나열한다.
      ② 그 수익원의 인식 서술 절이 자기 인용에 실제로 있다(수익원 이름과
         인식·계상이 같은 절에 함께).
      ③ 절이 단 기준·조건 표지(진행기준·특례·기간 한정 등)가 «그» 인식 서술
         절 안에 그대로 있다 — 기준을 바꾸거나 조건을 늘리면 면제되지 않는다.

    판단 소절은 인식 서술의 병렬 경계(`RECOGNITION_CLAUSE_RE`)로 나눈다.
    한 문장 안에서 수익원 A의 기준을 수익원 B에 옮겨 적으면 그 소절의
    수익원 인식 절에 그 기준이 없어 면제되지 않는다.
    """

    values = _source_texts(sources)
    if not values:
        return ""
    normalized = unicodedata.normalize("NFKC", clause)
    source_clauses = [
        source_clause
        for value in values
        for source_clause in SOURCE_CLAUSE_SPLIT_RE.split(
            unicodedata.normalize("NFKC", value))
        if _surface(source_clause)
    ]
    composition_surfaces = [
        _surface(source_clause) for source_clause in source_clauses
        if REVENUE_COMPOSITION_SUBJECT_RE.search(_surface(source_clause))
        and REVENUE_COMPOSITION_VERB_RE.search(_surface(source_clause))
    ]
    if not composition_surfaces:
        return ""
    sentence_streams = _stream_names(normalized)
    if not sentence_streams:
        return ""
    for sub_clause in RECOGNITION_CLAUSE_RE.split(normalized):
        surface_sub = _surface(sub_clause)
        if not surface_sub:
            continue
        criteria = tuple(match.group() for match
                         in REVENUE_RECOGNITION_CRITERION_RE.finditer(surface_sub))
        if not RECOGNITION_RE.search(surface_sub) and not criteria:
            # 인식 서술도 기준 표지도 없는 소절(수식·접속 꼬리)은 판단 대상이 아니다.
            continue
        streams = _stream_names(sub_clause) or sentence_streams
        if not all(
            any(stream + "매출" in composition for composition in composition_surfaces)
            for stream in streams
        ):
            return ""
        recognition_surfaces = [
            _surface(source_clause) for source_clause in source_clauses
            if RECOGNITION_RE.search(_surface(source_clause))
            and any(stream in _surface(source_clause) for stream in streams)
        ]
        if not recognition_surfaces:
            return ""
        if not all(any(marker in surface for surface in recognition_surfaces)
                   for marker in criteria):
            return ""
        for basis_re in (COMPLETION_RE, PROGRESS_RE):
            if basis_re.search(surface_sub) and not any(
                basis_re.search(surface) for surface in recognition_surfaces
            ):
                return ""
        candidate_limits = {(m["value"], m["unit"])
                            for m in DURATION_LIMIT_RE.finditer(surface_sub)}
        source_limits = {(m["value"], m["unit"]) for surface in recognition_surfaces
                         for m in DURATION_LIMIT_RE.finditer(surface)}
        if not candidate_limits <= source_limits:
            return ""
    return REVENUE_STREAM_EXEMPTION_NAME


def _exemption_with_sources(clause: str, sources: SourceTexts) -> str:
    """절 자체 면제가 먼저, 그다음 자기 인용 결속 면제 — 관측과 판정이 같은 길."""

    name = _exemption(clause)
    if name:
        return name
    if sources is None:
        return ""
    rule = _rule_hit(clause)
    if rule not in REVENUE_RECOGNITION_EXEMPTIBLE_RULES:
        return ""
    # 「회계처리방법」에 걸린 절은 실제 수익 인식 서술일 때만 검토한다 — 제목
    # 꼴만 있는 절(「회계처리 기준의 방법」)에 수익원 면제를 대지 않는다.
    if not REVENUE_RECOGNITION_ACT_RE.search(_surface(clause)):
        return ""
    return _stream_recognition_exemption(clause, sources)


def _rule_hit(clause: str) -> str:
    """면제를 «무시하고» 그 절에 걸리는 규칙 범주 이름을 돌려준다.

    면제 판단과 규칙 판단을 한 함수에 섞으면, 면제가 실제로 일하고 있는지를
    시험이 확인할 길이 사라진다(규칙이 애초에 안 걸려서 통과한 것과 구분이
    안 된다). 그래서 두 판단을 따로 두고 `_matched_rule` 이 합친다.
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


def _matched_rule(clause: str, sources: SourceTexts = None) -> str:
    """그 절이 회계정책 상용구면 걸린 범주 이름, 아니면 빈 문자열.

    범주 이름은 로그·시험에서 «어느 규칙이 걸렸는지»를 되짚는 데만 쓴다.
    공개 사유 코드는 언제나 하나다.

    ★ 면제가 «먼저»다 — 재사용하는 `culture_accounting_policy_problem` 보다도
      앞이다. 금액이 든 절은 그 규칙이 무엇이든 회사 고유 사실이기 때문이다.
      그래서 8장 전용 가드와 이 가드의 답이 금액 절에서 갈릴 수 있다. 8장은
      `verify` 가 전용 경로로 따로 맡으므로 8장의 기존 경계는 그대로다.
    ★ ``sources`` 가 있으면 자기 인용 결속 면제(수익원결속)까지 검토한다 —
      호출자가 2장 본문에만 원문을 넘기므로 다른 장의 판정은 종전 그대로다.
    """

    if _exemption_with_sources(clause, sources):
        return ""
    return _rule_hit(clause)


def _clause_verdicts(text: str, sources: SourceTexts = None) -> tuple[bool, ...]:
    """비어 있지 않은 절마다 «회계정책 상용구인가»를 차례대로 돌려준다.

    빈 절(문장부호 뒤 꼬리)은 아예 세지 않는다 — 세면 「모든 절이 상용구」가
    빈 꼬리 하나 때문에 거짓이 된다.
    """

    return tuple(
        bool(_matched_rule(clause, sources))
        for clause in SOURCE_CLAUSE_SPLIT_RE.split(text)
        if _surface(clause)
    )


def accounting_policy_problem(text: str, sources: SourceTexts = None) -> str:
    """항목 전체가 회계정책 주석 상용구면 고정 사유를 돌려준다.

    Args:
        text: 검수 후보 항목 하나의 본문(여러 문장일 수 있다).
        sources: 그 후보의 자기 인용 원문(선택). 주어지면 «실제 수익원의
            제공·인식 조건» 면제를 함께 검토한다 — 4차 실측에서 2장의 정확한
            진행 원칙·1년 특례 설명이 상용구로 오제거된 것을 막는 좁은 예외다.

    Returns:
        차단이면 `ACCOUNTING_POLICY_BOILERPLATE`, 아니면 빈 문자열.

    ⚠️ 절이 하나도 없으면(빈 문자열·공백) 차단하지 않는다. `all(())` 은 참이라
       그대로 두면 빈 항목이 «전부 상용구»로 읽힌다.
    """

    verdicts = _clause_verdicts(text, sources)
    if not verdicts:
        return ""
    return ACCOUNTING_POLICY_BOILERPLATE if all(verdicts) else ""


def accounting_policy_mixed(text: str, sources: SourceTexts = None) -> bool:
    """일부 절만 회계정책 상용구인 «혼합» 항목인가.

    차단 판단이 아니다 — 진단·로그 집계에만 쓴다. 회사 고유 사실과 상용구가
    한 항목에 섞여 있다는 뜻이므로, 그 항목을 지우면 사실까지 함께 사라진다.
    """

    verdicts = _clause_verdicts(text, sources)
    return any(verdicts) and not all(verdicts)


def accounting_policy_matched_rules(text: str, sources: SourceTexts = None) -> tuple[str, ...]:
    """절마다 걸린 범주 이름(안 걸린 절은 빈 문자열) — 관측·시험용.

    어느 규칙이 어느 절을 잡았는지 확인할 수 없으면, 차단 시험이 초록이어도
    «의도한 이유로» 걸린 것인지 알 수 없다.
    """

    return tuple(
        _matched_rule(clause, sources)
        for clause in SOURCE_CLAUSE_SPLIT_RE.split(text)
        if _surface(clause)
    )


def accounting_policy_exemptions(text: str, sources: SourceTexts = None) -> tuple[str, ...]:
    """절마다 걸린 면제 이름(면제 아닌 절은 빈 문자열) — 관측·시험용.

    «통과»만 보면 규칙이 애초에 안 걸린 것인지, 걸렸는데 면제된 것인지
    구분할 수 없다. 그 둘을 가르는 값이다.
    """

    return tuple(
        _exemption_with_sources(clause, sources)
        for clause in SOURCE_CLAUSE_SPLIT_RE.split(text)
        if _surface(clause)
    )


def accounting_policy_rules_ignoring_exemptions(text: str) -> tuple[str, ...]:
    """면제가 «없었다면» 절마다 걸렸을 규칙 이름 — 관측·시험용.

    면제 시험이 지켜야 할 것은 「지금 통과한다」가 아니라 「막히던 것이
    풀렸다」이다. 이 값이 있어야 면제가 실제로 일하고 있음을 단정할 수 있다.
    """

    return tuple(
        _rule_hit(clause)
        for clause in SOURCE_CLAUSE_SPLIT_RE.split(text)
        if _surface(clause)
    )
