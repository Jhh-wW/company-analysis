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


# 2026-09-23: 평면 경로 고정 접두부 10706 → 10794 (REVIEW_JSON_GUIDE «모든 번호 빠짐없이» 안내 88자).
# 2026-09-23: 공용 GROUNDING_GUIDE «인식기준» 안내 177자 → 11142→11319, 12196→12373.
#   그 177자만 되돌리면 옛 값이 그대로 재현되고, 아래 분할 표식 단정은 값과 무관하다.
@pytest.mark.parametrize("grouped,prefix_chars", ((False, 11319), (True, 12373)))
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
    (_golden_case, False, "43903829e61c77a71c4573caf71e56f1835686c0316804b5fee478e5e932b3ed"),
    (_golden_case, True, "c4a28d93298c40d1d1cef5a86e22d54aa074fbed1ca1b7e98afa02d21135041b"),
    (_boundary_case, False, "5272127506f6f2231bd7602712734e2c78e8e9e569e65d1bfceb29dc79104c62"),
    (_boundary_case, True, "176a4d84585e524765c36ff3ca76199880366d1b6c0f3eef029620f72b2d2813"),
))
def test_enabled_body_prompt_matches_existing_pre_change_byte_baseline(
    monkeypatch, factory, grouped, expected,
):
    # test_review_schema와 같은 현재 안내문 기준이다. 캐시 포장은 원문을 바꾸지 않는다.
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
    # 2026-09-23 공용 «인식기준» 안내 177자: 8326 → 8503 (되돌리면 8326 재현).
    assert cached.cache_prefix_chars == reference.cache_prefix_chars == 8503
    assert cached[:cached.cache_prefix_chars] == reference[:reference.cache_prefix_chars]
    assert texts["2"] not in cached[:cached.cache_prefix_chars]
    assert getattr(cached, "response_schema", None) is None


@pytest.mark.parametrize("kind,packet_schema", (
    ("flat", False), ("grouped", False), ("grouped", True), ("diagram", False),
), ids=("flat", "grouped-schema-off", "grouped-schema-on", "diagram"))
@pytest.mark.parametrize("initial_valid", (False, True))
def test_actual_review_preserves_call_count_retry_text_cache_and_schema(
    monkeypatch, kind, packet_schema, initial_valid,
):
    # packet 스키마 스위치(기본 꺼짐)는 grouped 에만 뜻이 있다 — 켠 갈래만 바꾼다.
    if packet_schema:
        monkeypatch.setattr(verify, "PACKET_REVIEW_SCHEMA_ENABLED", True)

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
    # 2026-09-23 — packet(grouped)도 평문과 같은 형식 재요청 1회를 보낸다.
    expected_count = 1 if initial_valid else 2
    assert len(plain_calls) == len(cached_calls) == expected_count
    assert plain_result == cached_result
    assert getattr(cached_calls[0], "response_schema", None) is None
    for plain, cached in zip(plain_calls, cached_calls):
        assert plain.encode("utf-8") == cached.encode("utf-8")
        assert cached.cache_prefix_chars == cached_calls[0].cache_prefix_chars > 0
        assert getattr(plain, "response_schema", None) is getattr(cached, "response_schema", None)
    if len(cached_calls) == 2:
        # 평문·도식의 재요청과 스키마를 켠 packet 재요청은 스키마를 싣는다. 스키마를 끈
        # packet 재요청(기본)은 캐시 표식만 가진 문자열이다 — 경계는 어느 쪽이든 같다.
        retry_has_schema = kind != "grouped" or packet_schema
        assert isinstance(cached_calls[1], ReviewPrompt) is retry_has_schema
        assert (getattr(cached_calls[1], "response_schema", None) is not None) is retry_has_schema
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
