"""요약 단계별 개수, 안전한 본문 보충, 한도 및 오류 시 미도달 기록을 검증한다.

수치 삭제 시험만 검수 반환 경계를 대체하며 실제 수치 검사·본문 보충을 실행한다.
"""

from __future__ import annotations

import json
from typing import Optional

import pytest

from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer.pipeline import _legacy_summary_stage
from src.features.composer.port import (
    AskFatalError,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.structured_claims import NumericSafetyFiltering
from src.shared.report_quality.composition_diagnostic_constants import (
    SUMMARY_STEP,
)
from src.shared.report_quality.composition_diagnostics import (
    observed_composition_steps,
)


# ══════════════════════════════════════════════════════════
# 시험 재료
# ══════════════════════════════════════════════════════════

_FRAGMENTS = {
    1: {"종류": "사업내용", "원문": "가나다전자는 반도체 검사 장비를 만든다."},
    2: {"종류": "사업내용", "원문": "가나다전자는 국내에 공장을 둔다."},
    3: {"종류": "사업내용", "원문": "가나다전자는 해외에 지사를 둔다."},
}

#: 작가가 «새로» 써서 조각 원문을 그대로 인용하는 3문장 — verify.py의 의미
#: 근거 검증을 실제로 통과하게 하려고 조각 원문과 같은 문장으로 만든다.
_GOOD_DRAFT = [
    ("가나다전자는 반도체 검사 장비를 만든다.", ("1",)),
    ("가나다전자는 국내에 공장을 둔다.", ("2",)),
    ("가나다전자는 해외에 지사를 둔다.", ("3",)),
]


def _body_sentence(text: str, citation: str) -> ComposedSentence:
    """본문에 이미 있는 «확인» 문장 — 조각 원문과 다른 문장이라 요약 재탕
    검출에 걸리지 않고, 보충 경로로 재사용될 때는 재검수를 타지 않는다."""

    return ComposedSentence(
        text=text, citations=(citation,), grade=GRADE_CONFIRMED,
        verification_state="verified",
    )


def _report(*section_sentences: tuple[str, str, str]) -> ComposedReport:
    return ComposedReport(
        sections=tuple(
            ComposedSection(section_id, (_body_sentence(text, citation),))
            for section_id, text, citation in section_sentences
        )
    )


_BODY_THREE = _report(
    ("identity", "가나다전자 개요 확인 문단이다.", "1"),
    ("business_model", "가나다전자 사업모델 확인 문단이다.", "2"),
    ("portfolio", "가나다전자 포트폴리오 확인 문단이다.", "3"),
)
_BODY_THIN = _report(
    ("identity", "가나다전자 개요 확인 문단이다.", "1"),
    ("business_model", "가나다전자 사업모델 확인 문단이다.", "2"),
)
#: ★ 수치 삭제 시험 전용 — 구조화 결속 없는 수치 문장 하나. 본문(verified)
#:   에는 절대 넣지 않는다(아래 test 함수 docstring 참고 — 실제 run_v2는
#:   pipeline.py:1302에서 enforce_public_numeric_safety(verified)를 이미
#:   거친 뒤에야 _legacy_summary_stage를 부르므로, 이런 미결속 수치 문장이
#:   «검증된 본문»으로 들어오는 입력 자체가 실제 생산 경로에서는 나오지
#:   않는다). 이 문장은 «요약 검수 결과」 자리에만 monkeypatch로 주입한다.
_UNSAFE_NUMERIC_SUMMARY_SENTENCE = ComposedSentence(
    text="가나다전자 매출은 100억원이다.",
    citations=("2",),
    grade=GRADE_CONFIRMED,
    verification_state="verified",
)


def _verdict_json(numbers: list[int]) -> str:
    return json.dumps(
        {"판정": [{"번호": n, "결과": "참"} for n in numbers]},
        ensure_ascii=False,
    )


class _FakeWriter:
    """준비된 응답을 차례로 돌려주거나, 지정된 예외를 그대로 던지는 가짜 작가."""

    def __init__(
        self,
        sentences: Optional[list[tuple[str, tuple[str, ...]]]] = None,
        error: Optional[BaseException] = None,
    ) -> None:
        self.sentences = sentences or []
        self.error = error
        self.calls = 0

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        items = [
            {"글": text, "인용": list(citations), "등급": GRADE_CONFIRMED}
            for text, citations in self.sentences
        ]
        return json.dumps({"문장들": items}, ensure_ascii=False)


class _FakeReviewer:
    """준비된 번호만 «참»으로 판정하거나, 지정된 예외를 그대로 던지는 가짜 검수."""

    def __init__(
        self,
        numbers: Optional[list[int]] = None,
        error: Optional[BaseException] = None,
    ) -> None:
        self.numbers = numbers
        self.error = error
        self.calls = 0

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return _verdict_json(self.numbers if self.numbers is not None else [])


def _run(
    verified: ComposedReport,
    *,
    writer_sentences: Optional[list[tuple[str, tuple[str, ...]]]] = None,
    writer_error: Optional[BaseException] = None,
    reviewer_numbers: Optional[list[int]] = None,
    reviewer_error: Optional[BaseException] = None,
):
    """가짜 작가·검수로 ``_legacy_summary_stage``를 한 번 돌린다.

    반환: (final, draft_count, numeric_filtering), diagnostics, writer, reviewer
    """

    writer = _FakeWriter(writer_sentences, writer_error)
    reviewer = _FakeReviewer(reviewer_numbers, reviewer_error)
    diagnostics: list[dict] = []
    result = _legacy_summary_stage(
        verified, _FRAGMENTS, None,
        writer_ask=writer, reviewer_ask=reviewer,
        body_numeric_filtering=NumericSafetyFiltering(),
        summary_diagnostics=diagnostics,
    )
    return result, diagnostics, writer, reviewer


def _assert_contract_valid(record: dict) -> None:
    """root가 공개한 shared 정규화기를 실제로 통과하는지 — 생산자·소비자 계약 일치."""

    normalized = observed_composition_steps([record])
    assert len(normalized) == 1, "정규화기가 이 레코드를 닫힌 계약 위반으로 버렸다"
    assert normalized[0] == record


#: 이 시험에서 실제로 쓰는 본문·인용·오류 문구 — 닫힌 필드 이름(예:
#: "작성한도도달"에 들어 있는 "한도")과 우연히 겹치지 않는, 시험에서만
#: 등장하는 고유한 문구만 골랐다.
_LEAK_CHECK_TOKENS = (
    "가나다전자", "엉뚱한 인용 문장", "근거 없이 지어낸 문장",
    "요청 한도 시험 사유", "결제 불확실 차단 시험 사유",
    '"1"', '"2"', '"3"', '"99"',
)


def _assert_no_leaked_content(record: dict) -> None:
    """원문·오류문·인용 id를 저장하지 않았는지 — 값 전체를 문자열로 훑어 확인한다."""

    blob = json.dumps(record, ensure_ascii=False)
    for token in _LEAK_CHECK_TOKENS:
        assert token not in blob, f"금지된 내용이 진단 레코드에 남았다: {token!r}"


# ══════════════════════════════════════════════════════════
# ① 정상 3문장 — 보충 불필요
# ══════════════════════════════════════════════════════════


def test_complete_summary_skips_supplement() -> None:
    (final, draft_count, _numeric), diagnostics, writer, reviewer = _run(
        _BODY_THREE, writer_sentences=_GOOD_DRAFT, reviewer_numbers=[1, 2, 3],
    )
    assert len(final.summary) == 3
    assert draft_count == 3
    assert writer.calls == 1 and reviewer.calls == 1

    assert len(diagnostics) == 1
    record = diagnostics[0]
    assert record == {
        "step": SUMMARY_STEP,
        "경로": "legacy",
        "도달단계": "최종",
        "본문후보수": 3,
        "초안수": 3,
        "검수후수": 3,
        "첫보충후수": None,
        "수치검사후수": 3,
        "최종수": 3,
        "작성한도도달": False,
        "검수한도도달": False,
    }
    _assert_contract_valid(record)
    _assert_no_leaked_content(record)


# ══════════════════════════════════════════════════════════
# ② 2문장 fail-closed — 본문 자체가 얇으면 억지로 채우지 않는다
# ══════════════════════════════════════════════════════════


def test_insufficient_body_stays_below_summary_minimum() -> None:
    (final, _draft_count, _numeric), diagnostics, _writer, _reviewer = _run(
        _BODY_THIN,
        writer_sentences=[("엉뚱한 인용 문장", ("99",))],
        reviewer_numbers=[],
    )
    # ★ fail-closed 확인 — 2/3~5 범위 미달인 채로 그대로 반환된다. 이 함수는
    #   길이를 억지로 맞추지 않는다(내용 없는 문장 채우기 금지 지시와 일치).
    assert len(final.summary) == 2

    record = diagnostics[0]
    assert record["본문후보수"] == 2
    assert record["최종수"] == 2
    assert record["첫보충후수"] == 2
    assert record["도달단계"] == "최종"
    assert record["작성한도도달"] is False
    assert record["검수한도도달"] is False
    _assert_contract_valid(record)
    _assert_no_leaked_content(record)


# ══════════════════════════════════════════════════════════
# ③ 보충 경로 — 초안이 검수에서 전부 탈락해도 본문 확인 문장으로 채운다
# ══════════════════════════════════════════════════════════


def test_rejected_draft_uses_confirmed_body() -> None:
    (final, _draft_count, _numeric), diagnostics, _writer, _reviewer = _run(
        _BODY_THREE,
        writer_sentences=[("근거 없이 지어낸 문장", ("1",))],
        reviewer_numbers=[],
    )
    assert len(final.summary) == 3
    assert {s.text for s in final.summary} == {
        section.sentences[0].text for section in _BODY_THREE.sections
    }

    record = diagnostics[0]
    assert record["검수후수"] == 0  # 도달은 했지만 살아남은 문장 0개 — None과 구분
    assert record["첫보충후수"] == 3
    assert record["최종수"] == 3
    assert record["도달단계"] == "최종"
    _assert_contract_valid(record)
    _assert_no_leaked_content(record)


# ══════════════════════════════════════════════════════════
# ④ 수치 삭제 — 미결속 수치 요약 문장은 수치 검사에서 빠진다
# ══════════════════════════════════════════════════════════


def test_numeric_filter_removes_unbound_summary_and_uses_safe_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 생산 전제 — 실제 run_v2는 ``pipeline.py``에서 ``_legacy_summary_stage``를
    부르기 전에 ``verified, body_numeric_filtering =
    enforce_public_numeric_safety(verified)``를 이미 실행한다(현재 줄번호
    기준 1302행 부근). 즉 이 함수가 실제로 받는 ``verified``(본문)에는
    미결속 수치 문장이 «절대 없다» — 있다면 그건 함수 계약을 어긴 잘못된
    시험 입력이지, 이 함수가 다뤄야 하는 실제 상황이 아니다.

    이전 candidate 버전은 이 전제를 어기고 ``verified.sections`` 안에
    직접 미결속 수치 문장("가나다전자 매출은 100억원이다")을 «이미 검증된
    본문»으로 넣어 두 번째 보충이 그 문장을 되돌리는 것을 관찰했다. 그
    관찰 자체는 실제로 일어난 계산 결과였지만, 입력이 실제 생산 경로가
    절대 주지 않는 모양이었으므로 «서비스가 방금 지운 숫자를 재삽입한다»는
    해석은 잘못이었다 — 철회한다. 실제 생산 경로에서는 본문에 애초에
    미결속 수치가 없으므로 두 번째 보충도 안전한 문장만 돌려준다.

    이 시험은 그 전제를 지킨다: ``verified``(본문)는 미결속 수치가 전혀
    없는 안전한 3문장(``_BODY_THREE``)이다. 미결속 수치 문장은 «요약 검수
    결과» 자리에만 넣는다 — 이 함수는 «단계별 개수」를 기록하는 단위
    시험이라, ``verify_sentences``가 실제로 이 문장을 통과시키는지(의미
    근거 검증을 어떻게 통과하는지)는 ``verify.py`` 자신의 시험 몫이다.
    여기서는 **``verify_sentences``의 반환 경계만 monkeypatch로 대체**해
    "검수가 이 미결속 수치 문장 하나만 남겼다"는 상황을 직접 만든다 —
    수치 필터(``enforce_public_numeric_safety``)·보충
    (``_supplement_summary``/``_supplement_safe_summary``)·
    ``_legacy_summary_stage`` 자체는 전부 실제 함수를 그대로 실행한다.
    """

    import src.features.composer.pipeline as pipeline_module

    verify_calls = {"n": 0}

    def _fake_verify_sentences(sentences, fragments, performance_table, ask, **kwargs):
        verify_calls["n"] += 1
        return (_UNSAFE_NUMERIC_SUMMARY_SENTENCE,)

    monkeypatch.setattr(pipeline_module, "verify_sentences", _fake_verify_sentences)

    (final, draft_count, numeric_filtering), diagnostics, _writer, reviewer = _run(
        _BODY_THREE,
        writer_sentences=_GOOD_DRAFT,
        reviewer_numbers=[1, 2, 3],  # monkeypatch가 verify_sentences를 통째로
        # 대체하므로 이 가짜 검수는 실제로 호출되지 않는다(아래에서 확인).
    )

    assert verify_calls["n"] == 1
    assert reviewer.calls == 0, "verify_sentences를 대체했으므로 reviewer_ask는 안 불린다"
    assert draft_count == 3  # compose_summary는 실제 함수 그대로 실행됐다

    # ★ 필수 assert — 최종 요약 어디에도 미결속 숫자가 돌아오지 않는다.
    numeric_texts = [
        s.text for s in final.summary
        if "매출" in s.text or "100억원" in s.text
    ]
    assert numeric_texts == [], f"미결속 수치 문장이 최종 요약에 남았다: {numeric_texts}"
    assert len(final.summary) == 3
    assert {s.text for s in final.summary} == {
        section.sentences[0].text for section in _BODY_THREE.sections
    }
    # 기존 본문 문장 그대로다 — 지워진 숫자를 대신할 새 «안전 문장»을
    # 지어내지 않았다(기존 원문에 없는 숫자를 안전 본문으로 꾸미지 않는다).

    record = diagnostics[0]
    assert record["초안수"] == 3
    assert record["검수후수"] == 1  # monkeypatch가 돌려준 1개(미결속 수치 포함)
    # 첫 보충 직후에는 미결속 수치 문장 1개 + 본문에서 보충한 안전한
    # 문장 2개 = 3개. 수치 검사가 미결속 수치 문장을 걷어내 2개로 줄인다.
    assert record["첫보충후수"] == 3
    assert record["수치검사후수"] == 2
    assert record["수치검사후수"] < record["첫보충후수"]
    assert numeric_filtering.removed_summary_count == 1
    # 두 번째 보충은 본문(verified)에서 다시 고르지만, 본문 자체가 이미
    # 안전한 3문장뿐이라 미결속 수치가 돌아올 자리가 없다 — 그래서
    # 최종수가 3이 되는 이유는 «지운 숫자의 재유입»이 아니라 «본문에 남아
    # 있던 세 번째 안전한 문장을 마저 채운 것»이다.
    assert record["최종수"] == 3
    assert record["도달단계"] == "최종"
    _assert_contract_valid(record)
    _assert_no_leaked_content(record)


# ══════════════════════════════════════════════════════════
# ⑤ 요청 한도 — 작성 한도 / 검수 한도 (둘 다 degradable=True)
# ══════════════════════════════════════════════════════════


def test_writer_limit_uses_body_without_review() -> None:
    (final, draft_count, _numeric), diagnostics, _writer, reviewer = _run(
        _BODY_THREE,
        writer_error=AskFatalError(RuntimeError("요청 한도 시험 사유"), call_limit=True),
        reviewer_numbers=[1, 2, 3],
    )
    assert draft_count == 0
    assert len(final.summary) == 3
    assert reviewer.calls == 0, "작성 한도로 끝났으면 검수는 호출되지 않아야 한다"

    record = diagnostics[0]
    assert record["작성한도도달"] is True
    assert record["검수한도도달"] is False
    assert record["초안수"] == 0
    assert record["검수후수"] is None  # 검수 단계 자체가 스킵됐다 — 0이 아니라 None
    assert record["첫보충후수"] == 3
    assert record["최종수"] == 3
    assert record["도달단계"] == "최종"
    _assert_contract_valid(record)
    _assert_no_leaked_content(record)


def test_reviewer_limit_records_empty_review_and_supplements() -> None:
    (final, draft_count, _numeric), diagnostics, _writer, _reviewer = _run(
        _BODY_THREE,
        writer_sentences=_GOOD_DRAFT,
        reviewer_error=AskFatalError(RuntimeError("요청 한도 시험 사유"), call_limit=True),
    )
    assert draft_count == 3
    assert len(final.summary) == 3

    record = diagnostics[0]
    assert record["작성한도도달"] is False
    assert record["검수한도도달"] is True
    assert record["초안수"] == 3
    assert record["검수후수"] == 0  # 검수는 «시도»했지만 한도로 결과가 비었다
    assert record["첫보충후수"] == 3
    assert record["최종수"] == 3
    assert record["도달단계"] == "최종"
    _assert_contract_valid(record)
    _assert_no_leaked_content(record)


# ══════════════════════════════════════════════════════════
# ⑥ 비분해 fatal 예외 — degradable=False, 재전파 + 도달단계만 폐쇄형으로 남는다
# ══════════════════════════════════════════════════════════


def test_writer_fatal_preserves_initial_observation() -> None:
    fatal = AskFatalError(RuntimeError("결제 불확실 차단 시험 사유"), call_limit=False, request_budget=False)
    diagnostics: list[dict] = []
    writer = _FakeWriter(error=fatal)
    reviewer = _FakeReviewer([1, 2, 3])

    with pytest.raises(AskFatalError) as excinfo:
        _legacy_summary_stage(
            _BODY_THREE, _FRAGMENTS, None,
            writer_ask=writer, reviewer_ask=reviewer,
            body_numeric_filtering=NumericSafetyFiltering(),
            summary_diagnostics=diagnostics,
        )
    assert excinfo.value.degradable is False
    assert reviewer.calls == 0, "작성 단계에서 죽었으면 검수는 아예 호출되지 않는다"

    assert len(diagnostics) == 1
    record = diagnostics[0]
    assert record["도달단계"] == "시작"
    # 본문후보수는 함수 진입 시 이미 계산되는 값(AI 호출 무관)이라 «시작»
    # 단계에서도 채워져 있다 — 도달하지 못한 나머지 단계만 None이어야 한다.
    assert record["본문후보수"] == 3
    for field in ("초안수", "검수후수", "첫보충후수", "수치검사후수", "최종수"):
        assert record[field] is None, f"{field}는 도달하지 못했으므로 None이어야 한다"
    assert record["작성한도도달"] is False
    assert record["검수한도도달"] is False
    _assert_no_leaked_content(record)


def test_reviewer_fatal_preserves_draft_observation() -> None:
    fatal = AskFatalError(RuntimeError("결제 불확실 차단 시험 사유"), call_limit=False, request_budget=False)
    diagnostics: list[dict] = []
    writer = _FakeWriter(_GOOD_DRAFT)
    reviewer = _FakeReviewer(error=fatal)

    with pytest.raises(AskFatalError) as excinfo:
        _legacy_summary_stage(
            _BODY_THREE, _FRAGMENTS, None,
            writer_ask=writer, reviewer_ask=reviewer,
            body_numeric_filtering=NumericSafetyFiltering(),
            summary_diagnostics=diagnostics,
        )
    assert excinfo.value.degradable is False

    assert len(diagnostics) == 1
    record = diagnostics[0]
    assert record["도달단계"] == "작성"
    assert record["초안수"] == 3
    assert record["검수후수"] is None
    assert record["첫보충후수"] is None
    assert record["수치검사후수"] is None
    assert record["최종수"] is None
    _assert_no_leaked_content(record)
