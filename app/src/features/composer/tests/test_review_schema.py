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

from src.features.composer import diagram_check, verify
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


@pytest.mark.parametrize("schema,expected", (
    (FLAT_REVIEW_SCHEMA, "dadbdfa61b5a88bdbf286f639ed3a20bc029149421a9bd905edce246306c3a52"),
    (DIAGRAM_REVIEW_SCHEMA, "52de628b50a1c2d19f6c56bb4939d6dd8b4bf474d4e75e5c8af73cde7abe5909"),
))
def test_retry_schema_hash_matches_provider_accepted_schema(schema, expected):
    encoded = json.dumps(schema, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    assert hashlib.sha256(encoded).hexdigest() == expected


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


@pytest.mark.parametrize("factory,grouped,expected", (
    (_golden_case, False, "9235869c919e626a5451293e145beae7f26c653e2658c0baa31d7a6ca47964fa"),
    (_golden_case, True, "f7e87fe57acde668bd24a3d131ef313cc374d5377d0a24d1aea68f4156ab2e57"),
    (_boundary_case, False, "2e6ae4afd661b996b44a32589a6fe8b434ecae90eb506778a763e9261035e5fe"),
    (_boundary_case, True, "90b880b7fcf6bcf2c066630ee9bc2115a050f82436fb98fc3ce1365be02d491b"),
))
def test_body_prompt_bytes_match_pre_schema_baseline(factory, grouped, expected):
    # de0a68e1의 원래 builder로 재생한 전체 UTF-8 프롬프트 해시다.
    # ★ 기준값은 «스키마 포장 이전 builder»가 «현재 안내문»으로 만든 프롬프트다. 안내문이
    #   바뀌면(2026-09-14: 역할·과금 안내의 유형 이름을 「」로 교체) 값도 함께 갱신한다 —
    #   스키마 포장 자체는 이 프롬프트를 바꾸지 않음을 같은 날 문구만 되돌려 재계산해 확인했다.
    prompt = _render_case(verify, factory(), grouped)
    assert hashlib.sha256(prompt.encode("utf-8")).hexdigest() == expected


@pytest.mark.parametrize("items,expected", (
    ((), "d1dbdb4351e79577fc78263d160e9288fa407283c4d68a08f166447f89ab9213"),
    (FLOW_ITEMS, "3e3d8173f5a018370c630e7b91686409fc878790dd8ce98390ae23241319ad4e"),
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
    assert type(calls[0][1]) is str
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


@pytest.mark.parametrize("raw", ("invalid", '{"판정":[]}'))
def test_grouped_does_not_add_retry_or_fallback(raw):
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return raw

    assert verify._ask_grouped_verdicts(ask, GROUPED_ITEMS, FRAGMENTS, None) is None
    assert len(calls) == 1
    assert type(calls[0]) is str
    assert getattr(calls[0], "response_schema", None) is None


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
