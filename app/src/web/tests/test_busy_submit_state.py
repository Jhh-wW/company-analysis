"""오래 걸리는 제출 폼에 「처리 중」 표시가 붙는지 검증한다.

사용자가 DART 조회·외부 조사처럼 오래 걸리는 제출 버튼을 두 번 눌러
중복 제출하는 사고를 막으려고 ``_busy_submit_script.html`` 공용 부분
템플릿을 만들고(``data-busy-submit`` 속성이 붙은 폼만 대상), 그 스크립트를
쓰는 4개 화면(input·not_found·company_candidates·confirm)의
``{% block scripts %}`` 에서만 include 한다. 이 시험은 다음을 확인한다.

1. 지켜야 할 폼마다 ``data-busy-submit``·``data-busy-label`` 이 실제로
   그 폼의 여는 태그 «안»에 붙어 있는지 (다른 자리에 붙으면 적용되지 않는다).
2. 옆에 있는 다른 폼(같은 화면의 다른 버튼)에는 번지지 않았는지.
3. 그 속성을 읽어 버튼을 잠그는 공용 스크립트가 부분 템플릿에 있고,
   대상 화면(첫 화면)의 실제 응답 HTML에는 실리지만 base.html 자체나
   관리자 화면에는 없는지.
   ★ 처음에는 이 스크립트를 base.html에 직접 넣었다가, 스크립트 안의
   ``button.disabled`` 문자열이 "관리자 화면에는 disabled 가 없어야 한다"는
   기존 회귀시험(test_admin_information_architecture.py)과 실측으로
   충돌해 되돌렸다 — 그 회귀가 다시 들어오지 않는지도 여기서 지킨다.
4. 서버가 이미 잠가 그리는 버튼(평가 미리보기 잠김)은 스크립트가 건드리지
   않도록 짜여 있는지.
5. ``company_candidates.html``·``confirm.html`` 은 실제로 두 단계(후보
   검색→선택, 확인 카드)를 거쳐 «렌더된 응답»에서 data-busy-submit·라벨을
   단정한다(템플릿 소스 문자열이 아니라). ``not_found.html`` 은 실패
   사유별 분기가 많아 그 조합을 재현하는 비용이 이 화면 하나의 배선 확인
   가치보다 커서, 템플릿 소스 단정으로 남긴다(대신 회사 후보 화면과
   확인 화면 두 곳을 실제 렌더로 확인해 최소 1개 화면 요건을 넘긴다).
6. 잠금이 «그 폼만»이 아니라 «이 화면의 data-busy-submit 폼 전체»인지 —
   company_candidates.html은 후보 수만큼 폼이 반복되고, confirm.html은
   「맞습니다」·「아닙니다」가 서로 다른 폼이면서 서버의 1회용 토큰
   (candidate_attempt_token·paid_attempt_token)을 공유한다. 한 폼만 잠그면
   다른 폼을 눌러 그 토큰을 먼저 써버리는 사고를 못 막는다. 이 동작은
   브라우저 클릭 시뮬레이션 없이는 직접 관측할 수 없어, 스크립트 소스가
   문서 전체 셀렉터로 잠그고 ``lockOnly``가 실제로 쓰이는지로 단정한다.

★ 확인 못 함: 실제로 버튼을 클릭했을 때 글자가 바뀌고 다시 눌리지 않는지,
  두 폼을 실제로 연달아 눌렀을 때 두 번째가 잠기는지, bfcache 뒤로가기
  복구·스크린리더 알림이 눈(귀)으로 보이는지는 브라우저가 있어야 확인할
  수 있어 이 시험 범위 밖이다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.core.constants import PIPELINE_ENV, PIPELINE_REAL
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.business_candidate import logic as candidate_logic
from src.features.business_candidate.logic import RawBusinessCandidate
from src.features.budget import logic as budget_logic
from src.features.pipeline.port import CompanyCard, CompanyLookupResult
from src.web import evaluation_mode, job_runtime, runtime
from src.web.main import app


WEB = Path(__file__).parents[1]
TEMPLATES = WEB / "templates"
STYLE = WEB / "static" / "style.css"
BASE = TEMPLATES / "base.html"
INPUT = TEMPLATES / "input.html"
NOT_FOUND = TEMPLATES / "not_found.html"
COMPANY_CANDIDATES = TEMPLATES / "company_candidates.html"
CONFIRM = TEMPLATES / "confirm.html"
SCRIPT_PARTIAL = TEMPLATES / "_busy_submit_script.html"
_BUSY_SCRIPT_INCLUDE = '{% include "_busy_submit_script.html" %}'


@pytest.fixture(autouse=True)
def _fresh_candidate_state(monkeypatch):
    """후보 검색 관련 전역 상태를 시험마다 새로 시작한다.

    ``job_runtime._CANDIDATE_ATTEMPTS``·``_CANDIDATE_SEARCH_GRANTS`` 는
    프로세스 전역이라, 같은 pytest 실행 안의 다른 시험 파일이 남긴 값과
    섞이면 candidate_attempt_token 대조가 엉뚱하게 실패한다
    (test_candidate_list_design.py의 같은 이름 fixture와 같은 이유).
    """
    monkeypatch.setattr(candidate_logic, "_RATE_HISTORY", budget_logic.RateHistory())
    job_runtime._CANDIDATE_ATTEMPTS.clear()
    job_runtime._CANDIDATE_SEARCH_GRANTS.clear()
    yield
    job_runtime._CANDIDATE_ATTEMPTS.clear()
    job_runtime._CANDIDATE_SEARCH_GRANTS.clear()


def _여는_태그(html: str, tag: str, *, id_: str) -> str:
    """특정 id를 가진 태그의 «여는 태그» 전체(그 요소 자신의 속성들)만 뽑는다.

    ★ 왜 태그 하나만 뽑나 — ``data-busy-submit`` 이 그 폼이 아니라 응답
      어딘가에 문자열로 존재하기만 해도 통과하는 시험은 「엉뚱한 곳에
      붙여도 초록불」이라는 구멍을 놓친다. 여는 태그 범위로 좁혀야 진짜
      그 요소에 붙었는지를 본다. ``[^>]`` 는 개행도 포함하므로 속성이
      여러 줄에 걸쳐 있어도 잡는다.
    """
    match = re.search(rf'<{tag}[^>]*id="{id_}"[^>]*>', html)
    assert match is not None, f'<{tag} id="{id_}"> 를 찾지 못했습니다'
    return match.group(0)


def _hidden(body: str, name: str) -> str:
    found = re.search(rf'name="{re.escape(name)}"\s+value="([^"]*)"', body)
    assert found is not None, (name, body[:500])
    return found.group(1)


def _admin_client() -> tuple[TestClient, str]:
    client = TestClient(
        app,
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


class _FakeRealPipeline:
    """실제 파이프라인 대신 — 이 시험은 첫 화면 렌더링만 본다."""


def test_analysisForm에_busy_속성이_붙는다() -> None:
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    폼 = _여는_태그(response.text, "form", id_="analysisForm")
    assert "data-busy-submit" in 폼
    assert 'data-busy-label="회사를 찾는 중…"' in 폼


def test_공용_busy_스크립트_부분템플릿의_내용이_맞다() -> None:
    script_source = SCRIPT_PARTIAL.read_text(encoding="utf-8")

    assert "data-busy-submit" in script_source
    assert "aria-busy" in script_source
    assert "data-busy-locked" in script_source
    # 서버가 이미 disabled로 그린 버튼(예: 평가 미리보기 잠김)은 건드리지
    # 않는다는 안전장치 — markBusy·lockOnly 두 곳 모두에 있어야 한다.
    assert script_source.count("if (button.disabled) return;") == 2
    # 뒤로 가기(bfcache) 복구.
    assert "pageshow" in script_source
    assert "event.persisted" in script_source


def test_busy_스크립트는_이_화면의_data_busy_submit_폼_전체를_잠근다() -> None:
    """항목 6 — 잠금 범위가 «그 폼»이 아니라 «문서 전체»인지 소스로 단정한다.

    브라우저 클릭 시뮬레이션 없이는 실제 잠금 동작을 관측할 수 없어,
    (a) 문서 전체를 훑는 셀렉터가 있고 (b) 제출 핸들러와 뒤로가기 복구
    둘 다 그 셀렉터로 얻은 버튼 전체를 순회하며 (c) 안 눌린 버튼에는
    ``lockOnly``가 실제로 호출되는지로 대신 확인한다.
    """
    script_source = SCRIPT_PARTIAL.read_text(encoding="utf-8")

    assert "BUSY_SUBMIT_SELECTOR" in script_source
    assert "document.querySelectorAll(BUSY_SUBMIT_SELECTOR)" in script_source
    # 제출 핸들러(잠금)·pageshow(복구) 둘 다 문서 전체 버튼을 순회한다.
    assert script_source.count("allBusySubmitButtons().forEach") == 2
    # lockOnly는 정의(`function lockOnly(button) {`)뿐 아니라 실제 호출부
    # (`lockOnly(button);`)가 있어야 «쓰인다»고 말할 수 있다.
    assert "function lockOnly(button) {" in script_source
    assert script_source.count("lockOnly(button);") == 1
    # 다른 스크립트가 이미 막은 전송에는 손대지 않는다(항목 4).
    assert "event.defaultPrevented" in script_source


def test_busy_스크립트는_스크린리더에_처리중_상태를_알린다() -> None:
    """항목 5 — 접근성. 잠그는 순간 라벨이 시각적으로만 바뀌면 스크린리더
    사용자는 아무것도 못 듣는다. 숨은 role=status 영역에 라벨을 띄운다."""
    script_source = SCRIPT_PARTIAL.read_text(encoding="utf-8")

    assert 'id="busySubmitStatus"' in script_source
    assert 'role="status"' in script_source
    assert 'aria-live="polite"' in script_source
    # 새 CSS 클래스를 만들지 않고 이미 있는 sr-only를 재사용한다.
    assert 'class="sr-only"' in script_source
    style_source = STYLE.read_text(encoding="utf-8")
    assert style_source.count(".sr-only {") == 1
    # 잠글 때 라벨을 채우고, 뒤로가기 복구 때 비운다.
    assert "announceBusy(busyLabel);" in script_source
    assert "announceBusy('');" in script_source


def test_대상_화면_4곳만_busy_스크립트를_include한다() -> None:
    # base.html 자체에는 스크립트를 직접 넣지 않는다 — 모든 화면에 새면
    # 관리자 화면 회귀시험과 부딪힌다(위 모듈 docstring 참고).
    base_source = BASE.read_text(encoding="utf-8")
    assert "data-busy-locked" not in base_source
    assert _BUSY_SCRIPT_INCLUDE not in base_source

    for 템플릿 in (INPUT, NOT_FOUND, COMPANY_CANDIDATES, CONFIRM):
        source = 템플릿.read_text(encoding="utf-8")
        assert _BUSY_SCRIPT_INCLUDE in source, f"{템플릿.name} 에 busy 스크립트 include가 없다"


def test_busy_스크립트는_첫화면에는_실리고_관리자_화면에는_안_실린다(monkeypatch) -> None:
    from src.features.pipeline.demo import DemoPipeline

    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())

    with TestClient(app) as client:
        home = client.get("/")
        session = auth_logic.create_session("admin@example.com", True)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        admin_home = client.get("/admin")

    assert home.status_code == 200
    assert "data-busy-locked" in home.text

    assert admin_home.status_code == 200
    # ★ 이 단정이 이 시험 파일의 핵심이다 — busy 스크립트를 base.html에
    # 바로 넣었을 때 관리자 화면에도 새어 나가 "disabled 없어야 한다"는
    # 기존 회귀시험을 깼다. 그 사고가 다시 들어오면 여기서 먼저 잡힌다.
    assert "data-busy-locked" not in admin_home.text
    assert "data-busy-submit" not in admin_home.text


def test_not_found_다시찾기_폼에만_busy_속성이_있다() -> None:
    """not_found.html은 실패 사유별 분기가 많아(데모/기술지연/거절/상한 등)
    그 조합을 실제로 재현하는 비용이 크다 — 템플릿 소스 단정으로 남긴다.
    company_candidates.html·confirm.html은 아래에서 실제 렌더로 확인한다.
    """
    html = NOT_FOUND.read_text(encoding="utf-8")
    # 이 화면엔 /confirm 으로 가는 폼이 여럿(Google Maps 후보 검색·데모 칩)
    # 있다. 그중 «다시 찾기» 폼 하나에만 붙었는지 개수로 못 박는다.
    assert html.count("data-busy-submit") == 1

    재시도_폼 = re.search(
        r'<form method="post" action="/confirm" style="margin-top:16px"[^>]*>',
        html,
    )
    assert 재시도_폼 is not None
    assert "data-busy-submit" in 재시도_폼.group(0)
    assert 'data-busy-label="다시 찾는 중…"' in 재시도_폼.group(0)

    구글맵_폼 = re.search(
        r'<form method="post" action="/confirm" style="margin-top:12px"[^>]*>',
        html,
    )
    assert 구글맵_폼 is not None
    assert "data-busy-submit" not in 구글맵_폼.group(0)


class _CandidateRowFakePipeline:
    """company_candidates.html을 실제로 렌더만 하는 무과금 가짜.

    test_candidate_list_design.py의 ``RowRenderFakePipeline``과 같은 모양
    — 이 파일 안에서 자기완결로 두려고 그대로 다시 쓴다(교차 시험파일
    import 관례가 이 저장소에 없다).
    """

    business_candidate_provider_costs_money = False

    def __init__(self, raw_candidates):
        self.raw_candidates = list(raw_candidates)

    def search_business_candidates(self, **_kwargs):
        return list(self.raw_candidates)

    def find_company_metered(self, user_input):
        raise AssertionError("후보가 있으면 이름 재조회를 부르면 안 됩니다")

    def find_company_by_ref_metered(self, user_input, candidate_ref):
        raise AssertionError("후보 선택 전에는 DART 재조회가 없어야 합니다")


_후보_A = RawBusinessCandidate(
    candidate_name="(주)제이와이피엔터테인먼트",
    address="서울특별시 강동구 강동대로 205",
    provider_name="DART",
    candidate_ref="00258689",
    name_match_kind="exact_name",
    name_similarity=1.0,
)
_후보_B = RawBusinessCandidate(
    candidate_name="(주)제이와이피",
    provider_name="DART",
    candidate_ref="00535454",
    name_match_kind="acronym_reading",
    name_similarity=1.0,
)


def test_company_candidates_모든_후보_폼에_busy_속성이_실제로_렌더된다(
    monkeypatch,
) -> None:
    """후보 2건을 실제로 렌더해, 폼 2개 모두 data-busy-submit 대상인지 본다.

    항목 6(문서 전체 잠금)이 지킬 실제 대상이 «폼 여러 개»라는 전제를
    실측으로 못 박는다.
    """
    monkeypatch.setattr(
        runtime, "_PIPELINE", _CandidateRowFakePipeline([_후보_A, _후보_B])
    )
    client, csrf = _admin_client()
    try:
        response = client.post("/confirm", data=_form(csrf))
    finally:
        client.close()

    assert response.status_code == 200
    assert "확인할 회사 후보입니다" in response.text

    폼들 = re.findall(r'<form method="post" action="/confirm"[^>]*>', response.text)
    assert len(폼들) == 2
    for 폼 in 폼들:
        assert "data-busy-submit" in 폼
        assert 'data-busy-label="확인 중…"' in 폼

    # 스크립트 include가 실제로 이 응답에 실려 왔는지(소스가 아니라 응답).
    assert "data-busy-locked" in response.text
    assert "busySubmitStatus" in response.text


class _SingleCandidatePipeline:
    """DART 후보 1건 → 선택 → confirm.html까지 가는 최소 가짜.

    test_company_candidate_flow.py의
    ``test_DART_local_후보는_사람이_선택해야만_DART를_다시_부르고_원입력을_보존한다``
    와 같은 2단계 흐름(후보 검색 → 선택)을 최소화해 그대로 재현한다.
    """

    business_candidate_provider_costs_money = False

    def __init__(self) -> None:
        self.lookup_refs: list[str] = []

    def search_business_candidates(self, **_kwargs):
        return [
            RawBusinessCandidate(
                candidate_name="(주)제이와이피엔터테인먼트",
                address="서울특별시 강동구 강동대로 205",
                provider_name="DART",
                candidate_ref="00258689",
                name_match_kind="exact_name",
                name_similarity=1.0,
            )
        ]

    def find_company_by_ref_metered(self, user_input, candidate_ref):
        self.lookup_refs.append(candidate_ref)
        return CompanyLookupResult(
            card=CompanyCard(
                legal_name="(주)제이와이피엔터테인먼트",
                typed_name=user_input.company,
                address="서울특별시 강동구 강동대로 205",
                ceo="정욱",
                founded="19970425",
                ref=candidate_ref,
            ),
            model="fake-dart-ref",
        )

    def find_company_metered(self, user_input):
        raise AssertionError("후보가 있으면 이름 재조회를 부르면 안 됩니다")


def test_confirm_런폼과_아니오폼_모두_busy_속성이_실제로_렌더된다(monkeypatch) -> None:
    """confirm.html을 실제 두 단계로 렌더해 「맞습니다」·「아닙니다」 두 폼
    모두 data-busy-submit 대상인지 본다(항목 2) — /reject도 같은
    paid_attempt_token을 버리므로(analysis.py의 _abandon_confirmation_attempt
    호출부) 「맞습니다」 대기 중 「아닙니다」를 누르는 경로도 잠가야 한다.
    """
    monkeypatch.setattr(runtime, "_PIPELINE", _SingleCandidatePipeline())
    client, csrf = _admin_client()
    try:
        candidates = client.post("/confirm", data=_form(csrf))
        assert candidates.status_code == 200
        assert "확인할 회사 후보입니다" in candidates.text

        confirmed = client.post(
            "/confirm",
            data=_form(
                csrf,
                candidate_resolution_confirmed="yes",
                candidate_attempt_token=_hidden(
                    candidates.text, "candidate_attempt_token"
                ),
                candidate_selection_token=_hidden(
                    candidates.text, "candidate_selection_token"
                ),
                candidate_index=_hidden(candidates.text, "candidate_index"),
                candidate_name=_hidden(candidates.text, "candidate_name"),
                candidate_provider=_hidden(candidates.text, "candidate_provider"),
                candidate_ref=_hidden(candidates.text, "candidate_ref"),
            ),
        )
    finally:
        client.close()

    assert confirmed.status_code == 200
    assert "이 회사가 맞나요?" in confirmed.text

    run_폼 = _여는_태그(confirmed.text, "form", id_="runForm")
    assert "data-busy-submit" in run_폼
    assert 'data-busy-label="보고서 준비 중…"' in run_폼

    # 「아닙니다」 폼은 id가 없다 — action="/reject" 앞에서 가장 가까운
    # <form 여는 태그를 잘라내 그 요소만 본다.
    action_at = confirmed.text.index('action="/reject"')
    form_open_at = confirmed.text.rindex("<form", 0, action_at)
    reject_폼 = confirmed.text[form_open_at : confirmed.text.index(">", action_at) + 1]
    assert "data-busy-submit" in reject_폼
    assert 'data-busy-label="잠시만요…"' in reject_폼

    assert "data-busy-locked" in confirmed.text
    assert "busySubmitStatus" in confirmed.text


def _평가_미리보기_잠금_환경(monkeypatch) -> None:
    """유료 공급자 없이 평가 모드를 켠다.

    이 조합에서 ``confirmSubmitButton`` 은 서버 렌더링 단계에서부터
    ``disabled`` 로 그려진다(input.html의
    ``{% if evaluation_mode and not evaluation_paid_providers %}disabled{% endif %}``).
    """
    monkeypatch.setenv(evaluation_mode.ENV_MODE, "1")
    monkeypatch.setenv(evaluation_mode.ENV_PAID_PROVIDERS, "0")
    monkeypatch.setenv(PIPELINE_ENV, PIPELINE_REAL)
    monkeypatch.setenv(evaluation_mode.ENV_DISABLE_ENGINE_DOTENV, "1")
    monkeypatch.setenv(evaluation_mode.ENV_PER_RUN_CAP_KRW, "2000")
    monkeypatch.setenv(evaluation_mode.ENV_DAILY_CAP_KRW, "2200")
    monkeypatch.setenv(auth_constants.ENV_COOKIE_INSECURE, "1")
    monkeypatch.setenv("BUSINESS_CANDIDATE_PROVIDER", "disabled")


def test_평가_미리보기_잠김_버튼은_서버렌더링부터_disabled라_busy스크립트_대상에서_빠진다(
    monkeypatch,
) -> None:
    _평가_미리보기_잠금_환경(monkeypatch)
    monkeypatch.setattr(runtime, "_PIPELINE", _FakeRealPipeline())

    with TestClient(
        app, base_url="http://127.0.0.1:8020", client=("127.0.0.1", 50123)
    ) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "미리보기 · 외부 조사 잠김" in response.text

    폼 = _여는_태그(response.text, "form", id_="analysisForm")
    assert "data-busy-submit" in 폼  # 폼 자체는 여전히 busy 대상이다

    버튼 = _여는_태그(response.text, "button", id_="confirmSubmitButton")
    # 버튼은 서버가 이미 잠갔다 — busy 스크립트의 `if (button.disabled)
    # return;` 가드(위 부분 템플릿 시험이 존재를 확인한다)가 이 버튼을
    # 다시 건드리지 않게 한다.
    assert "disabled" in 버튼
