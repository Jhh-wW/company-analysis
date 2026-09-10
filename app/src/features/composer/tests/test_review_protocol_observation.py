"""본문 검수 응답 «판독» 관측 — 실제 verify/logic 을 그대로 부르는 시험.

저장소에 그대로 들어가는 형태다. 로컬 대조 하네스에 기대지 않고
``src.features.composer.verify`` / ``logic`` 만 import 해 단독으로 돈다.

무엇을 지키는가
---------------
* 관측이 **판정·재시도·호출 횟수**를 바꾸지 않는다.
* 봉투 실패 네 가지(빈 응답 / 구문 오류 / 객체 아님 / 스키마)가 서로 구분된다.
  특히 ``null`` 은 «적법하게 읽혔지만 객체가 아님»이지 구문 오류가 아니다.
* 도달하지 않은 시도를 «응답 0건»으로 지어내지 않는다.
* 응답·프롬프트·근거 식별자·문장 본문이 관측에 남지 않는다.
"""

from __future__ import annotations

import json

import pytest

from src.features.composer import verify as verify_module
from src.features.composer.logic import extract_json_payload
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.shared.report_quality.composition_diagnostic_constants import (
    EXTRACT_DIRECT,
    EXTRACT_FAILED,
    EXTRACT_SLICED,
    PATH_FLAT,
    PATH_PACKET,
    READ_ALL_ROWS_INVALID,
    READ_EMPTY,
    READ_JSON_SYNTAX,
    READ_NOT_OBJECT,
    READ_OK,
    READ_VERDICTS_KEY_MISSING,
    READ_VERDICTS_NOT_LIST,
)
from src.features.composer.verify import verify_report, verify_sentences
from src.shared.report_quality.composition_diagnostic_constants import (
    PROTOCOL_COUNT_FIELDS,
    PROTOCOL_ENUM_FIELDS,
    PROTOCOL_OFFSET_FIELDS,
    PROTOCOL_ROW_REASONS,
    PROTOCOL_STEP,
)

SOURCE_TEXT = "회사는 2025년에 신규 서비스를 출시하였다."
FRAGMENT = CollectedFragment(fragment_id="f1", kind="공시", text=SOURCE_TEXT)
SENTENCE = ComposedSentence(text=SOURCE_TEXT, citations=("f1",), grade="확인")


def _body(rows):
    return json.dumps(
        {verify_module.REVIEW_VERDICTS_KEY: rows}, ensure_ascii=False
    )


def _row(number=1, result="참", section=None, evidence=None):
    row = {
        verify_module.REVIEW_NUMBER_KEY: number,
        verify_module.REVIEW_RESULT_KEY: result,
    }
    if section is not None:
        row[verify_module.REVIEW_SECTION_KEY] = section
    if evidence is not None:
        row[verify_module.REVIEW_EVIDENCE_IDS_KEY] = evidence
    return row


class _Reviewer:
    """호출 횟수와 보낸 프롬프트 길이만 세는 가짜 검수 호출자."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.answers[min(len(self.prompts) - 1, len(self.answers) - 1)]

    @property
    def calls(self) -> int:
        return len(self.prompts)


def _run_flat(*answers, protocol=None):
    ask = _Reviewer(*answers)
    kwargs = {} if protocol is None else {"protocol_diagnostics": protocol}
    return verify_sentences([SENTENCE], [FRAGMENT], None, ask, **kwargs), ask


def _packet_report():
    return ComposedReport(
        sections=(ComposedSection(section_id="identity", sentences=(SENTENCE,)),),
        summary=(),
    )


def _run_packet(*answers, protocol=None):
    ask = _Reviewer(*answers)
    kwargs = {} if protocol is None else {"protocol_diagnostics": protocol}
    result = verify_report(
        _packet_report(),
        [FRAGMENT],
        None,
        ask,
        allowed_fragment_ids_by_section={"identity": frozenset({"f1"})},
        **kwargs,
    )
    return result, ask


# ── 정상 ───────────────────────────────────────────────────────────────


def test_success_records_one_attempt():
    protocol: list[dict] = []
    result, ask = _run_flat(_body([_row()]), protocol=protocol)
    assert len(result) == 1
    assert ask.calls == 1
    assert len(protocol) == 1, "도달하지 않은 시도를 지어내지 않는다"
    record = protocol[0]
    assert record["step"] == PROTOCOL_STEP
    assert record["경로"] == PATH_FLAT
    assert record["시도"] == 1
    assert record["판독"] == READ_OK
    assert record["추출방식"] == EXTRACT_DIRECT
    assert record["응답행수"] == 1
    assert record["유효행수"] == 1
    assert record["미응답번호수"] == 0
    assert record["요청밖번호수"] == 0
    assert record["행탈락"] == {}


# ── 재시도 ─────────────────────────────────────────────────────────────


def test_first_parse_failure_records_retry():
    protocol: list[dict] = []
    result, ask = _run_flat("설명만 있고 JSON이 없다", _body([_row()]),
                            protocol=protocol)
    assert ask.calls == 2
    assert len(result) == 1
    assert [record["시도"] for record in protocol] == [1, 2]
    assert protocol[0]["판독"] == READ_JSON_SYNTAX
    assert protocol[1]["판독"] == READ_OK
    assert protocol[1]["입력문자"] > protocol[0]["입력문자"]


def test_two_parse_failures_stop_after_retry():
    protocol: list[dict] = []
    result, ask = _run_flat("설명만", "여전히 설명만", protocol=protocol)
    assert ask.calls == 2
    assert result == ()
    assert [record["판독"] for record in protocol] == [
        READ_JSON_SYNTAX, READ_JSON_SYNTAX,
    ]


# ── 봉투 실패 네 가지가 서로 다르다 ────────────────────────────────────


@pytest.mark.parametrize(
    ("answer", "expected_code", "expected_extract"),
    (
        ("", READ_EMPTY, EXTRACT_FAILED),
        ("   ", READ_EMPTY, EXTRACT_FAILED),
        ("설명만 있고 JSON이 없다", READ_JSON_SYNTAX, EXTRACT_FAILED),
        ("null", READ_NOT_OBJECT, EXTRACT_DIRECT),
        ("[1, 2, 3]", READ_NOT_OBJECT, EXTRACT_DIRECT),
        (json.dumps({"다른키": []}), READ_VERDICTS_KEY_MISSING, EXTRACT_DIRECT),
        (json.dumps({"판정": {}}), READ_VERDICTS_NOT_LIST, EXTRACT_DIRECT),
    ),
)
def test_envelope_failures_have_distinct_codes(answer, expected_code, expected_extract):
    protocol: list[dict] = []
    result, _ = _run_flat(answer, answer, protocol=protocol)
    assert result == ()
    assert protocol[0]["판독"] == expected_code
    assert protocol[0]["추출방식"] == expected_extract


def test_empty_response_is_not_syntax_error():
    protocol: list[dict] = []
    _run_flat("", "", protocol=protocol)
    assert protocol[0]["판독"] == READ_EMPTY
    assert protocol[0]["판독"] != READ_JSON_SYNTAX


def test_valid_json_without_valid_rows_is_not_syntax_error():
    protocol: list[dict] = []
    answer = _body([_row(result="계약밖판정")])
    _run_flat(answer, answer, protocol=protocol)
    assert protocol[0]["판독"] == READ_ALL_ROWS_INVALID
    assert protocol[0]["응답행수"] == 1
    assert protocol[0]["행탈락"] == {"result_not_allowed": 1}


def test_fenced_json_records_slice_offsets():
    protocol: list[dict] = []
    _run_flat("```json\n" + _body([_row()]) + "\n```", protocol=protocol)
    assert protocol[0]["추출방식"] == EXTRACT_SLICED
    assert protocol[0]["json시작offset"] > 0
    assert protocol[0]["json끝offset"] > protocol[0]["json시작offset"]


# ── packet 경로 ────────────────────────────────────────────────────────


def test_packet_review_does_not_retry():
    protocol: list[dict] = []
    _, ask = _run_packet("설명만", protocol=protocol)
    assert ask.calls == 1
    assert len(protocol) == 1
    assert protocol[0]["경로"] == PATH_PACKET
    assert protocol[0]["판독"] == READ_JSON_SYNTAX


@pytest.mark.parametrize(
    ("section", "evidence", "expected_reason"),
    (
        ("portfolio", ["f1"], "section_owner_mismatch"),
        ("identity", [], "evidence_ids_empty"),
        ("identity", ["f1", "f1"], "evidence_ids_duplicated"),
        ("identity", ["f9"], "evidence_ids_mismatch"),
    ),
)
def test_packet_row_failures_have_distinct_codes(section, evidence, expected_reason):
    protocol: list[dict] = []
    _run_packet(
        _body([_row(section=section, evidence=evidence)]), protocol=protocol
    )
    assert protocol[0]["행탈락"] == {expected_reason: 1}
    assert protocol[0]["판독"] == READ_ALL_ROWS_INVALID


# ── 기본값만 준 공개 호출에서도 기록이 유실되지 않는다 ─────────────────


def test_public_call_with_only_protocol_sink_records_attempt():
    """legacy fast path 가 새 인자를 떨어뜨리면 여기서 걸린다."""
    protocol: list[dict] = []
    ask = _Reviewer(_body([_row()]))
    report = verify_report(_packet_report(), [FRAGMENT], None, ask,
                           protocol_diagnostics=protocol)
    assert ask.calls == 1
    assert len(protocol) == 1, "allowed/diagnostics/initial_ask 기본값 경로에서 유실됐다"
    assert protocol[0]["경로"] == PATH_FLAT
    assert len(report.sections) == 1


def test_absent_sink_does_not_create_observations():
    result, ask = _run_flat(_body([_row()]))
    assert len(result) == 1
    assert ask.calls == 1


# ── 요청밖 번호는 보존하고 수만 기록 ───────────────────────────────────


def test_unexpected_numbers_are_preserved_and_counted():
    protocol: list[dict] = []
    raw = _body([_row(1), _row(99)])
    _run_flat(raw, protocol=protocol)
    assert protocol[0]["요청밖번호수"] == 1
    assert protocol[0]["유효행수"] == 2
    assert protocol[0]["미응답번호수"] == 0
    assert "number_not_requested" not in protocol[0]["행탈락"]
    assert 99 in verify_module._parse_verdicts(raw)


# ── 원문이 새지 않는다 ─────────────────────────────────────────────────


@pytest.mark.parametrize("leak", (
    "회사는 2099년 매출 999조원을 달성했다",
    "secret-token-value",
    "https://internal.example/prompt",
))
def test_response_text_is_never_recorded(leak):
    protocol: list[dict] = []
    raw = json.dumps(
        {
            verify_module.REVIEW_VERDICTS_KEY: [
                {
                    verify_module.REVIEW_NUMBER_KEY: 1,
                    verify_module.REVIEW_RESULT_KEY: leak,
                    "지문": leak,
                }
            ],
            "머리말": leak,
        },
        ensure_ascii=False,
    )
    _run_flat(raw, raw, protocol=protocol)
    dumped = json.dumps(protocol, ensure_ascii=False)
    assert leak not in dumped
    for token in leak.split():
        assert token not in dumped


def test_sentence_and_evidence_identifiers_are_never_recorded():
    protocol: list[dict] = []
    _run_packet(
        _body([_row(section="identity", evidence=["frag-비밀-0001"])]),
        protocol=protocol,
    )
    dumped = json.dumps(protocol, ensure_ascii=False)
    assert "frag-비밀-0001" not in dumped
    assert SOURCE_TEXT not in dumped


def test_values_follow_closed_contract():
    protocol: list[dict] = []
    _run_flat(_body([_row()]), protocol=protocol)
    for key, value in protocol[0].items():
        if key == "step":
            assert value == PROTOCOL_STEP
        elif key in PROTOCOL_ENUM_FIELDS:
            assert value in PROTOCOL_ENUM_FIELDS[key]
        elif key == "행탈락":
            assert all(name in PROTOCOL_ROW_REASONS for name in value)
            assert all(type(count) is int for count in value.values())
        else:
            assert type(value) is int, f"닫히지 않은 값: {key}={value!r}"


def test_normalization_preserves_observation_fields():
    from src.shared.report_quality.composition_diagnostics import (
        observed_composition_steps,
    )

    protocol: list[dict] = []
    _run_flat(_body([_row(), _row(2, "계약밖판정")]), protocol=protocol)
    steps = observed_composition_steps(protocol)
    assert len(steps) == 1
    for field in (
        *PROTOCOL_ENUM_FIELDS, *PROTOCOL_COUNT_FIELDS, *PROTOCOL_OFFSET_FIELDS,
    ):
        assert field in steps[0]
    assert steps[0]["행탈락"] == {"result_not_allowed": 1}


# ── 추출기: observe 를 줘도 반환값이 바뀌지 않는다 ─────────────────────


@pytest.mark.parametrize("raw", (
    "", "   ", "null", "[1,2,3]", "{}", '{"a": 1}', "설명만",
    '```json\n{"a": 1}\n```', '앞말 {"a": 1} 뒷말', "{", "}{", "3", "true",
))
def test_observation_preserves_json_extraction_result(raw):
    observe = {"추출방식": EXTRACT_FAILED, "json시작offset": -1, "json끝offset": -1}
    assert extract_json_payload(raw, observe=observe) == extract_json_payload(raw)
    assert observe["추출방식"] in PROTOCOL_ENUM_FIELDS["추출방식"]
