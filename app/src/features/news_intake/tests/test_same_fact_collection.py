"""수집 경로 전체(본문 검증 → same_event/select → 최종 조각)에서 같은 사실 경계를 본다.

★ 4차 최종 독립 검토 반례 두 건 — 상류에서 지워지면 composer의 날짜 보호에
  닿지도 못한다.
  ① 2025·2026년 기사가 각각 같은 글자로 «지난해 매출 100억원»을 보도(2024·2025년의
     다른 사실)했는데 완전 일치라는 이유로 하나가 지워졌다.
  ② «2025년 제품 갑/을에서 매출 100억원»처럼 제품명이 첫 수치 뒤에 오면 첫 수치 앞
     접두사가 같아 유사도로 합쳐졌다.
  이미 만든 조각이 아니라 실제 collect 경로에서 두 정보가 끝까지 남는지 확인한다.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.features.news_intake.tests.test_collection import POLICY, analyzer, collect, item

_IDENTITY = "가나다전자는 기업용 산업설비 제조 사업을 운영한다."
_RELATIVE_YEAR = "가나다전자는 지난해 매출 100억원을 달성했다."
_AFTER_YEAR_A = "가나다전자는 2025년 제품 갑에서 매출 100억원과 영업이익 10억원을 기록했다."
_AFTER_YEAR_B = "가나다전자는 2025년 제품 을에서 매출 100억원과 영업이익 10억원을 기록했다."
_OTHER_FACT = "가나다전자는 부산에 두 번째 설비 조립 공장을 세웠다."


def _excerpt(text: str, *, section: str = "past_changes", slot: str = "past_changes:change_context",
             topic: str = "business") -> dict[str, str]:
    return {"text": text, "section_id": section, "claim_slot": slot, "claim_kind": "reported_fact",
            "temporal_status": "completed", "topic": topic, "event_key": "매출 보도",
            "event_on": "", "time_evidence": "", "subject": "", "subject_evidence": ""}


def _body(number: int, claim: str) -> str:
    # 기사마다 다른 문장을 하나 둔다 — 본문 «전체»가 같으면 기존 복제 기사 정리
    # (duplicate_article_body) 대상이고, 이 시험은 문장만 같은 다른 기사를 본다.
    return f"{_IDENTITY} {claim} 가나다전자는 설명 자료 {number}건을 냈다."


def _run(claims_by_number: dict[int, str], items, *, policy=POLICY, topics: dict[int, str] | None = None):
    """기사 번호마다 본문 = 회사 소개 + 주장 + 기사 고유 문장, 모델은 주장만 인용한다."""
    def fetch(url: str) -> str:
        number = int(url.rsplit("/", 1)[-1])
        return _body(number, claims_by_number[number])

    def transform(rows, payload):
        for row, article in zip(rows, payload["articles"]):
            number = int(article["url"].rsplit("/", 1)[-1])
            topic = (topics or {}).get(number, row["excerpts"][0]["topic"])
            row["excerpts"] = [_excerpt(claims_by_number[number], topic=topic)]
        return rows

    return collect(items, fetch=fetch, analyze=analyzer(transform), policy=policy)


def _fragments_by_date(result):
    return sorted((fragment.published_on, fragment.text, fragment.url) for fragment in result.fragments)


def test_발행일이_다른_같은_상대연도_보도는_수집_결과에_둘_다_남는다():
    result = _run({1: _RELATIVE_YEAR, 2: _RELATIVE_YEAR},
                  [item(1, date="2026-06-01"), item(2, date="2025-06-01")])
    assert _fragments_by_date(result) == [
        ("2025-06-01", _RELATIVE_YEAR, "https://media.example/article/2"),
        ("2026-06-01", _RELATIVE_YEAR, "https://media.example/article/1"),
    ]
    assert "duplicate_event" not in result.diagnostics["제외"]
    for fragment in result.fragments:  # 원문 위치·글자는 바뀌지 않는다
        body = _body(int(fragment.url.rsplit("/", 1)[-1]), _RELATIVE_YEAR)
        assert body[fragment.span_start:fragment.span_end] == fragment.text


def test_같은_날_같은_상대연도_재보도는_수집에서_하나로_정리한다():
    result = _run({1: _RELATIVE_YEAR, 2: _RELATIVE_YEAR},
                  [item(1, date="2026-06-01"), item(2, date="2026-06-01", host="specialist.example")])
    assert [fragment.text for fragment in result.fragments] == [_RELATIVE_YEAR]
    assert result.diagnostics["제외"]["duplicate_event"] == 1


def test_첫_수치_뒤_제품명이_다른_보도는_수집_결과에_둘_다_남는다():
    result = _run({1: _AFTER_YEAR_A, 2: _AFTER_YEAR_B}, [item(1), item(2)])
    assert {fragment.text for fragment in result.fragments} == {_AFTER_YEAR_A, _AFTER_YEAR_B}
    assert len({fragment.url for fragment in result.fragments}) == 2
    assert "duplicate_event" not in result.diagnostics["제외"]


_OFF_LIST = "가나다전자는 지난 시즌 매출 100억원을 달성했다."


def test_목록_밖_상대_시점도_발행일이_다르면_수집_결과에_둘_다_남는다():
    result = _run({1: _OFF_LIST, 2: _OFF_LIST}, [item(1, date="2026-06-01"), item(2, date="2025-06-01")])
    assert _fragments_by_date(result) == [
        ("2025-06-01", _OFF_LIST, "https://media.example/article/2"),
        ("2026-06-01", _OFF_LIST, "https://media.example/article/1"),
    ]
    assert "duplicate_event" not in result.diagnostics["제외"]
    same_day = _run({1: _OFF_LIST, 2: _OFF_LIST},
                    [item(1, date="2026-06-01"), item(2, date="2026-06-01", host="specialist.example")])
    assert [fragment.text for fragment in same_day.fragments] == [_OFF_LIST]
    assert same_day.diagnostics["제외"]["duplicate_event"] == 1


def _same_body_run(dates: tuple[str, str], *, policy=POLICY, second_claim: str = _RELATIVE_YEAR):
    """두 주소가 본문 «전체»를 똑같이 싣는다. 모델은 1번에서 상대연도, 2번에서 second_claim을 인용."""
    body = f"{_IDENTITY} {_RELATIVE_YEAR} {_OTHER_FACT}"

    def transform(rows, payload):
        for row, article in zip(rows, payload["articles"]):
            claim = _RELATIVE_YEAR if article["url"].endswith("/1") else second_claim
            row["excerpts"] = [_excerpt(claim)]
        return rows

    items = [item(1, date=dates[0]), item(2, date=dates[1], host="specialist.example")]
    return collect(items, fetch=lambda url: body, analyze=analyzer(transform), policy=policy), body


def test_본문_전체가_같아도_발행일이_다르면_분석_전에_지우지_않는다():
    result, body = _same_body_run(("2026-06-01", "2025-06-01"))
    assert _fragments_by_date(result) == [
        ("2025-06-01", _RELATIVE_YEAR, "https://specialist.example/article/2"),
        ("2026-06-01", _RELATIVE_YEAR, "https://media.example/article/1"),
    ]
    assert "duplicate_article_body" not in result.diagnostics["제외"]
    # 날짜별 출처 메타(발행처·발행일·원문 위치)는 기사마다 그대로다.
    assert {(fragment.publisher, fragment.published_on) for fragment in result.fragments} == {
        ("media.example", "2026-06-01"), ("specialist.example", "2025-06-01")}
    for fragment in result.fragments:
        assert body[fragment.span_start:fragment.span_end] == fragment.text


def test_같은_날_같은_본문은_여전히_분석_전에_한_번만_읽는다():
    calls = []
    body = f"{_IDENTITY} {_RELATIVE_YEAR}"
    items = [item(1, date="2026-06-01"), item(2, date="2026-06-01", host="specialist.example")]
    result = collect(items, fetch=lambda url: body, analyze=analyzer(calls=calls))
    assert result.diagnostics["제외"]["duplicate_article_body"] == 1
    assert sum(len(call["articles"]) for call in calls) == 1


def test_다른_날짜_재게시_본문은_충분성을_새_기사로_채우지_않는다():
    policy = replace(POLICY, sufficient_events=2, sufficient_topics=1)
    # 재게시본에서 모델이 다른 문장을 골라도(출력엔 둘 다 남음) 기존처럼 한 기사로 센다.
    republished, _body = _same_body_run(("2026-06-01", "2025-06-01"), policy=policy, second_claim=_OTHER_FACT)
    assert {fragment.text for fragment in republished.fragments} == {_RELATIVE_YEAR, _OTHER_FACT}
    assert republished.diagnostics["완전성"] == "insufficient"
    # 대조: 본문이 다른 두 기사의 다른 사실이면 충분하다.
    distinct = _run({1: _RELATIVE_YEAR, 2: _OTHER_FACT}, [item(1), item(2)], policy=policy)
    assert distinct.diagnostics["완전성"] == "sufficient"


#: 독립 검증 반례 — 같은 날·같은 발행처, 주소 1→2→3. B는 A의 확장(순수 부분 인용).
_PLANT_A = "가나다전자는 부산 지역에 산업설비를 생산하는 새로운 공장을 세웠다."
_PLANT_B = f"{_PLANT_A} 회사는 핵심 장비 생산과 고객 지원을 담당하는 통합 거점을 운영한다."
_SENSOR_C = "가나다전자는 기업용 자동화 설비에 탑재하는 신형 센서 120개를 출시했다."
#: A+C(37+41자)는 들어가고 B+C(76+41자)는 넘치는 글자 예산.
_TIGHT_CHARS = len(_PLANT_A) + len(_SENSOR_C)


def test_실제_출력이_한_기사뿐이면_기존_판정이_충분해도_부족이다():
    policy = replace(POLICY, sufficient_events=2, sufficient_topics=1, max_fragment_chars=_TIGHT_CHARS)
    result = _run({1: _PLANT_A, 2: _PLANT_B, 3: _SENSOR_C}, [item(1), item(2), item(3)], policy=policy)
    # 기존 선택이라면 A+C 두 기사로 충분했지만, 실제 출력은 A를 흡수한 B 한 기사뿐이다.
    assert [(fragment.url, fragment.text) for fragment in result.fragments] == [
        ("https://media.example/article/2", _PLANT_B)]
    assert result.diagnostics["실질사건"] == 1 and result.diagnostics["독립기사"] == 1
    assert result.diagnostics["제외"]["duplicate_event"] == 1
    assert result.diagnostics["제외"]["fragment_budget"] == 1
    assert result.diagnostics["완전성"] == "insufficient"
    assert result.diagnostics["자료부족"] is True


def test_예산이_두_기사를_담으면_같은_반례도_충분하다():
    policy = replace(POLICY, sufficient_events=2, sufficient_topics=1,
                     max_fragment_chars=len(_PLANT_B) + len(_SENSOR_C))
    result = _run({1: _PLANT_A, 2: _PLANT_B, 3: _SENSOR_C}, [item(1), item(2), item(3)], policy=policy)
    assert {fragment.text for fragment in result.fragments} == {_PLANT_B, _SENSOR_C}
    assert result.diagnostics["실질사건"] == 2 and result.diagnostics["독립기사"] == 2
    assert result.diagnostics["완전성"] == "sufficient"


#: 기본 정책(6사건·3주제·글자 12000) 반례용 — 내용·수치가 서로 다른 독립 사실 네 개
#: (인용 최소 길이 GROUNDED_MIN_EXCERPT_CHARS를 넘는 문장).
_INDEPENDENT = {
    3: ("가나다전자는 창고 물류용 운반 제품군 3종을 여러 고객 현장에 적용했다.", "products"),
    4: ("가나다전자는 고객 상담 센터에 쓰이는 응대 설비 40대를 새로 공급했다.", "business"),
    5: ("가나다전자는 동남아시아 판매 확대를 위해 해외 법인 2곳을 세웠다.", "business"),
    6: ("가나다전자는 차세대 제어 기술을 개발하는 신규 연구소 1곳을 열었다.", "products"),
}


def _default_policy_run(first_two: dict[int, str]):
    claims = {**first_two, **{number: text for number, (text, _topic) in _INDEPENDENT.items()}}
    topics = {1: "operations", 2: "operations", **{number: topic for number, (_text, topic) in _INDEPENDENT.items()}}
    return _run(claims, [item(number) for number in claims], topics=topics)


def test_기본_정책에서도_실제_출력이_다섯_기사면_부족이다():
    """기존 선택은 A·B를 따로 세어 6기사지만, 실제 출력은 A를 흡수한 B까지 5기사다."""
    result = _default_policy_run({1: _PLANT_A, 2: _PLANT_B})
    assert _PLANT_A not in {fragment.text for fragment in result.fragments}
    assert len(result.fragments) == 5 and result.diagnostics["실질사건"] == 5
    assert result.diagnostics["독립기사"] == 5
    assert result.diagnostics["제외"]["duplicate_event"] == 1
    assert result.diagnostics["완전성"] == "insufficient"
    assert result.diagnostics["자료부족"] is True


def test_기본_정책의_진짜_독립_여섯_기사는_충분하다():
    result = _default_policy_run({1: _SENSOR_C, 2: _PLANT_B})
    assert len(result.fragments) == 6 and result.diagnostics["독립기사"] == 6
    assert result.diagnostics["완전성"] == "sufficient"


@pytest.mark.parametrize("claim", [
    "가나다전자는 2010년에 설립됐다. 가나다전자의 매출은 전년 대비 20% 증가했다.",
    "2010년에 설립된 가나다전자의 당기 매출은 100억원이다.",
])
def test_문장_안_다른_절의_연도를_빌려_다른_발행일_보도를_합치지_않는다(claim: str):
    result = _run({1: claim, 2: claim}, [item(1, date="2026-06-01"), item(2, date="2025-06-01")])
    assert _fragments_by_date(result) == [
        ("2025-06-01", claim, "https://media.example/article/2"),
        ("2026-06-01", claim, "https://media.example/article/1"),
    ]
    assert "duplicate_event" not in result.diagnostics["제외"]


def test_보존한_가능_중복은_수집_충분성을_새_사건으로_채우지_않는다():
    policy = replace(POLICY, sufficient_events=2, sufficient_topics=1)
    echo = _run({1: _AFTER_YEAR_A, 2: _AFTER_YEAR_B}, [item(1), item(2)], policy=policy)
    assert len(echo.fragments) == 2
    assert echo.diagnostics["완전성"] == "insufficient"
    assert echo.diagnostics["자료부족"] is True

    distinct = _run({1: _AFTER_YEAR_A, 2: _OTHER_FACT}, [item(1), item(2)], policy=policy)
    assert distinct.diagnostics["완전성"] == "sufficient"
