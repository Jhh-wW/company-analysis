# -*- coding: utf-8 -*-
"""최종 렌더 «문체·시점 표기» 기록이 실행 기록 계약을 닫힌 꼴로만 통과하는지 잰다.

★ 왜 이 시험이 있나 (2026-09-23 실측)
  문체 정규화기가 센 «지난 일정 미래형» 개수는 운영 로그에만 남고 실행 기록
  싱크에는 들어가지 않았다. 싱크에 넣기 시작하면 `observed_composition_steps`
  가 그 기록을 «받아야» 실행 진단(steps)에 실린다 — 닫힌 step 분기에 없으면
  조용히 버려진다. 여기서는 그 분기가 무엇을 받고 무엇을 버리는지를 잰다.

★ 여기서 지키는 것
  ⓐ 닫힌 step·닫힌 사유 코드·1 이상의 정수 개수만 남고, 원문·본문·응답 같은
     여분 칸은 결과에 옮겨 적지 않는다.
  ⓑ 사유 밖 키, bool·음수·0·문자열·실수·None 개수, 빈 «사유별», 사전이 아닌
     «사유별», 닫힌 값 밖의 «렌더» 구분은 기록을 통째로 버린다(fail-closed).
  ⓒ 기존 진단(판독·요약 등)은 그대로 통과하고 시도 순서도 보존된다.
  ⓓ 정규화는 생산자의 기록을 수정하지 않고, 결과는 사본이다.
"""
from __future__ import annotations

import json

import pytest

from src.shared.report_quality.composition_diagnostic_constants import (
    STYLE_COUNTS_FIELD,
    STYLE_REASON_PAST_DATED_FUTURE_TENSE,
    STYLE_REASONS,
    STYLE_RENDER_EVIDENCE_AVAILABLE,
    STYLE_RENDER_FIELD,
    STYLE_RENDER_PRIMARY,
    STYLE_RENDER_SUPPLEMENT,
    STYLE_RENDERS,
    STYLE_STEP,
)
from src.shared.report_quality.composition_diagnostics import observed_composition_steps

#: 실제 step 이름·칸 이름 — 상수가 바뀌면 생산자(composer)와 소비자(pipeline)가
#: 같이 바뀌어야 하므로 글자 그대로도 한 번 적어 둔다.
_STEP_LITERAL = "8_문체_표기"
_FIELD_LITERAL = "사유별"
_REASON_LITERAL = "past_dated_future_tense"
_RENDER_LITERAL = "렌더"
_RENDERS_LITERAL = frozenset({"1차", "보충", "확보근거"})
#: 시험에서만 쓰는 «비공개» 문구 — 결과 직렬화에 절대 나오면 안 된다.
_PRIVATE_SENTENCE = "가나다전자의 생산라인은 2026년 3월 31일자로 가동될 예정이다."


def _style_record(**changes: object) -> dict:
    return {
        "step": STYLE_STEP,
        STYLE_RENDER_FIELD: STYLE_RENDER_PRIMARY,
        STYLE_COUNTS_FIELD: {STYLE_REASON_PAST_DATED_FUTURE_TENSE: 1},
        **changes,
    }


def _protocol_record() -> dict:
    return {
        "step": "8_본문검수_응답판독",
        "경로": "flat", "판독": "ok", "추출방식": "direct",
        "시도": 1, "입력문자": 10, "응답문자": 20, "요청번호수": 1,
        "응답행수": 1, "유효행수": 1, "미응답번호수": 0, "요청밖번호수": 0,
        "json시작offset": 0, "json끝offset": 5,
        "행탈락": {},
    }


# ══════════════════════════════════════════════════════════
# 계약 글자 — 생산자·소비자가 같은 이름을 쓴다
# ══════════════════════════════════════════════════════════


def test_계약_글자가_고정돼_있다():
    assert STYLE_STEP == _STEP_LITERAL
    assert STYLE_COUNTS_FIELD == _FIELD_LITERAL
    assert STYLE_REASON_PAST_DATED_FUTURE_TENSE == _REASON_LITERAL
    assert STYLE_REASONS == frozenset({_REASON_LITERAL})
    assert STYLE_RENDER_FIELD == _RENDER_LITERAL
    assert STYLE_RENDERS == _RENDERS_LITERAL
    assert {STYLE_RENDER_PRIMARY, STYLE_RENDER_SUPPLEMENT,
            STYLE_RENDER_EVIDENCE_AVAILABLE} == _RENDERS_LITERAL


# ══════════════════════════════════════════════════════════
# ⓐ 닫힌 칸만 남기고 원문은 버린다
# ══════════════════════════════════════════════════════════


def test_닫힌_사유와_개수만_남기고_원문은_버린다():
    record = _style_record(
        원문=_PRIVATE_SENTENCE, 본문=_PRIVATE_SENTENCE, 응답="비공개 응답",
        error="임의 오류문", tokens=100,
    )
    observed = observed_composition_steps([record])
    assert observed == (
        {"step": _STEP_LITERAL, _RENDER_LITERAL: "1차",
         _FIELD_LITERAL: {_REASON_LITERAL: 1}},
    )
    serialized = json.dumps(observed, ensure_ascii=False)
    for private in (_PRIVATE_SENTENCE, "비공개 응답", "임의 오류문", "tokens"):
        assert private not in serialized


def test_개수가_여럿이어도_정수면_그대로_남는다():
    observed = observed_composition_steps([_style_record(사유별={_REASON_LITERAL: 7})])
    assert observed == (
        {"step": _STEP_LITERAL, _RENDER_LITERAL: "1차",
         _FIELD_LITERAL: {_REASON_LITERAL: 7}},
    )


@pytest.mark.parametrize("render", sorted(_RENDERS_LITERAL))
def test_닫힌_렌더_구분은_셋_다_그대로_남는다(render: str):
    """1차·보충·확보근거 — 같은 실행의 서로 다른 렌더 관측을 구별하는 칸이다."""
    observed = observed_composition_steps([_style_record(렌더=render)])
    assert observed == (
        {"step": _STEP_LITERAL, _RENDER_LITERAL: render,
         _FIELD_LITERAL: {_REASON_LITERAL: 1}},
    )


@pytest.mark.parametrize("render", [
    pytest.param("임의_렌더", id="닫힌 값 밖 글자"),
    pytest.param("", id="빈 문자열"),
    pytest.param(None, id="None"),
    pytest.param(1, id="정수"),
    pytest.param(True, id="bool"),
    pytest.param(["1차"], id="목록"),
    pytest.param("1차 ", id="공백 붙은 비슷한 값"),
])
def test_닫힌_값_밖의_렌더_구분은_기록을_버린다(render: object):
    assert observed_composition_steps([_style_record(렌더=render)]) == ()


def test_렌더_칸이_없으면_기록을_버린다():
    """구분 없는 기록을 받으면 1차·보충 관측이 다시 섞인다 — 칸은 필수다."""
    record = _style_record()
    del record[STYLE_RENDER_FIELD]
    assert observed_composition_steps([record]) == ()


# ══════════════════════════════════════════════════════════
# ⓑ 열린 값은 기록을 통째로 버린다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("counts", [
    pytest.param({_REASON_LITERAL: 0}, id="0건은 빈 이벤트라 계약 밖"),
    pytest.param({_REASON_LITERAL: -1}, id="음수"),
    pytest.param({_REASON_LITERAL: True}, id="bool은 정수가 아니다"),
    pytest.param({_REASON_LITERAL: "1"}, id="문자열 숫자"),
    pytest.param({_REASON_LITERAL: 1.0}, id="실수"),
    pytest.param({_REASON_LITERAL: None}, id="None"),
    pytest.param({_REASON_LITERAL: [1]}, id="목록"),
    pytest.param({}, id="빈 사유별"),
    pytest.param({"임의_사유": 1}, id="사유 목록 밖 키"),
    pytest.param({_REASON_LITERAL: 1, "임의_사유": 1}, id="닫힌 키와 섞여도 통째로"),
    pytest.param({_REASON_LITERAL: 1, "원문": _PRIVATE_SENTENCE}, id="원문이 사유 칸에"),
    pytest.param({1: 1}, id="문자열이 아닌 키"),
])
def test_열린_사유별_값은_기록을_버린다(counts: object):
    assert observed_composition_steps([_style_record(사유별=counts)]) == ()


@pytest.mark.parametrize("counts", [
    pytest.param("1", id="문자열"),
    pytest.param([_REASON_LITERAL], id="목록"),
    pytest.param(1, id="정수"),
    pytest.param(None, id="None"),
])
def test_사전이_아닌_사유별은_기록을_버린다(counts: object):
    assert observed_composition_steps([_style_record(사유별=counts)]) == ()


def test_사유별_칸이_없으면_기록을_버린다():
    record = _style_record()
    del record[STYLE_COUNTS_FIELD]
    assert observed_composition_steps([record]) == ()


def test_비슷한_이름의_열린_step은_받지_않는다():
    assert observed_composition_steps([
        _style_record(step="8_문체_표기_"), _style_record(step="문체_표기"),
        _style_record(step=None), _style_record(step=["8_문체_표기"]),
    ]) == ()


# ══════════════════════════════════════════════════════════
# ⓒ 기존 진단은 그대로 — 순서·개수 보존, 중복 제거 없음
# ══════════════════════════════════════════════════════════


def test_기존_진단_사이에_끼어도_순서와_개수가_보존된다():
    protocol = _protocol_record()
    observed = observed_composition_steps([
        protocol, _style_record(원문=_PRIVATE_SENTENCE), "오염", {},
        _style_record(사유별={_REASON_LITERAL: "1"}), protocol,
    ])
    assert [record["step"] for record in observed] == [
        "8_본문검수_응답판독", _STEP_LITERAL, "8_본문검수_응답판독",
    ]
    assert _PRIVATE_SENTENCE not in json.dumps(observed, ensure_ascii=False)


def test_같은_기록이_두_번_와도_합치지_않는다():
    """본 경로 렌더와 보충 렌더는 별개 관측이다 — 개수를 더하거나 지우지 않는다."""
    observed = observed_composition_steps([_style_record(), _style_record()])
    assert len(observed) == 2
    assert all(record[_FIELD_LITERAL] == {_REASON_LITERAL: 1} for record in observed)


# ══════════════════════════════════════════════════════════
# ⓓ 생산자 기록을 수정하지 않고 결과는 사본이다
# ══════════════════════════════════════════════════════════


def test_정규화는_생산자_기록을_수정하지_않고_사본을_돌려준다():
    counts = {_REASON_LITERAL: 3}
    record = _style_record(사유별=counts, 원문=_PRIVATE_SENTENCE)
    observed = observed_composition_steps([record])
    assert record["원문"] == _PRIVATE_SENTENCE, "생산자 기록에서 칸을 지우면 안 된다"
    observed[0][_FIELD_LITERAL][_REASON_LITERAL] = 999
    assert counts[_REASON_LITERAL] == 3, "결과가 생산자 사전과 같은 객체면 안 된다"
