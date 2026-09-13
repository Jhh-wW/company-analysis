"""네이티브 검수의 문자열·재요청·근거 계약을 무과금으로 확인한다.

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

from src.features.composer import diagram_check, verify
from src.features.composer.constants import RETRY_REMINDER
from src.features.composer.grounding import constrain_verdicts, grounding_problem
from src.features.composer.port import AskFatalError, CollectedFragment, ComposedSentence, FlowRow
from src.features.composer.review_schema import (
    DIAGRAM_REVIEW_SCHEMA, FLAT_REVIEW_SCHEMA, GROUPED_REVIEW_SCHEMA, ReviewPrompt,
)
from src.features.composer.tests import test_numeric_quote_refs as numeric_fixture
from src.features.composer.tests.test_review_prompt_serialization import (
    _boundary_case, _golden_case, _render_case,
)


SCHEMAS = (FLAT_REVIEW_SCHEMA, GROUPED_REVIEW_SCHEMA, DIAGRAM_REVIEW_SCHEMA)
SCHEMA_NAMES = ("flat", "grouped", "diagram")
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


@pytest.mark.parametrize("kind", SCHEMA_NAMES)
@pytest.mark.parametrize("empty", (False, True))
def test_builders_mark_only_review_text(kind, empty):
    if kind == "flat":
        prompt = verify._build_review_prompt(() if empty else FLAT_ITEMS, FRAGMENTS, "")
        schema = FLAT_REVIEW_SCHEMA
    elif kind == "grouped":
        prompt = verify._build_grouped_review_prompt(() if empty else GROUPED_ITEMS, FRAGMENTS, None)
        schema = GROUPED_REVIEW_SCHEMA
    else:
        prompt = diagram_check._review_prompt(() if empty else FLOW_ITEMS, {"1": TEXT})
        schema = DIAGRAM_REVIEW_SCHEMA
    assert isinstance(prompt, ReviewPrompt)
    assert prompt.response_schema is schema
    assert prompt


@pytest.mark.parametrize("factory,grouped,expected", (
    (_golden_case, False, "d3a778c9f13705e1b5c5a5f4d31905df14c780dd61bf69b56a2e091dfdecf20a"),
    (_golden_case, True, "1db8b19baf4a861551930843ce3d4692f2b624e1647b9f889b54e14465294367"),
    (_boundary_case, False, "5db25ef50dcedcfb437526d7dd11b3aa8ae6f32043515df2de91c317b904acb6"),
    (_boundary_case, True, "a2e212ace823302011ab8f0872813b5b29536e4c336a632499f89189d78003f3"),
))
def test_body_prompt_bytes_match_pre_schema_baseline(factory, grouped, expected):
    # de0a68e1의 원래 builder로 재생한 전체 UTF-8 프롬프트 해시다.
    prompt = _render_case(verify, factory(), grouped)
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == expected


@pytest.mark.parametrize("items,expected", (
    ((), "add29ee2e97fb46e5edace05e7d9ae3f316881b1dfdec61a5eaea08871b77431"),
    (FLOW_ITEMS, "c3f087e0cae73ded7338ae5e11297304088d453e42adcd3da2cbda15a1716ca6"),
))
def test_diagram_prompt_bytes_match_pre_schema_baseline(items, expected):
    prompt = diagram_check._review_prompt(items, {"1": TEXT})
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == expected


def test_flat_retry_keeps_metadata_and_original_retry_text():
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return "invalid" if len(calls) == 1 else '{"판정":[{"번호":1,"결과":"참"}]}'

    assert verify._ask_verdicts(ask, FLAT_ITEMS, FRAGMENTS, "") == {1: "참"}
    assert len(calls) == 2
    assert calls[1] == str(calls[0]) + RETRY_REMINDER
    assert all(p.response_schema is FLAT_REVIEW_SCHEMA for p in calls)


def test_diagram_retry_keeps_metadata_and_call_count():
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return "invalid" if len(calls) == 1 else '{"판정":[{"번호":1,"결과":"거짓"}]}'

    section, row = FLOW_ITEMS[0][1:]
    kept, dropped = diagram_check._review_rows(((section, (row,)),), {"1": TEXT}, ask)
    assert kept == {section: ()} and dropped
    assert len(calls) == 2
    assert calls[1] == str(calls[0]) + RETRY_REMINDER
    assert all(p.response_schema is DIAGRAM_REVIEW_SCHEMA for p in calls)


@pytest.mark.parametrize("raw", ("invalid", '{"판정":[]}'))
def test_grouped_does_not_add_retry_or_fallback(raw):
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return raw

    assert verify._ask_grouped_verdicts(ask, GROUPED_ITEMS, FRAGMENTS, None) is None
    assert len(calls) == 1
    assert calls[0].response_schema is GROUPED_REVIEW_SCHEMA


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


@pytest.mark.parametrize("kind", SCHEMA_NAMES)
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


@pytest.mark.parametrize("grouped", (False, True))
@pytest.mark.parametrize("section,text", (
    ("operations_partners", "회사는 여행 예약 플랫폼을 자체 개발했다."),
    ("past_changes", "회사는 공급 차질이 납기 지연의 주된 원인이라고 밝혔다."),
    ("future_strategy", ""),
))
def test_existing_role_cause_and_future_fixtures_fit_actual_prompt_schema(
    schema_validator, monkeypatch, section, text, grouped,
):
    from src.features.composer.tests import test_body_review_comparison as fixtures

    original = fixtures.verify_report
    calls = []

    def checked(report, fragments, table, ask, **kwargs):
        def checked_ask(prompt):
            raw = ask(prompt)
            schema = getattr(prompt, "response_schema", None)
            if schema is not None:
                payload = json.loads(raw)
                schema_validator(schema).validate(payload)
                schema_validator(transform_schema(schema)).validate(payload)
                calls.append(payload)
            return raw
        return original(report, fragments, table, checked_ask, **kwargs)

    monkeypatch.setattr(fixtures, "verify_report", checked)
    if section == "future_strategy":
        case = next(case for case in fixtures.CONTRASTS if case[0] == "plan")
        fixtures.test_controlled_false_removes_addition_and_true_keeps_supported_peer(case, grouped, "해석")
    else:
        fixtures.test_controlled_true_preserves_explicit_hr_cause_condition_and_development(section, text, grouped)
    assert len(calls) == 1


@pytest.mark.parametrize("grouped", (False, True))
def test_existing_golden_responses_fit_actual_prompt_schema(schema_validator, grouped):
    import re
    from src.features.composer.tests.review_evidence_fixture import grounded_review_response

    prompt = _render_case(verify, _golden_case(), grouped)
    # 기존 fixture 독자의 별도 등급 줄 처리만 보정해 응답을 읽는다.
    readable = re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt)
    payload = json.loads(grounded_review_response(readable))
    assert len(payload["판정"]) > 10
    native = _native_response(payload["판정"], prompt.response_schema)
    schema_validator(prompt.response_schema).validate(native)
    schema_validator(transform_schema(prompt.response_schema)).validate(native)


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
