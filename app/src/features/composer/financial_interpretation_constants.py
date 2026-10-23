"""손익 해석의 비교 기준과 원인 범위를 검사하는 닫힌 문법."""

import re


FINANCIAL_COMPARISON_BASIS_MISMATCH = "financial_comparison_basis_mismatch"
NET_LOSS_CAUSE_UNBOUND = "net_loss_operating_cause_unbound"
FINANCIAL_INTERPRETATION_REASONS = {
    FINANCIAL_COMPARISON_BASIS_MISMATCH:
        "손실 원인 해석에서 재무항목의 증감폭·증감률과 총액을 서로 비교했다.",
    NET_LOSS_CAUSE_UNBOUND:
        "영업항목만으로 순손실 원인을 설명했으나 자기 인용에 같은 인과 관계가 결속되지 않았다.",
}

FINANCIAL_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|[;\n]+")
FINANCIAL_LOSS_RE = re.compile(r"영업손실|(?:당기)?순손실")
NET_LOSS_RE = re.compile(r"(?:당기)?순손실")
OPERATING_METRIC_RE = re.compile(r"영업수익|매출액?|영업비용")
NONOPERATING_METRIC_RE = re.compile(r"금융(?:수익|비용|손익)|영업외|법인세|세금|이자(?:수익|비용)")
FINANCIAL_CAUSE_RE = re.compile(r"때문|(?:으)?로인(?:해|하여)|기인|원인|요인")
# 인과를 부인하는 설명은 긍정 원인 주장으로 세지 않는다. 다른 문장의 부정은
# 양성 인과를 면제하지 않으며 문장별로 같은 검사를 다시 한다.
FINANCIAL_CAUSE_DENIAL_RE = re.compile(
    r"(?:때문|원인|요인)(?:만)?(?:이|은|는|라고|이라고|이라|으로|로)*"
    r"(?:(?:볼|말할|단정할|판단할|보기|말하기|단정하기)"
    r"(?:수|근거|이유)?(?:가|는|도)?)?"
    r"(?:아니|아닙|없|어렵|않)"
    r"|(?:원인|요인)(?:을|를)(?:알|판단할|설명할)수없"
    r"|(?:원인|요인)(?:을|를)설명하지않"
    r"|때문에[^.!?;,]*?(?:발생|지속|유지|축소|확대|감소|증가)"
    r"(?:한|된|했던)것(?:은|이)(?:아니|아닙)"
)
_METRIC = r"(?:영업수익|매출액?|영업비용)(?:의)?"
_BASIS = r"(?:증가폭|감소폭|증가액|감소액|증감액|증가율|감소율|증감률|총액|총규모|규모|금액|수준)?"
FINANCIAL_COMPARISON_RE = re.compile(
    rf"{_METRIC}(?P<left>{_BASIS})(?:보다|에비해|대비)"
    rf"{_METRIC}(?P<right>{_BASIS})(?=이|가|은|는|도|을|를)"
)
FINANCIAL_SUBJECT_COMPARISON_RE = re.compile(
    rf"{_METRIC}(?P<left>{_BASIS})(?:이|가|은|는)"
    rf"{_METRIC}(?P<right>{_BASIS})(?:보다|에비해|대비)"
)
CHANGE_BASES = frozenset({"증가폭", "감소폭", "증가액", "감소액", "증감액"})
RATE_BASES = frozenset({"증가율", "감소율", "증감률"})
# 원인 쪽만 읽는다. 순손실 설명 앞의 무관한 금융수익 언급이 영업비용만의
# 원인을 정당화하지 않으며, 구문을 특정하지 못하면 기존 의미 검수에 남긴다.
RESULT_FIRST_CAUSE_RE = re.compile(
    r"(?:것은|이유는|원인은|요인은|배경은|(?:당기)?순손실은),?"
    r"(?P<cause>[^.;!?]+?)(?:때문|에기인)"
)
CAUSE_FIRST_RESULT_RE = re.compile(
    r"(?P<cause>[^.;!?]+?)(?:때문에|(?:으)?로인(?:해|하여))"
    r"[^.;!?]*(?:당기)?순손실"
)
NAMED_CAUSE_RE = re.compile(
    r"(?P<cause>[^.;!?]+?)(?:이|가)(?:당기)?순손실의(?:주요|주된)?(?:원인|요인)"
)
