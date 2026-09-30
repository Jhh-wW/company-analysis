"""동일 선택 ID의 반복만 정리하고 근거·의미칸 계약을 유지한다."""

import copy
import json
import logging

import pytest

from src.features.composer.logic import parse_section_response


PAIRS = {
    "p5-001": ("current_challenges:issue", "11"),
    "p5-002": ("current_challenges:issue", "22"),
    "p5-003": ("current_challenges:response", "11"),
}


def _parse(ids, **fields):
    item = {"글": "합성법인은 2023.04.10 안전시설 미흡으로 과태료를 부과받았다.",
            "등급": "확인", "근거선택": ids, **fields}
    return parse_section_response(
        json.dumps({"문장들": [item]}, ensure_ascii=False),
        "current_challenges", evidence_pairs=PAIRS,
    )


def test_같은선택의_반복은_첫출현순서로_정리하고_검증상태는_승격하지_않는다(caplog):
    with caplog.at_level(logging.INFO):
        result = _parse(["p5-002", "p5-001", "p5-002", "p5-001"])
    assert len(result) == 1
    assert result[0].citations == ("22", "11")
    assert result[0].planned_claim_slot == "current_challenges:issue"
    assert result[0].verification_state == "unverified"
    assert "반복개수=2" in caplog.text
    assert "합성법인" not in caplog.text


def test_정규화는_원응답객체와_글을_바꾸지_않는다():
    item = {"글": "합성 사업 문제.", "등급": "확인", "근거선택": ["p5-001"] * 3}
    payload = {"문장들": [item]}
    original = copy.deepcopy(payload)
    raw = json.dumps(payload, ensure_ascii=False)
    result = parse_section_response(raw, "current_challenges", evidence_pairs=PAIRS)
    assert payload == original
    assert json.loads(raw) == original
    assert result[0].citations == ("11",)
    assert result[0].text == item["글"]


@pytest.mark.parametrize("ids", [
    [], "p5-001", ("p5-001",), [None], [True], [1], [["p5-001"]],
    ["p5-001", "p5-999"], ["p5-001", "p5-003"],
])
def test_비정상_선택과_서로다른의미칸은_계속거절한다(ids):
    # JSON은 tuple을 list로 바꾸므로 실제 문자열형/배열형 계약만 여기서 시험한다.
    if isinstance(ids, tuple):
        from src.features.composer.logic import _sentence_from_item
        assert _sentence_from_item(
            {"글": "합성 문제.", "등급": "확인", "근거선택": ids},
            "current_challenges", evidence_pairs=PAIRS,
        ) is None
    else:
        assert _parse(ids) == ()


def test_완전JSON의_긴동일반복은_한근거이며_잘린JSON은_복구하지않는다():
    assert _parse(["p5-001"] * 841)[0].citations == ("11",)
    assert parse_section_response(
        '{"문장들":[{"글":"합성 문제.","등급":"확인","근거선택":["p5-001",',
        "current_challenges", evidence_pairs=PAIRS,
    ) is None


def test_서로다른17개_정상선택은_기존처럼_모두보존한다():
    pairs = {f"p5-{i:03d}": ("current_challenges:issue", str(i)) for i in range(1, 18)}
    payload = {"문장들": [{"글": "합성 문제.", "등급": "확인", "근거선택": list(pairs)}]}
    result = parse_section_response(json.dumps(payload), "current_challenges", evidence_pairs=pairs)
    assert result[0].citations == tuple(str(i) for i in range(1, 18))


def test_구필드가_함께있으면_정규화전_선택과_정확히같아야한다():
    ids = ["p5-001", "p5-001"]
    assert _parse(ids, 인용=["11", "11"], 주장슬롯="current_challenges:issue")[0].citations == ("11",)
    assert _parse(ids, 인용=["11"]) == ()
    assert _parse(ids, 인용=["22", "22"]) == ()
    assert _parse(ids, 주장슬롯="current_challenges:response") == ()


def test_필수내용_안의_동일반복도_같은문장파서로_정리한다():
    payload = {"필수내용": {
        "current_challenges:issue": [{"글": "합성 안전 문제.", "등급": "확인",
                                      "근거선택": ["p5-001"] * 3}],
        "current_challenges:response": [],
    }, "문장들": []}
    result = parse_section_response(json.dumps(payload), "current_challenges", evidence_pairs=PAIRS)
    assert len(result) == 1
    assert result[0].planned_claim_slot == "current_challenges:issue"


def test_선택쌍이없는_과거응답의_기존본문인용은_바꾸지않는다():
    payload = {"문장들": [{"글": "합성 과거 문제.", "등급": "확인", "인용": ["11"],
                            "주장슬롯": "current_challenges:issue"}]}
    raw = json.dumps(payload)
    assert parse_section_response(raw, "current_challenges", evidence_pairs=PAIRS) == parse_section_response(raw, "current_challenges")
