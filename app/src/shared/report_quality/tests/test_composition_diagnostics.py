"""실패 관측의 원문 제거와 미도달 단계 보존을 검사한다."""

import json

import pytest

from src.shared.report_quality.composition_diagnostics import observed_composition_steps


def _parse_event(**changes: object) -> dict:
    return {
        "step": "8_본문검수_응답판독", "경로": "flat", "시도": 1,
        "판독": "json_syntax", "추출방식": "failed",
        "json시작offset": -1, "json끝offset": -1,
        "입력문자": 400, "응답문자": 12, "요청번호수": 3,
        "응답행수": 0, "유효행수": 0, "미응답번호수": 3,
        "요청밖번호수": 0, "행탈락": {},
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
])
def test_rejects_invalid_protocol_fields(changes: dict) -> None:
    assert observed_composition_steps([_parse_event(**changes)]) == ()


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
