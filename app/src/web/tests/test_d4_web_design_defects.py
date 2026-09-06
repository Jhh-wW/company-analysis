"""화면(result.html·style.css)이 D-4 디자인 결함으로 되돌아가지 못하게 막는다.

근거(실측 PDF·화면 대조): 0선 아래 막대가 빨강이었고, 쉐브론 열 이름이 줄마다
반복됐으며, 「미확인」 칸이 가장 진한 단계로 채워졌다. PDF 쪽 방어는
``export_pdf/tests/test_d4_design_defects.py``가 맡는다 — 여기서는 두 채널이
«같은 값»을 쓰는지까지 함께 본다.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.composer.constants import FLOW_UNCONFIRMED_CELL
from src.features.export_pdf import constants as pdf_constants
from src.features.pipeline.canonical_demo import build_demo_report
from src.web import job_runtime, request_helpers
from src.web.main import app
from src.web.tests._visible_text import class_count
from src.web.tests.report_route_support import serve_legacy_report_snapshot


_WEB = Path(__file__).parents[1]
STYLE = _WEB / "static" / "style.css"
TEMPLATE = _WEB / "templates" / "result.html"

_FLOW_HEADERS = ("핵심 자산", "제품·서비스", "고객 행동·과금", "반복·확장 수익")
_FLOW_CAPTION = "가치와 수익이 흐르는 경로"


#: 흐름 도표의 «칸 한 개» 줄. 틀에서 이 줄을 그대로 꺼내 돌려 본다 —
#: 복사해 두면 틀이 바뀌어도 시험은 옛 글로 계속 통과한다.
_FLOW_CELL_LINE = re.compile(r"^\s*(<li\{% if value ==.*?</li>)\s*$", re.M)


def _flow_cell_template() -> str:
    """result.html에 «지금 적힌» 흐름 칸 줄을 꺼낸다."""

    template = TEMPLATE.read_text(encoding="utf-8")
    lines = _FLOW_CELL_LINE.findall(template)
    assert len(lines) == 2, (
        "result.html의 흐름 분기 두 곳에서 칸 줄을 찾지 못했습니다: "
        f"{len(lines)}개"
    )
    assert lines[0] == lines[1], "v1·v2 흐름 칸이 서로 다른 글로 그려집니다"
    return lines[0]


def _render_flow_row(values: tuple[str, ...]) -> str:
    """틀에서 꺼낸 칸 줄을 실제 Jinja로 한 줄 그려 본다."""

    from jinja2 import Environment  # noqa: PLC0415 - 이 시험만 쓰는 의존

    source = "{% for value in values %}" + _flow_cell_template() + "{% endfor %}"
    rendered = Environment(autoescape=True).from_string(source).render(
        values=list(values),
        t=SimpleNamespace(headers=list(_FLOW_HEADERS)),
    )
    return rendered


def _css() -> str:
    return STYLE.read_text(encoding="utf-8")


def _rule(css: str, selector: str) -> str:
    """``selector`` 규칙 한 덩어리의 본문을 돌려준다.

    선택자 바로 뒤가 ``{``(또는 다른 선택자를 잇는 ``,``)여야 한다 —
    ``.trend-bar``가 ``.trend-bar-wrap``을 집어 오지 않게 하려는 것이다.
    """

    pattern = re.compile(re.escape(selector) + r"\s*(?:,[^{}]*)?\{([^}]*)\}")
    match = pattern.search(css)
    assert match, f"CSS에서 {selector} 규칙을 찾지 못했습니다"
    return match.group(1)


# ══════════════════════════════════════════════════════════
# ① 0선 아래 막대는 무채색 — PDF와 «같은 값»
# ══════════════════════════════════════════════════════════


def test_0선_아래_막대는_빨강이_아니라_가장_진한_회색이다() -> None:
    css = _css()

    for selector in (
        ".result-page .trend-point.below .trend-bar",
        ".result-page .trend-panel.risk .trend-bar",
    ):
        body = _rule(css, selector)
        assert "var(--chart-5)" in body, f"{selector} 가 무채색을 쓰지 않습니다"
        assert "--risk" not in body, f"{selector} 에 아직 붉은색이 남아 있습니다"

    for selector in (
        ".result-page .trend-point.below .trend-value",
        ".result-page .trend-panel.risk .trend-value",
    ):
        body = _rule(css, selector)
        assert "--risk" not in body, f"{selector} 의 값 글자가 아직 붉습니다"


def test_화면과_PDF의_0선_아래_막대_색이_같은_값이다() -> None:
    """두 채널이 갈라지면 같은 그림이 색만 다르게 나온다."""

    css = _css()
    match = re.search(r"--chart-5:\s*(#[0-9A-Fa-f]{6})", css)
    assert match, "style.css에 --chart-5 정의가 없습니다"
    assert match.group(1).upper() == pdf_constants.COLOR_CHART_DARK.upper()


# ══════════════════════════════════════════════════════════
# ② 쉐브론 열 이름은 첫 줄에만
# ══════════════════════════════════════════════════════════


def test_두_번째_쉐브론_줄부터는_열_이름을_감춘다() -> None:
    css = _css()
    selector = (
        '.result-page .flow-row[data-flow-style="chevron"] ~ '
        '.flow-row[data-flow-style="chevron"] small'
    )
    body = _rule(css, selector)
    assert "clip-path" in body and "1px" in body, (
        "반복되는 열 이름을 감추는 규칙이 없습니다"
    )

    row_selector = (
        '.result-page .flow-row[data-flow-style="chevron"] ~ '
        '.flow-row[data-flow-style="chevron"] li'
    )
    row_body = _rule(css, row_selector)
    assert "--chevron-top: 0" in row_body, (
        "머리글을 감춘 줄의 쉐브론이 여전히 22px 아래에서 시작합니다"
    )


def test_열_이름_글자는_HTML에서_지우지_않는다() -> None:
    """감추는 것은 «모양»이다 — 낭독기와 봉인 대조가 읽는 글자는 그대로다."""

    first = _render_flow_row(("개발 역량", "응용 개발", "용역료 지급", "대형 계약"))
    second = _render_flow_row(("수행 능력", "정부지원 개발", "보조금 수령", "계약"))
    for header in _FLOW_HEADERS:
        assert header in first and header in second, (
            f"열 이름 「{header}」이 두 줄 모두에 남아 있어야 합니다"
        )


# ══════════════════════════════════════════════════════════
# ③ 「미확인」은 옅은 빈 칸
# ══════════════════════════════════════════════════════════


def test_미확인_칸에만_빈_칸_표시가_붙는다() -> None:
    html = _render_flow_row(
        ("수행 능력", "정부지원 개발", "보조금 수령", FLOW_UNCONFIRMED_CELL)
    )

    assert html.count('data-flow-cell="unconfirmed"') == 1
    cells = re.findall(r"<li[^>]*>.*?</li>", html, flags=re.S)
    marked = [cell for cell in cells if "data-flow-cell" in cell]
    assert len(marked) == 1 and FLOW_UNCONFIRMED_CELL in marked[0]

    filled = _render_flow_row(("개발 역량", "응용 개발", "용역료 지급", "대형 계약"))
    assert "data-flow-cell" not in filled, (
        "채워진 칸에도 빈 칸 표시가 붙었습니다"
    )


def test_틀에_적힌_미확인_낱말이_composer_상수와_같다() -> None:
    """틀 안의 낱말이 상수와 어긋나면 빈 칸 표시가 조용히 안 붙는다."""

    template = TEMPLATE.read_text(encoding="utf-8")
    literal = f"value == '{FLOW_UNCONFIRMED_CELL}'"
    assert template.count(literal) == 2, (
        f"result.html의 흐름 분기 두 곳이 「{FLOW_UNCONFIRMED_CELL}」을 "
        "그대로 쓰고 있지 않습니다"
    )


def test_미확인_칸은_채우지_않고_옅은_테두리와_회색_글자로_그린다() -> None:
    css = _css()

    fill = _rule(
        css,
        '.result-page .flow-row[data-flow-style="chevron"] '
        'li[data-flow-cell="unconfirmed"]::before',
    )
    assert "var(--white)" in fill, "「미확인」 칸이 여전히 회색 단계로 채워집니다"

    border = _rule(
        css,
        '.result-page .flow-row[data-flow-style="chevron"] '
        'li[data-flow-cell="unconfirmed"]::after',
    )
    assert "var(--grey-2)" in border and "clip-path" in border, (
        "「미확인」 칸에 옅은 회색 쉐브론 테두리가 없습니다"
    )

    text = _rule(
        css,
        '.result-page .flow-row[data-flow-style="chevron"] '
        'li[data-flow-cell="unconfirmed"] span',
    )
    assert "var(--grey-4)" in text, "「미확인」 글자가 옅은 회색이 아닙니다"


# ══════════════════════════════════════════════════════════
# ④ 표지 수치 카드 — 값이 셋이면 세 칸
# ══════════════════════════════════════════════════════════


class _CoverItem:
    def __init__(self, label: str, value: str, unit: str) -> None:
        self.label = label
        self.value = value
        self.unit = unit


class _ThreeMetrics:
    title = "2025 사업연도 실적"
    cite = "9"
    items = (
        _CoverItem("매출액", "26,499", "억원"),
        _CoverItem("영업이익", "493", "억원"),
        _CoverItem("당기순이익", "-2,544", "억원"),
    )

    def __bool__(self) -> bool:
        return True


def _result_html(monkeypatch: pytest.MonkeyPatch) -> str:
    report = build_demo_report()
    job_id = uuid.uuid4().hex
    serve_legacy_report_snapshot(monkeypatch, report, report_id=job_id)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    session = auth_logic.create_session("admin@example.com", True)

    with TestClient(app) as client:
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        response = client.get(f"/result/{job_id}")
    assert response.status_code == 200
    return response.text


def test_표지_실적_띠는_값이_셋이면_세_칸을_그린다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """고르는 쪽이 셋을 넘기면 화면은 그대로 세 칸을 그린다.

    ★ 지금 «고르는» 함수(report_standard.cover_metrics)는 두 개까지만 넘긴다.
      이 시험은 그리는 쪽(화면 틀·CSS)이 셋을 받을 준비가 됐음을 지킨다.
    """

    monkeypatch.setattr(
        request_helpers, "cover_metrics", lambda report: _ThreeMetrics()
    )
    body = _result_html(monkeypatch)

    assert class_count(body, "cover-metric") == 3
    for label in ("매출액", "영업이익", "당기순이익"):
        assert label in body
    assert "-2,544" in body


def test_표지_카드_줄은_칸_수를_고정하지_않는다() -> None:
    """두 칸짜리 회사와 세 칸짜리 회사가 같은 규칙으로 그려져야 한다."""

    body = _rule(_css(), ".result-page .cover-metrics-list")
    assert "grid-auto-flow: column" in body
    assert "grid-template-columns" not in body, (
        "칸 수를 고정하면 당기순이익 칸이 생겨도 자리가 안 늘어납니다"
    )
