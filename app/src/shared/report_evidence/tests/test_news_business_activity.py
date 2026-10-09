"""자기 사업 행동과 투자·계획·다른 주체를 구분한다."""

import pytest

from src.shared.report_evidence.news_business_activity import news_business_item


@pytest.mark.parametrize("text,state,item,role", [
    ("가람회사는 계측 장비를 제공하고 있다.", "ongoing", "계측 장비", "operating"),
    ("가람회사는 정밀 계측 사업을 영위한다.", "ongoing", "정밀 계측 사업", "operating"),
    ("가람회사는 계측 사업 부문을 인수했다.", "completed", "계측 사업 부문", "acquired_business"),
    ("가람회사는 계측 사업 부문을 인수해 시장 공략을 강화했다.", "completed", "계측 사업 부문", "acquired_business"),
    ("가람회사는 계측 사업 부문을 인수했다. 다른회사는 조명 사업 인수를 계획한다.", "completed", "계측 사업 부문", "acquired_business"),
])
def test_explicit_target_business_is_preserved(text, state, item, role):
    assert news_business_item(
        text, company_names=("가람회사",), claim_kind="reported_fact", temporal_status=state,
        published_on="2026-10-09",
    ) == (item, role)


@pytest.mark.parametrize("text,state", [
    ("다른회사는 계측 사업 부문을 인수했다.", "completed"),
    ("가람회사의 계열사는 계측 장비를 제공한다.", "ongoing"),
    ("가람회사 그룹은 계측 장비를 제공한다.", "ongoing"),
    ("가람회사는 계측 장비 회사의 지분을 인수했다.", "completed"),
    ("가람회사는 계측 장비 회사를 인수했다.", "completed"),
    ("가람회사는 계측 사업 펀드를 인수했다.", "completed"),
    ("가람회사는 계측 사업 부문을 인수할 예정이다.", "planned"),
    ("가람회사는 계측 사업 부문을 인수하지 않았다.", "completed"),
    ("가람회사는 계측 사업 부문을 인수했다는 소문이 있다.", "completed"),
    ("가람회사는 계측 사업 부문을 인수했다. 현재 해당 사업을 중단했다.", "completed"),
    ("가람회사는 2003년 계측 사업 부문을 인수했다.", "completed"),
    ("가람회사는 제품을 제공한다.", "ongoing"),
])
def test_unsupported_current_business_is_rejected(text, state):
    assert news_business_item(
        text, company_names=("가람회사",), claim_kind="reported_fact", temporal_status=state,
        published_on="2026-10-09",
    ) == ("", "")
