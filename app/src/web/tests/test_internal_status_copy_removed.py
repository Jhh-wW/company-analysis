"""일반 사용자 화면에서 우리 내부 사정(배포 범위·미완성 안내·모드 이름)을 빼는지 못 박는다.

★ 이 시험이 잡는 것 — 사용자가 몰라도 되는 내부 사정이 다시 화면에 슬며시 돌아오는 것.
  base.html 머리 배지의 「실제 조사」 태그와 _admin_nav.html의 배포 범위 배너 두 곳을
  뺐는데, 시험이 없으면 다음 수정에서 조용히 되살아나도 아무도 못 잡는다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.pipeline.demo import DemoPipeline
from src.features.storage import constants as storage_constants
from src.web import deployment_mode, main, runtime
from src.web.tests._visible_text import visible_text

WEB = Path(__file__).parents[1]
BASE_TEMPLATE = WEB / "templates" / "base.html"
ADMIN_NAV_TEMPLATE = WEB / "templates" / "_admin_nav.html"
CANDIDATE_TEMPLATE = WEB / "templates" / "company_candidates.html"
STYLE = WEB / "static" / "style.css"


class _가짜진짜파이프라인:
    """`DemoPipeline`이 아니기만 하면 된다 — `is_real` 판정이 그것으로 갈린다."""


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


def _session(client: TestClient, *, email: str, is_admin: bool) -> str:
    session = auth_logic.create_session(email, is_admin)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
    return auth_logic.csrf_token_for_session(session.token)


def test_실제_조사_모드에서도_실제조사_배지를_따로_보여주지_않는다(
    client: TestClient, monkeypatch
):
    """정상 상태(실제 조사)는 배지로 알릴 필요가 없다. 데모/시험 모드만 알린다."""
    monkeypatch.setattr(runtime, "_PIPELINE", _가짜진짜파이프라인())

    response = client.get("/")
    assert response.status_code == 200
    text = visible_text(response.text)

    assert "실제 조사" not in text
    # 실제 조사인데 「샘플 모드」라고 잘못 말해서도 안 된다 (P-79와 같은 종류의 사고).
    assert "샘플 모드" not in text


def test_데모_모드에서는_샘플_모드_배지를_그대로_보여준다(client: TestClient):
    """데모 모드까지 배지를 지우면 사용자가 실제 조사로 착각한다 — 이 배지는 남겨야 한다."""
    response = client.get("/")
    assert response.status_code == 200
    text = visible_text(response.text)

    assert "샘플 모드" in text


def test_base_html에_실제조사_배지_분기가_없다():
    template = BASE_TEMPLATE.read_text(encoding="utf-8")

    assert '<span class="tag real"' not in template
    assert "실제 조사</span>" not in template
    # 데모·시험 모드 배지는 그대로 남아 있어야 한다.
    assert "실시간 성능시험" in template
    assert "샘플 모드" in template


def test_admin_nav에_배포범위_배너가_없다():
    """narrow_admin_demo·narrow_admin_real 배너 둘 다 뺐다. 점검 상태 배너는 남는다."""
    template = ADMIN_NAV_TEMPLATE.read_text(encoding="utf-8")

    assert "narrow_admin_demo" not in template
    assert "narrow_admin_real" not in template
    assert "무료 관리자 데모" not in template
    assert "실제 분석 관리자 운영판" not in template
    assert "재배포 때 운영 데이터가 초기화" not in template
    # 진짜 운영 정보인 전역 점검 상태 배너는 그대로 남아야 한다.
    assert 'dashboard_service.status == "maintenance"' in template


def test_후보목록_화면은_기본폭이_아니라_wide로_넓힌다():
    """780px 기본폭에 4칸 행이 눌려 보이던 것을 이 화면에만 넓혀 고친다."""
    template = CANDIDATE_TEMPLATE.read_text(encoding="utf-8")
    css = STYLE.read_text(encoding="utf-8")

    assert "{% block wrapclass %}wide{% endblock %}" in template
    # 다른 화면까지 넓어지지 않게 기존 .wrap.wide 재사용 여부를 함께 확인한다.
    assert ".wrap.wide { max-width: 960px; }" in css


def test_무료_관리자_데모_계약에서도_배포범위_배너가_실제로_안_보인다(monkeypatch, tmp_path):
    """정적 텍스트 검사(위 test_admin_nav...)에 더해, 실제로 이 계약으로 렌더링해도
    배너가 안 나오는지까지 확인한다 — test_dashboard.py가 예전에 반대로 지키던 것."""
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(tmp_path / "storage.db"))
    monkeypatch.setenv(
        deployment_mode.ENV_DEPLOYMENT_RUNTIME_CONTRACT,
        deployment_mode.RENDER_ADMIN_DEMO_NO_FORWARDED_CONTRACT,
    )
    monkeypatch.setenv(deployment_mode.ENV_PUBLIC_ORIGIN, "https://demo.example")
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    runtime._PIPELINE = DemoPipeline()

    with TestClient(main.app, base_url="https://demo.example") as web_client:
        _session(web_client, email="admin@example.com", is_admin=True)
        dashboard = web_client.get("/admin")

    assert dashboard.status_code == 200
    assert "무료 관리자 데모" not in dashboard.text
    assert "재배포 때 운영 데이터가 초기화" not in dashboard.text


def test_실제분석_운영판_계약에서도_배포범위_배너가_실제로_안_보인다(monkeypatch, tmp_path):
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(tmp_path / "storage.db"))
    monkeypatch.setenv(
        deployment_mode.ENV_DEPLOYMENT_RUNTIME_CONTRACT,
        deployment_mode.RENDER_ADMIN_REAL_NO_FORWARDED_CONTRACT,
    )
    monkeypatch.setenv(deployment_mode.ENV_PUBLIC_ORIGIN, "https://pilot.example")
    monkeypatch.delenv(auth_constants.ENV_BETA_ADMIN_ONLY, raising=False)
    runtime._PIPELINE = DemoPipeline()

    with TestClient(main.app, base_url="https://pilot.example") as web_client:
        _session(web_client, email="admin@example.com", is_admin=True)
        dashboard = web_client.get("/admin")

    assert dashboard.status_code == 200
    assert "실제 분석 관리자 운영판" not in dashboard.text
    assert "외부 사용자용 친구 MEMBER와 지원 LINK는 아직 열지 않았습니다" not in dashboard.text


def test_후보_메타칸은_고정3열로_찌그러지지_않는다():
    """폭이 얼마든 항상 3등분하던 것을 남는 폭만큼 칸이 스스로 줄고 늘게 바꾼다."""
    css = STYLE.read_text(encoding="utf-8")

    assert "repeat(3, minmax(0, 1fr))" not in css.split(
        ".candidate-page .candidate-meta {"
    )[1].split("}")[0]
    assert "auto-fit" in css.split(".candidate-page .candidate-meta {")[1].split("}")[0]


def test_후보_메타칸_최소폭은_가장_긴_실제값이_한줄에_들어갈_만큼_크다():
    """84px였을 때 「전자공시(DART) 기업개황」(실측 155px 필요)이 3줄로 쪼개졌다.
    최솟값을 그 실측값보다 작게 되돌리면 같은 사고가 재발한다."""
    css = STYLE.read_text(encoding="utf-8")
    rule = css.split(".candidate-page .candidate-meta {")[1].split("}")[0]

    match = re.search(r"minmax\((\d+)px", rule)
    assert match, "minmax(...px, 1fr) 형태를 찾지 못했다"
    min_width = int(match.group(1))

    # 실측: 팀장이 실 배포본에서 잰 값 155px. 그보다 작으면 다시 낱글자가 갈린다.
    assert min_width >= 155
