"""저장된 과거 출처 URL을 관리자 브라우저의 실행 권한으로 승격하지 않는다."""

from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.core import paths


def _render_source_url(url: str) -> str:
    environment = Environment(
        loader=FileSystemLoader(str(paths.TEMPLATES_DIR)),
        autoescape=select_autoescape(("html",)),
    )
    template = environment.get_template("admin_report_detail.html")
    return template.render(
        report=SimpleNamespace(
            company="시험 회사",
            citations=(SimpleNamespace(label="저장 출처", url=url),),
            generated_at="",
            summary_items=(),
        ),
        report_state=SimpleNamespace(
            status="normal",
            blocked=False,
            version=1,
            company_type="",
            updated_at="",
        ),
        report_id="a" * 32,
        report_errors=(),
        report_events=(),
        report_surveys=(),
        report_trash=None,
        report_statuses=(),
        company_types=(),
        dashboard_status_labels={},
        dashboard_company_labels={},
        csrf_token="",
        auth_email="admin@example.test",
        auth_is_admin=True,
        evaluation_mode=False,
        is_real=True,
    )


def test_저장된_비웹_scheme은_관리자_상세에서_클릭_링크가_되지_않는다() -> None:
    for malicious in (
        "javascript:alert(document.domain)",
        "data:text/html,<script>alert(1)</script>",
        "//attacker.example/credential-prompt",
    ):
        html = _render_source_url(malicious)

        assert f'href="{malicious}' not in html
        assert "안전한 웹 주소가 아님" in html


def test_HTTP_S_출처만_새_탭의_격리된_링크로_보여준다() -> None:
    safe = "https://example.com/report?id=1&view=source"

    html = _render_source_url(safe)

    assert 'href="https://example.com/report?id=1&amp;view=source"' in html
    assert 'target="_blank"' in html
    assert 'rel="noopener noreferrer"' in html
