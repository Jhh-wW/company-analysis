"""요약 단계별 개수, 안전한 본문 보충, 한도 및 오류 시 미도달 기록을 검증한다.

★ 의도가 바뀐 근거 (2026-09-11) — 요약을 「AI가 새로 쓴다」에서 「검증된 본문
  문장 중 AI가 고른다」로 바꿨다. 그래서 이 파일에서 «요약 재검증»(검수)을
  다루던 시험은 재검증 단계 자체가 없어졌다. 삭제하지 않고, 그 자리를
  ① 「재검증을 한 번도 부르지 않는다」 ② 「검수 칸은 영구히 미도달(None)이다」
  ③ 「수치 안전 검사를 후보 단계에서 미리 건다」로 대체했다.
"""

from __future__ import annotations

import json
from typing import Optional

import pytest

from src.features.composer.constants import GRADE_CONFIRMED, GRADE_INTERPRETED
from src.features.composer.pipeline import (
    _legacy_summary_stage,
    _supplement_safe_summary,
)
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


def _body_sentence(text: str, citation: str) -> ComposedSentence:
    """본문에 이미 있는 «확인» 문장 — verify_report를 통과한 상태를 흉내 낸다."""

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


def _interpreted(text: str, citation: str) -> ComposedSentence:
    """reviewer 전역 실패로 «해석»까지 강등된 본문 문장."""

    return ComposedSentence(
        text=text, citations=(citation,), grade=GRADE_INTERPRETED,
        verification_state="verified",
    )


#: 장마다 «두» 문장이 있는 본문. 모두 «해석»이라 1차 보충(확인 문장만 고름)이
#: 아무것도 못 채우고 안전 보충 경로가 실제로 돌아간다.
_BODY_TWO_EACH = ComposedReport(
    sections=(
        ComposedSection("identity", (
            _interpreted("가나다전자 개요 첫 문단이다.", "1"),
            _interpreted("가나다전자 개요 둘째 문단이다.", "1"),
        )),
        ComposedSection("business_model", (
            _interpreted("가나다전자 사업모델 첫 문단이다.", "2"),
            _interpreted("가나다전자 사업모델 둘째 문단이다.", "2"),
        )),
        ComposedSection("portfolio", (
            _interpreted("가나다전자 포트폴리오 첫 문단이다.", "3"),
            _interpreted("가나다전자 포트폴리오 둘째 문단이다.", "3"),
        )),
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
#: 구조화 결속이 없는 수치 문장. 실제 run_v2는 이 함수를 부르기 전에
#: `enforce_public_numeric_safety(verified)`로 본문에서 이런 문장을 이미
#: 빼지만, 요약 후보 잣대는 그 «두 번째 겹»이다 — 본문에 남아 들어와도
#: 요약 후보로는 나가지 않아야 한다.
_UNSAFE_NUMERIC_TEXT = "가나다전자 매출은 100억원이다."
_BODY_WITH_UNSAFE_NUMERIC = ComposedReport(
    sections=_BODY_THREE.sections
    + (
        ComposedSection(
            "past_changes", (_body_sentence(_UNSAFE_NUMERIC_TEXT, "2"),)
        ),
    )
)


class _FakeSelector:
    """준비된 «번호» 응답을 돌려주거나, 지정된 예외를 그대로 던지는 가짜 AI."""

    def __init__(
        self,
        numbers: Optional[list[int]] = None,
        error: Optional[BaseException] = None,
        raw: Optional[str] = None,
    ) -> None:
        self.numbers = numbers
        self.error = error
        self.raw = raw
        self.calls = 0
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.calls += 1
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        if self.raw is not None:
            return self.raw
        return json.dumps(self.numbers or [], ensure_ascii=False)


def _run(
    verified: ComposedReport,
    *,
    numbers: Optional[list[int]] = None,
    error: Optional[BaseException] = None,
    raw: Optional[str] = None,
):
    """가짜 선택 AI로 ``_legacy_summary_stage``를 한 번 돌린다.

    반환: (final, draft_count, numeric_filtering), diagnostics, selector
    """

    selector = _FakeSelector(numbers, error, raw)
    diagnostics: list[dict] = []
    result = _legacy_summary_stage(
        verified,
        writer_ask=selector,
        body_numeric_filtering=NumericSafetyFiltering(),
        summary_diagnostics=diagnostics,
    )
    return result, diagnostics, selector


def _assert_contract_valid(record: dict) -> None:
    """root가 공개한 shared 정규화기를 실제로 통과하는지 — 생산자·소비자 계약 일치."""

    normalized = observed_composition_steps([record])
    assert len(normalized) == 1, "정규화기가 이 레코드를 닫힌 계약 위반으로 버렸다"
    assert normalized[0] == record


#: 이 시험에서 실제로 쓰는 본문·인용·오류 문구 — 닫힌 필드 이름(예:
#: "작성한도도달"에 들어 있는 "한도")과 우연히 겹치지 않는, 시험에서만
#: 등장하는 고유한 문구만 골랐다.
_LEAK_CHECK_TOKENS = (
    "가나다전자", "100억원",
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
    (final, draft_count, _numeric), diagnostics, selector = _run(
        _BODY_THREE, numbers=[1, 2, 3],
    )
    assert len(final.summary) == 3
    assert draft_count == 3
    assert selector.calls == 1, "고르기는 정확히 1회다 — 재요청도, 재검증도 없다"
    # 고른 문장은 본문 문장 «그 객체» 그대로다 — 요약은 그 본문 문장의
    # 결속과 같은 수준으로 결속된다(없는 결속을 만들어 주지는 않는다).
    본문문장 = [section.sentences[0] for section in _BODY_THREE.sections]
    assert list(final.summary) == 본문문장

    assert len(diagnostics) == 1
    record = diagnostics[0]
    assert record == {
        "step": SUMMARY_STEP,
        "경로": "legacy",
        "도달단계": "최종",
        "본문후보수": 3,
        "초안수": 3,
        # ★ 요약 재검증 단계가 사라졌다 — 0이 아니라 None이다. 0으로 적으면
        #   «검수했는데 다 떨어졌다»로 읽혀 실제로 안 돈 단계와 구분이 없다.
        "검수후수": None,
        "첫보충후수": None,
        "수치검사후수": 3,
        "최종수": 3,
        "작성한도도달": False,
        "검수한도도달": False,
    }
    _assert_contract_valid(record)
    _assert_no_leaked_content(record)


def test_요약_단계는_요약_재검증을_한_번도_부르지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 이 설계 변경의 핵심 — 고른 문장은 이미 verify_report를 통과했다.

    정의 모듈(`composer/verify.py`)에 spy를 건다. 파이프라인이 이름을 다시
    들여와도, 다른 모듈이 대신 불러도 여기서 잡힌다.
    """

    import src.features.composer.verify as verify_module

    calls: list[tuple] = []
    real = verify_module.verify_sentences

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(verify_module, "verify_sentences", spy)

    (final, _draft, _numeric), _diagnostics, _selector = _run(
        _BODY_THREE, numbers=[1, 2, 3],
    )

    assert len(final.summary) == 3, "요약이 비면 이 시험이 아무것도 못 잰다"
    assert calls == [], "요약 재검증이 다시 배선됐다 — 같은 문장을 두 번 검수한다"


# ══════════════════════════════════════════════════════════
# ② 2문장 fail-closed — 본문 자체가 얇으면 억지로 채우지 않는다
# ══════════════════════════════════════════════════════════


def test_insufficient_body_stays_below_summary_minimum() -> None:
    (final, _draft_count, _numeric), diagnostics, _selector = _run(
        _BODY_THIN, raw='[99, "없는 번호"]',
    )
    # ★ fail-closed 확인 — 2/3~5 범위 미달인 채로 그대로 반환된다. 이 함수는
    #   길이를 억지로 맞추지 않는다(내용 없는 문장 채우기 금지 지시와 일치).
    assert len(final.summary) == 2

    record = diagnostics[0]
    assert record["본문후보수"] == 2
    # ★ 0인 이유가 바뀌었다 (2026-09-11) — 예전에는 「범위 밖 번호를 하나도
    #   인정하지 않아서」였고, 지금은 후보가 최소 문장 수에 못 미쳐 «고르기를
    #   아예 안 불러서»다. 범위 밖 번호 판독은 `test_summary.py`가 지킨다.
    assert record["초안수"] == 0
    assert record["최종수"] == 2
    assert record["첫보충후수"] == 2
    assert record["도달단계"] == "최종"
    assert record["작성한도도달"] is False
    assert record["검수한도도달"] is False
    _assert_contract_valid(record)
    _assert_no_leaked_content(record)


# ══════════════════════════════════════════════════════════
# ③ 보충 경로 — 번호를 못 읽어도 본문 확인 문장으로 채운다
# ══════════════════════════════════════════════════════════


def test_unreadable_selection_uses_confirmed_body() -> None:
    (final, _draft_count, _numeric), diagnostics, selector = _run(
        _BODY_THREE, raw="번호를 고르기 어렵습니다",
    )
    assert selector.calls == 1
    assert len(final.summary) == 3
    assert {s.text for s in final.summary} == {
        section.sentences[0].text for section in _BODY_THREE.sections
    }

    record = diagnostics[0]
    assert record["초안수"] == 0  # 고르기는 «도달»했지만 읽을 번호가 없었다
    assert record["검수후수"] is None
    assert record["첫보충후수"] == 3
    assert record["최종수"] == 3
    assert record["도달단계"] == "최종"
    _assert_contract_valid(record)
    _assert_no_leaked_content(record)


def test_한_장에서_몰아_고르면_나머지는_다른_장으로_채운다() -> None:
    """★ 계약 「장당 최대 1개」를 운영 진입점에서 못 박는다 (P2-2).

    실측 재현 — 1장에 후보 3개를 두고 [1, 2, 3]을 답하게 하면 예전에는 요약
    3건이 전부 1장에서 나왔다. 이제는 그 장의 첫 번호만 남고, 빈자리는
    규칙 보충이 «다른 장»에서 채운다. 보충 쪽에도 같은 제한이 걸려 있어야
    한다 — 안 그러면 방금 버린 그 장의 다른 문장이 보충으로 되돌아온다.
    """

    본문 = ComposedReport(
        sections=(
            ComposedSection("identity", (
                _body_sentence("가나다전자 개요 첫 문단이다.", "1"),
                _body_sentence("가나다전자 개요 둘째 문단이다.", "1"),
                _body_sentence("가나다전자 개요 셋째 문단이다.", "1"),
            )),
            ComposedSection("business_model", (
                _body_sentence("가나다전자 사업모델 문단이다.", "2"),
            )),
            ComposedSection("portfolio", (
                _body_sentence("가나다전자 포트폴리오 문단이다.", "3"),
            )),
        )
    )
    장_by_text = {
        sentence.text: section.section_id
        for section in 본문.sections
        for sentence in section.sentences
    }

    (final, draft_count, _numeric), diagnostics, selector = _run(
        본문, numbers=[1, 2, 3],
    )

    assert selector.calls == 1
    assert draft_count == 1, "한 장에서 고른 셋 중 하나만 인정해야 한다"
    장들 = [장_by_text[sentence.text] for sentence in final.summary]
    assert len(final.summary) == 3
    assert len(set(장들)) == 3, f"한 장에서 여러 문장이 실렸다: {장들}"
    assert final.summary[0].text == "가나다전자 개요 첫 문단이다."

    record = diagnostics[0]
    assert record["초안수"] == 1
    assert record["첫보충후수"] == 3
    assert record["최종수"] == 3


def test_후보가_최소_문장_수에_못_미치면_고르기를_부르지_않는다() -> None:
    """★ 어차피 막힐 실행에서 유료 1회를 태우지 않는다 (P3-4).

    후보가 3문장 미만이면 무엇을 골라도 요약이 3문장을 못 채우고, 출고 검증이
    보고서 전체를 막는다(그 정책은 `test_pipeline.py`의 run_v2 시험이 지킨다).
    그러니 고르기 호출은 결과를 바꾸지 못한 채 사라질 뿐이다.
    """

    (final, draft_count, _numeric), diagnostics, selector = _run(
        _BODY_THIN, numbers=[1, 2],
    )

    assert selector.calls == 0, "막힐 실행에서 고르기 AI를 불렀다"
    assert selector.prompts == []
    assert draft_count == 0
    assert len(final.summary) == 2  # fail-closed — 억지로 채우지 않는다

    record = diagnostics[0]
    assert record["초안수"] == 0
    assert record["작성한도도달"] is False  # 한도가 아니라 «부를 이유가 없다»
    assert record["첫보충후수"] == 2
    assert record["최종수"] == 2


def test_partial_selection_is_topped_up_by_the_body() -> None:
    """하나만 골라도 나머지는 검증된 본문 문장이 채운다 — 호출은 여전히 1회."""

    (final, draft_count, _numeric), diagnostics, selector = _run(
        _BODY_THREE, numbers=[2],
    )
    assert draft_count == 1
    assert selector.calls == 1
    assert len(final.summary) == 3
    # 고른 문장이 맨 앞에 남고, 보충분이 뒤에 붙는다.
    assert final.summary[0].text == _BODY_THREE.sections[1].sentences[0].text

    record = diagnostics[0]
    assert record["초안수"] == 1
    assert record["첫보충후수"] == 3
    assert record["최종수"] == 3


# ══════════════════════════════════════════════════════════
# ④ 수치 안전 검사는 «후보 단계»에서 미리 걸린다
# ══════════════════════════════════════════════════════════


def test_unbound_numeric_sentence_never_becomes_a_candidate() -> None:
    """★ 예전에는 요약을 다 만든 «뒤»에 이 검사를 걸었다.

    그래서 걸러진 자리를 규칙 보충이 메우고, 그 보충분이 또 같은 이유로
    걸리는 일이 반복됐다(실측: 수치검사후수 0). 이제는 통과하지 못할 문장이
    애초에 후보 목록에 실리지 않는다 — 프롬프트에 그 글자가 없다.
    """

    (final, draft_count, numeric_filtering), diagnostics, selector = _run(
        _BODY_WITH_UNSAFE_NUMERIC, numbers=[1, 2, 3, 4, 5],
    )

    assert selector.calls == 1
    프롬프트 = selector.prompts[0]
    assert _UNSAFE_NUMERIC_TEXT not in 프롬프트, (
        "미결속 수치 문장이 요약 후보로 나갔다 — AI가 고를 수 있는 자리가 남았다"
    )
    # 후보는 안전한 3문장뿐이라 4·5번은 «범위 밖»으로 버려진다.
    assert draft_count == 3
    assert [s.text for s in final.summary] == [
        section.sentences[0].text for section in _BODY_THREE.sections
    ]
    assert all(_UNSAFE_NUMERIC_TEXT not in s.text for s in final.summary)
    # 요약에서 뺀 것이 «없다» — 애초에 들어가지 않았기 때문이다.
    assert numeric_filtering.removed_summary_count == 0

    record = diagnostics[0]
    assert record["본문후보수"] == 4  # 본문 문장 수는 그대로 4다
    assert record["초안수"] == 3
    assert record["수치검사후수"] == 3
    assert record["최종수"] == 3
    _assert_contract_valid(record)
    _assert_no_leaked_content(record)


# ══════════════════════════════════════════════════════════
# ⑤ 요청 한도 — 고르기 호출이 한도에 걸린 경우 (degradable=True)
# ══════════════════════════════════════════════════════════


def test_selector_limit_uses_body_without_extra_calls() -> None:
    (final, draft_count, _numeric), diagnostics, selector = _run(
        _BODY_THREE,
        error=AskFatalError(RuntimeError("요청 한도 시험 사유"), call_limit=True),
    )
    assert draft_count == 0
    assert len(final.summary) == 3
    assert selector.calls == 1, "한도에 걸린 뒤 다시 부르지 않는다"

    record = diagnostics[0]
    assert record["작성한도도달"] is True
    # ★ 검수 한도 칸은 영구히 False다 — 부를 검수 자체가 없다.
    assert record["검수한도도달"] is False
    assert record["초안수"] == 0
    assert record["검수후수"] is None
    assert record["첫보충후수"] == 3
    assert record["최종수"] == 3
    assert record["도달단계"] == "최종"
    _assert_contract_valid(record)
    _assert_no_leaked_content(record)


# ══════════════════════════════════════════════════════════
# ⑥ 비분해 fatal 예외 — degradable=False, 재전파 + 도달단계만 폐쇄형으로 남는다
# ══════════════════════════════════════════════════════════


def test_selector_fatal_preserves_initial_observation() -> None:
    fatal = AskFatalError(
        RuntimeError("결제 불확실 차단 시험 사유"),
        call_limit=False, request_budget=False,
    )
    diagnostics: list[dict] = []
    selector = _FakeSelector(error=fatal)

    with pytest.raises(AskFatalError) as excinfo:
        _legacy_summary_stage(
            _BODY_THREE,
            writer_ask=selector,
            body_numeric_filtering=NumericSafetyFiltering(),
            summary_diagnostics=diagnostics,
        )
    assert excinfo.value.degradable is False

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


def test_검수_단계는_어떤_경로에서도_도달하지_않는다() -> None:
    """★ 예전 «검수» 단계 시험 2건을 대체한다.

    요약 재검증이 없어졌으므로 「검수」 도달단계와 검수 개수 칸은 어떤
    입력에서도 채워지지 않아야 한다. 다시 채워진다면 재검증이 되살아났다는
    뜻이고, 그때 요약은 다시 «본문과 다른 잣대»로 걸러진다.
    """

    경우들 = [
        dict(numbers=[1, 2, 3]),           # 정상 선택
        dict(numbers=[1]),                  # 부분 선택 → 보충
        dict(raw="JSON 아님"),              # 판독 실패 → 보충
        dict(error=AskFatalError(
            RuntimeError("요청 한도 시험 사유"), call_limit=True,
        )),                                 # 한도 → 보충
    ]

    for 경우 in 경우들:
        (_final, _draft, _numeric), diagnostics, _selector = _run(
            _BODY_THREE, **경우,
        )
        record = diagnostics[0]
        assert record["검수후수"] is None, 경우
        assert record["검수한도도달"] is False, 경우
        assert record["도달단계"] == "최종", 경우


# ══════════════════════════════════════════════════════════
# 안전 보충의 «고르는 순서» (독립 검토 F6)
# ══════════════════════════════════════════════════════════


def test_안전보충은_각_장의_첫_문장을_마지막_순위로_돌린다() -> None:
    """★ 예전에는 이 경로가 걸릴 때마다 요약이 「1·2·3장 첫 문장」이었다.

    장을 번갈아 돌며 «각 장의 첫 문장»부터 집었기 때문이다. 실측 실행의 요약
    3건이 정확히 그 서명이었다. 장마다 다른 문장이 있으면 그것부터 쓴다.
    """

    chosen = _supplement_safe_summary((), _BODY_TWO_EACH)

    texts = [sentence.text for sentence in chosen]
    firsts = [section.sentences[0].text for section in _BODY_TWO_EACH.sections]
    assert len(texts) == 3
    assert not (set(texts) & set(firsts)), (
        f"각 장의 첫 문장이 그대로 요약이 됐다: {texts}"
    )
    assert texts == [
        section.sentences[1].text for section in _BODY_TWO_EACH.sections
    ]


def test_안전보충은_한_문장뿐인_장에서는_그_첫_문장을_쓴다() -> None:
    """순서만 바꿨을 뿐 «쓸 수 있는 문장 집합»은 같다 — 빈 요약 위험 0."""

    thin = ComposedReport(
        sections=tuple(
            ComposedSection(section.section_id, section.sentences[:1])
            for section in _BODY_TWO_EACH.sections
        )
    )

    chosen = _supplement_safe_summary((), thin)

    assert [sentence.text for sentence in chosen] == [
        section.sentences[0].text for section in thin.sections
    ]


def test_안전보충은_다른_문장을_다_쓴_뒤에야_첫_문장을_쓴다() -> None:
    """장이 둘뿐이라 최소 문장 수를 채우려면 첫 문장까지 가야 한다."""

    two_sections = ComposedReport(sections=_BODY_TWO_EACH.sections[:2])

    chosen = _supplement_safe_summary((), two_sections)
    texts = [sentence.text for sentence in chosen]

    assert len(texts) == 3
    # 둘째 문장 둘이 먼저, 그다음에야 첫 문장 하나가 온다.
    assert texts[:2] == [
        section.sentences[1].text for section in two_sections.sections
    ]
    assert texts[2] == two_sections.sections[0].sentences[0].text
