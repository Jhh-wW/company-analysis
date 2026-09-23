"""실패 관측의 원문 제거와 미도달 단계 보존을 검사한다."""

import json

import pytest

from src.shared.report_quality.composition_diagnostics import observed_composition_steps


def test_작성_시간과_상한만_남기고_원문은_버린다():
    record = {"step": "v2_작성_실행방식", "동시상한": 3, "장수": 9, "소요_ms": 200}
    assert observed_composition_steps([{**record, "원문": "비공개"}]) == (record,)


@pytest.mark.parametrize("target,skipped", [(7, 2), (0, 9), (9, 0)])
def test_부분작성_대상과_생략수는_함께_보존하고_임의_호출수는_버린다(target, skipped):
    record = {
        "step": "v2_작성_실행방식", "동시상한": 3, "장수": 9, "소요_ms": 200,
        "작성대상장수": target, "빈근거생략장수": skipped,
    }
    assert observed_composition_steps([{**record, "원문": "비공개", "실제호출수": 9}]) == (record,)


@pytest.mark.parametrize("counts", [
    {"작성대상장수": 7}, {"빈근거생략장수": 2},
    {"작성대상장수": -1, "빈근거생략장수": 10},
    {"작성대상장수": True, "빈근거생략장수": 8},
    {"작성대상장수": 7, "빈근거생략장수": "원문"},
    {"작성대상장수": 7, "빈근거생략장수": -2},
    {"작성대상장수": 7, "빈근거생략장수": False},
    {"작성대상장수": 7, "빈근거생략장수": 3},
    {"작성대상장수": 6, "빈근거생략장수": 2},
])
def test_부분작성_수의_누락과_열린값과_목차불일치는_거절한다(counts):
    record = {"step": "v2_작성_실행방식", "동시상한": 3, "장수": 9, "소요_ms": 200, **counts}
    assert observed_composition_steps([record]) == ()


@pytest.mark.parametrize("field,value", [
    ("동시상한", 0), ("동시상한", True), ("장수", -1), ("소요_ms", "원문"),
])
def test_작성_시간의_열린_값을_거부한다(field, value):
    record = {"step": "v2_작성_실행방식", "동시상한": 3, "장수": 9, "소요_ms": 200}
    record[field] = value
    assert observed_composition_steps([record]) == ()


def test_empty_recovery_records_closed_counts_and_machine_semantic_stages():
    records = [
        {"step": "8_본문검수_기계통과", "장별": {
            "past_changes": {"초안": 3, "기계통과": 0, "원문": "비공개"},
            "culture": {"초안": 2, "기계통과": 2}}},
        {"step": "8_본문검수_처분", "장별빈본문": ["past_changes", "culture"],
         "문장재작성허용": False, "판정별": {"근거결속실패_제거": 2}},
        {"step": "8_빈장_복구", "상태": "작성완료", "대상장": ["past_changes"],
         "작성문장수": 2, "요청밖장수": 1, "원문": "비공개", "응답": "비공개", "tokens": 100},
        {"step": "8_빈장_복구", "상태": "검수완료", "대상장": ["past_changes"],
         "복구장": ["past_changes"], "error": "비공개"},
        {"step": "8_빈장_복구", "상태": "호출중단", "대상장": ["culture"],
         "오류종류": "제공자오류", "원문": "비공개"},
    ]
    result = observed_composition_steps(records)
    assert len(result) == 5
    assert result[0]["장별"]["past_changes"] == {"초안": 3, "기계통과": 0}
    assert result[0]["장별"]["culture"]["기계통과"] == 2
    assert result[1]["장별빈본문"] == ["past_changes", "culture"]
    # 요청하지 않은 장을 몇 개 끼워 넣었는지까지 남는다 — 부분 수용으로 바뀌면서
    # 「형식이 어긋났다」는 사실이 통째 포기와 함께 사라지지 않게 하는 칸이다.
    assert result[2]["요청밖장수"] == 1
    assert result[3]["복구장"] == ["past_changes"]
    serialized = json.dumps(result, ensure_ascii=False)
    assert "비공개" not in serialized and "tokens" not in serialized


def test_recovery_not_started_records_carry_only_state_and_targets():
    """시작조차 못 한 두 사유가 닫힌 목록을 통과하고 원문 없이 남는다."""
    records = [
        {"step": "8_빈장_복구", "상태": "예산부족", "대상장": ["culture", "future_strategy"],
         "원문": "비공개"},
        {"step": "8_빈장_복구", "상태": "근거후보없음", "대상장": ["culture"], "tokens": 100},
    ]
    result = observed_composition_steps(records)
    assert result == (
        {"step": "8_빈장_복구", "상태": "예산부족", "대상장": ["culture", "future_strategy"]},
        {"step": "8_빈장_복구", "상태": "근거후보없음", "대상장": ["culture"]},
    )


@pytest.mark.parametrize("record", [
    {"step": "8_빈장_복구", "상태": [], "대상장": ["culture"]},
    {"step": "8_빈장_복구", "상태": "본문", "대상장": ["culture"]},
    {"step": "8_빈장_복구", "상태": "작성완료", "대상장": ["culture"], "작성문장수": True},
    {"step": "8_빈장_복구", "상태": "작성완료", "대상장": ["culture"], "작성문장수": -1},
    # 요청밖장수·시도는 «있어야» 하는 칸이다. 빠지면 그 기록을 통째로 버린다.
    {"step": "8_빈장_복구", "상태": "작성완료", "대상장": ["culture"], "작성문장수": 1},
    {"step": "8_빈장_복구", "상태": "작성완료", "대상장": ["culture"], "작성문장수": 1,
     "요청밖장수": -1},
    {"step": "8_빈장_복구", "상태": "작성형식실패", "대상장": ["culture"]},
    {"step": "8_빈장_복구", "상태": "작성형식실패", "대상장": ["culture"], "시도": 0},
    {"step": "8_빈장_복구", "상태": "검수완료", "대상장": ["culture"], "복구장": ["identity"]},
    {"step": "8_빈장_복구", "상태": "검수완료", "대상장": ["본문"], "복구장": []},
    {"step": "8_빈장_복구", "상태": "호출중단", "대상장": ["culture"], "오류종류": "비공개 오류문"},
    {"step": "8_본문검수_기계통과", "장별": {"본문": {"초안": 1, "기계통과": 1}}},
    {"step": "8_본문검수_기계통과", "장별": {"culture": {"초안": 1, "기계통과": 2}}},
    {"step": "8_본문검수_처분", "장별빈본문": ["culture"], "문장재작성허용": True, "판정별": {"본문": 1}},
])
def test_rejects_open_or_invalid_recovery_diagnostic_fields(record):
    assert observed_composition_steps([record]) == ()


def _parse_event(**changes: object) -> dict:
    return {
        "step": "8_본문검수_응답판독", "경로": "flat", "시도": 1,
        "판독": "json_syntax", "추출방식": "failed",
        "json시작offset": -1, "json끝offset": -1,
        "입력문자": 400, "응답문자": 12, "요청번호수": 3,
        "응답행수": 0, "유효행수": 0, "미응답번호수": 3,
        "요청밖번호수": 0, "구문탈락행수": 0, "행탈락": {},
        **changes,
    }


def _summary_event(**changes: object) -> dict:
    return {
        "step": "8_핵심요약_단계", "경로": "legacy", "도달단계": "작성",
        "본문후보수": 7, "초안수": 0, "검수후수": None,
        "첫보충후수": None, "수치검사후수": None, "최종수": None,
        "작성한도도달": False, "검수한도도달": False,
        **changes,
    }


def test_preserves_attempt_order_and_unreached_stages_without_raw_text() -> None:
    first = _parse_event(response="비공개 검수 응답", raw_text="회사 본문")
    second = _parse_event(시도=2, 행탈락={
        "number_not_int": 2, "응답전문": "비공개 검수 응답",
        "evidence_ids_mismatch": "문자 오염",
    })
    summary = _summary_event(error="임의 오류문")
    observed = observed_composition_steps([first, "오염", {}, second, summary])
    assert len(observed) == 3
    assert [record["시도"] for record in observed[:2]] == [1, 2]
    assert observed[1]["행탈락"] == {"number_not_int": 2}
    assert observed[2]["초안수"] == 0
    assert observed[2]["검수후수"] is None
    serialized = json.dumps(observed, ensure_ascii=False)
    for private in ("비공개", "회사 본문", "임의 오류문", "문자 오염"):
        assert private not in serialized
    # 정규화하면서 생산자가 가진 기록을 수정하지 않는다.
    assert first["response"] == "비공개 검수 응답"


@pytest.mark.parametrize("changes", [
    {"시도": True}, {"응답행수": -1}, {"json시작offset": -2},
    {"판독": "임의 응답"}, {"경로": ["flat"]}, {"추출방식": {}},
    {"미응답번호수": "0"}, {"행탈락": "본문"},
    {"구문탈락행수": -1}, {"구문탈락행수": True}, {"구문탈락행수": "1"},
    {"추출방식": "row_salvage_guess"},
])
def test_rejects_invalid_protocol_fields(changes: dict) -> None:
    assert observed_composition_steps([_parse_event(**changes)]) == ()


def test_row_salvage_observation_passes_with_dropped_row_count() -> None:
    """행 단위 구제 관측(2026-09-23)이 닫힌 목록을 통과하고 두 칸이 그대로 남는다.

    ★ 정화기는 닫힌 목록 밖 값을 만나면 기록을 «통째로» 버린다. 새 추출방식과
      새 개수 칸을 목록에 넣지 않으면 구제가 일어난 바로 그 실행의 판독 기록이
      실행 기록에서 사라진다.
    """
    event = _parse_event(
        판독="ok", 추출방식="row_salvage", 응답행수=41, 유효행수=41,
        요청번호수=42, 미응답번호수=1, 구문탈락행수=1,
        json시작offset=8, json끝offset=6558, 응답="비공개 응답 원문",
    )
    (observed,) = observed_composition_steps([event])
    assert observed["추출방식"] == "row_salvage"
    assert observed["구문탈락행수"] == 1
    assert (observed["응답행수"], observed["미응답번호수"]) == (41, 1)
    assert "응답" not in observed


@pytest.mark.parametrize(
    "read_code", [
        "call_limit_reached", "request_budget_exhausted", "global_failure",
        "provider_failure_degraded",
    ],
)
def test_optional_call_abort_read_codes_pass_the_sanitizer(read_code: str) -> None:
    """두 번째 검수 호출을 요청 AI 몫 소진으로 포기한 시도(2026-09-23)와 요청 «전역»
    장애로 멈춘 시도(``global_failure``, 2026-09-24 결정 3)의 판독 코드.

    닫힌 목록에 없으면 정화기가 그 시도 기록을 통째로 버려, 「왜 후속이 안 됐나」가
    실행 기록에서 사라진다.
    """
    extra = {"원인종류": "ProviderBudgetExceeded"} if read_code == "global_failure" else {}
    event = _parse_event(
        시도=2, 판독=read_code, 응답문자=0, 요청번호수=1, 미응답번호수=1, **extra,
    )
    (observed,) = observed_composition_steps([event])
    assert observed["판독"] == read_code
    assert observed == event  # 전역 장애의 원인 «종류» 칸도 그대로 지난다


@pytest.mark.parametrize("cause_kind", [
    None, "", "예산 소진", "Provider Budget", "일일 예산이 소진됐습니다: 잔액 0원", "A" * 81, 7,
], ids=("missing", "empty", "korean", "space", "message", "too_long", "not_text"))
def test_global_failure_without_a_closed_cause_kind_is_dropped(cause_kind) -> None:
    """원인 «종류»는 예외 클래스 이름 모양만 받는다 — 오류 문구가 새면 기록째 버린다."""
    extra = {} if cause_kind is None else {"원인종류": cause_kind}
    event = _parse_event(시도=2, 판독="global_failure", 응답문자=0, **extra)
    assert observed_composition_steps([event]) == ()


def test_cause_kind_is_not_carried_on_other_read_codes() -> None:
    event = _parse_event(시도=2, 판독="call_limit_reached", 응답문자=0, 원인종류="RuntimeError")
    (observed,) = observed_composition_steps([event])
    assert "원인종류" not in observed


def test_protocol_record_without_dropped_row_count_is_rejected() -> None:
    """구문탈락행수는 필수 칸이다 — 생산자(new_protocol_observation)는 늘 0 이상을 채운다."""
    event = _parse_event()
    del event["구문탈락행수"]
    assert observed_composition_steps([event]) == ()


@pytest.mark.parametrize("changes", [
    {"도달단계": "원문"}, {"도달단계": []}, {"작성한도도달": 1},
    {"최종수": False}, {"초안수": -1}, {"경로": "FULL"},
])
def test_rejects_invalid_summary_fields(changes: dict) -> None:
    assert observed_composition_steps([_summary_event(**changes)]) == ()


def test_does_not_deduplicate_retry_observations() -> None:
    event = _parse_event()
    assert len(observed_composition_steps([event, event])) == 2


# ══════════════════════════════════════════════════════════
# 도식 파생 비율 단계 — 수 칸과 지문 칸의 닫힌 검사
# ══════════════════════════════════════════════════════════
#
# ★ 왜 이 시험이 있나 (2026-09-11 독립 검토 P2) — 이 단계는 «후보가 적어 낸
#   백분율»과 «근거 쌍 지문»만 받도록 닫혀 있는데, 그 검사를 무력화해도 깨지는
#   시험이 하나도 없었다. 검사가 없는 것과 같았다.
# ★ 지키는 것 둘 — ① 십진수가 아닌 문자열(회사명·쉼표 금액·음수·지수·퍼센트)은
#   버린다. ② 근거 쌍은 지문(16진수 64자리)으로만 받는다. 원문 금액이 실려 오면
#   항목을 통째로 버린다. 이 칸이 열리면 실행 기록으로 원문이 새어 나간다.

_지문 = "a" * 64


def _파생비율_기록(**changes: object) -> dict:
    return {
        "step": "8_도식_파생비율",
        "장": "portfolio",
        "사유코드": "derived_ratio_recomputed",
        "종류": "구성비",
        "백분율": "89.63",
        "근거지문": _지문,
        **changes,
    }


def test_파생비율_기록을_그대로_통과시킨다() -> None:
    """먼저 «정상은 통과한다»를 못 박는다 — 아래 거절 시험이 다른 이유로
    초록이 되는 것을 막기 위해서다."""

    관측 = observed_composition_steps([_파생비율_기록()])

    assert 관측 == (
        {
            "step": "8_도식_파생비율",
            "장": "portfolio",
            "사유코드": "derived_ratio_recomputed",
            "종류": "구성비",
            "백분율": "89.63",
            "근거지문": _지문,
        },
    )


@pytest.mark.parametrize("백분율", [
    "가나다전자",        # 회사명
    "27,351,053,389",   # 쉼표 금액
    "-12.5",            # 음수
    "1e5",              # 지수 표기
    "89.63%",           # 단위가 붙은 표기
    "",                 # 빈 문자열 — 백분율은 언제나 있어야 한다
    " 89.63 ",          # 공백이 붙은 표기
    89.63,              # 문자열이 아님
])
def test_십진수가_아닌_백분율은_버린다(백분율: object) -> None:
    assert observed_composition_steps([_파생비율_기록(백분율=백분율)]) == ()


@pytest.mark.parametrize("지문", [
    "24514287835/27351053389",  # 원문 금액 쌍 — 이 칸으로 새면 안 되는 바로 그것
    "24514287835",              # 원문 금액 하나
    "A" * 64,                   # 대문자 16진수 (sha256 hexdigest 는 소문자다)
    "a" * 63,                   # 63자리
    "a" * 65,                   # 65자리
    "지문없음",
    None,
])
def test_지문_칸에_원문_금액이나_다른_문자열을_받지_않는다(지문: object) -> None:
    assert observed_composition_steps([_파생비율_기록(근거지문=지문)]) == ()


def test_근거_쌍이_없는_기록은_빈_지문으로_통과한다() -> None:
    """상한 초과 기록에는 근거 쌍이 없다. 그 기록까지 버리면 «왜 인정 안 했나»가
    통째로 사라진다."""

    관측 = observed_composition_steps([
        _파생비율_기록(사유코드="derived_ratio_pair_limit", 종류="", 근거지문="")
    ])

    assert len(관측) == 1
    assert 관측[0]["근거지문"] == ""


@pytest.mark.parametrize("changes", [
    {"장": "없는장"}, {"장": 3}, {"사유코드": "임의사유"},
    {"사유코드": None}, {"종류": "증감률"}, {"종류": []},
])
def test_장과_사유코드와_종류는_닫힌_목록만_받는다(changes: dict) -> None:
    assert observed_composition_steps([_파생비율_기록(**changes)]) == ()


def test_계약에_없는_칸은_기록에_남지_않는다() -> None:
    """임의 키를 얹어도 정규화 결과에는 계약 칸만 남는다."""

    관측 = observed_composition_steps([
        _파생비율_기록(분자="24514287835", 분모="27351053389", 원문="회사 본문")
    ])

    assert len(관측) == 1
    assert set(관측[0]) == {"step", "장", "사유코드", "종류", "백분율", "근거지문"}
    직렬 = json.dumps(관측, ensure_ascii=False)
    for 비공개 in ("24514287835", "27351053389", "회사 본문"):
        assert 비공개 not in 직렬


def test_recovery_response_shape_and_stage_counts_survive_sanitizing():
    """2026-09-17 실측: 새로 넣은 응답꼴·관문별 수가 화이트리스트에 걸러져 운영 진단에서 사라졌다."""
    records = [
        {"step": "8_빈장_복구", "상태": "작성형식실패", "대상장": ["past_changes"], "시도": 2,
         "응답꼴": ["요청장없음", "읽기실패"], "원문": "비공개"},
        {"step": "8_빈장_복구", "상태": "작성완료", "대상장": ["past_changes"],
         "작성문장수": 2, "요청밖장수": 0, "응답꼴": "포장없음"},
        {"step": "8_빈장_복구", "상태": "검수완료", "대상장": ["past_changes"], "복구장": [],
         "검수통과": 1, "안전검사후": 1, "최종반영": 0, "응답": "비공개"},
    ]
    result = observed_composition_steps(records)
    assert result == (
        {"step": "8_빈장_복구", "상태": "작성형식실패", "대상장": ["past_changes"], "시도": 2,
         "응답꼴": ["요청장없음", "읽기실패"]},
        {"step": "8_빈장_복구", "상태": "작성완료", "대상장": ["past_changes"],
         "작성문장수": 2, "요청밖장수": 0, "응답꼴": "포장없음"},
        {"step": "8_빈장_복구", "상태": "검수완료", "대상장": ["past_changes"], "복구장": [],
         "검수통과": 1, "안전검사후": 1, "최종반영": 0},
    )


def test_recovery_records_without_new_fields_still_pass():
    """옛 기록(응답꼴·관문별 수 없음)은 그대로 통과한다 — 저장된 실행의 진단을 깨지 않는다."""
    records = [
        {"step": "8_빈장_복구", "상태": "작성형식실패", "대상장": ["culture"], "시도": 2},
        {"step": "8_빈장_복구", "상태": "검수완료", "대상장": ["culture"], "복구장": ["culture"]},
    ]
    assert observed_composition_steps(records) == (
        {"step": "8_빈장_복구", "상태": "작성형식실패", "대상장": ["culture"], "시도": 2},
        {"step": "8_빈장_복구", "상태": "검수완료", "대상장": ["culture"], "복구장": ["culture"]},
    )


@pytest.mark.parametrize("record", [
    {"step": "8_빈장_복구", "상태": "작성형식실패", "대상장": ["culture"], "시도": 2, "응답꼴": "요청장없음"},
    {"step": "8_빈장_복구", "상태": "작성형식실패", "대상장": ["culture"], "시도": 2, "응답꼴": []},
    {"step": "8_빈장_복구", "상태": "작성형식실패", "대상장": ["culture"], "시도": 2, "응답꼴": ["원문 그대로"]},
    {"step": "8_빈장_복구", "상태": "작성완료", "대상장": ["culture"], "작성문장수": 1, "요청밖장수": 0,
     "응답꼴": ["계약"]},
    {"step": "8_빈장_복구", "상태": "검수완료", "대상장": ["culture"], "복구장": [], "검수통과": -1},
    {"step": "8_빈장_복구", "상태": "검수완료", "대상장": ["culture"], "복구장": [], "최종반영": "둘"},
])
def test_rejects_open_response_shapes_and_invalid_stage_counts(record):
    assert observed_composition_steps([record]) == ()


# ══════════════════════════════════════════════════════════
# 근거 결속 재작성 단계 — 개수·닫힌 코드만 통과시킨다
# ══════════════════════════════════════════════════════════
#
# ★ 왜 필요한가 — 이 기록 하나가 「고쳐 쓰지 않았다면 사라졌을 문장 수(대상)」와
#   「고쳐 써서 살아난 문장 수(최종반영)」를 같은 실행 안에 함께 담아, 기능
#   유무를 실측으로 바로 비교할 수 있게 한다. 원문·재작성 응답 글자는 이
#   함수에 들어오는 칸 자체가 아니지만, 닫힌 목록이어야 하는 칸(응답꼴·
#   오류종류)이 열린 문자열을 받아들이면 안 된다.

def _근거결속_재작성_완료_기록(**changes: object) -> dict:
    return {
        "step": "8_근거결속_재작성", "상태": "완료", "대상장": ["past_changes"],
        "대상": 5, "재작성수신": 5, "포기": 0, "기계검사통과": 4,
        "재검수참": 4, "재검수애매": 1, "최종반영": 3,
        **changes,
    }


def test_근거결속_재작성_완료_기록을_닫힌_칸만_남기고_통과시킨다():
    records = [_근거결속_재작성_완료_기록(원문="비공개", 응답="비공개", tokens=100)]

    result = observed_composition_steps(records)

    assert result == (
        {"step": "8_근거결속_재작성", "상태": "완료", "대상장": ["past_changes"],
         "대상": 5, "재작성수신": 5, "포기": 0, "기계검사통과": 4,
         "재검수참": 4, "재검수애매": 1, "최종반영": 3},
    )
    serialized = json.dumps(result, ensure_ascii=False)
    assert "비공개" not in serialized and "tokens" not in serialized


def test_근거결속_재작성_완료_기록의_응답꼴은_있으면_닫힌_목록만_받는다():
    passed = observed_composition_steps([_근거결속_재작성_완료_기록(응답꼴="포장없음")])
    assert passed[0]["응답꼴"] == "포장없음"

    assert observed_composition_steps(
        [_근거결속_재작성_완료_기록(응답꼴="원문 그대로")]
    ) == ()


def test_근거결속_재작성_작성형식실패_기록은_응답꼴_목록과_함께_통과한다():
    records = [{"step": "8_근거결속_재작성", "상태": "작성형식실패", "대상장": ["culture"],
                "대상": 3, "응답꼴": ["요청장없음", "읽기실패"], "원문": "비공개"}]

    result = observed_composition_steps(records)

    assert result == (
        {"step": "8_근거결속_재작성", "상태": "작성형식실패", "대상장": ["culture"],
         "대상": 3, "응답꼴": ["요청장없음", "읽기실패"]},
    )


def test_근거결속_재작성_호출중단_기록은_닫힌_오류종류만_받는다():
    records = [{"step": "8_근거결속_재작성", "상태": "호출중단", "대상장": ["culture"],
                "대상": 2, "오류종류": "호출한도", "원문": "비공개"}]

    result = observed_composition_steps(records)

    assert result == (
        {"step": "8_근거결속_재작성", "상태": "호출중단", "대상장": ["culture"],
         "대상": 2, "오류종류": "호출한도"},
    )


@pytest.mark.parametrize("record", [
    # 열린 상태 문자열
    {"step": "8_근거결속_재작성", "상태": "완료중", "대상장": ["culture"], "대상": 1},
    # 요약 장은 대상장에 올 수 없다 — 재작성은 본문 장만 대상이다.
    {"step": "8_근거결속_재작성", "상태": "완료", "대상장": ["summary"], "대상": 1,
     "재작성수신": 1, "포기": 0, "기계검사통과": 1, "재검수참": 1, "재검수애매": 0, "최종반영": 1},
    # 공통 칸 「대상」이 없거나 음수·bool
    {"step": "8_근거결속_재작성", "상태": "완료", "대상장": ["culture"]},
    {"step": "8_근거결속_재작성", "상태": "완료", "대상장": ["culture"], "대상": -1},
    {"step": "8_근거결속_재작성", "상태": "완료", "대상장": ["culture"], "대상": True},
    # 완료인데 개수 칸 하나가 빠짐
    {"step": "8_근거결속_재작성", "상태": "완료", "대상장": ["culture"], "대상": 1,
     "재작성수신": 1, "포기": 0, "기계검사통과": 1, "재검수참": 1, "재검수애매": 0},
    # 완료인데 개수 칸이 음수
    {"step": "8_근거결속_재작성", "상태": "완료", "대상장": ["culture"], "대상": 1,
     "재작성수신": 1, "포기": 0, "기계검사통과": 1, "재검수참": 1, "재검수애매": -1, "최종반영": 1},
    # 완료인데 응답꼴이 닫힌 목록 밖
    {"step": "8_근거결속_재작성", "상태": "완료", "대상장": ["culture"], "대상": 1,
     "재작성수신": 1, "포기": 0, "기계검사통과": 1, "재검수참": 1, "재검수애매": 0, "최종반영": 1,
     "응답꼴": "원문 그대로"},
    # 작성형식실패인데 응답꼴이 없거나 빈 목록이거나 닫힌 목록 밖 원소
    {"step": "8_근거결속_재작성", "상태": "작성형식실패", "대상장": ["culture"], "대상": 1},
    {"step": "8_근거결속_재작성", "상태": "작성형식실패", "대상장": ["culture"], "대상": 1, "응답꼴": []},
    {"step": "8_근거결속_재작성", "상태": "작성형식실패", "대상장": ["culture"], "대상": 1,
     "응답꼴": ["원문 그대로"]},
    # 호출중단인데 오류종류가 없거나 닫힌 목록 밖
    {"step": "8_근거결속_재작성", "상태": "호출중단", "대상장": ["culture"], "대상": 1},
    {"step": "8_근거결속_재작성", "상태": "호출중단", "대상장": ["culture"], "대상": 1,
     "오류종류": "비공개 오류문"},
])
def test_근거결속_재작성_기록은_닫힌_계약_밖이면_버려진다(record):
    assert observed_composition_steps([record]) == ()


def test_근거결속_재작성_장부자리없음_기록은_대상만_담고_그대로_지난다():
    """FULL 장부가 재작성 자리가 없다고 답한 경우(2026-09-24 발견 1 확정 (a))."""
    record = {
        "step": "8_근거결속_재작성", "상태": "장부자리없음",
        "대상장": ["identity", "culture"], "대상": 3,
    }
    assert observed_composition_steps([record]) == (record,)


# ── 출고 모드 한 줄(2026-09-24 후속) ──
#   모드 이름·자리별 검수 호출 수·장부 사용 여부만 실행 기록으로 간다. «장부사용»과
#   «검수호출»은 짝이다 — 장부가 없으면 null, 있으면 닫힌 두 자리의 0 이상 정수.


def _release_record(**overrides):
    record = {
        "step": "8_출고모드_적용",
        "요청모드": "FULL",
        "적용모드": "FULL",
        "강등출처": "",
        "검수호출": {"bundled": 1, "bundled_retry": 1},
        "장부사용": True,
    }
    record.update(overrides)
    return record


@pytest.mark.parametrize("overrides", [
    {},
    {"적용모드": ""},
    {"적용모드": "SHADOW", "강등출처": "FULL"},
    {"요청모드": "SHADOW", "적용모드": "SHADOW", "검수호출": None, "장부사용": False},
    {"적용모드": "SHADOW", "강등출처": "FULL", "검수호출": None, "장부사용": False},
], ids=("full", "blocked", "downgraded_after_ledger", "shadow", "downgraded_before_ledger"))
def test_출고모드_줄은_정화기를_그대로_지난다(overrides):
    record = _release_record(**overrides)
    assert observed_composition_steps([record]) == (record,)


def test_출고모드_줄의_계약_밖_칸은_실행_기록으로_가지_않는다():
    (observed,) = observed_composition_steps([_release_record(회사="가나다전자")])
    assert "회사" not in observed


@pytest.mark.parametrize("overrides", [
    {"요청모드": ""},
    {"요청모드": "PARTIAL"},
    {"적용모드": "full"},
    {"강등출처": "SHADOW_X"},
    {"장부사용": 1},
    {"장부사용": "true"},
    {"검수호출": None},
    {"장부사용": False},
    {"검수호출": {"bundled": 1}},
    {"검수호출": {"bundled": 1, "bundled_retry": 1, "extra": 0}},
    {"검수호출": {"bundled": -1, "bundled_retry": 0}},
    {"검수호출": {"bundled": True, "bundled_retry": 0}},
    {"검수호출": {"bundled": "1", "bundled_retry": 0}},
])
def test_출고모드_줄이_닫힌_계약_밖이면_버려진다(overrides):
    assert observed_composition_steps([_release_record(**overrides)]) == ()


def test_출고모드_줄의_이름표는_장부와_출고모드_정본과_같다():
    """정화기의 닫힌 목록이 정본과 어긋나면 진짜 줄이 조용히 버려진다(쌍둥이 대조)."""
    from src.shared.report_generation.models import (
        BUNDLED_REVIEW_RETRY_SECTION_ID,
        BUNDLED_REVIEW_SECTION_ID,
    )
    from src.shared.report_quality.composition_diagnostic_constants import (
        RELEASE_MODE_NAMES,
        RELEASE_MODE_REVIEW_SLOTS,
    )

    assert RELEASE_MODE_REVIEW_SLOTS == (
        BUNDLED_REVIEW_SECTION_ID, BUNDLED_REVIEW_RETRY_SECTION_ID,
    )
    assert RELEASE_MODE_NAMES == {"SHADOW", "ENFORCE_NO_PARTIAL", "FULL"}
