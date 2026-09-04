"""새 UI는 첫 화면에만 적용되고 공통 화면에는 새지 않는다."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.web.main import app


STYLE = Path(__file__).parents[1] / "static" / "style.css"
FAVICON = Path(__file__).parents[1] / "static" / "favicon.svg"
TEMPLATES = Path(__file__).parents[1] / "templates"


def test_첫화면에만_새_디자인_범위가_붙는다(monkeypatch):
    for env in (
        auth_constants.ENV_CLIENT_ID,
        auth_constants.ENV_CLIENT_SECRET,
        auth_constants.ENV_REDIRECT_URI,
    ):
        monkeypatch.delenv(env, raising=False)

    with TestClient(app) as client:
        home = client.get("/")
        admin = client.get("/admin")
        favicon = client.get("/static/favicon.svg")

    assert home.status_code == 200
    assert '<body class="home-page">' in home.text
    assert '<a class="skip-link" href="#main-content">본문으로 건너뛰기</a>' in home.text
    assert '<main id="main-content" class="wrap home" tabindex="-1">' in home.text
    assert home.text.index('class="skip-link"') < home.text.index('<header class="topbar">')
    assert '<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">' in home.text
    assert FAVICON.is_file()
    assert favicon.status_code == 200
    assert favicon.headers["content-type"].startswith("image/svg+xml")
    assert favicon.text.lstrip().startswith("<svg")
    assert (
        '<a class="brand brand-mark brand-mark-compact" href="/" '
        'aria-label="기업 분석">기업 분석<span class="brand-mark-dot" '
        'aria-hidden="true"></span></a>'
    ) in home.text
    assert "처음으로" not in home.text
    assert "COMPANY INTELLIGENCE REPORT" not in home.text
    assert (
        '<h1 id="home-title" class="brand-mark brand-mark-hero">기업 분석'
        '<span class="brand-mark-dot" aria-hidden="true"></span></h1>'
    ) in home.text
    assert "취업 준비생을 위한 3분 기업 분석" in home.text
    assert "동종업계 속 회사의 우위와 근거를 이해하는 도구" not in home.text
    assert "5분 기업 파악" not in home.text
    assert "3분" in home.text
    assert "지원할 회사, 분석하세요" not in home.text
    assert "지원 준비를 위한 기업·공고 분석" not in home.text
    assert "공시·뉴스·회사 홈페이지를 근거로" not in home.text
    assert "실제 공시·뉴스를 조사합니다" not in home.text
    base_template = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    # 「실제 조사」 배지는 화면에서 뺐다(정상 상태라 알릴 필요가 없다) — 남는 것은
    # 옛 카피가 다시 새지 않는지뿐이다. 배지 자체 부재는 test_internal_status_copy_removed.py가 지킨다.
    assert "실제 공시·뉴스를 조사합니다" not in base_template
    # ★ `admin_home.html`은 어느 라우트도 렌더하지 않는 고아
    #   템플릿이라 지웠다. 이 시험이 읽던 문구는 «아무도 못 보는 글자»였다.
    #   실제로 관리자가 보는 첫 화면은 `/admin`이고, 그 화면의 계약은 아래
    #   `admin.status_code`·`<body class="">` 단정과 test_admin_frame.py가 지킨다.
    assert not (TEMPLATES / "admin_home.html").exists()
    assert "보고서 제공 범위" not in home.text
    assert "샘플 보고서로 먼저 둘러보기" in home.text
    assert "분석할 회사 확인하기" in home.text
    assert 'id="confirmSubmitButton"' in home.text
    assert "보고서 구성" not in home.text
    assert 'class="report-preview"' not in home.text
    assert "보고서 미리보기" not in home.text
    assert "지원할 회사를 조사해 드립니다" not in home.text
    assert "분석할 회사 정보" in home.text
    assert "회사 주소 구/군까지" in home.text
    assert 'placeholder="예) 서울 성동구"' in home.text
    assert "회사 소재지" not in home.text
    assert "analysis_engine/data/pilot/" not in home.text
    assert "로그인 설정 필요" not in home.text
    assert 'class="auth-status"' not in home.text
    assert 'href="/auth/login"' not in home.text
    assert 'href="/admin"' not in home.text
    assert admin.status_code in {200, 401, 403}
    assert '<body class="">' in admin.text
    assert "home-page" not in admin.text


def test_홈_디자인은_로컬글꼴과_지정한_모서리를_쓴다():
    css = STYLE.read_text(encoding="utf-8")

    assert ".home-page {" in css
    assert "--home-card-radius: 24px;" in css
    assert "--home-control-radius: 18px;" in css
    assert "--ink-3: #5f5f5f;" in css
    assert "--no: #e7000b;" in css
    assert "--home-error-text: #d0000a;" in css
    assert 'font-family: "Freesentation"' in css
    assert "word-break: keep-all;" in css
    assert "text-wrap: balance;" in css
    assert "max-width: 1350px;" in css
    assert "max-width: 1080px;" in css
    assert "text-align: center;" in css
    assert ".brand-mark-dot" in css
    assert "--brand-mark-base-size: .12em;" in css
    assert "--brand-mark-square-size: .1296em;" in css
    assert "inline-size: var(--brand-mark-square-size);" in css
    assert "block-size: var(--brand-mark-square-size);" in css
    assert "border-radius: 0;" in css
    assert "background: var(--brand-mark-dot);" in css
    assert "transform: translate(.2em, .06em);" in css
    assert ".home-page .home-subtitle" in css
    assert "white-space: nowrap;" in css
    assert ".home-page .home-trust" not in css
    assert ".home-page .report-preview" not in css
    assert ".home-page .preview-grid" not in css
    assert "http://" not in css
    assert "https://" not in css


def test_출고_기준을_통과한_canonical_샘플만_보인다():
    with TestClient(app) as client:
        home = client.get("/")

    assert home.status_code == 200
    assert "현재 출고 기준을 통과한 샘플만 보여드립니다" in home.text
    assert home.text.count('data-featured-sample="true"') == 1
    assert home.text.count('class="chip report sample-select"') == 1
    assert home.text.count("보고서 있음") == 1
    assert "(주)진영" in home.text
    assert '<details class="all-samples">' not in home.text
    assert 'class="chip unavailable-sample"' not in home.text
    assert "보고서 없음" not in home.text
    assert "document.querySelectorAll('.sample-select')" in home.text


def test_홈_모바일_입력과_긴_이메일_보호가_있다():
    css = STYLE.read_text(encoding="utf-8")

    assert ".home-page .auth-status .auth-email" in css
    assert "text-overflow: ellipsis;" in css
    assert "@media (max-width: 700px)" in css
    assert "font-size: 16px;" in css
    assert ".home-page .all-samples:not([open]) > :not(summary) { display: none; }" in css
    assert '.home-page .topbar > a[href="/"] { display: none; }' not in css
    assert ".skip-link:focus" in css
    assert "#main-content:focus" in css
    assert ".home-page :focus-visible" in css
    assert "outline: 3px solid var(--ink);" in css


def test_홈_제목은_데스크톱에서_정확히_2점5배이고_모바일은_별도_축소한다():
    css = STYLE.read_text(encoding="utf-8")
    desktop = re.search(
        r"\.home-page h1 \{.*?font-size:\s*(\d+(?:\.\d+)?)px;",
        css,
        re.DOTALL,
    )

    assert desktop is not None
    assert float(desktop.group(1)) / 48 == 2.5
    assert "font-size: clamp(32px, 10vw, 42px);" in css
    assert "vertical-align: baseline;" in css


def test_시각_브랜드_슬롯은_공통_기업분석_red_square_mark를_쓴다():
    templates = {
        path.name: path.read_text(encoding="utf-8")
        for path in TEMPLATES.glob("*.html")
    }
    visual_brand_slots = [
        (name, line)
        for name, source in templates.items()
        for line in source.splitlines()
        if 'class="brand ' in line or 'class="brand-mark ' in line
    ]

    assert len(visual_brand_slots) == 2
    assert {name for name, _line in visual_brand_slots} == {"base.html", "input.html"}
    for _name, line in visual_brand_slots:
        assert "기업 분석" in line
        assert 'class="brand-mark-dot"' in line
        assert 'aria-hidden="true"' in line
        assert 'aria-hidden="true"></span>' in line
    assert 'aria-label="기업 분석"' in templates["base.html"]
    assert ">기업분석</a>" not in templates["base.html"]


def test_브랜드_마침표는_직전_정사각형의_정확히_90퍼센트이고_글리프_밑선에_맞춘다():
    """Chrome 실측 허용오차는 hero/topbar 모두 0.75 CSS px다.

    접근성 근거: WCAG 2.2 SC 1.1.1(장식 무시), WAI-ARIA 1.2의
    접근성 트리 제외 규칙, WCAG 2.2 SC 1.4.10(320 CSS px reflow).
    """
    css = STYLE.read_text(encoding="utf-8")
    size_match = re.search(r"--brand-mark-square-size:\s*([.\d]+)em;", css)

    assert size_match is not None
    assert Decimal(size_match.group(1)) == Decimal(".144") * Decimal(".9")
    assert "background: var(--brand-mark-dot);" in css
    assert "border-radius: 0;" in css
    assert "transform: translate(.2em, .06em);" in css


def test_회사주소_도움말은_입력칸_description으로_연결된다():
    with TestClient(app) as client:
        home = client.get("/")

    assert home.status_code == 200
    assert 'id="region-hint"' in home.text
    assert 'aria-describedby="region-hint"' in home.text
    assert '<label for="region">회사 주소 구/군까지</label>' in home.text
    assert '회사 주소 구/군까지 <span class="optional">(선택)</span>' not in home.text
    assert "본사 주소와 달라도 확인 화면에서 선택할 수 있습니다" not in home.text
    assert 'name="region" required' not in home.text
    assert 'name="job"' not in home.text
    assert 'name="posting_text"' not in home.text
    assert 'name="posting_image_consent"' not in home.text
    assert "주소는 같은 이름의 회사를 구분할 때만 입력하세요" in home.text
    assert "document.getElementById('confirmSubmitButton').focus()" in home.text
    assert "회사 주소를 구/군까지 입력해 주세요" not in home.text
