"""기사 첫 기자 표기와 자기완결 인용의 주어 검수를 구분한다."""

from copy import deepcopy
import json

import pytest

from src.features.news_intake import quote_selection_constants as qc
from src.features.news_intake.grounded import build_grounded_prompt
from src.features.news_intake.quote_selection import _selection_subject_supported, quote_candidates
from src.features.news_intake.tests.test_collection import AS_OF, COMPANY, item, snapshot
from src.features.news_intake.tests.test_quote_selection import selected, validate


BYLINE = "[새로운경제 김하늘 기자] "
EVENT = "가나다전자는 고객을 초청해 산업설비의 성능을 체험하는 행사를 열었다."


@pytest.mark.parametrize("tail", ["", " 회사는 행사 참가자에게 산업설비의 성능을 설명했다."])
def test_opening_byline_keeps_original_complete_quote(tail):
    candidate = snapshot([item()])[0].candidates[0]
    body = BYLINE + EVENT + tail
    raw = selected(candidate, body)
    before = deepcopy(raw)
    quotes = quote_candidates(candidate, body, COMPANY)
    result, rejected = validate(raw, candidate, body)
    assert result and not rejected and result[0].text == body
    assert raw == before and quote_candidates(candidate, body, COMPANY) == quotes


@pytest.mark.parametrize("body", [
    "[가나다전자 김하늘 기자] 다른기술은 고객을 초청해 행사를 열었다.",
    BYLINE + "다른기술은 가나다전자를 만나 인터뷰에서 자신의 사업을 설명했다.",
    BYLINE + EVENT + " 다른기술은 산업설비를 개발해 공급했다.",
    "가나다전자는 산업설비 기업이다. " + BYLINE + EVENT,
    "가나다전자는 산업설비 기업이다. [다른기술 기자] 다른기술은 산업설비를 공급했다.",
    BYLINE + "기사 제공: 가나다전자. 다른기술은 행사를 열었다.",
    BYLINE + "기자는 가나다전자를 만나 다른기술의 신제품을 설명했다.",
    BYLINE + "다른제조는 가나다전자를 취재하여 자사 사업을 설명했다.",
])
def test_byline_does_not_prove_target_actor_or_remove_later_reporting(body):
    assert not _selection_subject_supported(body, COMPANY, body)


def test_third_party_reporting_recipient_is_rejected_by_final_validation():
    candidate = snapshot([item()])[0].candidates[0]
    body = BYLINE + "다른제조는 가나다전자를 취재하여 자사 사업을 설명했다."
    result, rejected = validate(selected(candidate, body), candidate, body)
    assert not result and rejected["grounded_invalid_excerpt"] == 1


def test_selected_middle_byline_cannot_borrow_opening_exception():
    text = BYLINE + EVENT
    body = "다른기술은 신제품을 출시했다. " + text
    assert not _selection_subject_supported(text, COMPANY, body)


def test_opening_byline_after_complete_photo_caption_keeps_quote():
    text = BYLINE + EVENT
    body = "산업설비 체험 행사 모습. (사진=가나다전자)\n" + text
    assert _selection_subject_supported(text, COMPANY, body)


def test_byline_scope_offset_uses_selected_span_with_repeated_content(monkeypatch):
    from src.features.news_intake import quote_selection
    text = BYLINE + EVENT
    body = EVENT.rstrip(".") + "\n" + text
    observed = []

    def bind(text, body, company, start, *, max_chars):
        observed.append(start)
        return text, start

    monkeypatch.setattr(quote_selection, "bind_relation_subject", bind)
    assert _selection_subject_supported(text, COMPANY, body)
    assert observed == [body.index(text) + len(BYLINE)]


def test_enclosing_selection_guide_preserves_payload_schema_and_candidate_budget():
    candidate = snapshot([item()])[0].candidates[0]
    body = BYLINE + EVENT
    old = build_grounded_prompt(COMPANY, [(candidate, body)], AS_OF)
    new = build_grounded_prompt(COMPANY, [(candidate, body)], AS_OF, selection=True)
    assert qc.QUOTE_SELF_CONTAINED_SELECTION_GUIDE in new
    assert qc.QUOTE_SELF_CONTAINED_SELECTION_GUIDE not in old
    payload = json.loads(new.split("자료 시작:\n", 1)[1])
    assert payload["articles"][0]["body"] == body
    assert len(payload["articles"][0]["quote_candidates"]) <= qc.QUOTE_MAX_CANDIDATES_PER_ARTICLE
