"""실패 관측의 원문 제거와 미도달 단계 보존을 검사한다."""

import json

import pytest

from src.shared.report_quality.composition_diagnostics import observed_composition_steps


def _parse_event(**changes: object) -> dict:
    return {
        "step": "8_본문검수_응답판독", "경로": "flat", "시도": 1,
        "판독": "json_syntax", "추출방식": "failed",
        "json시작offset": -1, "json끝offset": -1,
        "입력문자": 400, "응답문자": 12, "요청번호수": 3,
        "응답행수": 0, "유효행수": 0, "미응답번호수": 3,
        "요청밖번호수": 0, "행탈락": {},
        **changes,
    }


def _summary_event(**changes: object) -> dict:
    return {
        "step": "8_핵심요약_단계", "경로": "legacy", "도달단계": "작성",
        "본문후보수": 7, "초안수": 0, "검수후수": None,
        "첫보충후수": None, "수치검사후수": None, "최종수": None,
        "작성한도도달": False, "검수한도도달": False,
        **changes,
    }


def test_preserves_attempt_order_and_unreached_stages_without_raw_text() -> None:
    first = _parse_event(response="비공개 검수 응답", raw_text="회사 본문")
    second = _parse_event(시도=2, 행탈락={
        "number_not_int": 2, "응답전문": "비공개 검수 응답",
        "evidence_ids_mismatch": "문자 오염",
    })
    summary = _summary_event(error="임의 오류문")
    observed = observed_composition_steps([first, "오염", {}, second, summary])
    assert len(observed) == 3
    assert [record["시도"] for record in observed[:2]] == [1, 2]
    assert observed[1]["행탈락"] == {"number_not_int": 2}
    assert observed[2]["초안수"] == 0
    assert observed[2]["검수후수"] is None
    serialized = json.dumps(observed, ensure_ascii=False)
    for private in ("비공개", "회사 본문", "임의 오류문", "문자 오염"):
        assert private not in serialized
    # 정규화하면서 생산자가 가진 기록을 수정하지 않는다.
    assert first["response"] == "비공개 검수 응답"


@pytest.mark.parametrize("changes", [
    {"시도": True}, {"응답행수": -1}, {"json시작offset": -2},
    {"판독": "임의 응답"}, {"경로": ["flat"]}, {"추출방식": {}},
    {"미응답번호수": "0"}, {"행탈락": "본문"},
])
def test_rejects_invalid_protocol_fields(changes: dict) -> None:
    assert observed_composition_steps([_parse_event(**changes)]) == ()


@pytest.mark.parametrize("changes", [
    {"도달단계": "원문"}, {"도달단계": []}, {"작성한도도달": 1},
    {"최종수": False}, {"초안수": -1}, {"경로": "FULL"},
])
def test_rejects_invalid_summary_fields(changes: dict) -> None:
    assert observed_composition_steps([_summary_event(**changes)]) == ()


def test_does_not_deduplicate_retry_observations() -> None:
    event = _parse_event()
    assert len(observed_composition_steps([event, event])) == 2
