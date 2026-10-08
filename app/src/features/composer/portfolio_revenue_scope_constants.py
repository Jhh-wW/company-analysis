"""포트폴리오 역할에서 외부 거래 범위를 확인하는 좁은 문법."""
import re

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
