"""포트폴리오 역할에서 외부 거래 범위를 확인하는 좁은 문법."""
import re
from decimal import Decimal

PORTFOLIO_SECTION = "portfolio"
EXTERNAL_SCOPE_RE = re.compile(
    r"외부(?:수주|고객|거래처|매출|판매|공급|납품)|"
    r"외부(?:의|에|로|를대상으로|업체|기업|법인)[^.!?。;]{0,24}(?:수주|판매|공급|납품|매출)|"
    r"(?:사외|대외)(?:수주|판매|공급|납품|매출)"
)
EXTERNAL_ACTIVITIES = (
    ("수주", re.compile(r"수주|주문(?:을|를)?받|발주(?:를|을)?받")),
    ("판매", re.compile(r"판매")),
    ("공급", re.compile(r"공급|납품")),
    ("매출", re.compile(r"매출|수익")),
)
EXTERNAL_OBJECT_RE = re.compile(r"([가-힣A-Za-z0-9_-]+)(?:을|를|의)(?=\s)")
EXTERNAL_OBJECT_PREFIX_BOUNDARY_RE = re.compile(
    r"(?:은|는|이|가|을|를|의|에|에게|에서|로|으로|와|과|하고|하며)$"
)
EXTERNAL_GENERIC_OBJECTS = frozenset({
    "제품", "상품", "서비스", "수주", "주문", "발주", "매출", "수익",
    "역할", "사업", "고객", "고객사", "거래처", "외부수주",
})
EXTERNAL_ASSERTION_DENIAL_RE = re.compile(
    r"외부[^.!?。;]{0,50}(?:하지않|되지않|없|아니|미확인|확인되지|확인할수없)|"
    r"외부[^.!?。;]{0,50}(?:할계획|예정|검토중)"
)
EXTERNAL_TITLE_ONLY_RE = re.compile(
    r"^(?:외부(?:수주|고객|거래처|매출|판매|공급|납품)(?:현황|계획|목록|내역)?|"
    r"(?:수주|매출|판매|공급|납품)(?:현황|계획|목록|내역))$"
)

# 명시 부문별 외부매출 표는 외부 거래 방식·고객명까지 증명하지 않는다.
EXTERNAL_TABLE_OWNER_HEADERS = frozenset({"사업부문", "사업부", "부문"})
EXTERNAL_TABLE_REVENUE_HEADERS = frozenset({"외부고객매출", "외부고객매출액", "외부매출", "외부매출액"})
EXTERNAL_TABLE_MIN_COLUMNS = 2
EXTERNAL_TABLE_ZERO = Decimal("0")
EXTERNAL_TABLE_ROW_BOUNDARY_RE = re.compile(r"\r?\n|;")
EXTERNAL_TABLE_ALIGNMENT_RE = re.compile(r":?-+:?")
EXTERNAL_TABLE_AMOUNT_RE = re.compile(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?")
EXTERNAL_TABLE_NON_ACTUAL_RE = re.compile(r"미확인|검토|예정|계획|추정|미정|목표")
EXTERNAL_TABLE_CAPTION_NON_ACTUAL_RE = re.compile(
    r"(?<![가-힣])(?:미확인|검토|예정|계획|추정|미정|목표)|"
    r"매출(?:액|현황|표)?(?:미확인|검토|예정|계획|추정|미정|목표)"
)
EXTERNAL_TABLE_AGGREGATE_RE = re.compile(r"^(?:합계|총계|전체|전사)$")
EXTERNAL_TABLE_REVENUE_ASSERTION_RE = re.compile(
    r"^외부(?:고객)?매출(?:액)?(?:을|이|도|은|는)?"
    r"(?:창출한다|창출하고있다|발생한다|발생하고있다|발생시킨다|얻는다|올린다|있다)[.]?$"
)
