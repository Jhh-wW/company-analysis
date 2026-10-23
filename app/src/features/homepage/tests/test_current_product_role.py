"""현재 제품 역할은 같은 HTML의 제품명·기능·section 문맥에 결속한다."""
import pytest

from src.features.homepage.current_product_role import current_product_role_ranges
from src.features.homepage.wide_extract import extract_usable_ranges

TITLE = "정산도우미 | 가나다장비"
URL = "https://ganada.example/products/settlement"
FUNCTION = "정산도우미는 판매자가 결제 데이터를 조회하고 고객의 주문 업무를 자동으로 처리할 수 있도록 제공하는 솔루션입니다."


def _roles(body, *, title=TITLE, url=URL):
    document = f"<html><head><title>{title}</title></head><body><main>{body}</main></body></html>"
    ranges, _ = extract_usable_ranges(document)
    return current_product_role_ranges(document, source_url=url, title=title, usable_ranges=ranges)


def test_현재_제품명_핵심기능_대상을_원문_그대로_보존한다():
    assert _roles(f"<section><h2>제품 기능</h2><p>{FUNCTION}</p></section>") == (FUNCTION,)


@pytest.mark.parametrize("heading", ["곧 출시될 새로운 기능", "출시 예정", "서비스 종료 안내"])
def test_짧은_상태제목이_추출에서_빠져도_현재형_기능을_승격하지_않는다(heading):
    assert not _roles(f"<section><h2>{heading}</h2><p>{FUNCTION}</p></section>")


def test_현재와_예정_기능이_별도_section이면_현재만_살린다():
    future = "판매자는 국가별 결제 데이터를 고객별로 조회하고 조건에 맞는 결제 화면을 자동으로 설정할 수 있어요."
    assert _roles(f"<section><h2>현재 기능</h2><p>{FUNCTION}</p></section><section><h2>곧 출시</h2><p>{future}</p></section>") == (FUNCTION,)


def test_하나의_section에_현재와_예정이_섞이면_보수적으로_제외한다():
    assert not _roles(f"<section><p>{FUNCTION}</p><h2>곧 출시</h2><p>새 기능입니다.</p></section>")


def test_안쪽_section도_바깥_예정상태를_상속한다():
    assert not _roles(f"<section><h2>곧 출시</h2><section><p>{FUNCTION}</p></section></section>")


def test_안쪽_section도_바깥_FAQ문맥을_상속한다():
    support = "사용자는 신용카드와 계좌이체 및 휴대전화 소액결제를 등록하고 고객의 결제 수단을 변경하고 관리할 수 있어요."
    assert not _roles(f"<section><h2>자주 묻는 질문</h2><section><p>{support}</p></section></section>")


def test_공통_article_아래_다른_예정section이_현재section을_오염시키지_않는다():
    assert _roles(f"<article><section><h2>현재 기능</h2><p>{FUNCTION}</p></section><section><h2>곧 출시</h2><p>새로운 제품입니다.</p></section></article>") == (FUNCTION,)


def test_제목을_직접_가진_바깥div의_예정상태도_상속한다():
    assert not _roles(f"<div><h2>곧 출시</h2><section><p>{FUNCTION}</p></section></div>")


@pytest.mark.parametrize("wrapper", ["footer", "nav", "aside"])
def test_공통_탐색_꼬리말은_제품기능으로_승격하지_않는다(wrapper):
    assert not _roles(f"<{wrapper}><p>{FUNCTION}</p></{wrapper}>")


@pytest.mark.parametrize("body", [
    "쿠키 관리 서비스는 고객이 결제 데이터를 조회하고 사용 설정을 수정할 수 있도록 제공하는 솔루션입니다.",
    "행사에 참석한 대표는 정산도우미를 통해 판매자가 고객의 결제 데이터를 조회하고 처리할 수 있다고 발표했습니다.",
    "정산도우미 출시 예정 기능은 판매자가 결제 데이터를 조회하고 고객의 주문 업무를 자동으로 처리할 수 있어요.",
    "신용카드와 계좌이체 및 휴대전화 소액결제를 등록하고 고객의 결제 수단을 변경하고 관리할 수 있어요.",
])
def test_쿠키_행사발언_예정기능_FAQ결제수단_나열은_제품역할이_아니다(body):
    assert not _roles(f"<section><h2>자주 묻는 질문</h2><p>{body}</p></section>")


def test_FAQ라도_제품명과_솔루션_정의가_있는_원문은_유효하다():
    assert _roles(f"<section><h2>자주 묻는 질문</h2><p>{FUNCTION}</p></section>") == (FUNCTION,)


@pytest.mark.parametrize("url", ["https://ganada.example/", "https://ganada.example/blog/products/settlement", "https://ganada.example/events/settlement"])
def test_상품상세_문맥이_없거나_마케팅기사_경로면_현재_제품으로_추정하지_않는다(url):
    assert not _roles(f"<section><p>{FUNCTION}</p></section>", url=url)
