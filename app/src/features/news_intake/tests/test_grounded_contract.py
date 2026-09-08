"""응답 누락을 요청 단계에서도 제한하고 사건일·원문 검증은 유지한다."""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.features.news_intake.grounded import GROUNDED_ANALYSIS_SCHEMA, build_grounded_schema
from src.features.news_intake.tests.test_collection import BODY, analyzer, collect, item, snapshot


@pytest.mark.parametrize("size", [1, 2, 3, 4])
def test_batch_schema_binds_count_and_ids_without_mutating_shared_definition(size):
    before = deepcopy(GROUNDED_ANALYSIS_SCHEMA)
    snap, _ = snapshot([item(number) for number in range(size)])
    schema = build_grounded_schema([(candidate, BODY) for candidate in snap.candidates])
    items = schema["properties"]["items"]
    assert items["minItems"] == items["maxItems"] == size
    assert items["items"]["properties"]["id"]["enum"] == [candidate.id for candidate in snap.candidates]
    assert GROUNDED_ANALYSIS_SCHEMA == before


def test_date_in_another_paragraph_is_not_the_excerpt_event_date():
    text = "가나다전자는 기업용 산업설비 제조 사업을 운영하며 자동화 설비를 공급했다."

    def with_date(rows, payload):
        rows[0]["excerpts"][0].update(text=text, event_on="2026-09-01", time_evidence=BODY)
        return rows

    result = collect(fetch=lambda url: BODY + " " + text, analyze=analyzer(with_date))
    assert not result.fragments
    assert result.diagnostics["제외"]["grounded_event_date_unverified"] == 1

    def without_date(rows, payload):
        rows[0]["excerpts"][0].update(text=text, event_on="", time_evidence="")
        return rows

    result = collect(fetch=lambda url: BODY + " " + text, analyze=analyzer(without_date))
    assert len(result.fragments) == 1
    assert result.fragments[0].text == text
    assert result.fragments[0].event_on == ""


def test_analyzer_receives_schema_bound_to_batch_ids_and_count():
    schemas = []
    real_analyzer = analyzer()

    def analyze(prompt, schema, max_tokens):
        schemas.append(schema)
        return real_analyzer(prompt, schema, max_tokens)

    collect([item(0), item(1)], fetch=lambda url: BODY + " " + url, analyze=analyze)
    assert len(schemas) == 1
    assert schemas[0]["properties"]["items"]["minItems"] == 2
    assert len(schemas[0]["properties"]["items"]["items"]["properties"]["id"]["enum"]) == 2
