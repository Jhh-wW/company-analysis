"""회사 후보 목록의 가로 항목(row) 화면 계약.

행마다 필수 필드·일치 근거 칩이 보이고, 없는 데이터는 추정 대신 '미확인'으로
표기하며, 후보 데이터가 제공하지 않는 법인/폐업 필터를 만들지 않는지를 지킨다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.budget import logic as budget_logic
from src.features.business_candidate import logic as candidate_logic
from src.features.business_candidate.logic import RawBusinessCandidate
from src.web import job_runtime, main, runtime
from src.web.tests._visible_text import class_count, visible_text

WEB = Path(__file__).parents[1]
TEMPLATE = WEB / "templates" / "company_candidates.html"
STYLE = WEB / "static" / "style.css"


@pytest.fixture(autouse=True)
def _fresh_candidate_state(monkeypatch):
    monkeypatch.setattr(candidate_logic, "_RATE_HISTORY", budget_logic.RateHistory())
    job_runtime._CANDIDATE_ATTEMPTS.clear()
    job_runtime._CANDIDATE_SEARCH_GRANTS.clear()
    yield
    job_runtime._CANDIDATE_ATTEMPTS.clear()
    job_runtime._CANDIDATE_SEARCH_GRANTS.clear()


class RowRenderFakePipeline:
    """선택 전 후보 목록 렌더만 보는 무과금 fixture."""

    business_candidate_provider_costs_money = False

    def __init__(self, raw_candidates):
        self.raw_candidates = list(raw_candidates)

    def search_business_candidates(self, **_kwargs):
        return list(self.raw_candidates)

    def find_company_metered(self, user_input):
        raise AssertionError("후보가 있으면 이름 재조회를 부르면 안 됩니다")

    def find_company_by_ref_metered(self, user_input, candidate_ref):
        raise AssertionError("후보 선택 전에는 DART 재조회가 없어야 합니다")


def _admin_client() -> tuple[TestClient, str]:
    client = TestClient(
        main.app,
        base_url="http://127.0.0.1:8000",
        headers={"Origin": "http://127.0.0.1:8000"},
    )
    session = auth_logic.create_session("admin@example.com", True)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
    return client, auth_logic.csrf_token_for_session(session.token)


def _form(csrf: str, **changes: str) -> dict[str, str]:
    data = {
        "company": "JYP",
        "job": "",
        "region": "서울 강동구",
        "posting_text": "",
        "csrf_token": csrf,
    }
    data.update(changes)
    return data


_RICH_CANDIDATE = RawBusinessCandidate(
    candidate_name="(주)제이와이피엔터테인먼트",
    english_name="JYP Entertainment Corporation",
    address="서울특별시 강동구 강동대로 205",
    homepage="https://www.jype.com/",
    source_label="전자공시(DART) 기업개황",
    source_url="https://opendart.fss.or.kr/",
    provider_name="DART",
    candidate_ref="00258689",
    stock_code="035900",
    modify_date="20221206",
    name_match_kind="exact_name",
    name_similarity=1.0,
)
# 주소·홈페이지·종목코드·갱신일이 없는 후보. 화면은 추정하지 않고 미확인으로 말해야 한다.
_SPARSE_CANDIDATE = RawBusinessCandidate(
    candidate_name="(주)제이와이피",
    provider_name="DART",
    candidate_ref="00535454",
    name_match_kind="acronym_reading",
    name_similarity=1.0,
)


def _render_candidates() -> str:
    client, csrf = _admin_client()
    try:
        response = client.post("/confirm", data=_form(csrf))
    finally:
        client.close()
    assert response.status_code == 200
    return response.text


def test_행마다_필수필드와_선택버튼이_보인다(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "_PIPELINE",
        RowRenderFakePipeline([_RICH_CANDIDATE, _SPARSE_CANDIDATE]),
    )
    body = _render_candidates()
    text = visible_text(body)

    assert class_count(body, "candidate-row") == 2
    assert "총 2건" in text
    assert "(주)제이와이피엔터테인먼트" in text
    assert "JYP Entertainment Corporation" in text
    assert "서울특별시 강동구 강동대로 205" in text
    assert "035900" in text
    assert "2022-12-06" in text
    assert "일치 근거" in text
    assert text.count("이 기업 선택") == 2
    # 선택 form의 서명 hidden field가 행 안에 유지된다.
    assert len(re.findall(r'name="candidate_selection_token"\s+value="[^"]+"', body)) == 2


def test_없는_데이터는_추정하지_않고_미확인으로_표기한다(monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", RowRenderFakePipeline([_SPARSE_CANDIDATE]))
    text = visible_text(_render_candidates())

    assert "주소 미확인" in text
    assert "홈페이지 미확인" in text
    # 종목코드·DART 정보 갱신 컬럼 모두 미확인으로 남는다.
    assert text.count("미확인") >= 4


def test_일치근거_칩은_계산된_근거만_요약하고_상세근거를_보존한다(monkeypatch):
    monkeypatch.setattr(
        runtime,
        "_PIPELINE",
        RowRenderFakePipeline([_RICH_CANDIDATE, _SPARSE_CANDIDATE]),
    )
    body = _render_candidates()
    text = visible_text(body)

    assert "법인명 일치" in text
    assert "주소 일치" in text
    assert "법인명 부분 일치" in text
    assert "주소 불확실" in text
    # 이름·주소 두 칩씩: 확실 2(ok) / 부분 1(part) / 불확실 1(unknown).
    assert class_count(body, "tone-ok") == 2
    assert class_count(body, "tone-part") == 1
    assert class_count(body, "tone-unknown") == 1
    # 문장형 상세 근거는 칩으로 바뀌어도 사라지지 않는다.
    assert "자세한 근거" in text
    assert "입력한 회사명과 DART 정식명칭이 일치합니다" in text


def test_후보데이터에_없는_법인_폐업_필터는_만들지_않는다(monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", RowRenderFakePipeline([_RICH_CANDIDATE]))
    text = visible_text(_render_candidates())

    assert "총 1건" in text
    assert "법인만" not in text
    assert "개인사업자" not in text
    assert "폐업 제외" not in text


def test_하단_재검색_동선은_기존_입력화면을_재사용한다(monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", RowRenderFakePipeline([_RICH_CANDIDATE]))
    body = _render_candidates()
    text = visible_text(body)

    assert "원하는 기업이 없으신가요?" in text
    assert "검색어 수정하기" in text
    assert 'class="candidate-refine-link" href="/"' in body


def test_후보목록은_candidate_page_범위에서_모바일_한열로_무너진다():
    template = TEMPLATE.read_text(encoding="utf-8")
    css = STYLE.read_text(encoding="utf-8")

    assert "{% block bodyclass %}candidate-page{% endblock %}" in template
    assert ".candidate-page .candidate-row {" in css
    assert "@media (max-width: 720px)" in css
    assert ".candidate-page .candidate-row { grid-template-columns: 1fr; }" in css


def _display_candidate(**overrides):
    base = dict(
        candidate_name="(주)제이와이피엔터테인먼트",
        address="서울특별시 강동구 강동대로 205",
        homepage="",
        source_label="",
        source_url="",
        provider_name="DART",
        attributions=(),
        score=0.9,
        evidence=(),
    )
    base.update(overrides)
    return candidate_logic.BusinessCandidate(**base)


def _labels(chips) -> tuple[str, ...]:
    return tuple(chip.label for chip in chips)


def test_칩매핑은_이름과_주소_비교결과만_말한다():
    exact = candidate_logic.candidate_match_chips(
        _display_candidate(name_match_kind="exact_name"),
        query="JYP",
        address_hint="서울 강동구",
    )
    assert _labels(exact) == ("법인명 일치", "주소 일치")
    assert tuple(chip.tone for chip in exact) == ("ok", "ok")

    partial = candidate_logic.candidate_match_chips(
        _display_candidate(name_match_kind="acronym_token"),
        query="JYP",
        address_hint="부산 해운대구",
    )
    assert _labels(partial) == ("법인명 부분 일치", "주소 불일치")
    assert partial[1].tone == "no"

    # 입력 주소가 없으면 일치·불일치를 단정하지 않는다.
    no_hint = candidate_logic.candidate_match_chips(
        _display_candidate(name_match_kind="exact_name"),
        query="JYP",
        address_hint="",
    )
    assert _labels(no_hint)[1] == "주소 불확실"
    assert no_hint[1].tone == "unknown"

    # 이름 근거가 계산되지 않은 후보는 일치라고 말하지 않는다.
    unrelated = candidate_logic.candidate_match_chips(
        _display_candidate(candidate_name="한빛물산", address=""),
        query="JYP",
        address_hint="서울 강동구",
    )
    assert _labels(unrelated) == ("법인명 불확실", "주소 불확실")


def test_칩매핑은_match_kind가_없어도_같은_결정규칙으로_재계산한다():
    # Google Maps 후보는 name_match_kind가 비어 있다. 약어 토큰 규칙을 재사용한다.
    google = candidate_logic.candidate_match_chips(
        _display_candidate(
            candidate_name="JYP 엔터테인먼트",
            provider_name="Google Maps",
            address="서울특별시 강동구 강동대로 205",
        ),
        query="JYP",
        address_hint="서울 강동구",
    )
    assert _labels(google) == ("법인명 부분 일치", "주소 일치")

    same_name = candidate_logic.candidate_match_chips(
        _display_candidate(
            candidate_name="JYP 엔터테인먼트",
            provider_name="Google Maps",
        ),
        query="JYP 엔터테인먼트",
        address_hint="서울 강동구",
    )
    assert same_name[0].label == "법인명 일치"
