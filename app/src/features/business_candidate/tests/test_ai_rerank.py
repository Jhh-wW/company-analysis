"""동점 후보의 AI 보조 재정렬 경계 — 순서만 바꾸고 후보는 잃지 않는다."""

from __future__ import annotations

import json

from src.features.business_candidate import ai_rerank
from src.features.business_candidate.constants import (
    AI_RERANK_MAX_CANDIDATES,
    AI_RERANK_MIN_TIE,
    CANDIDATE_AI_RERANK_ENV_NAME,
    MAX_CANDIDATES,
)
from src.features.business_candidate.logic import BusinessCandidate


def _candidate(name: str, *, score: float = 0.44, **changes) -> BusinessCandidate:
    values = {
        "candidate_name": name,
        "address": "서울특별시 가나구 가나대로 1",
        "homepage": "",
        "source_label": "전자공시(DART) 기업개황 fixture",
        "source_url": "https://opendart.fss.or.kr/",
        "provider_name": "DART",
        "attributions": (),
        "score": score,
        "evidence": ("입력한 회사명이 후보명에 포함됩니다",),
        "candidate_ref": "",
        "stock_code": "",
        "modify_date": "",
        "english_name": "",
        "name_match_kind": "",
        "name_similarity": 0.0,
    }
    values.update(changes)
    return BusinessCandidate(**values)


def _tied(count: int, *, extra_low: int = 0) -> list[BusinessCandidate]:
    """1위와 같은 점수 ``count``개 뒤에 낮은 점수를 ``extra_low``개 붙인다."""

    rows = [_candidate(f"가나다전자{index}", score=0.44) for index in range(count)]
    rows += [
        _candidate(f"가나다물산{index}", score=0.30) for index in range(extra_low)
    ]
    return rows


# ── (a) 재정렬 조건 경계 ────────────────────────────────


def test_동점이_기준보다_하나_적으면_AI를_부르지_않는다():
    assert AI_RERANK_MIN_TIE == 4
    rows = _tied(AI_RERANK_MIN_TIE - 1, extra_low=2)

    assert ai_rerank.should_rerank(rows) is False


def test_동점이_기준과_같아지면_AI에_순서를_묻는다():
    rows = _tied(AI_RERANK_MIN_TIE, extra_low=1)

    assert ai_rerank.should_rerank(rows) is True


def test_후보가_화면_상한_이하이면_동점이어도_부르지_않는다():
    assert MAX_CANDIDATES == 3
    rows = _tied(MAX_CANDIDATES)

    assert len(rows) >= AI_RERANK_MIN_TIE - 1
    assert ai_rerank.should_rerank(rows) is False


def test_후보가_넷이어도_동점이_아니면_부르지_않는다():
    rows = [
        _candidate("가나다전자", score=0.62),
        _candidate("가나다전자서비스", score=0.44),
        _candidate("가나다전자물류", score=0.44),
        _candidate("가나다전자판매", score=0.44),
    ]

    assert ai_rerank.should_rerank(rows) is False


# ── 운영 스위치 ────────────────────────────────


def test_official_candidates_with_small_score_differences_allow_ai_rerank():
    rows = [
        _candidate(f"후보{i}", score=0.70 - i * 0.01, name_match_kind="acronym_token")
        for i in range(5)
    ]
    assert ai_rerank.should_rerank(rows, address_hint="서울") is True


def test_unambiguous_address_and_official_id_do_not_allow_ai_override():
    from dataclasses import replace

    rows = [
        _candidate("일치회사", score=0.9, name_match_kind="acronym_token", address="서울 성동구 왕십리로 83-21"),
        *[_candidate(f"후보{i}", score=0.85, name_match_kind="acronym_token", address="서울 강남구 테헤란로 1") for i in range(4)],
    ]
    assert ai_rerank.should_rerank(rows, address_hint="서울 성동구 왕십리로 83-21") is False
    rows[0] = replace(rows[0], name_match_kind="exact_id")
    assert ai_rerank.should_rerank(rows) is False


def test_스위치는_기본_켜짐이고_0일때만_꺼진다(monkeypatch):
    monkeypatch.delenv(CANDIDATE_AI_RERANK_ENV_NAME, raising=False)
    assert ai_rerank.ai_rerank_enabled() is True

    monkeypatch.setenv(CANDIDATE_AI_RERANK_ENV_NAME, "1")
    assert ai_rerank.ai_rerank_enabled() is True

    monkeypatch.setenv(CANDIDATE_AI_RERANK_ENV_NAME, "0")
    assert ai_rerank.ai_rerank_enabled() is False


def test_알_수_없는_스위치값은_돈이_드는_경로를_열지_않는다(monkeypatch):
    for value in ("yes", "true", "on", ""):
        monkeypatch.setenv(CANDIDATE_AI_RERANK_ENV_NAME, value)
        assert ai_rerank.ai_rerank_enabled() is False


# ── (b) 프롬프트 ────────────────────────────────


def test_프롬프트는_후보_공개필드와_지시를_담고_사용자입력을_JSON으로_이스케이프한다():
    rows = [
        _candidate(
            "가나다전자",
            english_name="GND ENTERTAINMENT",
            stock_code="123456",
            address="서울특별시 가나구 가나대로 1",
        ),
        _candidate("가나다전자서비스", address=""),
    ]

    prompt = ai_rerank.build_rerank_prompt(
        query='가나다"전자',
        address_hint="서울 가나구",
        candidates=rows,
    )

    # 지시문
    assert '{"order":[0,2,1]}' in prompt
    assert "확신이 없으면 order를 빈 목록으로 두세요" in prompt
    assert "설명·추측·회사 소개를 쓰지 마세요" in prompt
    # 후보 공개 필드
    assert '"한글명":"가나다전자"' in prompt
    assert '"영문명":"GND ENTERTAINMENT"' in prompt
    assert '"상장코드":"123456"' in prompt
    assert '"주소":"서울특별시 가나구 가나대로 1"' in prompt
    assert '"i":0' in prompt and '"i":1' in prompt
    # 주소가 없는 후보는 빈 칸을 지어내지 않고 아예 넣지 않는다.
    assert prompt.count('"주소"') == 1
    # 사용자 입력은 그대로 싣되 JSON 문자열로 이스케이프한다.
    assert json.dumps('가나다"전자', ensure_ascii=False) in prompt
    assert '가나다"전자"' not in prompt.replace('\\"', "")
    assert json.dumps("서울 가나구", ensure_ascii=False) in prompt


def test_프롬프트는_상한을_넘는_후보를_보내지_않는다():
    rows = [
        _candidate(f"가나다전자{index}")
        for index in range(AI_RERANK_MAX_CANDIDATES + 4)
    ]

    prompt = ai_rerank.build_rerank_prompt(
        query="가나다전자", address_hint="", candidates=rows
    )

    assert f'"i":{AI_RERANK_MAX_CANDIDATES - 1}' in prompt
    assert f'"i":{AI_RERANK_MAX_CANDIDATES}' not in prompt


# ── (c) 응답 파서 ────────────────────────────────


def test_정상_응답은_그대로_순서가_된다():
    assert ai_rerank.parse_rerank_order('{"order":[2,0,1]}', 3) == (2, 0, 1)


def test_부분_목록과_빈_목록도_받는다():
    assert ai_rerank.parse_rerank_order('{"order":[2]}', 5) == (2,)
    assert ai_rerank.parse_rerank_order('{"order":[]}', 5) == ()


def test_범위밖_중복_비정수_불리언은_응답전체를_버린다():
    assert ai_rerank.parse_rerank_order('{"order":[0,5]}', 3) is None
    assert ai_rerank.parse_rerank_order('{"order":[-1]}', 3) is None
    assert ai_rerank.parse_rerank_order('{"order":[1,1]}', 3) is None
    assert ai_rerank.parse_rerank_order('{"order":["1"]}', 3) is None
    assert ai_rerank.parse_rerank_order('{"order":[1.0]}', 3) is None
    assert ai_rerank.parse_rerank_order('{"order":[true]}', 3) is None
    assert ai_rerank.parse_rerank_order('{"order":[0,1,2,0]}', 3) is None


def test_JSON이_아니거나_order가_없으면_버린다():
    assert ai_rerank.parse_rerank_order("가장 맞는 회사는 가나다전자입니다", 3) is None
    assert ai_rerank.parse_rerank_order("", 3) is None
    assert ai_rerank.parse_rerank_order('{"items":[0]}', 3) is None
    assert ai_rerank.parse_rerank_order('{"order":[0],"why":"설명"}', 3) is None
    assert ai_rerank.parse_rerank_order('{"order":{"0":1}}', 3) is None
    assert ai_rerank.parse_rerank_order("[0,1,2]", 3) is None
    assert ai_rerank.parse_rerank_order('{"order":[0],"order":[1]}', 3) is None
    assert ai_rerank.parse_rerank_order('{"order":NaN}', 3) is None
    assert ai_rerank.parse_rerank_order(None, 3) is None
    assert ai_rerank.parse_rerank_order(b'{"order":[0]}', 3) is None


# ── (d) 순서 적용 ────────────────────────────────


def test_재정렬은_후보를_더하지도_빼지도_않는다():
    rows = _tied(5)

    reordered = ai_rerank.apply_rerank(rows, (3, 1))

    assert [row.candidate_name for row in reordered] == [
        "가나다전자3",
        "가나다전자1",
        "가나다전자0",
        "가나다전자2",
        "가나다전자4",
    ]
    assert sorted(id(row) for row in reordered) == sorted(id(row) for row in rows)
    assert len(reordered) == len(rows)


def test_빈_순서는_원래_순서를_그대로_둔다():
    rows = _tied(5)

    assert ai_rerank.apply_rerank(rows, ()) == rows


# ── (e) 묶음 동작 ────────────────────────────────


def test_ask가_예외를_던져도_원래순서와_failed를_돌려준다():
    rows = _tied(5)

    def 폭발하는_ask(_prompt: str) -> str:
        raise RuntimeError("provider 오류")

    result, status = ai_rerank.rerank_candidates(
        rows, query="가나다전자", address_hint="", ask=폭발하는_ask
    )

    assert status == ai_rerank.RERANK_STATUS_FAILED
    assert result == rows


def test_동점이_없으면_ask를_한번도_부르지_않는다():
    rows = _tied(2, extra_low=3)
    calls: list[str] = []

    result, status = ai_rerank.rerank_candidates(
        rows, query="가나다전자", address_hint="", ask=calls.append
    )

    assert calls == []
    assert status == ai_rerank.RERANK_STATUS_NO_TIE
    assert result == rows


def test_ask가_None이면_원래순서를_그대로_쓴다():
    rows = _tied(5)

    result, status = ai_rerank.rerank_candidates(
        rows, query="가나다전자", address_hint="", ask=None
    )

    assert status == ai_rerank.RERANK_STATUS_NO_TIE
    assert result == rows


def test_이상한_응답은_invalid로_남기고_원래순서를_쓴다():
    rows = _tied(5)

    result, status = ai_rerank.rerank_candidates(
        rows,
        query="가나다전자",
        address_hint="",
        ask=lambda _prompt: "제일 맞는 건 3번입니다",
    )

    assert status == ai_rerank.RERANK_STATUS_INVALID
    assert result == rows


def test_정상_응답은_applied로_순서를_바꾸고_후보수를_지킨다():
    rows = _tied(5)

    result, status = ai_rerank.rerank_candidates(
        rows,
        query="가나다전자",
        address_hint="서울 가나구",
        ask=lambda _prompt: '{"order":[4,2]}',
    )

    assert status == ai_rerank.RERANK_STATUS_APPLIED
    assert [row.candidate_name for row in result[:2]] == ["가나다전자4", "가나다전자2"]
    assert len(result) == len(rows)
    assert {row.candidate_name for row in result} == {
        row.candidate_name for row in rows
    }


def test_상한을_넘는_후보는_보내지도_잃지도_않는다():
    rows = _tied(AI_RERANK_MAX_CANDIDATES + 3)
    seen: list[str] = []

    def 기록하는_ask(prompt: str) -> str:
        seen.append(prompt)
        return '{"order":[1]}'

    result, status = ai_rerank.rerank_candidates(
        rows, query="가나다전자", address_hint="", ask=기록하는_ask
    )

    assert status == ai_rerank.RERANK_STATUS_APPLIED
    assert len(seen) == 1
    assert f'"i":{AI_RERANK_MAX_CANDIDATES}' not in seen[0]
    assert result[0].candidate_name == "가나다전자1"
    assert len(result) == len(rows)
    assert {row.candidate_name for row in result} == {
        row.candidate_name for row in rows
    }
