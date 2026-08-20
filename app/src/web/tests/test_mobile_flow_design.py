"""확인·진행 화면의 좁은 폭 레이아웃 회귀를 막는다."""

from __future__ import annotations

from pathlib import Path


WEB = Path(__file__).parents[1]
CONFIRM = WEB / "templates" / "confirm.html"
PROGRESS = WEB / "templates" / "progress.html"
STYLE = WEB / "static" / "style.css"


def test_확인과_진행_화면은_모바일_전용_범위를_가진다():
    confirm = CONFIRM.read_text(encoding="utf-8")
    progress = PROGRESS.read_text(encoding="utf-8")

    assert "{% block bodyclass %}confirm-page{% endblock %}" in confirm
    assert "{% block bodyclass %}progress-page{% endblock %}" in progress


def test_모바일_상단바와_확인_버튼은_줄바꿈으로_깨지지_않는다():
    css = STYLE.read_text(encoding="utf-8")

    assert "body:not(.home-page):not(.result-page) .topbar" in css
    assert "body:not(.home-page):not(.result-page) .auth-status" in css
    assert "body:not(.home-page):not(.result-page) .auth-status .auth-action" in css
    assert "body:not(.home-page):not(.result-page) .auth-status > .auth-email" in css
    assert "text-overflow: ellipsis;" in css
    assert "white-space: nowrap;" in css
    assert ".confirm-page .btn-row { flex-direction: column; }" in css
    assert ".confirm-page .btn-row > form" in css
    assert "min-height: 44px;" in css


def test_회사분석_확인과_진행화면에는_공고이미지상태가_없다():
    confirm = CONFIRM.read_text(encoding="utf-8")
    progress = PROGRESS.read_text(encoding="utf-8")

    assert "PostingImageStore" not in confirm
    assert "PostingImageStore" not in progress
    assert 'name="posting_images"' not in confirm
    assert 'name="posting_image_consent"' not in confirm


def test_확인화면은_회사와_주소만_다음요청으로_보낸다():
    confirm = CONFIRM.read_text(encoding="utf-8")

    assert 'name="company"' in confirm
    assert 'name="region"' in confirm
    assert 'name="job"' not in confirm
    assert 'name="posting_text"' not in confirm
