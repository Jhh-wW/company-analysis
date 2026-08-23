"""작가 → 독립 Reviewer → 실패만 1회 재작성 → 새 Reviewer 흐름."""

from __future__ import annotations

from src.features.writer.logic import Evidence, Sentence
from src.features.writer.revision import (
    REVERIFY_STEP,
    REWRITE_STEP,
    apply_rewrites,
    review_with_single_rewrite,
)
from src.features.writer.verify import make_pairs


EVIDENCE = {
    "future_strategy": [
        Evidence("future_strategy-1", "JYP는 2026년 신인 그룹 데뷔를 계획했다.", "조각 1·IR"),
        Evidence("future_strategy-2", "JYP는 일본 공연 횟수를 12회로 밝혔다.", "조각 2·IR"),
    ]
}
WRITTEN = {
    "future_strategy": [
        Sentence("JYP는 2026년 신인 그룹 데뷔를 계획했다.", "future_strategy-1"),
        Sentence("JYP는 일본 공연을 20회 연다.", "future_strategy-2"),
    ]
}


def test_모두_통과하면_재작성과_재검사를_안_부른다():
    calls: list[str] = []

    def reviewer(prompt, schema):
        calls.append("첫검사")
        return {
            "판정": [
                {"번호": 1, "근거에있다": True},
                {"번호": 2, "근거에있다": True},
            ]
        }, {}

    def should_not_call(prompt, schema):
        calls.append("추가")
        raise AssertionError("통과 문장에 추가 비용을 쓰면 안 된다")

    passed, steps = review_with_single_rewrite(
        reviewer,
        should_not_call,
        should_not_call,
        written=WRITTEN,
        evidence=EVIDENCE,
    )

    assert passed == WRITTEN
    assert calls == ["첫검사"]
    assert len(steps) == 1


def test_실패한_문장만_고치고_새_Reviewer가_재검사한다():
    prompts: dict[str, str] = {}

    def first_review(prompt, schema):
        prompts["첫검사"] = prompt
        return {
            "판정": [
                {"번호": 1, "근거에있다": True},
                {"번호": 2, "근거에있다": False},
            ]
        }, {"in": 100}

    def rewrite(prompt, schema):
        prompts["재작성"] = prompt
        return {
            "재작성": [
                {"번호": 2, "글": "JYP는 일본 공연을 12회 진행한다고 밝혔다."}
            ]
        }, {"in": 50}

    def second_review(prompt, schema):
        prompts["재검사"] = prompt
        return {"판정": [{"번호": 1, "근거에있다": True}]}, {"in": 30}

    passed, steps = review_with_single_rewrite(
        first_review,
        rewrite,
        second_review,
        written=WRITTEN,
        evidence=EVIDENCE,
    )

    texts = [sentence.text for sentence in passed["future_strategy"]]
    assert texts == [
        "JYP는 2026년 신인 그룹 데뷔를 계획했다.",
        "JYP는 일본 공연을 12회 진행한다고 밝혔다.",
    ]
    assert passed["future_strategy"][1].sid == "future_strategy-2"
    # 첫 통과 문장은 재작성·재검사 프롬프트에 다시 실리지 않는다.
    assert "2026년 신인" not in prompts["재작성"]
    assert "2026년 신인" not in prompts["재검사"]
    assert steps[1]["step"] == REWRITE_STEP
    assert steps[2]["step"] == REVERIFY_STEP


def test_두_번째_Reviewer도_거짓으로_보면_삭제한다():
    passed, steps = review_with_single_rewrite(
        lambda p, s: ({"판정": [
            {"번호": 1, "근거에있다": True},
            {"번호": 2, "근거에있다": False},
        ]}, {}),
        lambda p, s: ({"재작성": [
            {"번호": 2, "글": "JYP는 일본 공연을 12회 진행한다고 밝혔다."}
        ]}, {}),
        lambda p, s: ({"판정": [{"번호": 1, "근거에있다": False}]}, {}),
        written=WRITTEN,
        evidence=EVIDENCE,
    )

    assert [sentence.sid for sentence in passed["future_strategy"]] == ["future_strategy-1"]
    assert steps[-1]["버림"] == 1


def test_첫_Reviewer가_죽으면_전부_버리고_추가호출하지_않는다():
    extra_calls: list[int] = []

    def extra(prompt, schema):
        extra_calls.append(1)
        return {}, {}

    passed, steps = review_with_single_rewrite(
        lambda p, s: (None, {"error": "APIError"}),
        extra,
        extra,
        written=WRITTEN,
        evidence=EVIDENCE,
    )

    # 원문과 strip 완전일치한 1번은 코드로 이미 증명됐고,
    # AI가 필요한 2번만 안전상 버린다.
    assert [sentence.sid for sentence in passed["future_strategy"]] == ["future_strategy-1"]
    assert extra_calls == []
    assert steps[0]["재작성대상"] == 0


def test_판정_누락은_삭제하지만_재작성하지_않는다():
    extra_calls: list[int] = []

    def extra(prompt, schema):
        extra_calls.append(1)
        return {}, {}

    passed, steps = review_with_single_rewrite(
        lambda p, s: ({"판정": [{"번호": 1, "근거에있다": True}]}, {}),
        extra,
        extra,
        written=WRITTEN,
        evidence=EVIDENCE,
    )

    assert [sentence.sid for sentence in passed["future_strategy"]] == ["future_strategy-1"]
    assert extra_calls == []
    assert steps[0]["재작성대상"] == 0


def test_재작성이_빈손이면_두_번째_Reviewer를_안_부른다():
    second_calls: list[int] = []

    passed, steps = review_with_single_rewrite(
        lambda p, s: ({"판정": [
            {"번호": 1, "근거에있다": True},
            {"번호": 2, "근거에있다": False},
        ]}, {}),
        lambda p, s: ({"재작성": []}, {}),
        lambda p, s: (second_calls.append(1), ({}, {}))[1],
        written=WRITTEN,
        evidence=EVIDENCE,
    )

    assert [sentence.sid for sentence in passed["future_strategy"]] == ["future_strategy-1"]
    assert second_calls == []
    assert len(steps) == 2


def test_재작성은_원래_fact_evidence_sid를_바꾸지_못한다():
    pairs = make_pairs(WRITTEN, EVIDENCE)

    revisions, discarded = apply_rewrites(
        {"재작성": [{"번호": 2, "글": "공연 횟수는 12회다."}]},
        [pairs[1]],
    )

    assert revisions[0].sentence.sid == WRITTEN["future_strategy"][1].sid
    assert revisions[0].original.evidence.sid == "future_strategy-2"
    assert sum(discarded.values()) == 0


def test_중복_재작성_번호는_안전상_버린다():
    pair = make_pairs(WRITTEN, EVIDENCE)[1]

    revisions, discarded = apply_rewrites(
        {"재작성": [
            {"번호": 2, "글": "첫 답"},
            {"번호": 2, "글": "두 번째 답"},
        ]},
        [pair],
    )

    assert revisions == []
    assert discarded["중복"] == 1
