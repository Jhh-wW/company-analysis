"""문화 기사 선택 안내는 직원 문화와 외부 고객 마케팅을 구별한다."""

import json

from src.features.news_intake.grounded import build_grounded_prompt, build_grounded_schema
from src.features.news_intake.tests.test_industry_context import AS_OF, BODY, CANDIDATE, COMPANY


def test_뉴스문화안내만한정하고_실제자료와선택schema는유지한다():
    articles = [(CANDIDATE, BODY)]
    prompt = build_grounded_prompt(COMPANY, articles, AS_OF, selection=True)
    assert "임직원 인재상·인사제도·내부 일하는 원칙" in prompt
    assert "소비자·고객·인플루언서 대상 마케팅이나 외부 고객 접점" in prompt
    assert "직원의 구체적인 업무·협업 방식" in prompt
    payload = json.loads(prompt.split("자료 시작:\n", 1)[1])
    assert payload["articles"][0]["body"] == BODY
    schema = build_grounded_schema(articles, selection=True, company=COMPANY)
    assert schema["properties"]["items"]["items"]["properties"]["excerpts"]["items"]["properties"]["subject_is_target"] == {"type": "boolean"}
