"""화면에서 우리 내부 사정(배포 범위·미완성 안내·모드 이름·서버 실행 방법)을 빼는지 못 박는다.

★ 이 시험이 잡는 것 — 사용자가 몰라도 되는 내부 사정이 다시 화면에 슬며시 돌아오는 것.

★ 두 겹으로 지킨다.
  1) 아래쪽 개별 시험 — 이미 뺀 「실제 조사」 배지·배포 범위 배너처럼 특정 화면의 사정.
  2) `test_모든_화면_템플릿에_내부_사정_표현이_없다` — 템플릿 **전부**를 훑는 전수 검사.
     옛 문장 전문만 단언하면 문구를 조금 바꿔 되살릴 때 못 잡는다. 그래서 문장이 아니라
     「표현」(예: `-EnablePaidProviders`, `실조사`, `성능시험`)을 금지한다.
     일부 화면만 보고 「전수 점검했다」고 말했던 사고(6/36)를 되풀이하지 않기 위한 장치다.
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
from src.web import deployment_mode, evaluation_mode, main, runtime
from src.web.tests._visible_text import visible_text

WEB = Path(__file__).parents[1]
BASE_TEMPLATE = WEB / "templates" / "base.html"
ADMIN_NAV_TEMPLATE = WEB / "templates" / "_admin_nav.html"
CANDIDATE_TEMPLATE = WEB / "templates" / "company_candidates.html"
ADMIN_ROUTER = WEB / "routers" / "admin.py"
TEMPLATES = WEB / "templates"
STYLE = WEB / "static" / "style.css"

#: 화면에 있으면 안 되는 「표현」과 그 이유. 문장 전문이 아니라 표현이라 조금 바꿔
#: 되살려도 잡힌다.
BANNED_EXPRESSIONS: dict[str, str] = {
    "EnablePaidProviders": "서버 실행 플래그 — 사용자는 서버를 켜지 않는다",
    "실조사": "내부 모드 이름",
    "성능시험": "내부 모드 이름",
    "유료 AI": "내부 원가 구조",
    "유료 장소 검색": "내부 원가 구조 — 사용자에게는 「회사 후보 검색」이다",
    "친구 MEMBER": "내부 표현이자 오타 — 사용자는 MEMBER가 무엇인지 모른다",
    "임시 검수": "내부 개발 단계 이름",
    "승인 전": "내부 절차 상태",
    "아직 열지": "미착수 안내 — 사용자에게 필요한 건 «지금 되는지»뿐이다",
    "열지 않았": "미착수 안내",
    "아직 시작하지": "미착수 안내",
    "아직 안 했": "미착수 안내",
    "아직 결정하지": "미착수 안내",
    "보류": "우리 일정 사정 — 언제 열릴지는 사용자가 알 수 없다",
    "정식 저장소": "내부 로드맵",
}

#: 일부러 남긴 것. 파일 이름 → {표현: 남긴 이유}. 예외를 늘릴 때는 반드시 이유를 적는다.
ALLOWED_EXPRESSIONS: dict[str, dict[str, str]] = {
    "base.html": {
        # 지금이 시험 모드라는 사실은 사용자가 알아야 하는 진짜 고지다. 「샘플 모드」
        # 배지와 같은 이유로 남긴다 — 아래 test_base_html... 이 존치를 이미 못 박는다.
        "성능시험": "모드 배지 — 실제 조사로 오해하지 않게 하는 고지",
    },
    "evaluation_consent_required.html": {
        # 비용 동의 화면에 아직 남아 있는 모드 이름. 「지금 이 화면에서 무엇이
        # 되는지」로 바꾸는 것이 정리 방향이고, 그때까지 이 예외가 구멍을
        # 한 화면·한 표현으로 좁혀 둔다.
        "성능시험": "아직 사용자 문구로 바꾸지 않은 모드 이름",
    },
    "_posting_image_field.html": {
        # 「실조사에서는 이미지 원본을 …」 문장이 아직 남아 있다. 위와 같은
        # 이유로 예외를 좁게 둔다.
        "실조사": "아직 사용자 문구로 바꾸지 않은 모드 이름",
    },
}

#: 태그 안에 숨은 사람이 읽는 글자도 함께 본다 (툴팁·대체글·입력칸 안내).
VISIBLE_ATTRIBUTES: tuple[str, ...] = (
    "title",
    "alt",
    "placeholder",
    "aria-label",
    "value",
    "summary",
)

#: 실측 2026-09-01 기준 화면 템플릿은 38개다(고아 템플릿 5개를 지운 뒤).
#: 「일부만 보고 전수」를 막는 하한선 — 실측치에서 3개 여유를 둔 값이다.
MINIMUM_SCANNED_TEMPLATES = 35


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


def _template_visible_text(path: Path) -> str:
    """템플릿 파일에서 화면에 글자로 나오는 부분만 남긴다.

    Jinja 주석·태그·치환칸을 먼저 걷어낸 뒤 HTML 태그를 지운다. 순서를 바꾸면
    ``{% if a > b %}`` 같은 조건문이 태그로 오인돼 본문이 잘린다.
    """
    raw = path.read_text(encoding="utf-8")
    without_comments = re.sub(r"(?s)\{#.*?#\}", " ", raw)
    without_jinja = re.sub(r"(?s)\{[%{].*?[%}]\}", " ", without_comments)
    attribute_pattern = (
        r'(?i)\b(?:' + "|".join(VISIBLE_ATTRIBUTES) + r')\s*=\s*"([^"]*)"'
    )
    attribute_texts = re.findall(attribute_pattern, without_jinja)
    return "\n".join([visible_text(without_jinja), *attribute_texts])


def test_모든_화면_템플릿에_내부_사정_표현이_없다():
    """★ 전수 — 화면 템플릿 전부를 훑는다. 일부만 보고 「전수」라고 하지 않기 위해."""
    scanned: list[Path] = sorted(TEMPLATES.glob("*.html"))
    offenders: list[str] = []

    for path in scanned:
        text = _template_visible_text(path)
        allowed = ALLOWED_EXPRESSIONS.get(path.name, {})
        for phrase in BANNED_EXPRESSIONS:
            if phrase in text and phrase not in allowed:
                offenders.append(f"{path.name} 「{phrase}」")

    assert len(scanned) >= MINIMUM_SCANNED_TEMPLATES, (
        f"검사한 템플릿이 {len(scanned)}개뿐이다 — 전수가 아니다"
    )
    assert not offenders, (
        "화면에 내부 사정 문구가 남아 있다: "
        + ", ".join(offenders)
        + " — 사용자가 지금 무엇을 할 수 있는지로 바꿔라"
    )


def test_예외로_남긴_표현이_실제로_그_화면에_아직_있다():
    """예외 목록이 낡으면 구멍만 남는다. 사라진 예외는 지워야 한다."""
    stale: list[str] = []

    for file_name, phrases in ALLOWED_EXPRESSIONS.items():
        path = TEMPLATES / file_name
        assert path.exists(), f"예외 목록의 {file_name}이 없다"
        text = _template_visible_text(path)
        for phrase in phrases:
            if phrase not in text:
                stale.append(f"{file_name} 「{phrase}」")

    assert not stale, "이미 사라진 예외가 남아 있다: " + ", ".join(stale)


def test_미리보기_차단_안내에_서버_실행_방법이_없다():
    """이 상수 하나가 `/confirm`·`/start` 등 세 곳의 응답으로 그대로 나간다."""
    message = evaluation_mode.PREVIEW_BLOCKED_MESSAGE

    for phrase in BANNED_EXPRESSIONS:
        assert phrase not in message, f"미리보기 안내에 「{phrase}」가 남아 있다"
    # 「왜 막혔는지」와 「비용이 안 나갔다」는 남아야 한다 — 지우면 사용자가 불안해진다.
    assert "미리보기" in message
    assert "비용" in message


def test_관리자_초대_거절_응답에도_내부_표현이_없다():
    """같은 문구를 409 본문으로 돌려주던 곳(POST /admin/invite)."""
    source = ADMIN_ROUTER.read_text(encoding="utf-8")

    assert "친구 MEMBER" not in source
    assert "초대를 보류" not in source
    assert "이 운영판에서는 친구를 초대할 수 없습니다." in source


def test_관리자_접근관리_화면에도_내부_사정_표현이_없다(monkeypatch, tmp_path):
    """정적 검사에 더해, 좁은 운영판 계약으로 **실제로 그려도** 안 나오는지 본다."""
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
        access = web_client.get("/admin/access")

    assert access.status_code == 200
    text = visible_text(access.text)
    for phrase in BANNED_EXPRESSIONS:
        assert phrase not in text, f"관리자 접근관리 화면에 「{phrase}」가 남아 있다"
    # 관리자가 실제로 할 수 있는 일(철회·해제)은 그대로 알려야 한다.
    assert "이 운영판에서는 LINK를 발급할 수 없습니다." in text
    assert "이 운영판에서는 친구를 초대할 수 없습니다." in text
