"""Canonical 보고서의 표가 웹에서 안전한 도표 또는 원표로 표현되는지 검증한다."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from html.parser import HTMLParser

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.pipeline.canonical_demo import build_demo_report
from src.web import job_runtime
from src.web.main import app


_COMPOSITION_CAPTION = "2026년 상반기 매출 구성 (단위: %)"
_TREND_CAPTION = "완료 사업연도 연결 실적 (단위: 억원)"
_OPERATIONS_CAPTION = "자원순환 운영 구조"
_STYLE_RATIO = re.compile(
    r"^(?P<property>width|height): "
    r"(?P<ratio>(?:100|[1-9][0-9]|[0-9])\.\d{4})%$"
)
_VOID_ELEMENTS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


@dataclass
class _Element:
    tag: str
    attrs: dict[str, str]
    parent: "_Element | None" = None
    children: list["_Element"] = field(default_factory=list)
    text_parts: list[str] = field(default_factory=list)

    @property
    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())

    @property
    def text(self) -> str:
        pieces = [*self.text_parts, *(child.text for child in self.children)]
        return re.sub(r"\s+", " ", " ".join(pieces)).strip()


class _RenderedDOM(HTMLParser):
    """외부 HTML parser 없이 실제 result 응답의 요소 관계를 보존한다."""

    def __init__(self, body: str) -> None:
        super().__init__()
        self.root = _Element("#document", {})
        self.stack = [self.root]
        self.elements: list[_Element] = []
        self.feed(body)

    def handle_starttag(self, tag: str, attrs) -> None:
        element = _Element(
            tag,
            {key: value or "" for key, value in attrs},
            self.stack[-1],
        )
        self.stack[-1].children.append(element)
        self.elements.append(element)
        if tag not in _VOID_ELEMENTS:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_ELEMENTS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        self.stack[-1].text_parts.append(data)

    def all(self, tag: str) -> list[_Element]:
        return [element for element in self.elements if element.tag == tag]

    def by_id(self, element_id: str) -> _Element | None:
        return next(
            (element for element in self.elements if element.attrs.get("id") == element_id),
            None,
        )

    def by_class(self, class_name: str) -> list[_Element]:
        return [element for element in self.elements if class_name in element.classes]


def _descendants(
    element: _Element,
    *,
    tag: str | None = None,
    class_name: str | None = None,
) -> list[_Element]:
    found: list[_Element] = []
    for child in element.children:
        if (tag is None or child.tag == tag) and (
            class_name is None or class_name in child.classes
        ):
            found.append(child)
        found.extend(_descendants(child, tag=tag, class_name=class_name))
    return found


def _ancestor_with_attr(element: _Element, attribute: str) -> _Element | None:
    current = element.parent
    while current is not None:
        if attribute in current.attrs:
            return current
        current = current.parent
    return None


def _only_visual(dom: _RenderedDOM, class_name: str, caption: str) -> _Element:
    matches = [
        element
        for element in dom.by_class(class_name)
        if element.tag == "figure" and caption in element.text
    ]
    assert len(matches) == 1
    return matches[0]


def _assert_figure_label(dom: _RenderedDOM, figure: _Element, caption: str) -> None:
    labelledby = figure.attrs.get("aria-labelledby", "")
    label = dom.by_id(labelledby)
    assert label is not None
    assert label.tag == "figcaption"
    assert caption in label.text


def _render_demo(monkeypatch: pytest.MonkeyPatch) -> tuple[_RenderedDOM, str]:
    report = build_demo_report()
    job_id = f"report-visualizations-{uuid.uuid4().hex}"
    job_runtime._JOBS.pop(job_id, None)
    monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _report_id: report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    session = auth_logic.create_session("admin@example.com", True)

    with TestClient(app) as client:
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        response = client.get(f"/result/{job_id}")

    assert response.status_code == 200
    return _RenderedDOM(response.text), response.text


def test_진영_2장과_4장은_도표로_바꾸고_7장_운영구조는_원표를_유지한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dom, body = _render_demo(monkeypatch)

    composition = _only_visual(dom, "composition-chart", _COMPOSITION_CAPTION)
    trend = _only_visual(dom, "trend-chart", _TREND_CAPTION)
    assert _ancestor_with_attr(composition, "data-report-cell").attrs[
        "data-report-cell"
    ] == "business_model"
    assert _ancestor_with_attr(trend, "data-report-cell").attrs[
        "data-report-cell"
    ] == "past_changes"
    assert _descendants(composition, tag="table") == []
    assert _descendants(trend, tag="table") == []

    table_labels: list[str] = []
    for table in dom.all("table"):
        labelledby = table.attrs.get("aria-labelledby", "")
        label = dom.by_id(labelledby)
        if label is not None:
            table_labels.append(label.text)
    assert not any(_COMPOSITION_CAPTION in label for label in table_labels)
    assert not any(_TREND_CAPTION in label for label in table_labels)
    assert sum(_OPERATIONS_CAPTION in label for label in table_labels) == 1

    operations_labels = [
        element
        for element in dom.by_class("cap")
        if _OPERATIONS_CAPTION in element.text
    ]
    assert len(operations_labels) == 1
    operations_block = operations_labels[0].parent
    assert operations_block is not None and "numtable" in operations_block.classes
    operations_tables = _descendants(operations_block, tag="table")
    assert len(operations_tables) == 1
    assert _ancestor_with_attr(operations_block, "data-report-cell").attrs[
        "data-report-cell"
    ] == "operations_partners"
    assert "flow-chart" not in body


def test_진영_도표는_값_단위_출처_계산안내와_숫자_style을_접근가능하게_낸다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dom, _body = _render_demo(monkeypatch)
    composition = _only_visual(dom, "composition-chart", _COMPOSITION_CAPTION)
    trend = _only_visual(dom, "trend-chart", _TREND_CAPTION)
    _assert_figure_label(dom, composition, _COMPOSITION_CAPTION)
    _assert_figure_label(dom, trend, _TREND_CAPTION)

    tracks = _descendants(composition, class_name="composition-track")
    assert len(tracks) == 1
    assert tracks[0].attrs == {
        "class": "composition-track",
        "role": "img",
        "aria-label": _COMPOSITION_CAPTION,
    }
    legends = _descendants(composition, tag="ul", class_name="chart-legend")
    assert len(legends) == 1
    assert legends[0].attrs.get("aria-label") == "구성 항목"
    for expected in (
        "가구용 시트·엣지",
        "70.0%",
        "산업용 시트",
        "9.1%",
        "열분해유",
        "6.3%",
        "기타 제품·상품·매출",
        "14.6%",
    ):
        assert expected in composition.text

    panels = _descendants(trend, tag="section", class_name="trend-panel")
    assert {panel.attrs.get("aria-label") for panel in panels} == {
        "매출",
        "영업이익(손실)",
    }
    assert sum("(억원)" in panel.text for panel in panels) == 2
    for expected in (
        "2023",
        "2024",
        "2025",
        "309.0",
        "342.2",
        "324.2",
        "-23.6",
        "-29.4",
        "-26.6",
    ):
        assert expected in trend.text
    bar_wrappers = _descendants(trend, class_name="trend-bar-wrap")
    assert len(bar_wrappers) == 6
    assert all(wrapper.attrs.get("aria-hidden") == "true" for wrapper in bar_wrappers)

    notes = {
        "composition": _descendants(composition, tag="p", class_name="chart-note"),
        "trend": _descendants(trend, tag="p", class_name="chart-note"),
    }
    assert len(notes["composition"]) == len(notes["trend"]) == 1
    assert "원문 비율을 소수 첫째 자리로 반올림해 표시" in notes[
        "composition"
    ][0].text
    assert "원값을 억원 단위로 환산해 표시" in notes["trend"][0].text
    for name, source_number in (("composition", "1"), ("trend", "2")):
        links = _descendants(notes[name][0], tag="a", class_name="ref")
        assert len(links) == 1
        assert links[0].attrs.get("href") == f"#src{source_number}"
        assert links[0].attrs.get("title") == f"출처 {source_number}번"
        assert links[0].text == source_number

    styled = [
        element
        for figure in (composition, trend)
        for element in _descendants(figure)
        if "style" in element.attrs
    ]
    assert len(styled) == 10  # 구성 4개 + 추세 막대 6개
    assert sum("composition-segment" in element.classes for element in styled) == 4
    assert sum("trend-bar" in element.classes for element in styled) == 6
    for element in styled:
        style = element.attrs["style"]
        matched = _STYLE_RATIO.fullmatch(style)
        assert matched is not None, style
        ratio = float(matched.group("ratio"))
        assert 0.0 <= ratio <= 100.0
        assert not re.search(r"(?i)(?:nan|inf|[+\-]|e\d)", style)
