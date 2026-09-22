"""검수 캐시 표식만 달라지고 실제 원문·검수 계약은 같은지 무과금으로 확인한다."""

from __future__ import annotations

import hashlib
import json

import pytest

from src.features.composer import combined_relation_guard, diagram_check, verify
from src.features.composer.constants import RETRY_REMINDER
from src.features.composer.port import FlowRow
from src.features.composer.prompt_cache_constants import REVIEW_PROMPT_CACHE_ENV
from src.features.composer.prompt_metadata import PromptMetadata, with_review_prompt_cache
from src.features.composer.review_schema import FLAT_REVIEW_SCHEMA, ReviewPrompt
from src.features.composer.tests import test_review_schema as schema_fixture
from src.features.composer.tests.test_review_prompt_serialization import (
    _Case, _boundary_case, _golden_case, _large_case, _render_case,
)


@pytest.fixture(autouse=True)
def _cache_off_by_default(monkeypatch):
    monkeypatch.delenv(REVIEW_PROMPT_CACHE_ENV, raising=False)


@pytest.mark.parametrize("configured", (None, "", "0", "false", "off", "no", "invalid"))
def test_cache_requires_explicit_opt_in(monkeypatch, configured):
    if configured is not None:
        monkeypatch.setenv(REVIEW_PROMPT_CACHE_ENV, configured)
    prompt = with_review_prompt_cache("고정 지침\n개별 근거", fixed_prefix_chars=5)
    assert type(prompt) is str
    assert getattr(prompt, "cache_prefix_chars", 0) == 0


@pytest.mark.parametrize("configured", ("1", "true", "yes", "on", " TRUE "))
def test_explicit_opt_in_only_attaches_metadata(monkeypatch, configured):
    monkeypatch.setenv(REVIEW_PROMPT_CACHE_ENV, configured)
    text = "고정 지침\n개별 근거"
    prompt = with_review_prompt_cache(text, fixed_prefix_chars=5)
    assert isinstance(prompt, str)
    assert prompt.cache_prefix_chars == 5
    assert prompt.encode("utf-8") == text.encode("utf-8")
    assert getattr(prompt, "response_schema", None) is None


@pytest.mark.parametrize("grouped,prefix_chars", ((False, 10580), (True, 11722)))
@pytest.mark.parametrize("factory", (_golden_case, _large_case, _boundary_case))
def test_body_cache_preserves_entire_prompt_and_input_independent_prefix(
    monkeypatch, grouped, prefix_chars, factory,
):
    case = factory()
    plain = _render_case(verify, case, grouped)
    monkeypatch.setenv(REVIEW_PROMPT_CACHE_ENV, "1")
    cached = _render_case(verify, case, grouped)
    empty = _render_case(verify, _Case({}, ()), grouped)
    assert type(plain) is str
    assert cached.encode("utf-8") == plain.encode("utf-8")
    assert cached.cache_prefix_chars == empty.cache_prefix_chars == prefix_chars
    assert cached[:prefix_chars] == empty[:prefix_chars]
    assert cached[:prefix_chars] + cached[prefix_chars:] == plain
    assert getattr(cached, "response_schema", None) is None
    assert "[조각 " not in cached[:prefix_chars]
    if grouped:
        assert cached[prefix_chars:].startswith("\n===== 장별 검수 블록 시작: ")
    else:
        assert cached[prefix_chars:].startswith("장별 작성 범위: ")


@pytest.mark.parametrize("factory,grouped,expected", (
    (_golden_case, False, "42780485a8c79ba3c887d3460dcc2f6bb16d26ffb4a302290f254a43939ba958"),
    (_golden_case, True, "452c8fc61344d3926546733b6adccd2f85d45683deabbba70bfe3ee2f4b0671f"),
    (_boundary_case, False, "9db1c4f8a61fd7550a71fef5f68dd14f276223da796be5f890498170e6d128a2"),
    (_boundary_case, True, "58abb2b5f0f31380dcdeeadff19e69ba9781ca584a67d1b0361aadc2a8257ab7"),
))
def test_enabled_body_prompt_matches_existing_pre_change_byte_baseline(
    monkeypatch, factory, grouped, expected,
):
    # 기존 test_review_schema의 캐시 도입 전 전체 프롬프트 해시를 그대로 쓴다.
    monkeypatch.setenv(REVIEW_PROMPT_CACHE_ENV, "1")
    prompt = _render_case(verify, factory(), grouped)
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == expected


def test_grouped_cache_does_not_merge_individual_evidence_boundaries(monkeypatch):
    monkeypatch.setenv(REVIEW_PROMPT_CACHE_ENV, "1")
    case = _boundary_case()
    prompt = _render_case(verify, case, True)
    head, rest = prompt[:prompt.cache_prefix_chars], prompt[prompt.cache_prefix_chars:]
    blocks = rest.split("===== 장별 검수 블록 시작: ")[1:]
    assert len(blocks) == 2
    assert blocks[0].startswith('"past_changes"')
    assert blocks[1].startswith('"identity"')
    assert "[조각 other]" in blocks[0] and "[조각 other]" not in blocks[1]
    assert "[조각 supplementary]" in blocks[1] and "[조각 supplementary]" not in blocks[0]
    for block in blocks:
        assert block.count("[조각 shared] 원문(JSON 문자열): ") == 1
        assert json.dumps(case.fragments["shared"].text, ensure_ascii=False) in block
    assert case.fragments["shared"].text not in head
    assert "인용: 조각 shared, 조각 other, 조각 shared" in blocks[0]


@pytest.mark.parametrize("grouped", (False, True))
def test_instruction_mode_is_evaluated_each_time_and_gets_a_distinct_prefix(
    monkeypatch, grouped,
):
    monkeypatch.setenv(REVIEW_PROMPT_CACHE_ENV, "1")
    case = _boundary_case()
    prompts = []
    for enforced in (False, True):
        monkeypatch.setattr(combined_relation_guard, "COMBINED_RELATION_ENFORCED", enforced)
        cached = _render_case(verify, case, grouped)
        with monkeypatch.context() as scoped:
            scoped.setenv(REVIEW_PROMPT_CACHE_ENV, "0")
            assert cached == _render_case(verify, case, grouped)
        prompts.append(cached)
    assert prompts[0][:prompts[0].cache_prefix_chars] != prompts[1][:prompts[1].cache_prefix_chars]
    assert prompts[0][prompts[0].cache_prefix_chars:] == prompts[1][prompts[1].cache_prefix_chars:]


@pytest.mark.parametrize("items", (
    (), schema_fixture.FLOW_ITEMS,
    ((2, "identity", FlowRow(("제품", "역할", "근거"), ("2",))),),
))
def test_diagram_cache_excludes_card_switch_sources_and_rows(monkeypatch, items):
    texts = {"1": schema_fixture.TEXT, "2": "개별 회사의 다른 원문"}
    plain = diagram_check._review_prompt(items, texts)
    monkeypatch.setenv(REVIEW_PROMPT_CACHE_ENV, "1")
    cached = diagram_check._review_prompt(items, texts)
    reference = diagram_check._review_prompt((), {})
    assert cached.encode("utf-8") == plain.encode("utf-8")
    assert cached.cache_prefix_chars == reference.cache_prefix_chars == 8200
    assert cached[:cached.cache_prefix_chars] == reference[:reference.cache_prefix_chars]
    assert texts["2"] not in cached[:cached.cache_prefix_chars]
    assert getattr(cached, "response_schema", None) is None


@pytest.mark.parametrize("kind", ("flat", "grouped", "diagram"))
@pytest.mark.parametrize("initial_valid", (False, True))
def test_actual_review_preserves_call_count_retry_text_cache_and_schema(
    monkeypatch, kind, initial_valid,
):
    def run(enabled):
        monkeypatch.setenv(REVIEW_PROMPT_CACHE_ENV, "1" if enabled else "0")
        calls = []

        def ask(prompt):
            calls.append(prompt)
            if not initial_valid and len(calls) == 1:
                return "형식 오류"
            return '{"판정":[{"번호":1,"장":"identity","근거":["1"],"결과":"거짓"}]}'

        result = schema_fixture._run_review(kind, ask)
        return calls, result

    plain_calls, plain_result = run(False)
    cached_calls, cached_result = run(True)
    expected_count = 1 if initial_valid or kind == "grouped" else 2
    assert len(plain_calls) == len(cached_calls) == expected_count
    assert plain_result == cached_result
    assert getattr(cached_calls[0], "response_schema", None) is None
    for plain, cached in zip(plain_calls, cached_calls):
        assert plain.encode("utf-8") == cached.encode("utf-8")
        assert cached.cache_prefix_chars == cached_calls[0].cache_prefix_chars > 0
        assert getattr(plain, "response_schema", None) is getattr(cached, "response_schema", None)
    if len(cached_calls) == 2:
        assert isinstance(cached_calls[1], ReviewPrompt)
        assert cached_calls[1] == str(cached_calls[0]) + RETRY_REMINDER


def test_schema_wrapping_and_suffix_concatenation_keep_original_prefix():
    original = PromptMetadata("고정 지침\n개별 근거", cache_prefix_chars=5)
    prompt = ReviewPrompt(original + RETRY_REMINDER, FLAT_REVIEW_SCHEMA)
    for wrapped in (prompt, prompt + RETRY_REMINDER, prompt + "", "" + prompt):
        assert isinstance(wrapped, ReviewPrompt)
        assert wrapped.cache_prefix_chars == original.cache_prefix_chars
        assert wrapped.response_schema is FLAT_REVIEW_SCHEMA
        assert wrapped[:wrapped.cache_prefix_chars] == original[:original.cache_prefix_chars]
    assert PromptMetadata(original).cache_prefix_chars == original.cache_prefix_chars
    assert hash(original) == hash(str(original))
    assert json.dumps(original, ensure_ascii=False) == json.dumps(str(original), ensure_ascii=False)
    assert type(original[:original.cache_prefix_chars]) is str
    assert type(str(original)) is str


def test_prepended_unknown_text_disables_cache_without_losing_schema():
    prompt = ReviewPrompt("고정 지침\n개별 근거", FLAT_REVIEW_SCHEMA, cache_prefix_chars=5)
    prefixed = "새 원문\n" + prompt
    assert prefixed == "새 원문\n" + str(prompt)
    assert prefixed.cache_prefix_chars == 0
    assert prefixed.response_schema is FLAT_REVIEW_SCHEMA
    assert (prefixed + RETRY_REMINDER).cache_prefix_chars == 0


@pytest.mark.parametrize("boundary,error", (
    (-1, ValueError), (100, ValueError), (True, TypeError), (1.0, TypeError), ("1", TypeError),
))
def test_invalid_boundaries_are_rejected(boundary, error):
    with pytest.raises(error):
        PromptMetadata("짧은 지침", cache_prefix_chars=boundary)


def test_invalid_concatenation_stays_a_type_error():
    prompt = ReviewPrompt("지침", FLAT_REVIEW_SCHEMA, cache_prefix_chars=1)
    with pytest.raises(TypeError):
        prompt + 1
    with pytest.raises(TypeError):
        1 + prompt
