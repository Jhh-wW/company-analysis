"""파싱 재요청에만 적용하는 네이티브 검수 계약을 무과금으로 확인한다.

시험 의존성 jsonschema로 구조를 검사하고 기존 의미 검증을 함께 실행한다.
SDK 변환이 같다는 사실과 실제 제공자의 문법 컴파일 성공은 별개다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy

import pytest
from anthropic import transform_schema
from jsonschema import Draft202012Validator

from src.features.composer import diagram_check, review_schema, verify
from src.features.composer.constants import RETRY_REMINDER
from src.features.composer.grounding import constrain_verdicts, grounding_problem
from src.features.composer.port import AskFatalError, CollectedFragment, ComposedSentence, FlowRow
from src.features.composer.review_schema import (
    DIAGRAM_REVIEW_SCHEMA, FLAT_REVIEW_SCHEMA, ReviewPrompt,
)
from src.features.composer.tests import test_numeric_quote_refs as numeric_fixture
from src.features.composer.tests.test_review_prompt_serialization import (
    _boundary_case, _golden_case, _render_case,
)


SCHEMAS = (FLAT_REVIEW_SCHEMA, DIAGRAM_REVIEW_SCHEMA)
SCHEMA_NAMES = ("flat", "diagram")
REVIEW_KINDS = ("flat", "grouped", "diagram")
TEXT = "회사는 제품을 판매한다."
SENTENCE = ComposedSentence(TEXT, ("1",), "확인")
FRAGMENTS = {"1": CollectedFragment("1", "공시", TEXT)}
FLAT_ITEMS = (verify._ReviewItem(1, SENTENCE, "identity"),)
GROUPED_ITEMS = (verify._GroupedReviewItem(
    1, "identity", "문장", ("1",), sentence=SENTENCE,
),)
FLOW_ITEMS = ((1, "business_model", FlowRow(("제품", "판매", "고객"), ("1",))),)


def _schema_nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _schema_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _schema_nodes(child)


@pytest.mark.parametrize("schema", SCHEMAS, ids=SCHEMA_NAMES)
def test_schema_roundtrips_sdk_and_stays_below_explicit_complexity_limits(schema):
    original = deepcopy(schema)
    assert transform_schema(schema) == schema == original
    nodes = list(_schema_nodes(schema))
    assert sum(len(set(n.get("properties", {})) - set(n.get("required", [])))
               for n in nodes) <= 24
    assert sum("anyOf" in n or isinstance(n.get("type"), list) for n in nodes) <= 16
    assert all(n["additionalProperties"] is False for n in nodes if n.get("type") == "object")
    row = schema["properties"]["판정"]["items"]
    keys = list(row["properties"])
    reason = "대조근거" if schema is DIAGRAM_REVIEW_SCHEMA else "근거대조"
    assert keys.index(reason) < keys.index("결과")
    assert reason in row["required"]
    assert "검증근거" not in row["required"]
    assert row["properties"]["번호"] == {"type": "integer"}
    assert row["properties"]["근거"]["items"] == {"type": "string"}


def test_prompt_is_string_and_concatenation_preserves_schema_and_exact_bytes():
    value = '  원문 "인용" \\ 경로\t탭\n줄\u2028끝  '
    prompt = ReviewPrompt(value, FLAT_REVIEW_SCHEMA)
    assert isinstance(prompt, str)
    assert isinstance(prompt.response_schema, Mapping)
    assert prompt == value and hash(prompt) == hash(value)
    assert json.dumps(prompt, ensure_ascii=False) == json.dumps(value, ensure_ascii=False)
    for wrapped, plain in (
        (prompt + RETRY_REMINDER, value + RETRY_REMINDER),
        ("접두\n" + prompt, "접두\n" + value),
        (prompt + "" + RETRY_REMINDER, value + RETRY_REMINDER),
    ):
        assert wrapped.encode("utf-8") == plain.encode("utf-8")
        assert wrapped.response_schema is FLAT_REVIEW_SCHEMA
    with pytest.raises(TypeError):
        prompt + 1


def _schema_sha(schema):
    encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# ★ 2026-09-23 정성 인식기준 근거 배열(«인식기준»)을 더했고, 이어서 관계 항목에
#   결합 유형의 두 칸(«범위»·«관계»)을 더했다. 제공자가 실제로 문법을 컴파일해
#   받아들인 값은 두 추가 «전» 해시(accepted)다. 새 스키마는 그 값에서 선택 배열
#   하나와 선택 문자열 칸 두 개만 더한 것임을 아래에서 증명한다 — 새 스키마의
#   제공자 컴파일 성공은 유료 호출 없이는 확인되지 않았으므로 «accepted»로 부르지
#   않는다(current). recognition은 인식기준만 더한 직전 값이다.
@pytest.mark.parametrize("schema,accepted,recognition,current", (
    (FLAT_REVIEW_SCHEMA,
     "ec40152ca2ad8aa43192180dd0583bff4a6c3f5e52042ac1f2b915bc7fd0b94d",
     "ff4e031accf5776e3f189483531e5c5270bea8c8d1ec8cd6da75008384f901ab",
     "bb974f18bc5d33def32725d9d6a401a3eb0265f878af6ac624bcb7ff2af966cf"),
    (DIAGRAM_REVIEW_SCHEMA,
     "ba1d778829286673bd6cde6ebb5d559274c78f0202b92d6e4128891736dfc1c8",
     "43db498656efc5545d712fcc1f0c9893fe69fbd1f2dcca8a42a470c03e4bcb8a",
     "5e650e7f2d7b91af0d6a9d45f632f0880accfd835b8c3d801207880d02660201"),
))
def test_retry_schema_hash_matches_provider_accepted_schema(schema, accepted, recognition, current):
    assert _schema_sha(schema) == current
    relation = schema["$defs"]["grounding"]["properties"]["관계"]["items"]
    assert relation["properties"]["범위"] == relation["properties"]["관계"] == {"type": "string"}
    assert relation["required"] == ["근거", "원문", "유형"]
    assert relation["additionalProperties"] is False
    previous = deepcopy(schema)
    for key in ("범위", "관계"):
        del previous["$defs"]["grounding"]["properties"]["관계"]["items"]["properties"][key]
    assert _schema_sha(previous) == recognition
    schema = previous
    grounding = schema["$defs"]["grounding"]
    assert grounding["properties"]["인식기준"] == {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"표현": {"type": "string"}, "근거": {"type": "string"},
                           "원문": {"type": "string"}},
            "required": ["표현", "근거", "원문"],
            "additionalProperties": False,
        },
    }
    assert "인식기준" not in grounding["required"]
    previous = deepcopy(schema)
    del previous["$defs"]["grounding"]["properties"]["인식기준"]
    assert _schema_sha(previous) == accepted


@pytest.mark.parametrize("kind", REVIEW_KINDS)
@pytest.mark.parametrize("empty", (False, True))
def test_initial_builders_return_plain_strings_without_schema(kind, empty):
    if kind == "flat":
        prompt = verify._build_review_prompt(() if empty else FLAT_ITEMS, FRAGMENTS, "")
    elif kind == "grouped":
        prompt = verify._build_grouped_review_prompt(() if empty else GROUPED_ITEMS, FRAGMENTS, None)
    else:
        prompt = diagram_check._review_prompt(() if empty else FLOW_ITEMS, {"1": TEXT})
    assert type(prompt) is str
    assert getattr(prompt, "response_schema", None) is None
    assert prompt


# 2026-09-23 공용 GROUNDING_GUIDE 끝에 «인식기준» 근거 안내 177자 추가. 그 177자만
#   되돌리면 네 해시·도식 두 해시가 모두 직전 값(a87c264a·c39e729c·73a6b9f9·
#   c9a8489a / a370eb2b·f769528b)과 같음을 재생해 확인했다(tmp 무과금 재생 기록).
@pytest.mark.parametrize("factory,grouped,expected", (
    (_golden_case, False, "43903829e61c77a71c4573caf71e56f1835686c0316804b5fee478e5e932b3ed"),
    (_golden_case, True, "c4a28d93298c40d1d1cef5a86e22d54aa074fbed1ca1b7e98afa02d21135041b"),
    (_boundary_case, False, "5272127506f6f2231bd7602712734e2c78e8e9e569e65d1bfceb29dc79104c62"),
    (_boundary_case, True, "176a4d84585e524765c36ff3ca76199880366d1b6c0f3eef029620f72b2d2813"),
))
def test_body_prompt_bytes_match_pre_schema_baseline(factory, grouped, expected):
    # de0a68e1의 원래 builder로 재생한 전체 UTF-8 프롬프트 해시다.
    # ★ 기준값은 «스키마 포장 이전 builder»가 «현재 안내문»으로 만든 프롬프트다. 안내문이
    #   바뀌면(2026-09-14: 역할·과금 안내의 유형 이름을 「」로 교체) 값도 함께 갱신한다 —
    #   스키마 포장 자체는 이 프롬프트를 바꾸지 않음을 같은 날 문구만 되돌려 재계산해 확인했다.
    # 2026-09-22 회사 계획 귀속 안내 126자 추가: 옛 안내문만 복원하면 옛 해시와 같다.
    # 2026-09-23 평면 경로 REVIEW_JSON_GUIDE 에 «모든 번호를 빠짐없이 판정» 안내 88자 추가
    #   (뤼튼 실측 요청 50·응답 20). 묶음(grouped) 안내문은 그대로라 그쪽 해시는 같다.
    # 2026-09-23 원칙/특례·제품 귀속·제외 주석·발표/실행 시점 안내 추가.
    # 추가 안내만 제거한 4개 해시는 변경 직전 프롬프트와 같음을 재생해 확인했다.
    prompt = _render_case(verify, factory(), grouped)
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == expected


@pytest.mark.parametrize("items,expected", (
    ((), "4d9a30dd4be59739ee487d661b9f388d0f50bc30df1d9aa725390a86972191cc"),
    (FLOW_ITEMS, "7a0baa2a1ca065230e04d05b2d8e56381c55c8a6ce809df65fd8112872d9a7e1"),
))
def test_diagram_prompt_bytes_match_pre_schema_baseline(items, expected):
    prompt = diagram_check._review_prompt(items, {"1": TEXT})
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == expected


def _run_review(kind, ask):
    if kind == "flat":
        return verify._ask_verdicts(ask, FLAT_ITEMS, FRAGMENTS, "")
    if kind == "grouped":
        return verify._ask_grouped_verdicts(ask, GROUPED_ITEMS, FRAGMENTS, None)
    section, row = FLOW_ITEMS[0][1:]
    return diagram_check._review_rows(((section, (row,)),), {"1": TEXT}, ask)


@pytest.mark.parametrize("kind", REVIEW_KINDS)
def test_first_valid_response_never_requests_native_schema(kind):
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return '{"판정":[{"번호":1,"장":"identity","근거":["1"],"결과":"참"}]}'

    _run_review(kind, ask)
    assert len(calls) == 1
    assert type(calls[0]) is str
    assert getattr(calls[0], "response_schema", None) is None


def test_flat_retry_keeps_metadata_and_original_retry_text():
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return "invalid" if len(calls) == 1 else '{"판정":[{"번호":1,"결과":"참"}]}'

    assert verify._ask_verdicts(ask, FLAT_ITEMS, FRAGMENTS, "") == {1: "참"}
    assert len(calls) == 2
    assert type(calls[0]) is str
    assert calls[1] == calls[0] + RETRY_REMINDER
    assert calls[1].response_schema is FLAT_REVIEW_SCHEMA


def test_diagram_retry_keeps_metadata_and_call_count():
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return "invalid" if len(calls) == 1 else '{"판정":[{"번호":1,"결과":"거짓"}]}'

    section, row = FLOW_ITEMS[0][1:]
    kept, dropped = diagram_check._review_rows(((section, (row,)),), {"1": TEXT}, ask)
    assert kept == {section: ()} and dropped
    assert len(calls) == 2
    assert type(calls[0]) is str
    assert calls[1] == calls[0] + RETRY_REMINDER
    assert calls[1].response_schema is DIAGRAM_REVIEW_SCHEMA


@pytest.mark.parametrize("separate_retry", (False, True))
def test_flat_native_retry_preserves_initial_retry_callable(separate_retry):
    calls = []

    def initial(prompt):
        calls.append(("initial", prompt))
        return "형식 오류" if len(calls) == 1 else '{"판정":[{"번호":1,"결과":"참"}]}'

    def retry(prompt):
        calls.append(("retry", prompt))
        return '{"판정":[{"번호":1,"결과":"참"}]}'

    def followup(prompt):
        pytest.fail("최초 검수가 후속 호출자를 사용했습니다")

    assert verify._ask_verdicts(
        followup, FLAT_ITEMS, FRAGMENTS, "", initial_ask=initial,
        initial_retry_ask=retry if separate_retry else None,
    ) == {1: "참"}
    assert [name for name, _prompt in calls] == ["initial", "retry" if separate_retry else "initial"]
    # 2026-09-22: 최초 본문 검수(initial_ask)는 «첫 요청부터» 스키마를 싣는다 —
    #   test_initial_review_schema.py 가 이 계약을 고정한다. 재요청 문구는 그대로다.
    assert isinstance(calls[0][1], ReviewPrompt)
    assert calls[0][1].response_schema is FLAT_REVIEW_SCHEMA
    assert calls[1][1] == calls[0][1] + RETRY_REMINDER
    assert calls[1][1].response_schema is FLAT_REVIEW_SCHEMA


def test_followup_retry_does_not_borrow_initial_retry_callable():
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return "형식 오류" if len(calls) == 1 else '{"판정":[{"번호":1,"결과":"참"}]}'

    def initial_retry(prompt):
        pytest.fail("후속 검수가 최초 재요청 호출자를 사용했습니다")

    assert verify._ask_verdicts(
        ask, FLAT_ITEMS, FRAGMENTS, "", initial_retry_ask=initial_retry,
    ) == {1: "참"}
    assert len(calls) == 2 and type(calls[0]) is str
    assert calls[1].response_schema is FLAT_REVIEW_SCHEMA


@pytest.mark.parametrize("kind,schema", tuple(zip(SCHEMA_NAMES, SCHEMAS)))
@pytest.mark.parametrize("failure", ("invalid", "error", "fatal"))
def test_native_retry_failure_does_not_add_hidden_fallback(kind, schema, failure):
    calls = []

    def ask(prompt):
        calls.append(prompt)
        if len(calls) == 1 or failure == "invalid":
            return "형식 오류"
        if failure == "fatal":
            raise AskFatalError("제공자 장애")
        raise RuntimeError("스키마 요청 오류")

    if failure == "fatal":
        with pytest.raises(AskFatalError):
            _run_review(kind, ask)
    else:
        result = _run_review(kind, ask)
        if kind == "flat":
            assert result is None
        else:
            assert result[0] == {"business_model": ()}
    assert len(calls) == 2
    assert type(calls[0]) is str
    assert calls[1].response_schema is schema


def test_semantic_proof_failure_does_not_enable_native_retry():
    text = "매출액은 5억원이다."
    item = verify._ReviewItem(1, ComposedSentence(text, ("1",), "확인"), "past_changes")
    fragments = {"1": CollectedFragment("1", "공시", "매출액은 500000000원이다.")}
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return '{"판정":[{"번호":1,"결과":"참"}]}'

    verdicts = verify._ask_verdicts(ask, (item,), fragments, "")
    assert verdicts[1] != "참"
    assert len(calls) == 1 and type(calls[0]) is str


#: packet 스키마 스위치의 «소비 지점»(verify 가 이름을 가져다 쓴다).
PACKET_SCHEMA_SWITCH = "src.features.composer.verify.PACKET_REVIEW_SCHEMA_ENABLED"


def test_packet_schema_switch_is_off_by_default():
    """packet 검수의 네이티브 스키마는 기본 꺼짐이다(2026-09-23 총괄 결정).

    보관된 실제 공급자 호출 24건 모두 스키마 없이 나갔다 — 스키마 요청은 실제
    공급자로 검증된 적이 없고, 거절되면 AskFatalError 로 요청 전체가 멈춘다.
    켜려면 유료 실측으로 먼저 확인하고 이 기대값을 «함께» 바꾼다.
    """
    assert review_schema.PACKET_REVIEW_SCHEMA_ENABLED is False
    assert verify.PACKET_REVIEW_SCHEMA_ENABLED is False


@pytest.mark.parametrize("schema_enabled", (False, True), ids=("schema-off", "schema-on"))
@pytest.mark.parametrize("raw", ("invalid", '{"판정":[]}'))
def test_grouped_retries_once_and_then_fails_closed_without_fallback(
    monkeypatch, raw, schema_enabled,
):
    """2026-09-23 — packet 도 평문과 같은 형식 재요청 1회. 그 뒤에는 숨은 3차 호출이 없다.

    예전 «1회 고정»에서는 JSON 한 글자 오류로 판정 42행이 통째로 사라졌다
    (`test_grouped_review_resilience.py`). 첫 요청은 initial_ask 가 없으므로
    예전처럼 표식 없는 문자열이고, 재요청은 같은 글자 + 형식 상기문이다. 재요청의
    스키마는 스위치(기본 꺼짐)를 따른다.
    """
    if schema_enabled:
        monkeypatch.setattr(PACKET_SCHEMA_SWITCH, True)
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return raw

    assert verify._ask_grouped_verdicts(ask, GROUPED_ITEMS, FRAGMENTS, None) is None
    assert len(calls) == 2
    assert type(calls[0]) is str
    assert getattr(calls[0], "response_schema", None) is None
    assert calls[1] == calls[0] + RETRY_REMINDER
    expected = FLAT_REVIEW_SCHEMA if schema_enabled else None
    assert getattr(calls[1], "response_schema", None) is expected


@pytest.mark.parametrize("rewrite", (False, True))
def test_summary_and_rewritten_sentence_start_with_plain_review(rewrite):
    corrected = "회사는 플랫폼을 보유한다."
    bad = "회사는 플랫폼을 자체 개발했다."
    calls = []

    def ask(prompt):
        calls.append(prompt)
        if verify.REWRITE_PROMPT_HEADER in prompt:
            return corrected
        verdict = "거짓" if rewrite and len(calls) == 1 else "참"
        return json.dumps({"판정": [{"번호": 1, "결과": verdict}]}, ensure_ascii=False)

    kept = verify.verify_sentences(
        (ComposedSentence(bad if rewrite else corrected, ("1",), "확인"),),
        (CollectedFragment("1", "공시", corrected),), None, ask,
    )
    assert [sentence.text for sentence in kept] == [corrected]
    assert len(calls) == (3 if rewrite else 1)
    assert all(type(prompt) is str for prompt in calls)
    assert all(getattr(prompt, "response_schema", None) is None for prompt in calls)


def test_rewrite_is_still_unmarked_plain_text():
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return TEXT

    assert verify._ask_rewrite(ask, SENTENCE, FRAGMENTS) == TEXT
    assert len(calls) == 1 and type(calls[0]) is str
    assert getattr(calls[0], "response_schema", None) is None


def test_legacy_parsers_keep_reasonless_and_numeric_string_compatibility():
    raw = '{"판정":[{"번호":"1","장":"identity","근거":[1],"결과":"참"}]}'
    assert verify._parse_verdicts(raw) == {1: "참"}
    assert verify._parse_grouped_verdicts(raw, {1: "identity"}, {1: frozenset({"1"})}) == {1: "참"}
    assert diagram_check._parse_verdicts(raw) == {1: "참"}
    assert all(not Draft202012Validator(schema).is_valid(json.loads(raw)) for schema in SCHEMAS)


@pytest.mark.parametrize("kind", REVIEW_KINDS)
def test_fatal_provider_error_is_not_schema_fallback(kind):
    calls = []
    error = AskFatalError("제공자 장애")

    def ask(prompt):
        calls.append(prompt)
        raise error

    with pytest.raises(AskFatalError) as raised:
        if kind == "flat":
            verify._ask_verdicts(ask, FLAT_ITEMS, FRAGMENTS, "")
        elif kind == "grouped":
            verify._ask_grouped_verdicts(ask, GROUPED_ITEMS, FRAGMENTS, None)
        else:
            section, row = FLOW_ITEMS[0][1:]
            diagram_check._review_rows(((section, (row,)),), {"1": TEXT}, ask)
    assert raised.value is error and len(calls) == 1
    assert type(calls[0]) is str


@pytest.fixture
def schema_validator():
    return Draft202012Validator


def _native_response(rows, schema):
    """구형 fixture에 현재 지시의 설명만 더하며 proof 생략·값은 보존한다."""
    properties = schema["properties"]["판정"]["items"]["properties"]
    reason = "대조근거" if "대조근거" in properties else "근거대조"
    return {"판정": [{reason: "공시: 원문 대조", **row} for row in rows]}


@pytest.mark.parametrize("schema", SCHEMAS, ids=SCHEMA_NAMES)
@pytest.mark.parametrize("result", ("참", "거짓", "애매"))
@pytest.mark.parametrize("proof", (None, {}, {"수치": [], "추세": [], "시점": [], "관계": [], "미래근거": []}))
def test_schema_accepts_evidence_omission_without_fabricating_proof(schema_validator, schema, result, proof):
    row = {"번호": 1, "장": "identity", "근거": ["1"], "결과": result}
    if proof is not None:
        row["검증근거"] = proof
    schema_validator.check_schema(schema)
    schema_validator(schema).validate(_native_response([row], schema))
    schema_validator(schema).validate({"판정": []})
    # 구조가 맞아도 수치 증명을 요구하는 참·애매는 기존 가드가 제외한다.
    numeric_text = "매출액은 5억원이다."
    raw = json.dumps({"판정": [row]}, ensure_ascii=False)
    constrained, problems = constrain_verdicts(
        raw, {1: result}, {1: (numeric_text, {"1": "매출액은 500000000원이다."})},
    )
    if result == "거짓":
        assert constrained == {1: "거짓"} and not problems
    else:
        assert constrained[1] != "참" and problems


@pytest.mark.parametrize("schema", SCHEMAS, ids=SCHEMA_NAMES)
@pytest.mark.parametrize("references", (False, True))
def test_existing_numeric_fixtures_pass_schema_and_semantics_unchanged(schema_validator, schema, references):
    entries = (numeric_fixture._three_year_ref_entries() if references
               else numeric_fixture._three_year_direct_entries())
    row = {"번호": 1, "장": "past_changes", "근거": ["공시"], "결과": "참",
           "검증근거": {"수치": entries}}
    before = deepcopy(row)
    payload = _native_response([row], schema)
    schema_validator(schema).validate(payload)
    schema_validator(transform_schema(schema)).validate(payload)
    assert grounding_problem(numeric_fixture._THREE_YEAR_TEXT, numeric_fixture._THREE_YEAR_SOURCE, row) == ""
    assert row == before


@pytest.mark.parametrize("reference", (0, 2, 99))
def test_schema_cannot_approve_bad_quote_reference(schema_validator, reference):
    entries = numeric_fixture._three_year_ref_entries()
    entries[1]["원문참조"] = reference
    row = {"번호": 1, "결과": "참", "검증근거": {"수치": entries}}
    schema_validator(FLAT_REVIEW_SCHEMA).validate(_native_response([row], FLAT_REVIEW_SCHEMA))
    assert grounding_problem(numeric_fixture._THREE_YEAR_TEXT, numeric_fixture._THREE_YEAR_SOURCE, row)


@pytest.mark.parametrize("case_name", (
    "test_same_metric_increasing_series_passes",
    "test_explicit_current_source_preserves_current_claim",
))
def test_existing_trend_and_time_fixtures_fit_all_schemas(schema_validator, monkeypatch, case_name):
    from src.features.composer.tests import test_grounding as fixtures

    calls = []

    def checked(text, sources, evidence):
        row = {"번호": 1, "장": "past_changes", "근거": list(sources),
               "결과": "참", **evidence}
        for schema in SCHEMAS:
            payload = _native_response([row], schema)
            schema_validator(schema).validate(payload)
            schema_validator(transform_schema(schema)).validate(payload)
        if "추세" in evidence["검증근거"]:
            # 원문항목이 없으면 항목을 쓰는 기존 관측 fallback도 보존한다.
            omitted = deepcopy(row)
            for trend in omitted["검증근거"]["추세"]:
                for point in trend["관측"]:
                    point.pop("원문항목", None)
            for schema in SCHEMAS:
                schema_validator(schema).validate(_native_response([omitted], schema))
            assert grounding_problem(text, sources, omitted) == ""
        calls.append(row)
        return grounding_problem(text, sources, evidence)

    monkeypatch.setattr(fixtures, "grounding_problem", checked)
    getattr(fixtures, case_name)()
    assert calls


@pytest.mark.parametrize("section,text", (
    ("operations_partners", "회사는 여행 예약 플랫폼을 자체 개발했다."),
    ("past_changes", "회사는 공급 차질이 납기 지연의 주된 원인이라고 밝혔다."),
    ("future_strategy", ""),
))
def test_existing_role_cause_and_future_fixtures_fit_actual_retry_schema(
    schema_validator, monkeypatch, section, text,
):
    from src.features.composer.tests import test_body_review_comparison as fixtures

    original = fixtures.verify_report
    calls = []

    def checked(report, fragments, table, ask, **kwargs):
        sent = []

        def checked_ask(prompt):
            sent.append(prompt)
            if len(sent) == 1:
                assert type(prompt) is str
                return "형식 오류"
            raw = ask(prompt)
            assert prompt.response_schema is FLAT_REVIEW_SCHEMA
            payload = json.loads(raw)
            schema_validator(prompt.response_schema).validate(payload)
            schema_validator(transform_schema(prompt.response_schema)).validate(payload)
            calls.append(payload)
            return raw
        result = original(report, fragments, table, checked_ask, **kwargs)
        assert len(sent) == 2
        return result

    monkeypatch.setattr(fixtures, "verify_report", checked)
    if section == "future_strategy":
        case = next(case for case in fixtures.CONTRASTS if case[0] == "plan")
        fixtures.test_controlled_false_removes_addition_and_true_keeps_supported_peer(case, False, "해석")
    else:
        fixtures.test_controlled_true_preserves_explicit_hr_cause_condition_and_development(section, text, False)
    assert len(calls) == 1


def test_existing_golden_responses_fit_flat_retry_schema(schema_validator):
    import re
    from src.features.composer.tests.review_evidence_fixture import grounded_review_response

    prompt = _render_case(verify, _golden_case(), False)
    # 기존 fixture 독자의 별도 등급 줄 처리만 보정해 응답을 읽는다.
    readable = re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt)
    payload = json.loads(grounded_review_response(readable))
    assert len(payload["판정"]) > 10
    native = _native_response(payload["판정"], FLAT_REVIEW_SCHEMA)
    schema_validator(FLAT_REVIEW_SCHEMA).validate(native)
    schema_validator(transform_schema(FLAT_REVIEW_SCHEMA)).validate(native)


@pytest.mark.parametrize("schema", SCHEMAS, ids=SCHEMA_NAMES)
def test_fee_and_outlook_proof_fields_remain_available(schema_validator, schema):
    from src.features.composer.future_plan_guard import future_plan_prose_problem
    from src.features.composer.role_binding import role_binding_problem

    fee_source = "가람 서비스는 수수료를 부과한다."
    fee_proof = {"관계": [{"유형": "과금", "근거": "공시", "원문": fee_source,
                          "대상": "가람 서비스", "역할값": "수수료"}]}
    future_source = "당사는 신규 플랫폼 사업을 확대할 것으로 기대하고 있습니다."
    future_text = "회사는 신규 플랫폼 사업을 확대할 것으로 기대한다."
    future_proof = {"미래근거": [{"근거": "공시", "대상": "신규 플랫폼 사업", "활동": "확대",
                               "원문": future_source, "양태": "전망"}]}
    for proof in (fee_proof, future_proof):
        row = {"번호": 1, "장": "future_strategy", "근거": ["공시"], "결과": "참", "검증근거": proof}
        payload = _native_response([row], schema)
        schema_validator(schema).validate(payload)
        schema_validator(transform_schema(schema)).validate(payload)
    assert role_binding_problem(fee_source, {"공시": fee_source}, fee_proof, None) == ""
    assert future_plan_prose_problem(future_text, {"공시": future_source}, future_proof) == ""


@pytest.mark.parametrize("defect", ("both", "missing", "null", "boolean"))
def test_malformed_numeric_proof_is_rejected_by_schema_and_semantics(schema_validator, defect):
    entries = numeric_fixture._three_year_ref_entries()
    if defect == "both":
        entries[1]["원문"] = entries[0]["원문"]
    elif defect == "missing":
        del entries[1]["원문참조"]
    elif defect == "null":
        entries[1]["원문참조"] = None
    else:
        entries[1]["원문참조"] = True
    row = {"번호": 1, "결과": "참", "검증근거": {"수치": entries}}
    assert not schema_validator(FLAT_REVIEW_SCHEMA).is_valid(_native_response([row], FLAT_REVIEW_SCHEMA))
    assert grounding_problem(numeric_fixture._THREE_YEAR_TEXT, numeric_fixture._THREE_YEAR_SOURCE, row)


# ══════════════════════════════════════════════════════════
# 결합(수량 범위 결속) 항목 — 안내문대로 쓴 항목이 실제 공급자 스키마를 통과하고
# 실제 파서·가드까지 가서 원문 결속으로만 승인되는가
# ══════════════════════════════════════════════════════════

_COMBINED_CLAIM = "당사는 32개 협력사로부터 부품을 납품받는다."
_COMBINED_SOURCE = "당사는 32개 협력사로부터 부품을 납품받습니다."
_COMBINED_ITEM = {"근거": "1", "범위": "32개", "관계": "납품",
                  "원문": "당사는 32개 협력사로부터 부품을 납품받", "유형": "결합"}


def _combined_payload(schema, *relations):
    row = {"번호": 1, "장": "identity", "근거": ["1"], "결과": "참",
           "검증근거": {"관계": list(relations)}}
    return _native_response([row], schema)


def _assert_fits(schema_validator, schema, payload):
    schema_validator(schema).validate(payload)
    schema_validator(transform_schema(schema)).validate(payload)


@pytest.mark.parametrize("schema", SCHEMAS, ids=SCHEMA_NAMES)
def test_combined_item_from_the_guide_fits_actual_provider_schemas(schema_validator, schema):
    """안내문(combined_relation_constants)이 요구하는 다섯 칸 그대로 — 기존 원인·역할 항목과 함께."""
    from src.features.composer.combined_relation_constants import (
        COMBINED_RELATION_REVIEW_GUIDE_OBSERVED,
    )

    for key in _COMBINED_ITEM:
        assert f'"{key}"' in COMBINED_RELATION_REVIEW_GUIDE_OBSERVED
    cause = {"근거": "1", "원문": "공급 차질로 납기가 지연됐다.", "유형": "인과",
             "원인": "공급 차질", "결과": "납기 지연"}
    role = {"근거": "1", "원문": "가람 서비스는 수수료를 부과한다.", "유형": "과금",
            "대상": "가람 서비스", "역할값": "수수료"}
    _assert_fits(schema_validator, schema, _combined_payload(schema, _COMBINED_ITEM, cause, role))
    assert transform_schema(schema) == schema  # SDK 정규화도 두 칸을 그대로 둔다


@pytest.mark.parametrize("schema", SCHEMAS, ids=SCHEMA_NAMES)
@pytest.mark.parametrize("defect", (
    {"범휘": "32개"},            # 칸 이름 오타 — 알 수 없는 키
    {"관계어": "납품"},          # 칸 이름 오타
    {"비고": "추가 설명"},       # 알 수 없는 키
    {"범위": 32},               # 문자열이 아님
    {"관계": None},             # null 불허
    {"유형": "결함"},           # 유형 enum 오타
))
def test_combined_item_typos_and_unknown_keys_stay_rejected(schema_validator, schema, defect):
    item = {**_COMBINED_ITEM, **defect}
    payload = _combined_payload(schema, item)
    assert not schema_validator(schema).is_valid(payload)
    assert not schema_validator(transform_schema(schema)).is_valid(payload)


@pytest.mark.parametrize("schema", SCHEMAS, ids=SCHEMA_NAMES)
@pytest.mark.parametrize("missing", ("근거", "원문", "유형"))
def test_combined_item_keeps_relation_required_fields(schema_validator, schema, missing):
    item = {key: value for key, value in _COMBINED_ITEM.items() if key != missing}
    assert not schema_validator(schema).is_valid(_combined_payload(schema, item))


def test_schema_valid_combined_item_reaches_parser_and_guard_with_source_binding(schema_validator):
    """실제 파서(support_entries_by_number)가 항목을 그대로 가드에 넘기고, 가드는 원문과 대조한다."""
    from src.features.composer.combined_relation_constants import (
        COMBINED_SCOPE_QUOTE_NOT_IN_SOURCE, COMBINED_SCOPE_RANGE_NOT_IN_QUOTE,
        COMBINED_SCOPE_SOURCE_NOT_CITED,
    )
    from src.features.composer.combined_relation_guard import combined_relation_report
    from src.features.composer.direct_support import support_entries_by_number

    sources = {"1": _COMBINED_SOURCE}
    cases = (
        (_COMBINED_ITEM, ""),
        ({**_COMBINED_ITEM, "원문": "당사는 32개 협력사로부터 부품을 공급받"},
         COMBINED_SCOPE_QUOTE_NOT_IN_SOURCE),
        ({**_COMBINED_ITEM, "범위": "40개"}, COMBINED_SCOPE_RANGE_NOT_IN_QUOTE),
        ({**_COMBINED_ITEM, "근거": "2"}, COMBINED_SCOPE_SOURCE_NOT_CITED),
    )
    for item, expected in cases:
        payload = _combined_payload(FLAT_REVIEW_SCHEMA, item)
        _assert_fits(schema_validator, FLAT_REVIEW_SCHEMA, payload)  # 모양은 모두 합법
        entries = support_entries_by_number(json.dumps(payload, ensure_ascii=False))[1]
        assert entries["관계"] == [item]
        report = combined_relation_report(_COMBINED_CLAIM, sources, entries)
        assert report.triggers and report.problem == expected


def test_allowing_the_fields_does_not_approve_evidence_in_enforced_verdicts(schema_validator, monkeypatch):
    """스위치를 임시로 켜 실제 판정 경로(constrain_verdicts)를 부른다. 칸을 허용했다고
    승인되지 않는다 — 원문 결속이 맞는 결합 항목만 «참»을 지킨다."""
    from src.features.composer.combined_relation_constants import (
        COMBINED_SCOPE_EVIDENCE_MISSING, COMBINED_SCOPE_QUOTE_NOT_IN_SOURCE,
    )
    from src.features.composer.grounding_constants import REVIEW_GROUNDING_REJECTED

    monkeypatch.setattr(
        "src.features.composer.combined_relation_guard.COMBINED_RELATION_ENFORCED", True)
    candidates = {1: (_COMBINED_CLAIM, {"1": _COMBINED_SOURCE})}
    cases = (
        (_COMBINED_ITEM, "참", None),
        ({**_COMBINED_ITEM, "원문": "당사는 32개 협력사로부터 부품을 공급받"},
         REVIEW_GROUNDING_REJECTED, COMBINED_SCOPE_QUOTE_NOT_IN_SOURCE),
        # 결합 칸을 달았어도 유형이 결합이 아니면 결합 근거가 아니다.
        ({**_COMBINED_ITEM, "유형": "역할"}, REVIEW_GROUNDING_REJECTED, COMBINED_SCOPE_EVIDENCE_MISSING),
    )
    for item, verdict, problem in cases:
        payload = _combined_payload(FLAT_REVIEW_SCHEMA, item)
        _assert_fits(schema_validator, FLAT_REVIEW_SCHEMA, payload)
        constrained, problems = constrain_verdicts(
            json.dumps(payload, ensure_ascii=False), {1: "참"}, candidates,
            confirmed_prose_numbers=frozenset({1}),
        )
        assert constrained[1] == verdict
        assert problems.get(1) == problem
