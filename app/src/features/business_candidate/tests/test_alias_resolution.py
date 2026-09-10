"""별명 → 정식 법인명 번역 단계의 발동 조건과 엄격 응답 검증 시험."""

import json

import pytest

from src.features.business_candidate import alias_resolution
from src.features.business_candidate.constants import (
    AI_ALIAS_MAX_NAMES,
    ALIAS_RESPONSE_KEY,
    ALIAS_STRONG_MATCH_KINDS,
    DART_ALIAS_SOURCE_LABEL,
    MAX_NAME_CHARS,
    MAX_SOURCE_LABEL_CHARS,
)
from src.features.business_candidate.dart_identity import MATCH_KIND_PRIORITY
from src.features.pipeline.candidate_profile_constants import (
    DART_PROFILE_EXACT_MATCH_KINDS,
)


def 응답(names) -> str:
    return json.dumps({ALIAS_RESPONSE_KEY: names}, ensure_ascii=False)


# ── 강한 종류 목록이 다른 두 곳과 어긋나지 않는지 ──────────────────


def test_강한_종류는_matcher가_아는_이름이고_보강_우선순위와_같다():
    """세 곳이 «강하다»를 다르게 정의하면 번역이 엉뚱할 때 돈다.

    한쪽만 이름을 바꾸거나 종류를 더하면 여기서 먼저 깨져 사람이 판단하게 된다.
    """

    assert set(ALIAS_STRONG_MATCH_KINDS) <= set(MATCH_KIND_PRIORITY)
    assert set(ALIAS_STRONG_MATCH_KINDS) == set(DART_PROFILE_EXACT_MATCH_KINDS)


def test_AI_출처_표시는_화면_상한_안이고_결정적_표시와_다르다():
    assert len(DART_ALIAS_SOURCE_LABEL) <= MAX_SOURCE_LABEL_CHARS
    assert DART_ALIAS_SOURCE_LABEL != "전자공시(DART) 기업개황"


# ── 발동 조건 ────────────────────────────────────────────────


@pytest.mark.parametrize("kind", sorted(ALIAS_STRONG_MATCH_KINDS))
def test_강한_후보가_있으면_묻지_않는다(kind):
    assert alias_resolution.should_ask_alias([kind, "token"], query="가나다") is False


def test_후보가_아예_없으면_영문_질의도_묻는다():
    assert alias_resolution.should_ask_alias([], query="ZZQ") is True
    assert alias_resolution.should_ask_alias([], query="가나다") is True


def test_약한_후보만_있고_영문_질의면_약어_경로에_맡긴다():
    assert alias_resolution.should_ask_alias(["acronym_token"], query="SM") is False
    assert alias_resolution.should_ask_alias(["token", "trigram"], query="YG") is False


def test_약한_후보만_있고_한글이_섞이면_묻는다():
    assert alias_resolution.should_ask_alias(["trigram"], query="배민") is True
    assert alias_resolution.should_ask_alias(["token"], query="현대차") is True


# ── 응답 파싱·검증 ────────────────────────────────────────────


def test_정상_응답은_순서를_지켜_돌려준다():
    assert alias_resolution.parse_alias_names(응답(["우아한형제들", "우아한청년들"])) == (
        "우아한형제들",
        "우아한청년들",
    )


def test_빈_목록은_유효하며_빈_튜플이다():
    assert alias_resolution.parse_alias_names(응답([])) == ()


def test_공백은_정리하고_같은_이름은_한_번만_쓴다():
    parsed = alias_resolution.parse_alias_names(
        응답(["  비바  리퍼블리카 ", "비바 리퍼블리카", "BIVA"])
    )
    assert parsed == ("비바 리퍼블리카", "BIVA")


def test_대소문자만_다른_같은_이름도_한_번만_쓴다():
    assert alias_resolution.parse_alias_names(응답(["Naver", "NAVER"])) == ("Naver",)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "설명을 덧붙였습니다 " + 응답(["우아한형제들"]),
        "[]",
        '{"names":["우아한형제들"]}',
        json.dumps({ALIAS_RESPONSE_KEY: ["가"], "설명": "덤"}, ensure_ascii=False),
        응답("우아한형제들"),
        응답([["우아한형제들"]]),
        응답([123]),
        응답([True]),
        응답([None]),
        응답([""]),
        응답(["   "]),
        '{"정식명":["가"],"정식명":["나"]}',
        '{"정식명":NaN}',
    ],
)
def test_계약을_벗어난_응답은_전부_거절한다(text):
    assert alias_resolution.parse_alias_names(text) is None


def test_문자열이_아닌_입력도_거절한다():
    assert alias_resolution.parse_alias_names(None) is None
    assert alias_resolution.parse_alias_names({ALIAS_RESPONSE_KEY: ["가"]}) is None


def test_상한을_넘는_이름_수는_응답_전체를_버린다():
    가능 = 응답([f"회사{index}" for index in range(AI_ALIAS_MAX_NAMES)])
    초과 = 응답([f"회사{index}" for index in range(AI_ALIAS_MAX_NAMES + 1)])
    assert len(alias_resolution.parse_alias_names(가능)) == AI_ALIAS_MAX_NAMES
    assert alias_resolution.parse_alias_names(초과) is None


def test_이름_길이_상한을_넘으면_거절한다():
    assert alias_resolution.parse_alias_names(응답(["가" * MAX_NAME_CHARS])) == (
        "가" * MAX_NAME_CHARS,
    )
    assert alias_resolution.parse_alias_names(응답(["가" * (MAX_NAME_CHARS + 1)])) is None


@pytest.mark.parametrize(
    "name",
    [
        "https://example.com",
        "www.example.com",
        "우아한형제들 baemin.com",
        "javascript://x",
    ],
)
def test_URL이_섞인_이름은_거절한다(name):
    assert alias_resolution.parse_alias_names(응답([name])) is None


@pytest.mark.parametrize("name", ["우아한\x00형제들", "우아한\n형제들", "가​나"])
def test_제어문자가_섞인_이름은_거절한다(name):
    assert alias_resolution.parse_alias_names(응답([name])) is None


# ── 프롬프트 ─────────────────────────────────────────────────


def test_프롬프트에는_사용자_입력과_형식만_담는다():
    prompt = alias_resolution.build_alias_prompt(query="배민", address_hint="서울 송파구")
    assert '"배민"' in prompt
    assert '"서울 송파구"' in prompt
    assert ALIAS_RESPONSE_KEY in prompt
    # 회사 예시를 박으면 특정 기업에 치우친 사전이 된다.
    assert "우아한형제들" not in prompt


def test_프롬프트는_너무_긴_입력을_잘라_담는다():
    prompt = alias_resolution.build_alias_prompt(
        query="가" * (MAX_NAME_CHARS + 50), address_hint="나" * 5000
    )
    assert "가" * (MAX_NAME_CHARS + 1) not in prompt
    assert "나" * 5000 not in prompt


# ── 한 번만 묻는다 ────────────────────────────────────────────


class 각본:
    def __init__(self, response):
        self._response = response
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if isinstance(self._response, BaseException):
            raise self._response
        return self._response


def 물어본다(response, *, kinds=(), query="배민"):
    ask = 각본(response)
    names, status = alias_resolution.alias_names_from_ask(
        query=query, address_hint="서울", ask=ask, match_kinds=kinds
    )
    return ask, names, status


def test_한_번의_검색에서_AI는_한_번만_불린다():
    ask, names, status = 물어본다(응답(["우아한형제들"]))
    assert len(ask.prompts) == 1
    assert names == ("우아한형제들",)
    assert status == alias_resolution.ALIAS_STATUS_APPLIED


def test_ask가_없으면_부르지도_않고_필요없음이다():
    names, status = alias_resolution.alias_names_from_ask(
        query="배민", address_hint="", ask=None, match_kinds=()
    )
    assert names == ()
    assert status == alias_resolution.ALIAS_STATUS_NOT_NEEDED


def test_강한_후보가_있으면_호출_0회다():
    ask, names, status = 물어본다(응답(["우아한형제들"]), kinds=("exact_name",))
    assert ask.prompts == []
    assert (names, status) == ((), alias_resolution.ALIAS_STATUS_NOT_NEEDED)


def test_잘못된_응답은_결정적_결과를_지키는_사유로_끝난다():
    _ask, names, status = 물어본다("설명입니다")
    assert (names, status) == ((), alias_resolution.ALIAS_STATUS_INVALID_RESPONSE)


def test_빈_목록은_못찾음이다():
    _ask, names, status = 물어본다(응답([]))
    assert (names, status) == ((), alias_resolution.ALIAS_STATUS_NO_MATCH)


def test_예외는_삼키고_실패로_남긴다():
    _ask, names, status = 물어본다(RuntimeError("provider down"))
    assert (names, status) == ((), alias_resolution.ALIAS_STATUS_FAILED)


def test_취소는_삼키지_않는다():
    ask = 각본(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        alias_resolution.alias_names_from_ask(
            query="배민", address_hint="", ask=ask, match_kinds=()
        )


# ── 운영 스위치 ──────────────────────────────────────────────


def test_스위치_기본값은_켜짐이고_이상한_값은_전부_꺼짐이다(monkeypatch):
    monkeypatch.delenv("CANDIDATE_AI_ALIAS", raising=False)
    assert alias_resolution.ai_alias_enabled() is True
    monkeypatch.setenv("CANDIDATE_AI_ALIAS", "1")
    assert alias_resolution.ai_alias_enabled() is True
    for 값 in ("0", "", "true", "yes", "1 1"):
        monkeypatch.setenv("CANDIDATE_AI_ALIAS", 값)
        assert alias_resolution.ai_alias_enabled() is False


def test_모든_사유_코드는_관측_허용_목록에_들어_있다():
    codes = {
        alias_resolution.ALIAS_STATUS_APPLIED,
        alias_resolution.ALIAS_STATUS_NOT_NEEDED,
        alias_resolution.ALIAS_STATUS_INVALID_RESPONSE,
        alias_resolution.ALIAS_STATUS_FAILED,
        alias_resolution.ALIAS_STATUS_SKIPPED_BUDGET,
        alias_resolution.ALIAS_STATUS_NO_MATCH,
    }
    assert codes == set(alias_resolution.ALIAS_STATUS_VALUES)
    assert len(codes) == 6
