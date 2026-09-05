"""뉴스룸 화면 글자 날짜의 프로그램·AI 대조·캐시 계약."""

from __future__ import annotations

import json
from datetime import date

import pytest

from src.features.homepage import logic as homepage_logic
from src.features.homepage.constants import NEWSROOM_DATE_REASON_CODES
from src.features.homepage.newsroom_date import (
    NewsroomDateResult,
    extract_newsroom_date,
    find_program_newsroom_dates,
)


def _list_page(date_text: str = "2026.05.12") -> str:
    return (
        "<html><body>"
        "<header><h1>뉴스룸</h1></header>"
        "<article><h2>첫 번째 소식</h2><p>새 제품을 공개했습니다.</p></article>"
        "<article><h2>두 번째 소식</h2><p>새 공장을 열었습니다.</p></article>"
        f"<footer>하단 게시일 {date_text} 회사 소식 끝</footer>"
        "</body></html>"
    )


@pytest.mark.parametrize(
    ("raw_date", "expected"),
    [
        ("2026.05.12", "2026-05-12"),
        ("2026. 5. 12", "2026-05-12"),
        ("2026-05-12", "2026-05-12"),
        ("2026년 5월 12일", "2026-05-12"),
        ("26.05.12", "2026-05-12"),
    ],
)
def test_제목_옆의_허용된_날짜모양은_프로그램으로_읽는다(
    raw_date: str,
    expected: str,
) -> None:
    raw_html = f"<article><h1>신제품 공개</h1><p>게시일 {raw_date}</p></article>"

    result = extract_newsroom_date(raw_html)

    assert result == NewsroomDateResult(expected, "program", 1, "program_found")


def test_첫_굵은글도_제목_대체로_제한해_읽는다() -> None:
    raw_html = "<article><strong>신제품 공개</strong><span>26.05.12</span></article>"

    assert extract_newsroom_date(raw_html).date == "2026-05-12"


def test_제목범위의_서로다른_날짜는_충돌로_닫는다() -> None:
    raw_html = (
        "<article><h1>신제품 공개</h1>"
        "<p>입력 2026.05.12 / 수정 2026.05.13</p></article>"
    )

    result = extract_newsroom_date(raw_html)

    assert result == NewsroomDateResult("", "", 1, "program_conflict")


def test_목록에서_프로그램날짜가_없고_스위치가_off면_AI를_부르지_않는다() -> None:
    calls: list[str] = []

    result = extract_newsroom_date(
        _list_page(),
        ai_call=lambda prompt: calls.append(prompt) or "{}",
        ai_enabled=False,
    )

    assert result == NewsroomDateResult("", "", 2, "ai_skipped_switch_off")
    assert calls == []


def test_AI날짜와_문맥이_페이지에_그대로_있으면_통과한다() -> None:
    calls: list[str] = []

    def ai_call(prompt: str) -> str:
        calls.append(prompt)
        return json.dumps(
            {
                "date_text": "2026.05.12",
                "context": "하단 게시일 2026.05.12 회사 소식 끝",
            },
            ensure_ascii=False,
        )

    result = extract_newsroom_date(
        _list_page(),
        ai_call=ai_call,
        ai_enabled=True,
        today=date(2026, 9, 6),
    )

    assert result == NewsroomDateResult(
        "2026-05-12", "ai_assisted", 3, "ai_verified"
    )
    assert len(calls) == 1
    assert "입력·등록·게시·작성" in calls[0]


def test_AI날짜가_페이지에_없으면_닫는다() -> None:
    response = json.dumps(
        {
            "date_text": "2026.05.13",
            "context": "하단 게시일 2026.05.13 회사 소식 끝",
        },
        ensure_ascii=False,
    )

    result = extract_newsroom_date(
        _list_page(),
        ai_call=lambda _prompt: response,
        ai_enabled=True,
        today=date(2026, 9, 6),
    )

    assert result.reason_code == "ai_not_in_page"
    assert result.date == ""


def test_AI문맥의_앞뒤가_페이지와_다르면_닫는다() -> None:
    response = json.dumps(
        {
            "date_text": "2026.05.12",
            "context": "다른 앞글자 2026.05.12 다른 뒤글자",
        },
        ensure_ascii=False,
    )

    result = extract_newsroom_date(
        _list_page(),
        ai_call=lambda _prompt: response,
        ai_enabled=True,
        today=date(2026, 9, 6),
    )

    assert result.reason_code == "ai_context_mismatch"
    assert result.date == ""


def test_AI문맥이_날짜글자만_되풀이하면_닫는다() -> None:
    response = json.dumps(
        {"date_text": "2026.05.12", "context": "2026.05.12"},
        ensure_ascii=False,
    )

    result = extract_newsroom_date(
        _list_page(),
        ai_call=lambda _prompt: response,
        ai_enabled=True,
        today=date(2026, 9, 6),
    )

    assert result.reason_code == "ai_context_mismatch"


def test_AI날짜가_오늘보다_미래면_닫는다() -> None:
    response = json.dumps(
        {
            "date_text": "2026.05.12",
            "context": "하단 게시일 2026.05.12 회사 소식 끝",
        },
        ensure_ascii=False,
    )

    result = extract_newsroom_date(
        _list_page(),
        ai_call=lambda _prompt: response,
        ai_enabled=True,
        today=date(2026, 5, 11),
    )

    assert result.reason_code == "ai_future_date"
    assert result.date == ""


def test_AI날짜글자가_허용모양이_아니면_닫는다() -> None:
    response = json.dumps(
        {
            "date_text": "게시일",
            "context": "하단 게시일 2026.05.12 회사 소식 끝",
        },
        ensure_ascii=False,
    )

    result = extract_newsroom_date(
        _list_page(),
        ai_call=lambda _prompt: response,
        ai_enabled=True,
        today=date(2026, 9, 6),
    )

    assert result.reason_code == "ai_unparseable"


def test_캐시적중은_같은_페이지에서_AI를_다시_부르지_않는다() -> None:
    stored: dict[str, dict[str, str]] = {}
    calls = 0

    def ai_call(_prompt: str) -> str:
        nonlocal calls
        calls += 1
        return json.dumps(
            {
                "date_text": "2026.05.12",
                "context": "하단 게시일 2026.05.12 회사 소식 끝",
            },
            ensure_ascii=False,
        )

    def cache_load(key: str) -> dict[str, str] | None:
        return stored.get(key)

    def cache_save(key: str, payload: dict[str, str]) -> None:
        stored[key] = dict(payload)

    first = extract_newsroom_date(
        _list_page(),
        ai_call=ai_call,
        ai_enabled=True,
        cache_load=cache_load,
        cache_save=cache_save,
        today=date(2026, 9, 6),
    )
    second = extract_newsroom_date(
        _list_page(),
        ai_call=ai_call,
        ai_enabled=True,
        cache_load=cache_load,
        cache_save=cache_save,
        today=date(2026, 9, 6),
    )

    assert first.reason_code == "ai_verified"
    assert second == NewsroomDateResult(
        "2026-05-12", "ai_assisted", 4, "cache_hit"
    )
    assert calls == 1


def test_기사한개_페이지는_스위치와_AI를_조회하지_않는다() -> None:
    calls: list[str] = []

    def read_switch() -> bool:
        raise AssertionError("기사 한 개 페이지는 AI 스위치를 읽으면 안 됩니다")

    result = extract_newsroom_date(
        "<article><h1>날짜 없는 단일 기사</h1><p>본문입니다.</p></article>",
        ai_call=lambda prompt: calls.append(prompt) or "{}",
        ai_enabled=read_switch,
    )

    assert result == NewsroomDateResult("", "", 1, "not_found")
    assert calls == []


def test_title과_h1이_같은_단일기사도_AI를_조회하지_않는다() -> None:
    calls: list[str] = []
    raw_html = (
        "<html><head><title>날짜 없는 단일 기사 | 회사</title></head>"
        "<body><article><h1>날짜 없는 단일 기사</h1><p>본문입니다.</p>"
        "</article></body></html>"
    )

    result = extract_newsroom_date(
        raw_html,
        ai_call=lambda prompt: calls.append(prompt) or "{}",
        ai_enabled=True,
    )

    assert result.reason_code == "not_found"
    assert calls == []


def test_machine_readable_날짜가_있으면_새경로를_호출하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_html = (
        '<meta property="article:published_time" content="2026-05-12">'
        "<p>" + ("회사 공식 소식입니다. " * 10) + "</p>"
    )
    fragments: list[dict[str, str]] = []

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("machine-readable 날짜가 새 경로보다 우선이어야 합니다")

    monkeypatch.setattr(homepage_logic, "extract_newsroom_date", fail_if_called)

    homepage_logic._collect_page(
        "https://example.com/newsroom/one",
        raw_html,
        fragments,
        set(),
        0,
    )

    assert fragments[0]["문서일"] == "2026-05-12"
    assert "date_origin" not in fragments[0]


def test_프로그램날짜는_홈페이지조각에_출처와_함께_연결된다() -> None:
    raw_html = (
        "<article><h1>신제품 공개</h1><p>게시일 2026.05.12</p>"
        "<p>" + ("회사 공식 소식입니다. " * 10) + "</p></article>"
    )
    fragments: list[dict[str, str]] = []

    homepage_logic._collect_page(
        "https://example.com/newsroom/one",
        raw_html,
        fragments,
        set(),
        0,
    )

    assert fragments[0]["문서일"] == "2026-05-12"
    assert fragments[0]["date_origin"] == "program"


def test_AI대조날짜도_홈페이지조각에_연결된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_html = _list_page() + ("<p>회사 공식 소식입니다.</p>" * 10)
    fragments: list[dict[str, str]] = []
    response = json.dumps(
        {
            "date_text": "2026.05.12",
            "context": "하단 게시일 2026.05.12 회사 소식 끝",
        },
        ensure_ascii=False,
    )
    monkeypatch.setattr(homepage_logic, "newsroom_date_ai_enabled", lambda: True)

    homepage_logic._collect_page(
        "https://example.com/newsroom/list",
        raw_html,
        fragments,
        set(),
        0,
        newsroom_date_ai_call=lambda _prompt: response,
    )

    assert fragments[0]["문서일"] == "2026-05-12"
    assert fragments[0]["date_origin"] == "ai_assisted"


def test_페이지제목이_따로있어도_목록항목의_첫굵은글을_제목으로_읽는다() -> None:
    raw_html = (
        "<header><h1>뉴스룸</h1></header><ul>"
        "<li><a><strong>첫 소식</strong></a><span>2026.05.11</span></li>"
        "<li><a><strong>둘째 소식</strong></a><span>2026.05.12</span></li>"
        "</ul>"
    )

    assert find_program_newsroom_dates(raw_html) == (
        "2026-05-11",
        "2026-05-12",
    )


def test_인지형_목록_20건의_제목범위_날짜를_프로그램이_10건이상_찾는다() -> None:
    cards = "".join(
        f"<article><h3>보도자료 {index}</h3><span>게시일 2026.05.{index:02d}</span></article>"
        for index in range(1, 21)
    )
    raw_html = f"<html><body>{cards}</body></html>"

    program_dates = find_program_newsroom_dates(raw_html)
    response = json.dumps(
        {
            "date_text": "2026.05.20",
            "context": "보도자료 20 게시일 2026.05.20",
        },
        ensure_ascii=False,
    )
    combined = extract_newsroom_date(
        raw_html,
        ai_call=lambda _prompt: response,
        ai_enabled=True,
        today=date(2026, 9, 6),
    )

    assert len(program_dates) >= 10
    assert len(program_dates) >= 18
    assert combined.reason_code == "ai_verified"


def test_reason_code는_성공과실패를_모두_닫힌목록으로_제한한다() -> None:
    assert NEWSROOM_DATE_REASON_CODES == {
        "program_found",
        "ai_verified",
        "cache_hit",
        "program_conflict",
        "ai_skipped_switch_off",
        "ai_not_in_page",
        "ai_context_mismatch",
        "ai_future_date",
        "ai_unparseable",
        "not_found",
    }
    with pytest.raises(ValueError):
        NewsroomDateResult("", "", 1, "임의 사유")
