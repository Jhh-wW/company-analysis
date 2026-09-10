"""사업 사례·미래 목표를 조직문화로 격상하는 좁은 경계를 제한한다.

빈 문자열은 문화가 검증됐다는 뜻이 아니다. 공식 문화·구체적인 절차가 있는
자료의 주어, 시점, 주장 범주 일치는 기존 의미 검수에서 판단해야 한다.
"""

from collections.abc import Mapping, Sequence
import unicodedata

from src.features.composer.culture_constants import (
    CULTURE_ACCOUNTING_COMPENSATION_COST_RE,
    CULTURE_ACCOUNTING_COMPENSATION_GOVERNANCE_NEGATION_RE,
    CULTURE_ACCOUNTING_COMPENSATION_GOVERNANCE_VERB_RE,
    CULTURE_ACCOUNTING_COMPENSATION_REMEASURE_RE,
    CULTURE_ACCOUNTING_COMPENSATION_SETTLEMENT_RE,
    CULTURE_ACCOUNTING_COMPENSATION_TREATMENT_RE,
    CULTURE_ACCOUNTING_CREDIT_CHARACTERISTIC_RE,
    CULTURE_ACCOUNTING_GOVERNANCE_NEGATION_RE,
    CULTURE_ACCOUNTING_GOVERNANCE_VERB_RE,
    CULTURE_ACCOUNTING_OVERDUE_BASIS_RE,
    CULTURE_ACCOUNTING_POLICY_MISPLACED,
    CULTURE_ACCOUNTING_RECOGNITION_RE,
    CULTURE_ATTRIBUTION_RE,
    CULTURE_EVIDENCE_SCOPE_MISMATCH,
    CULTURE_FINANCIAL_RISK_CATEGORY_RE,
    CULTURE_FINANCIAL_RISK_DERIVATIVE_RE,
    CULTURE_FINANCIAL_RISK_EXPOSURE_RE,
    CULTURE_FINANCIAL_RISK_GOAL_CONTEXT_RE,
    CULTURE_FINANCIAL_RISK_GOVERNANCE_NEGATION_RE,
    CULTURE_FINANCIAL_RISK_GOVERNANCE_VERB_RE,
    CULTURE_FINANCIAL_RISK_ORG_ACTOR_RE,
    CULTURE_FINANCIAL_RISK_POLICY_GOAL_RE,
    CULTURE_FINANCIAL_RISK_RULE_GOVERNANCE_NEGATION_RE,
    CULTURE_FINANCIAL_RISK_RULE_GOVERNANCE_VERB_RE,
    CULTURE_FINANCIAL_RISK_RULE_RE,
    CULTURE_FINANCIAL_RISK_SCOPE_MISPLACED,
    CULTURE_FLOW_CELL_COUNT,
    CURRENT_CULTURE_DENIAL_RE,
    EXPLICIT_CULTURE_RE,
    FLOW_GOAL_QUALIFIER_RE,
    GOAL_NEGATION_RE,
    ORGANIZATIONAL_CLAIM_RE,
    OTHER_ORGANIZATION_RE,
    SOURCE_CLAUSE_SPLIT_RE,
    SOURCE_GOAL_RE,
    SOURCE_UNAVAILABLE_RE,
)


def _surface(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def culture_problem(text: str, sources_mapping: Mapping[str, str]) -> str:
    """문화 장 후보의 근거 범위 확대가 보일 때 고정 사유를 반환한다.

    호출자는 culture 장/슬롯 또는 원래 장이 생략된 요약 후보와 인용한
    원문만 전달한다. 공시라는 출처 유형만으로 사업 실적을 공식문화로
    인정하지 않는다. 구체적 절차·문화가 있는 원문의 적절한 바꿔쓰기는
    이 어휘 검사만으로 거절하지 않는다.
    """
    candidate = _surface(text)
    if not (ORGANIZATIONAL_CLAIM_RE.search(candidate)
            and CULTURE_ATTRIBUTION_RE.search(candidate)):
        return ""

    for source in sources_mapping.values():
        if candidate and candidate in _surface(source):
            return ""
        for clause in SOURCE_CLAUSE_SPLIT_RE.split(source):
            normalized = _surface(clause)
            if (EXPLICIT_CULTURE_RE.search(normalized)
                    and not SOURCE_UNAVAILABLE_RE.search(normalized)):
                # 이는 승인 조건이 아니다. 명시된 문화·절차가 후보의 바로 그
                # 주장인지와 출처 공식성은 같은 기존 검수 호출이 판정한다.
                return ""
    return CULTURE_EVIDENCE_SCOPE_MISMATCH


def culture_accounting_policy_problem(text: str) -> str:
    """순수 회계 인식·측정 정책이 culture 장에 들어간 경우만 좁게 차단한다.

    ★ 반드시 culture 장 후보(요약이 아니라 이 장 원문)일 때만 호출한다.
    culture_problem과 달리 조직문화 추론 어휘(«조직문화를 보여준다» 같은
    귀속 표현)가 있는지는 보지 않는다 — 반대로 매출채권·계약자산의 기대
    신용손실 간편법, 신용위험 특성·연체일 기준 손실충당금 산출처럼 순수
    회계 인식·측정 어휘가 있으면 조직문화 주장이 전혀 없어도 걸린다.

    인용 원문과 후보 문장이 정확히 일치해도 판정은 바뀌지 않는다 — 그래서
    이 함수는 sources를 받지 않는다. 문제는 사실 정확성이 아니라 "인재상과
    일하는 방식" 장 계약과의 부적합이다.

    ★ "절차 문장은 회계 어휘를 쓰지 않으므로 걸리지 않는다"는 항상 참이
    아니다 — 재무팀이 손실충당금 산출을 검토하고 이사회가 승인한다처럼
    실제 담당·검토·승인·감독 절차가 같은 문장 안에서 그 회계처리와 결속될
    수 있다. 그래서 이 함수는 «절» 단위(마침표 등으로 나눈 한 문장) 안에서만
    판단한다: 같은 절에 인식·측정 어휘가 다 있어도, 그 절 안에 부정되지
    않은 검토/승인/감독 절차 동사가 함께 있으면 차단하지 않는다. 다른(뒤)
    문장에 있는 이사회·담당자 언급이나, 부정된 승인("승인을 받지 못한다")은
    이 절차 결속으로 인정하지 않는다 — 관계없는 절이나 부정된 절차로
    면제를 만들 수 없다. 이사회·담당자 같은 명사가 있다는 것만으로도
    면제하지 않는다 — 반드시 검토/승인/감독 동사가 있어야 한다.

    이사회 감독·재무 부서 주관·자본관리 목표와 부채비율 관리·현금흐름
    모니터링·회사 위험관리 절차처럼 회계 인식·측정 어휘 자체가 없는 절은
    애초에 이 판단의 대상이 아니다. 재무 단어가 있다는 이유만으로는 절대
    차단하지 않는다 — 인식 어휘와 측정 기준 어휘가 같은 절에 함께 있을
    때만 차단 대상이 되고, 그 절에 결속된 절차가 있으면 다시 보존한다.

    ★ 손실충당금 묶음과 별도로, 순수 주식기준보상 «회계 인식·측정» 절
    (현금결제방식 + 회계처리 + 부채의 공정가치 재측정 + 보상원가가 같은
    절에 전부 있는 경우)도 같은 사유(CULTURE_ACCOUNTING_POLICY_MISPLACED)로
    걸린다 — 새 사유를 만들지 않는다. 이 절의 면제 판단은
    CULTURE_ACCOUNTING_COMPENSATION_GOVERNANCE_VERB_RE(활용형만 인정,
    "감독당국" 같은 명사는 제외)를 쓴다 — 손실충당금 블록의 느슨한 버전을
    재사용하지 않는다. "…보상을 관리하고 있다"의 "관리"는 이 동사 목록에
    없으므로 그 수사만으로는 여전히 면제되지 않는다.
    """
    return (CULTURE_ACCOUNTING_POLICY_MISPLACED
            if (_pure_accounting_measurement_clauses(text)
                or _pure_compensation_measurement_clauses(text))
            else "")


def _pure_accounting_measurement_clauses(text: str) -> tuple[str, ...]:
    """«순수 회계 인식·측정»인 절만 표면형으로 모아 준다.

    한 절이 인식 어휘와 측정 기준 어휘(신용위험 특성·연체일)를 모두 담고 있으면
    회계 서술이고, 같은 절 안에 부정되지 않은 검토·승인·감독이 결속돼 있으면
    그 절차 서술이 우선하므로 «순수»가 아니다 — 그 절은 여기서 빠진다.

    ★ 판단 경계는 «같은 절»이다. 뒤 문장의 이사회 언급이나 부정된 승인으로는
      면제가 만들어지지 않는다. 이 규칙은 산문·도식이 똑같이 쓴다.
    """

    found: list[str] = []
    for clause in SOURCE_CLAUSE_SPLIT_RE.split(text):
        surface_clause = _surface(clause)
        if not surface_clause:
            continue
        if not (CULTURE_ACCOUNTING_RECOGNITION_RE.search(surface_clause)
                and CULTURE_ACCOUNTING_CREDIT_CHARACTERISTIC_RE.search(surface_clause)
                and CULTURE_ACCOUNTING_OVERDUE_BASIS_RE.search(surface_clause)):
            continue
        governance_bound = (
            CULTURE_ACCOUNTING_GOVERNANCE_VERB_RE.search(surface_clause)
            and not CULTURE_ACCOUNTING_GOVERNANCE_NEGATION_RE.search(surface_clause)
        )
        if governance_bound:
            continue
        found.append(surface_clause)
    return tuple(found)


def _pure_compensation_measurement_clauses(text: str) -> tuple[str, ...]:
    """«순수 주식기준보상 회계 인식·측정»인 절만 표면형으로 모아 준다.

    한 절이 현금결제(방식)·회계처리·부채의 공정가치 재측정·보상원가 네
    어휘를 모두 담고 있으면 회계 서술이고, 같은 절 안에 부정되지 않은
    검토·승인·감독이 결속돼 있으면 그 절차 서술이 우선하므로 «순수»가
    아니다 — 그 절은 여기서 빠진다.

    ★ 넷 중 하나라도 빠지면 대상이 아니다 — «보상»·«주식»·«공정가치» 같은
      낱말 하나만으로는 걸리지 않는다. 성과평가 대상기간·지급기준일·
      보상위원회 승인 같은 실제 직원 보상제도 서술은 이 네 어휘를 같은
      절에 전부 담지 않으므로 애초에 대상이 아니다.
    ★ 판단 경계는 «같은 절»이다. 면제(같은 절 결속 절차) 판단은 이 절 전용
      CULTURE_ACCOUNTING_COMPENSATION_GOVERNANCE_VERB_RE/NEGATION_RE를 쓴다
      (손실충당금 블록의 버전은 재사용하지 않는다). "감독당국"·"승인권한"·
      "감독의무"·"승인한도"처럼 활용되지 않은 명사만으로는 면제하지 않고,
      "승인한 바 없다"·"검토한 적이 없다"처럼 부정된 절차도 면제로 인정하지
      않는다 — "감독을 받는다"·"승인을 받는다"·"검토하고 승인한다"처럼
      실제 결속된 절차는 그대로 보존한다.
    """

    found: list[str] = []
    for clause in SOURCE_CLAUSE_SPLIT_RE.split(text):
        surface_clause = _surface(clause)
        if not surface_clause:
            continue
        if not (CULTURE_ACCOUNTING_COMPENSATION_SETTLEMENT_RE.search(surface_clause)
                and CULTURE_ACCOUNTING_COMPENSATION_TREATMENT_RE.search(surface_clause)
                and CULTURE_ACCOUNTING_COMPENSATION_REMEASURE_RE.search(surface_clause)
                and CULTURE_ACCOUNTING_COMPENSATION_COST_RE.search(surface_clause)):
            continue
        governance_bound = (
            CULTURE_ACCOUNTING_COMPENSATION_GOVERNANCE_VERB_RE.search(surface_clause)
            and not CULTURE_ACCOUNTING_COMPENSATION_GOVERNANCE_NEGATION_RE.search(surface_clause)
        )
        if governance_bound:
            continue
        found.append(surface_clause)
    return tuple(found)


def _recognition_terms(surface_text: str) -> frozenset[str]:
    """그 문자열이 실제로 쓴 회계 인식 표현만 모은다."""

    return frozenset(match.group(0) for match
                     in CULTURE_ACCOUNTING_RECOGNITION_RE.finditer(surface_text))


def culture_financial_risk_goal_problem(text: str) -> str:
    """문화 본문의 재무위험 목표·노출·관리규정 설명을 제외한다.

    세 모양을 같은 사유로 잡는다 — 일반 목표, 위험 노출, 그리고 신용·유동성·
    환위험 같은 «위험 범주 + 관리 규정» 서술. 앞의 둘은 같은 절에 부정되지
    않은 업무 절차 «동사»가 있으면 보존하고, 세 번째는 그 절이 조직 주체와
    절차 동사를 «둘 다» 말할 때만 보존한다(상수 주석의 실측 근거). 절차의
    주체나 사실성은 기존 인용 검수가 맡으며 이 함수는 장 배치만 검사한다.
    """
    return (CULTURE_FINANCIAL_RISK_SCOPE_MISPLACED
            if (_pure_financial_risk_goal_clauses(text)
                or _financial_risk_rule_clauses(text)) else "")


def _financial_risk_rule_clauses(text: str) -> tuple[str, ...]:
    """«재무위험 범주 + 관리 규정» 절만 표면형으로 모아 준다.

    한 절이 재무·금융 위험의 범주와 그 위험을 다루는 규정·정책·원칙·절차·
    기준·체계를 함께 말하면 이 장의 계약(인재상·조직문화·일하는 방식)과 맞지
    않는 재무위험 관리 서술로 본다.

    ★ 면제는 «누가 + 무엇을 한다»가 같은 절에 다 있을 때만 만들어진다.
      조직 주체(이사회·위원회·담당부서·팀 등)와 부정되지 않은 절차 동사가
      둘 다 있어야 한다. 동사만으로 면제하지 않는 이유는 실측에 있다 —
      «매 보고기간말에 손상여부를 검토하는»의 «검토»는 사람·조직이 하는
      승인 절차가 아니라 회계 동작인데, 동사만 보면 면제가 만들어진다.
    ★ 절차 동사는 이 블록 «전용» 목록을 쓴다
      (CULTURE_FINANCIAL_RISK_RULE_GOVERNANCE_VERB_RE). 심의·의결·수립·운영·
      점검·관리까지 담아 «누가 맡는지»를 말한 제도 문장을 보존하되, 조직
      주체를 요구하지 않는 목표/노출 블록의 목록은 넓히지 않는다.
    ★ «회사»·«당사»·«관리주체» 같은 일반 명사는 주체로 세지 않는다. 그런
      말은 거의 모든 문장에 있어서 인정하면 이 규칙이 통째로 꺼진다.
    ★ 판단 경계는 «같은 절»이다 — culture_accounting·목표/노출 블록과 같다.
      뒤 절의 이사회 언급이나 부정된 승인으로는 면제가 만들어지지 않는다.
    """

    found: list[str] = []
    for clause in SOURCE_CLAUSE_SPLIT_RE.split(text):
        surface_clause = _surface(clause)
        if not surface_clause:
            continue
        if not (CULTURE_FINANCIAL_RISK_CATEGORY_RE.search(surface_clause)
                and CULTURE_FINANCIAL_RISK_RULE_RE.search(surface_clause)):
            continue
        governance_bound = (
            CULTURE_FINANCIAL_RISK_ORG_ACTOR_RE.search(surface_clause)
            and CULTURE_FINANCIAL_RISK_RULE_GOVERNANCE_VERB_RE.search(surface_clause)
            and not CULTURE_FINANCIAL_RISK_RULE_GOVERNANCE_NEGATION_RE.search(
                surface_clause
            )
        )
        if governance_bound:
            continue
        found.append(surface_clause)
    return tuple(found)


def _pure_financial_risk_goal_clauses(text: str) -> tuple[str, ...]:
    """«순수 재무위험 목표·노출» 절만 표면형으로 모아 준다.

    ★ 판단 경계는 «같은 절»이다. 뒤 절의 이사회 언급이나 부정된 승인으로는
      면제가 만들어지지 않는다 — culture_accounting과 같은 경계 규칙.
    """

    found: list[str] = []
    for clause in SOURCE_CLAUSE_SPLIT_RE.split(text):
        surface_clause = _surface(clause)
        if not surface_clause:
            continue
        is_goal = (CULTURE_FINANCIAL_RISK_POLICY_GOAL_RE.search(surface_clause)
                   and CULTURE_FINANCIAL_RISK_GOAL_CONTEXT_RE.search(surface_clause))
        is_exposure = (CULTURE_FINANCIAL_RISK_EXPOSURE_RE.search(surface_clause)
                       or CULTURE_FINANCIAL_RISK_DERIVATIVE_RE.search(surface_clause))
        if not (is_goal or is_exposure):
            continue
        governance_bound = (
            CULTURE_FINANCIAL_RISK_GOVERNANCE_VERB_RE.search(surface_clause)
            and not CULTURE_FINANCIAL_RISK_GOVERNANCE_NEGATION_RE.search(surface_clause)
        )
        if governance_bound:
            continue
        found.append(surface_clause)
    return tuple(found)


def culture_accounting_flow_problem(
    cells: Sequence[str], sources_mapping: Mapping[str, str]
) -> str:
    """축약된 culture 행이 «자기 인용»의 순수 회계 측정 정책을 옮겨 적었을 때만 막는다.

    ★ 왜 산문 검사만으로는 못 잡나 — 칸은 원문을 줄여 적는다. 실제 반례의 세 칸
      「신용위험 관리 / 전체기간 기대신용손실 간편법 적용 / (빈칸)」에는 「신용위험
      특성」도 「연체일」도 없어서, 칸만 보는 세 표지 결합 조건이 성립하지 않는다.
      그래서 «칸»이 아니라 «그 행이 인용한 원문»에서 순수 회계 절을 찾고, 칸이 바로
      그 절의 인식 표현을 옮겼을 때만 막는다(source-aware).

    ★ 면제는 «원문 쪽»에서만 만들어진다. 그 절 안에 부정되지 않은 검토·승인·감독이
      결속돼 있으면 애초에 순수 회계 절이 아니어서 여기까지 오지 않는다. 반대로
      후보가 원문에 없는 검토·승인을 칸에 덧붙여도 면제되지 않는다 — 칸의 낱말은
      면제 근거가 아니다. 다른 절의 절차나 부정된 승인으로도 면제되지 않는다.

    ★ 결속 조건: 칸이 쓴 인식 표현이 «그 절»에도 있어야 한다. 다른 회계 문장을
      가져다 붙인 행이 우연히 걸리지 않게 하는 좁힘이다.

    ★ 회사명·조각 id·원문 전체 부분문자열 면제·글자수 기준을 쓰지 않는다.
      다른 장의 정상 회계 설명은 이 함수를 지나가지 않는다 — 호출자가 culture
      행일 때만 부른다. 빈 문자열은 그 행이 옳다는 뜻이 아니다.
    """

    if isinstance(cells, (str, bytes)) or len(cells) != CULTURE_FLOW_CELL_COUNT:
        return ""  # 형식 검사는 기존 도식 검증기가 담당한다.
    stated = _surface(" ".join(cells))
    stated_terms = _recognition_terms(stated)
    if not stated_terms:
        # 인식 표현이 없으면 회계 측정 정책을 옮긴 행이 아니다. 「재무」라는
        # 낱말이나 위험 관리 절차만 적은 행은 여기서 그대로 지나간다.
        return ""
    for source in sources_mapping.values():
        for clause in _pure_accounting_measurement_clauses(source):
            if stated_terms & _recognition_terms(clause):
                return CULTURE_ACCOUNTING_POLICY_MISPLACED
    return ""


def culture_flow_problem(cells: Sequence[str], sources_mapping: Mapping[str, str]) -> str:
    """사업목표를 현재의 일하는 원칙으로 격상한 culture 행만 제한한다.

    culture 장에서만, 원래 순서의 세 칸(가치/원칙/사례)과 그 행의 인용
    원문을 전달한다. 열 제목이 이미 문화 의미를 부여하므로 셀에 '문화'가
    없어도 검사한다. 같은 절에 가치 셀의 원문 표현과 목표 표지가 있는
    경우만 다루며, 낯선 바꿔쓰기·법인 동일성·공식성은 의미 검수에 남긴다.
    빈 결과는 승인도, 목표가 거짓이라는 판정도 아니다.
    """
    if isinstance(cells, (str, bytes)) or len(cells) != CULTURE_FLOW_CELL_COUNT:
        return ""  # 형식 검사는 기존 도식 검증기가 담당한다.
    value, principle = (_surface(cell) for cell in cells[:2])
    if not value or not principle:
        return ""
    explicit_attribution = (ORGANIZATIONAL_CLAIM_RE.search(principle)
                            and CULTURE_ATTRIBUTION_RE.search(principle))
    if (FLOW_GOAL_QUALIFIER_RE.search(principle) and not GOAL_NEGATION_RE.search(principle)
            and not explicit_attribution):
        return ""  # 원칙 칸 자체가 계획임을 명시한 경우 현재형 격상은 아니다.
    clauses = [_surface(clause) for source in sources_mapping.values()
               for clause in SOURCE_CLAUSE_SPLIT_RE.split(source)]
    goal_clauses = [clause for clause in clauses if value in clause
                    and SOURCE_GOAL_RE.search(clause) and not GOAL_NEGATION_RE.search(clause)]
    if not goal_clauses:
        return ""  # 목표 근거와 결속하지 못한 행을 문화 어휘만으로 삭제하지 않는다.
    for clause in clauses:
        if (principle in clause and EXPLICIT_CULTURE_RE.search(clause)
                and not SOURCE_GOAL_RE.search(clause)
                and not SOURCE_UNAVAILABLE_RE.search(clause)
                and not CURRENT_CULTURE_DENIAL_RE.search(clause)
                and not OTHER_ORGANIZATION_RE.search(clause)):
            # 다른 절의 문화 단어만으로 면제하지 않는다. 바로 이 원칙을
            # 명시한 현재 절차가 있으면 그 의미·주체 검증은 기존 검수에 남긴다.
            return ""
    return CULTURE_EVIDENCE_SCOPE_MISMATCH
