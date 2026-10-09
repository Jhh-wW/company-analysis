"""실제 조업 사실과 조건부 제조간접원가 배부 기준의 닫힌 관계."""
from typing import Final
import re

OVERHEAD_ALLOCATION_SUBJECT_RE = re.compile(
    r"실제조업도(?:(?!실제조업도)[^.!?。;\n])*정상조업도"
    r"(?:(?!실제조업도)[^.!?。;\n])*미달(?:하는|할)경우"
)
OVERHEAD_ALLOCATION_TREATMENT_RE = re.compile(
    r"고정제조간접원가[^.!?。;\n]*?배부[^.!?。;\n]*?비용[^.!?。;\n]*?인식|"
    r"배부되지않은고정제조간접원가[^.!?。;\n]*?비용[^.!?。;\n]*?인식"
)
OVERHEAD_ACTUAL_BUSINESS_RE = re.compile(
    r"(?:생산|공장가동|조업)[^.!?。;\n]*(?:중단|지연|정지|축소)"
    r"(?:했|하였|됐|되었|되어|하고있|된상태)|"
    r"(?:생산원가|제조원가)[^.!?。;\n]*(?:급증|급등|상승|증가)"
    r"(?:했|하였|됐|되었|해|하고있)|"
    # 실제 미달·관측 비율의 종결 서술만 보존한다. 조건·예상·가정은 제외한다.
    r"실제조업도[^.!?。;\n]*정상조업도(?:에|보다)미달"
    r"(?:했다|했습니다|하였다|하였습니다)(?=$|[.!?。;\n]|고(?:공시|보고|밝혔))|"
    r"실제조업도[^.!?。;\n]*정상조업도의[0-9]+(?:\.[0-9]+)?%에"
    r"(?:그쳤다|그쳤습니다)(?=$|[.!?。;\n]|고(?:공시|보고|밝혔))"
)
OVERHEAD_ALLOCATION_RULE_NAME: Final[str] = "조업도조건원가배부"
ALLOCATION_RECOGNITION_SUFFIX_RE = re.compile(r"(?:하였습니다|했습니다|하였다|했다|합니다|한다|하며|하고)")
CLAUSE_SPLIT_RE = re.compile(r"[;!?。\n]|(?<!\d)\.|\.(?!\d)")

# 실제 사업 동사를 넘어 다른 절의 회계 처리를 빌리지 않는 닫힌 상각 구문이다.
AMORTIZATION_WINDOW_CHARS: Final[int] = 160
AMORTIZATION_SUBJECT_PATTERN: Final[str] = (
    r"무형자산으로계상된개발비|개발비|(?:유형|무형)자산|감가상각(?:비)?"
)
AMORTIZATION_REAL_ACTION_PATTERN: Final[str] = (
    r"(?:개발|제조|생산|공급|판매|제공|운영)(?:하고있|한다|합니다|하였다|했다|하며|하여)"
    r"|(?:증가|감소|상승|급증|급등)(?:했|하였|하고있|해|하므로)"
    r"|[0-9,]+(?:백만원|천원|억원|원)"
)
AMORTIZATION_BRIDGE_PATTERN: Final[str] = (
    rf"(?:(?!{AMORTIZATION_SUBJECT_PATTERN}|{AMORTIZATION_REAL_ACTION_PATTERN})"
    rf"[^,;.!?。\n]){{0,{AMORTIZATION_WINDOW_CHARS}}}?"
)
AMORTIZATION_END_PATTERN: Final[str] = (
    r"(?:하고있(?:습니다|다|으며)|합니다|한다|하며|하여|하였습니다|하였다|했다"
    r"|하는(?:방식|방법)(?:으로)?운영(?:된다|되고있다|한다)|(?=$|[,;.!?。\n]))"
)
ASSET_AMORTIZATION_POLICY_RE = re.compile(
    rf"(?:{AMORTIZATION_SUBJECT_PATTERN}){AMORTIZATION_BRIDGE_PATTERN}"
    r"(?:정액법|정률법|내용연수|상각기간|[0-9]+(?:년|개월)(?:동안)?)"
    rf"{AMORTIZATION_BRIDGE_PATTERN}상각{AMORTIZATION_END_PATTERN}"
)
AMORTIZATION_COST_POLICY_RE = re.compile(
    r"(?:그)?(?:상각액|개발비(?:의)?상각(?:액|비)|감가상각비)"
    r"(?:을|를|은|는)?(?:제조원가|원가|비용)(?:로|에)?계상"
    rf"{AMORTIZATION_END_PATTERN}"
)
AMORTIZATION_UNIT_SPLIT_RE = re.compile(r"[,;!?。\n]|(?<!\d)\.|\.(?!\d)")
# 고객 소유 자산의 상각을 맡는 본업은 회사 자체 자산의 회계정책과 구분한다.
EXTERNAL_ASSET_ACCOUNTING_RE = re.compile(
    r"(?:고객(?:사|기업)?|거래처|의뢰인)(?:의|보유|에게|을위해|를위해)"
    r"(?:개발비|(?:유형|무형)자산|감가상각비|상각액)"
)
AMORTIZATION_RESIDUE_RE = re.compile(r"(?:(?:당사|회사)(?:는|가)?|그|해당|[\s,.;!?。])*\Z")
ASSET_AMORTIZATION_RULE_NAME: Final[str] = "자산상각원가계상"
