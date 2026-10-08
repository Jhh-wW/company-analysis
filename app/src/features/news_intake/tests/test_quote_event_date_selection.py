"""원문에 적힌 완전한 사건 날짜만 선택지로 제공하고 최종 자기 인용을 검사한다."""

from copy import deepcopy

import pytest

from src.features.news_intake import quote_selection_constants as qc
from src.features.news_intake.grounded import build_grounded_prompt, build_grounded_schema
from src.features.news_intake.quote_selection import quote_candidates, restore_quote_response
from src.features.news_intake.tests.test_collection import AS_OF, BODY, COMPANY, item, snapshot
from src.features.news_intake.tests.test_quote_selection import selected, validate


def event_dates(articles):
    schema = build_grounded_schema(articles, selection=True, company=COMPANY)
    return schema["properties"]["items"]["items"]["properties"]["excerpts"]["items"]["properties"]["event_on"]["enum"]


@pytest.mark.parametrize("date", ["2026", "2026-09", "지난 2~3일", "2026-02-30", "2026.13.01", "0000-01-01"])
def test_incomplete_or_invalid_calendar_date_has_only_empty_choice(date):
    candidate = snapshot([item()])[0].candidates[0]
    assert event_dates([(candidate, f"가나다전자는 {date} 산업설비 체험 행사를 열었다.")]) == [""]


@pytest.mark.parametrize("date", ["2026-09-01", "2026.09.01", "2026.9.1", "2026/09/01", "2026년 9월 1일"])
def test_explicit_supported_calendar_dates_are_iso_choices(date):
    candidate = snapshot([item()])[0].candidates[0]
    assert event_dates([(candidate, f"가나다전자는 {date} 산업설비를 공급했다.")]) == ["", "2026-09-01"]


def test_date_choices_are_deduplicated_without_article_publication_fallback():
    candidate = snapshot([item()])[0].candidates[0]
    assert candidate.published_on == "2026-09-01"
    assert event_dates([(candidate, "가나다전자는 2026년 체험 행사를 열었다.")]) == [""]
    assert event_dates([(candidate, "2026.9.1 / 2026년 9월 1일 / 2024-02-29")]) == ["", "2024-02-29", "2026-09-01"]


def test_other_article_date_choice_still_needs_own_selected_text_evidence():
    candidates = snapshot([item(), item(1)])[0].candidates
    body = "가나다전자는 기업용 산업설비 제조 사업을 운영하며 고객사에 자동화 설비를 공급했다."
    other_body = "가나다전자는 2026년 9월 1일 산업설비를 공급했다."
    assert event_dates([(candidates[0], body), (candidates[1], other_body)]) == ["", "2026-09-01"]
    raw = selected(candidates[0], body)
    raw["items"][0]["excerpts"][0]["event_on"] = "2026-09-01"
    result, rejected = validate(raw, candidates[0], body)
    assert not result and rejected["grounded_event_date_unverified"] == 1


def test_exact_own_date_and_evidence_survive_final_validation():
    candidate = snapshot([item()])[0].candidates[0]
    raw = selected(candidate, BODY)
    row = raw["items"][0]["excerpts"][0]
    row["event_on"] = "2026-09-01"
    row["time_evidence_quote_id"] = row["text_quote_id"]
    result, rejected = validate(raw, candidate, BODY)
    assert result and not rejected and result[0].event_on == "2026-09-01"


def test_schema_date_choice_does_not_approve_future_completed_event():
    candidate = snapshot([item()])[0].candidates[0]
    body = "가나다전자는 2027년 9월 1일 산업설비를 공급했다."
    raw = selected(candidate, body)
    row = raw["items"][0]["excerpts"][0]
    row["event_on"] = "2027-09-01"
    row["time_evidence_quote_id"] = row["text_quote_id"]
    assert event_dates([(candidate, body)]) == ["", "2027-09-01"]
    result, rejected = validate(raw, candidate, body)
    assert not result and rejected["grounded_event_date_unverified"] == 1


def test_year_only_original_response_is_never_silently_changed():
    candidate = snapshot([item()])[0].candidates[0]
    raw = selected(candidate, BODY)
    raw["items"][0]["excerpts"][0]["event_on"] = "2026"
    before = deepcopy(raw)
    restored = restore_quote_response(raw, articles=[(candidate, BODY)], company=COMPANY)
    assert raw == before and restored["items"][0]["excerpts"][0]["event_on"] == "2026"
    assert not validate(raw, candidate, BODY)[0]


def test_legacy_schema_and_quote_id_ranges_remain_unchanged():
    candidate = snapshot([item()])[0].candidates[0]
    old = build_grounded_schema([(candidate, BODY)])
    assert old["properties"]["items"]["items"]["properties"]["excerpts"]["items"]["properties"]["event_on"] == {"type": "string"}
    before = quote_candidates(candidate, BODY, COMPANY)
    build_grounded_schema([(candidate, BODY)], selection=True, company=COMPANY)
    assert quote_candidates(candidate, BODY, COMPANY) == before
    assert qc.QUOTE_EVENT_DATE_GUIDE in build_grounded_prompt(COMPANY, [(candidate, BODY)], AS_OF, selection=True)
    assert qc.QUOTE_EVENT_DATE_GUIDE not in build_grounded_prompt(COMPANY, [(candidate, BODY)], AS_OF)
