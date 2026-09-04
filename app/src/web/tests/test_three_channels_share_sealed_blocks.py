"""웹·PDF·노션 세 채널이 «같은 봉인 블록»의 같은 글자를 낸다.

★ 왜 이 파일이 따로 필요한가
  PDF·웹·노션 채널 작업은 각각 자기 채널 하나만 봤다. 채널마다
  「봉인 값을 썼다」는 시험은 있었지만, **같은 저장본 하나를 세 채널에 동시에
  통과시켜 글자를 맞대 본 시험이 없었다.** 그래서 한 채널만 조용히 빠져도
  아무 시험이 깨지지 않았다 — 3개년 «변화 요약» 띠 제목이 웹에서는
  ``aria-label`` 속성에만 있고 눈에 보이는 글자로는 없던 결함이
  그렇게 살아남았다.

★ 이 파일이 지키는 것
  ① 3개년 띠 제목(``display.period_summary.title``)이 웹·PDF·노션 **모두에서**
     사람이 읽는 글자로 나온다. 속성값·대체텍스트는 세지 않는다.
  ② 부록 「사실 검증」 열 라벨과 부분 보고서 고지 문장이 세 채널에 다 나온다.
  ③ 감사 장부(fact_id·subject_scope)만 바꾼 저장본은 ``display_sha256``이 같고
     ``content_sha256``만 다르며, 세 채널의 글자가 각각 그대로다(불변식 I7).

★ 채널 고유 «모양»은 허용한다. 웹은 인용 번호를 위첨자
  링크로, PDF·노션은 ``[n]``·``〔n〕`` 평문으로 그린다. 그래서 이 시험은
  블록 값을 **부분 문자열**로 찾고, 공백을 걷어낸 뒤 비교한다.

★ 실제 AI·네트워크를 쓰지 않는다. 보고서는 결정론적 재료로 만든다.
"""

from __future__ import annotations

import io
import re
from types import SimpleNamespace
from typing import Any

import pdfplumber
import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.export_notion import logic as notion_logic
from src.features.export_pdf import logic as pdf_logic

# ★ 시험 재료를 다시 짓지 않고 PDF 시험이 쓰는 «그 저장본»을 그대로 가져온다.
#   재료가 갈라지면 「세 채널이 같은 블록을 쓴다」를 확인할 수 없다.
from src.features.export_pdf.tests.test_v2_public_projection import (
    _PRIVATE_SCOPE,
    _v2_full_report,
)
from src.features.pipeline.port import Report
from src.shared.report_generation.public_projection import build_report_digest
from src.web import job_runtime
from src.web import main as web_main
from src.web.routers import reports as reports_router
from src.web.tests._visible_text import visible_text


#: 부록 자료 표의 「사실 검증」 열 이름. 세 채널이 같은 글자를 써야 한다.
#: ★ 생산 상수를 import하지 않고 **리터럴**로 적는다 — 상수를 같이 읽으면
#:   라벨이 바뀌어도 시험이 함께 움직여 아무것도 못 잡는다.
_FACT_VERIFICATION_LABEL = "사실 검증"

#: 장부만 바꾼 두 번째 저장본이 쓰는 비공개 감사 문자열.
_OTHER_PRIVATE_SCOPE = "또 다른 비공개 감사 문자열"

#: 화면 본문(표지·아홉 장·부록)의 시작과 끝. 화면 장식은 본문이 아니다.
_ARTICLE_START = '<article class="report-paper">'
_ARTICLE_END = '<aside class="report-support ui-only"'


# ══════════════════════════════════════════════════════════
# 세 채널에서 «사람이 읽는 글자»만 꺼내는 도구
# ══════════════════════════════════════════════════════════


def _squeezed(value: str) -> str:
    """줄바꿈·자간 공백을 지운 글자만 남긴다.

    PDF 글자 추출은 줄 끝에서 문장을 자르고 표 칸 사이에 공백을 넣는다. 웹은
    태그 자리에 공백이 생긴다. 세 채널의 «글자»를 맞대려면 공백을 걷어내야 한다.
    """

    return re.sub(r"\s+", "", value)


def _render_from_stored_delivery(
    report: Report, monkeypatch: pytest.MonkeyPatch, *, report_id: str
) -> str:
    """영속 Delivery 갈래로 결과 화면을 그린다 — 지금 만든 보고서가 가는 길.

    ★ legacy snapshot 갈래는 화면을 ``legacy_readonly=True``로 그려 부분 보고서
      고지가 통째로 꺼진다. 고지를 보는 시험이 그 갈래로 그리면
      「없는 게 맞는 화면」을 「고지가 사라졌다」로 잘못 읽는다.
    ★ 만료 판정은 전용 라우트 시험이 소유한다 — 여기서는 만료 아님으로 고정한다.
    """

    stored = SimpleNamespace(delivery=SimpleNamespace(), report=report)
    monkeypatch.setattr(
        reports_router, "_stored_public_delivery", lambda _public_id: stored
    )
    monkeypatch.setattr(reports_router, "_delivery_is_expired", lambda _delivery: False)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    job_runtime._JOBS.pop(report_id, None)
    session = auth_logic.create_session("admin@example.com", True)
    with TestClient(web_main.app) as client:
        response = client.get(
            f"/result/{report_id}",
            cookies={auth_constants.SESSION_COOKIE_NAME: session.token},
        )
    assert response.status_code == 200, response.text[:500]
    assert "PDF 원본 확인 불가" not in response.text, "legacy 갈래로 샜다"
    return response.text


def _web_page_text(body: str) -> str:
    """페이지 전체에서 사람이 읽는 글자만 남긴다.

    ★ ``visible_text``는 script·style을 지우고 **태그를 통째로** 없앤다. 그래서
      ``aria-label``·``title`` 같은 속성값은 여기 남지 않는다 — 이 시험이
      「보이는 글자」와 「대체 텍스트」를 갈라내는 지점이 바로 여기다.
    """

    return _squeezed(visible_text(body))


def _web_article_text(body: str) -> str:
    """보고서 본문(표지·장·부록)만 잘라 낸 뒤 보이는 글자를 남긴다."""

    article = body[body.index(_ARTICLE_START) : body.index(_ARTICLE_END)]
    return _squeezed(visible_text(article))


def _pdf_text(report: Report) -> str:
    """실제 PDF를 만들어 페이지에서 글자를 추출한다."""

    pdf_bytes = pdf_logic.build_pdf(report)
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as document:
        pages = [page.extract_text() or "" for page in document.pages]
    return _squeezed("\n".join(pages))


def _notion_plain_text(report: Report) -> str:
    """노션 블록에서 평문만 재귀로 모은다.

    문단·헤딩은 ``rich_text[].text.content``에, 표는 ``table_row.cells[][]`` 안의
    같은 자리에 글자가 들어 있다. 블록 구조가 늘어나도 따라가도록 재귀로 훑되
    ``text.content``**만** 센다 — 링크 주소(``text.link.url``)는 글자가 아니다.
    """

    blocks = notion_logic.build_blocks(report)
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if (
                    key == "text"
                    and isinstance(value, dict)
                    and isinstance(value.get("content"), str)
                ):
                    parts.append(value["content"])
                    continue
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(blocks)
    return _squeezed("\n".join(parts))


def _band_titles(report: Report) -> list[str]:
    """저장본이 봉인해 둔 3개년 띠 제목을 장 순서대로 모은다."""

    projection = report.public_projection
    assert projection is not None, "재료에 봉인이 없다 — 시험이 무의미해진다"
    return [
        block.display.period_summary.title
        for block in projection.sections
        if block.display.period_summary is not None
        and block.display.period_summary.items
    ]


def _missing_by_channel(
    needles: list[str], channels: dict[str, str]
) -> dict[str, list[str]]:
    """채널마다 «빠진 글자»를 모은다 — 실패 메시지가 어느 채널인지 말하게."""

    return {
        channel: [needle for needle in needles if _squeezed(needle) not in text]
        for channel, text in channels.items()
    }


# ══════════════════════════════════════════════════════════
# ① 3개년 띠 제목 — 세 채널 모두에서 «보이는 글자»여야 한다
# ══════════════════════════════════════════════════════════


def test_3개년_띠_제목은_웹_PDF_노션에_모두_보인다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PDF·노션은 굵은 글씨로 찍는 제목을 웹만 속성에 숨겨 두면 안 된다.

    ★ 수정 전 실측 — 웹만 FAILED. 화면 HTML은 제목을 ``aria-label`` 속성에만
      담아, 태그를 걷어낸 «보이는 글자»에는 한 글자도 남지 않았다.
    """

    report = _v2_full_report()
    titles = _band_titles(report)
    assert titles, "재료가 3개년 띠를 만들지 못했다 — 시험이 무의미해진다"

    body = _render_from_stored_delivery(
        report, monkeypatch, report_id="f1q-band-title"
    )
    missing = _missing_by_channel(
        titles,
        {
            "웹": _web_article_text(body),
            "PDF": _pdf_text(report),
            "노션": _notion_plain_text(report),
        },
    )

    assert not any(missing.values()), f"띠 제목이 안 보이는 채널: {missing}"


# ══════════════════════════════════════════════════════════
# ② 부록 라벨·부분 보고서 고지 — 세 채널이 같은 글자를 쓴다
# ══════════════════════════════════════════════════════════


def test_부록_사실검증_라벨과_고지_문장은_세_채널이_같다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """열 이름과 고지 문장은 채널마다 사본을 들면 조용히 갈라진다."""

    report = _v2_full_report()
    projection = report.public_projection
    assert projection is not None
    notice_title, notice_detail = projection.grade_notice
    assert (
        notice_title and notice_detail
    ), "재료가 부분 보고서가 아니다 — 고지 시험이 무의미해진다"
    assert projection.citations, "재료에 부록 행이 없다 — 라벨 시험이 무의미해진다"

    body = _render_from_stored_delivery(
        report, monkeypatch, report_id="f1q-shared-copy"
    )
    # ★ 고지는 화면 장식(`ui-only`)이라 본문 `<article>` 밖에 있다. 그래서
    #   이 시험만 페이지 전체의 보이는 글자를 본다 — 속성값은 여전히 안 센다.
    missing = _missing_by_channel(
        [_FACT_VERIFICATION_LABEL, notice_title, notice_detail],
        {
            "웹": _web_page_text(body),
            "PDF": _pdf_text(report),
            "노션": _notion_plain_text(report),
        },
    )

    assert not any(missing.values()), f"채널별 누락 문구: {missing}"


# ══════════════════════════════════════════════════════════
# ③ 장부만 바꾼 저장본 — 세 채널 글자가 그대로다
# ══════════════════════════════════════════════════════════


def test_장부만_바꾼_저장본은_세_채널_글자가_같다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """감사 장부는 지문에만 남고 화면·PDF·노션 글자에는 한 글자도 안 나온다."""

    original = _v2_full_report(suffix="1", scope=_PRIVATE_SCOPE)
    changed = _v2_full_report(suffix="2", scope=_OTHER_PRIVATE_SCOPE)
    assert original.public_projection is not None
    assert changed.public_projection is not None

    original_seal = build_report_digest(original.public_projection)
    changed_seal = build_report_digest(changed.public_projection)
    assert original_seal.display_sha256 == changed_seal.display_sha256
    assert original_seal.content_sha256 != changed_seal.content_sha256

    # ★ 두 저장본을 «같은 report_id»로 그린다 — 주소에서 나오는 글자까지
    #   똑같이 두어, 차이가 나면 그 원인이 오직 장부뿐이게 만든다.
    original_body = _render_from_stored_delivery(
        original, monkeypatch, report_id="f1q-ledger"
    )
    changed_body = _render_from_stored_delivery(
        changed, monkeypatch, report_id="f1q-ledger"
    )

    assert _web_article_text(original_body) == _web_article_text(changed_body)
    assert _pdf_text(original) == _pdf_text(changed)
    assert _notion_plain_text(original) == _notion_plain_text(changed)

    for scope, page in (
        (_PRIVATE_SCOPE, _web_page_text(original_body)),
        (_OTHER_PRIVATE_SCOPE, _web_page_text(changed_body)),
    ):
        assert _squeezed(scope) not in page
    assert _squeezed(_PRIVATE_SCOPE) not in _pdf_text(original)
    assert _squeezed(_PRIVATE_SCOPE) not in _notion_plain_text(original)
    assert _squeezed(_OTHER_PRIVATE_SCOPE) not in _pdf_text(changed)
    assert _squeezed(_OTHER_PRIVATE_SCOPE) not in _notion_plain_text(changed)
