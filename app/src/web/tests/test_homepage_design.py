"""새 UI는 첫 화면에만 적용되고 공통 화면에는 새지 않는다."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.web.main import app


STYLE = Path(__file__).parents[1] / "static" / "style.css"
FAVICON = Path(__file__).parents[1] / "static" / "favicon.svg"


def test_첫화면에만_새_디자인_범위가_붙는다():
    with TestClient(app) as client:
        home = client.get("/")
        admin = client.get("/admin")
        favicon = client.get("/static/favicon.svg")

    assert home.status_code == 200
    assert '<body class="home-page">' in home.text
    assert '<main class="wrap home">' in home.text
    assert '<link rel="icon" type="image/svg+xml" href="/static/favicon.svg">' in home.text
    assert FAVICON.is_file()
    assert favicon.status_code == 200
    assert favicon.headers["content-type"].startswith("image/svg+xml")
    assert favicon.text.lstrip().startswith("<svg")
    assert "진하게 채워진 버튼 = 보고서가 나오는 회사" in home.text
    assert "초록색 = 보고서가 나오는 회사" not in home.text
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
    assert "http://" not in css
    assert "https://" not in css


def test_홈_모바일_입력과_긴_이메일_보호가_있다():
    css = STYLE.read_text(encoding="utf-8")

    assert ".home-page .auth-status .auth-email" in css
    assert "text-overflow: ellipsis;" in css
    assert "@media (max-width: 700px)" in css
    assert "font-size: 16px;" in css
    assert ".home-page :focus-visible" in css
    assert "outline: 3px solid var(--ink);" in css
