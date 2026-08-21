"""관리·중단 화면의 실제 렌더 HTML 접근성 관계를 검증한다."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.observability import constants as obs
from src.features.observability.records import RunRecord, append_record
from src.features.pipeline.demo import DemoPipeline
from src.features.pipeline.port import (
    CompanyCard,
    Outcome,
    RunResult,
    SourceStatus,
    UserInput,
)
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import store as share_store
from src.features.storage import db as storage_db
from src.web import job_runtime, main, runtime
from src.web.recording import records_path


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
    def text(self) -> str:
        pieces = [*self.text_parts, *(child.text for child in self.children)]
        return re.sub(r"\s+", " ", " ".join(pieces)).strip()


class _RenderedDOM(HTMLParser):
    """외부 parser 의존성 없이 실제 응답의 요소 관계를 보존한다."""

    def __init__(self, html: str) -> None:
        super().__init__()
        self.root = _Element("#document", {})
        self.stack = [self.root]
        self.elements: list[_Element] = []
        self.feed(html)

    def handle_starttag(self, tag: str, attrs) -> None:
        element = _Element(tag, {key: value or "" for key, value in attrs}, self.stack[-1])
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


def _descendants(element: _Element, tag: str) -> list[_Element]:
    found: list[_Element] = []
    for child in element.children:
        if child.tag == tag:
            found.append(child)
        found.extend(_descendants(child, tag))
    return found


def _ancestor(element: _Element, *, role: str) -> _Element | None:
    parent = element.parent
    while parent is not None:
        if parent.attrs.get("role") == role:
            return parent
        parent = parent.parent
    return None


def _record() -> RunRecord:
    return RunRecord(
        run_id="a11y-row",
        at=dt.datetime.now().isoformat(timespec="seconds"),
        corp_type=obs.CORP_TYPE_UNKNOWN,
        job="영업",
        end_step=obs.END_STEP_IDENTIFY,
        cache_hit=obs.CACHE_HIT_NONE,
        fragments_collected=0,
        fragments_cited=0,
        sentences_made=0,
        sentences_passed=0,
        cells_filled=0,
        cells_missing=[],
        cells_suspect=[],
        grade=obs.GRADE_NONE,
        human_check=obs.HUMAN_CHECK_NONE,
        cost_krw=0.0,
        elapsed_sec=0.1,
        model="a11y-test",
    )


def _render_admin_and_stopped_pages() -> tuple[str, dict[str, str]]:
    runtime._PIPELINE = DemoPipeline()
    link_key = "a" * 32
    stopped_id = "b" * 32

    with TestClient(main.app) as client:
        session = auth_logic.create_session("admin@example.com", True)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)

        empty_access = client.get("/admin/access")
        assert empty_access.status_code == 200

        with storage_db.connect() as conn:
            share_store.insert_new(
                conn,
                key=link_key,
                company="카카오",
                job="마케팅",
                now_iso="2026-08-18T10:00:00",
            )
            share_allow.invite(
                conn,
                email="friend@example.com",
                note="접근성 시험",
                now_iso="2026-08-18T10:00:00",
            )
        append_record(_record(), records_path())

        job_runtime._JOBS[stopped_id] = job_runtime.Job(
            job_id=stopped_id,
            user_input=UserInput("우리엔", "영업", "서울", "공고"),
            card=CompanyCard("우리엔", "우리엔", "서울", "", ""),
            finished=True,
            result=RunResult(
                outcome=Outcome.GATE_STOPPED,
                message="저장된 근거가 부족합니다.",
                sources=[SourceStatus("전자공시", "none", "자료 없음")],
            ),
        )

        responses = {
            "admin-home": client.get("/admin"),
            "admin-access": client.get("/admin/access"),
            "admin-link": client.get(f"/admin/link/{link_key}"),
            "admin-dashboard": client.get("/admin/dashboard"),
            "stopped": client.get(f"/result/{stopped_id}"),
        }

    assert all(response.status_code == 200 for response in responses.values())
    return empty_access.text, {name: response.text for name, response in responses.items()}


def test_관리표는_이름_머리글_scope_키보드_scroll영역을_가진다():
    empty_access, pages = _render_admin_and_stopped_pages()

    # 조건부 표가 없을 때도 두 영역의 제목과 정직한 빈 상태가 렌더된다.
    empty_dom = _RenderedDOM(empty_access)
    assert empty_dom.all("table") == []
    assert empty_dom.by_id("company-links-title") is not None
    assert empty_dom.by_id("invited-members-title") is not None
    assert "아직 만든 링크가 없습니다" in empty_dom.root.text
    assert "아직 초대한 사람이 없습니다" in empty_dom.root.text

    expected_table_counts = {
        "admin-access": 2,
        "admin-link": 1,
        "admin-dashboard": 2,
        "stopped": 1,
    }
    for page_name, expected_count in expected_table_counts.items():
        dom = _RenderedDOM(pages[page_name])
        tables = dom.all("table")
        assert len(tables) == expected_count, page_name

        for table in tables:
            labelledby = table.attrs.get("aria-labelledby", "")
            captions = _descendants(table, "caption")
            assert labelledby or captions, page_name
            if labelledby:
                label = dom.by_id(labelledby)
                assert label is not None and label.text, (page_name, labelledby)

            headers = _descendants(table, "th")
            assert headers, page_name
            assert all(header.attrs.get("scope") in {"col", "row"} for header in headers)

            region = _ancestor(table, role="region")
            assert region is not None, page_name
            assert region.attrs.get("tabindex") == "0", page_name
            region_labelledby = region.attrs.get("aria-labelledby", "")
            assert region_labelledby, page_name
            region_label = dom.by_id(region_labelledby)
            assert region_label is not None and region_label.text, page_name


def test_관리화면은_페이지목적_h1_하나와_연속_heading계층을_가진다():
    _empty_access, pages = _render_admin_and_stopped_pages()
    expected_h1 = {
        "admin-home": "관리자 화면",
        "admin-access": "초대·회사 링크 관리",
        "admin-link": "카카오",
        "admin-dashboard": "품질 대시보드",
    }

    for page_name, page_h1 in expected_h1.items():
        dom = _RenderedDOM(pages[page_name])
        headings = [
            element
            for element in dom.elements
            if re.fullmatch(r"h[1-6]", element.tag)
        ]
        h1s = [heading for heading in headings if heading.tag == "h1"]

        assert headings and headings[0].tag == "h1", page_name
        assert len(h1s) == 1, page_name
        assert page_h1 in h1s[0].text, page_name

        levels = [int(heading.tag[1]) for heading in headings]
        assert all(current - previous <= 1 for previous, current in zip(levels, levels[1:])), (
            page_name,
            levels,
        )
