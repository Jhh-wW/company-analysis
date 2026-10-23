"""손익 해석의 비교 기준과 원인 범위를 검사하는 닫힌 문법."""

import re

FINANCIAL_CAUSALITY_GUIDE = (
    "손익 원인 해석: '따른 결과로 읽힌다·해석된다'처럼 완곡하게 써도 원인·결과 "
    "관계를 주장한 것이다. 숫자의 병렬 나열만으로 원인을 서술하거나 승인하지 "
    "않는다. 특히 영업비용 항목의 증감만으로 순손실이 발생·지속한 이유를 "
    "만들지 말고, 자기 인용에서 같은 원인·결과 관계를 직접 확인한다. "
    "원인이 확인되지 않으면 정확한 수치·증감 비교만 남긴다. 동일 기간·범위의 "
    "수익과 비용을 검산한 산술 관계는 유지하되 영업외손익·세금을 생략해 "
    "순손익의 원인으로 넓히지 않는다.\n"
)


FINANCIAL_COMPARISON_BASIS_MISMATCH = "financial_comparison_basis_mismatch"
NET_LOSS_CAUSE_UNBOUND = "net_loss_operating_cause_unbound"
FINANCIAL_INTERPRETATION_REASONS = {
    FINANCIAL_COMPARISON_BASIS_MISMATCH:
        "손실 원인 해석에서 재무항목의 증감폭·증감률과 총액을 서로 비교했다.",
    NET_LOSS_CAUSE_UNBOUND:
        "영업항목만으로 순손실 원인을 설명했으나 자기 인용에 같은 인과 관계가 결속되지 않았다.",
}

FINANCIAL_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+|[;\n]+")
# 원인·결과의 재서술에서 생략해도 같은 회사 범위인 자기 호칭만 허용한다.
FINANCIAL_SELF_PREFIX_RE = re.compile(r"^(?:회사|당사)(?:는|의|에서|에서는)?[,，:]?$")
FINANCIAL_SOURCE_MODAL_RE = re.compile(
    r"만약|가정|가설|가상|(?:한|하는|할|된|되는|될)경우|"
    r"(?:다|라|으)면|(?:증가|감소|발생|확대|축소|변동)하면"
)
# 원인·결과 사이의 접속어만 떼고 남는 주어 수식은 그대로 대조한다.
FINANCIAL_CAUSAL_BRIDGE_RE = re.compile(
    r"^(?:(?:의)?(?:영향|결과)(?:으)?로|때문(?:에)?|(?:으)?로인(?:해|한|하여)|"
    r"에힘입어|덕분(?:에)?|의(?:주요|주된|핵심|가장큰)?"
    r"(?:원인|배경|요인|이유)(?:에는|은|는|에)?|은|는|이|가)"
)
FINANCIAL_LOSS_RE = re.compile(r"영업손실|(?:당기)?순손실")
NET_LOSS_RE = re.compile(r"(?:당기)?순손실")
OPERATING_METRIC_RE = re.compile(r"영업수익|매출액?|영업비용")
NONOPERATING_METRIC_RE = re.compile(r"금융(?:수익|비용|손익)|영업외|법인세|세금|이자(?:수익|비용)")
FINANCIAL_RESULT_CAUSE = r"(?:데|것에?|에)따른(?:결과|영향)"
FINANCIAL_CAUSE_RE = re.compile(
    rf"때문|(?:으)?로인(?:해|하여)|기인|원인|요인|배경|이유|"
    rf"{FINANCIAL_RESULT_CAUSE}|(?:의)?영향으로|(?:의)?결과로|반영"
)
# 인과를 부인하는 설명은 긍정 원인 주장으로 세지 않는다. 다른 문장의 부정은
# 양성 인과를 면제하지 않으며 문장별로 같은 검사를 다시 한다.
FINANCIAL_CAUSE_DENIAL_RE = re.compile(
    rf"(?:때문|원인|요인|배경|이유|{FINANCIAL_RESULT_CAUSE})(?:만)?"
    r"(?:이|은|는|라고|이라고|이라|으로|로)*"
    r"(?:(?:이라|라)는(?:뜻|의미)(?:이|가|은|는))?"
    r"(?:(?:볼|말할|단정할|판단할|보기|말하기|단정하기)"
    r"(?:수|근거|이유)?(?:가|는|도)?)?"
    r"(?:아니|아닙|없|어렵|않|읽히지않)"
    r"|(?:원인|요인|배경|이유)(?:을|를)(?:알|판단할|설명할)수없"
    r"|(?:원인|요인|배경|이유)(?:을|를)설명하지않"
    r"|반영(?:하지않|되지않|하지못|되지못|한것(?:은|이)아니|"
    r"한결과(?:라고|로)(?:볼|읽힐)수없)"
    r"|(?:때문에|영향으로|결과로)[^.!?;,]*?(?:발생|지속|유지|축소|확대|감소|증가)"
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
    rf"(?P<cause>[^.;!?]+?)(?:때문|에기인|{FINANCIAL_RESULT_CAUSE}|"
    r"(?:의)?영향으로|(?:의)?결과로|(?:을|를|이|가)반영)"
)
CAUSE_FIRST_RESULT_RE = re.compile(
    r"(?P<cause>[^.;!?]+?)(?:때문에|(?:으)?로인(?:해|하여)|"
    r"(?:의)?영향으로|(?:의)?결과로)"
    r"[^.;!?]*(?:당기)?순손실"
)
NAMED_CAUSE_RE = re.compile(
    r"(?P<cause>[^.;!?]+?)(?:이|가)(?:당기)?순손실의"
    r"(?:주요|주된)?(?:원인|요인|배경|이유)"
)
RESULT_FIRST_BACKGROUND_CAUSE_RE = re.compile(
    r"(?:당기)?순손실의(?:주요|주된)?(?:원인|요인|배경|이유)(?:은|는|에는|에),?"
    r"(?P<cause>[^.;!?]+?)(?:이다|이었다|으로|로|이있|가있)"
)
