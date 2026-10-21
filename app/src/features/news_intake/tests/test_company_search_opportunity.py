"""산업 질의를 추가해도 회사의 제품·공식 발표·과거 탐색 기회는 보존한다."""

from dataclasses import replace

import pytest

from src.features.news_intake import industry_constants as ic
from src.features.news_intake.search_snapshot import search_plan
from src.features.news_intake.tests.test_industry_context import ANCHOR, AS_OF, COMPANY
from src.shared.report_generation.models import exact_text_sha256


@pytest.mark.parametrize("aliases,domain", [
    ((), ""),
    (("Example Company",), ""),
    (("Example Company", "Example Inc"), "company.example"),
])
@pytest.mark.parametrize("anchor_count", [1, 2])
def test_산업질의가_회사제품과과거기사를_모두밀어내지않는다(aliases, domain, anchor_count):
    text = "예제법인은 사업0, 사업1을 운영한다."
    anchors = tuple(replace(ANCHOR, anchor_id=f"anchor-{index}", business_item=f"사업{index}",
                            exact_text=text, text_sha256=exact_text_sha256(text),
                            location=f"chars:0-{len(text)}")
                    for index in range(anchor_count))
    company = replace(COMPANY, aliases=aliases, domain=domain, business_anchors=anchors)
    plain = search_plan(replace(company, business_anchors=()), AS_OF)
    active = search_plan(company, AS_OF)
    company_rows = tuple(row for row in active if not row[2].startswith("industry_"))
    industry_rows = tuple(row for row in active if row[2].startswith("industry_"))

    assert len(active) == len(plain)
    assert active[:2] == plain[:2]
    assert len(industry_rows) == ic.INDUSTRY_QUERY_COUNT
    for topic in ("products", "official", "archive"):
        original = next(row for row in plain if row[2] == topic)
        assert original in company_rows
    assert next(row[0] for row in company_rows if row[2] == "archive").endswith(str(AS_OF.year - 1))
    # 선택된 회사 질의의 기존 순서와 질의·기간·검색 방식도 바꾸지 않는다.
    assert company_rows == tuple(row for row in plain if row in company_rows)
    for anchor in anchors:
        assert {row[2].split(":", 1)[0] for row in industry_rows
                if row[2].split(":", 1)[1] == anchor.anchor_id} == {
                    "industry_domestic", "industry_global",
                }


def test_사업앵커가없으면_기존회사질의와_세과거연도를_모두유지한다():
    company = replace(COMPANY, business_anchors=(), aliases=("Example Company",))
    plan = search_plan(company, AS_OF)
    assert [row[2] for row in plan] == [
        "recent", "business", "alias", "products", "partnerships", "strategy", "official",
        "archive", "archive", "archive",
    ]
    assert [row[0] for row in plan if row[2] == "archive"] == [
        f"예제법인 {AS_OF.year - years}" for years in (1, 2, 3)
    ]
