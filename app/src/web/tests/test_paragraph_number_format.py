"""화면(result.html)의 문단 번호 형식을 못 박는다.

★ 왜 이 파일이 생겼나
  ─────────────────────────────────────────────────────────
  문단 번호를 «장번호-문단번호»(2-1)에서 «문단번호만»(1.)로 바꿨다.
  이유: 이미 「2. 사업 구조와 수익 모델」이라는 장 제목 아래에 있으므로
  장 번호를 문단마다 되풀이할 이유가 없다.

  그런데 착수 전에 세어 보니 **웹 쪽 문단 번호를 지켜 주는 시험이 0개**였다.
  PDF 쪽만 `export_pdf/tests/test_paragraph_numbers.py`가 지키고 있었고, 웹은
  아무도 안 지켰다. 그래서 「웹만 바꾸고 PDF를 안 바꿔서 어긋난」 v2-32→v2-36
  사고가 다시 나도 시험이 못 잡는다.

  ★ 이 파일이 그 짝이다. 웹·PDF **둘 다** 있어야 「3번 문단 보세요」가 성립한다.
    한쪽만 고치면 다른 쪽 시험이 빨간불이 되도록 두 파일이 같은 형식을 못 박는다.

★ 왜 «문자열 비교»가 아니라 «찍힌 HTML»을 보나
  ─────────────────────────────────────────────────────────
  템플릿 원문을 grep하면 Jinja 표현식(`{{ loop.index }}`)만 보이고, 그것이 실제로
  무엇을 찍는지는 안 보인다. 실제 요청을 보내 «찍힌 결과»를 봐야 한다.
  ★ 이 프로젝트는 오늘 «시험이 전부 초록불인데 실물이 망가진» 사고를 두 번 겪었다.
    원인은 둘 다 「손으로 지은 값만 봐서 실물 모양을 재현하지 못한 것」이었다.

★ 네트워크·과금 없음 — 저장된 보고서를 읽는 자리를 가짜로 바꿔 끼운다.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Final

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.pipeline.port import Report
from src.web import job_runtime
from src.web.main import app
from src.web.routers import reports as reports_router
from src.web.tests.report_route_support import serve_legacy_report_snapshot

# 출고 검증(validate_v2 — 내부 키·인용-부록 1:1·요약 문장 수)을 통과하는 v2 보고서를
# 만드는 방법은 v2 화면 시험이 이미 갖고 있다. 같은 것을 두 벌 만들면 한쪽만
# 고쳐져 조용히 어긋나므로 그대로 가져다 쓴다.
# ★ 직접 손으로 지은 Report를 넘겼더니 화면이 409(출고 검증 실패)를 돌려줬다
#   (실측) — 문단 번호를 보기도 전에 막힌다.
from src.web.tests.test_reports_v2_output import _v2_report

#: 화면에 찍힌 문단 번호를 뽑는 그물. `result.html`의 `.pno` span 그대로다.
_PNO_RE: Final[re.Pattern[str]] = re.compile(
    r'<span class="pno" aria-hidden="true">([^<]*)</span>'
)

#: 문단 번호를 세기 위한 문단 3개. 글 내용은 번호와 무관하다.
_PARAGRAPHS: Final[tuple[str, ...]] = (
    "첫 번째 문단입니다.",
    "두 번째 문단입니다.",
    "세 번째 문단입니다.",
)

_STYLE_PATH: Final[Path] = Path(__file__).parents[1] / "static" / "style.css"


def _v2_report_with_paragraphs(*, display_number: str = "2") -> Report:
    """문단이 «정확히 3개»인 v2 보고서 하나.

    ★ 왜 ``prose_paragraphs``만 갈아 끼우나 — 이 값은 «표시용 묶음»이라
      출고 검증이 보는 대상(``prose_lines``·인용·요약)이 아니다
      (`pipeline/port.py:220-225`). 그래서 갈아 끼워도 검증은 그대로 통과하고,
      문단 개수는 시험이 원하는 대로 고정된다.
    ★ 문단이 있는 장 «하나만» 남긴다. 다른 장에도 문단이 있으면 번호가 섞여
      「1. 2. 3. 1. 2.」처럼 나와 세기 어렵다.
    """
    base = _v2_report()
    target = next(
        section for section in base.sections if section.prose_lines
    )
    return replace(
        base,
        sections=[
            replace(
                target,
                display_number=display_number,
                prose_paragraphs=list(_PARAGRAPHS),
            )
        ],
    )


def _render(monkeypatch: pytest.MonkeyPatch, report: Report) -> str:
    """저장 보고서 화면을 실제로 그려 HTML을 돌려준다."""
    job_id = f"pno-{uuid.uuid4().hex}"
    job_runtime._JOBS.pop(job_id, None)
    serve_legacy_report_snapshot(monkeypatch, report, report_id=job_id)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    # PDF 자동출고 해시 결속은 이 시험의 관심사가 아니다 — 화면 렌더만 격리한다.
    monkeypatch.setattr(
        reports_router, "_release_state", lambda **_kwargs: (object(), None)
    )
    session = auth_logic.create_session("admin@example.com", True)
    with TestClient(app) as client:
        response = client.get(
            f"/result/{job_id}",
            cookies={auth_constants.SESSION_COOKIE_NAME: session.token},
        )
    assert response.status_code == 200, response.status_code
    return response.text


# ══════════════════════════════════════════════════════════
# ① 형식 — «1.» «2.» «3.» 이고 장번호는 안 붙는다
# ══════════════════════════════════════════════════════════


def test_문단_번호는_장번호_없이_순번과_마침표다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _render(monkeypatch, _v2_report_with_paragraphs())

    assert _PNO_RE.findall(body) == ["1.", "2.", "3."]


def test_옛_장번호_문단번호_형식이_되살아나면_빨간불(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 되돌아가기 방지 못.

    옛 형식은 `{{ s.display_number or loop.index0 }}-{{ loop.index }}` 였다.
    `display_number="2"`인 이 보고서에서 그 표현식은 「2-1」을 찍는다.
    """
    body = _render(monkeypatch, _v2_report_with_paragraphs(display_number="2"))

    numbers = _PNO_RE.findall(body)
    assert not any("-" in number for number in numbers), (
        f"문단 번호에 장번호가 다시 붙었습니다: {numbers}"
    )


# ══════════════════════════════════════════════════════════
# ② display_number가 비어도 번호가 흔들리지 않는다
# ══════════════════════════════════════════════════════════


def test_display_number가_비어도_문단_번호는_같다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 옛 quirk가 사라졌다는 것을 못으로 박는다.

    전에는 ``display_number``가 비면 Jinja의 ``loop.index0``가 «바깥 장 루프»가
    아니라 «이 문단 루프»를 가리켜서 「0-1」「1-2」라는 이상한 번호가 나왔고,
    PDF도 웹과 갈리지 않으려고 그 이상한 동작을 그대로 흉내 내야 했다.
    이제 장번호를 아예 안 쓰므로 그 자리가 없어졌다.
    """
    body = _render(monkeypatch, _v2_report_with_paragraphs(display_number=""))

    assert _PNO_RE.findall(body) == ["1.", "2.", "3."]


# ══════════════════════════════════════════════════════════
# ③ 웹과 PDF가 «같은 형식»이다 (한쪽만 고치는 것을 막는 못)
# ══════════════════════════════════════════════════════════


def test_웹과_PDF가_같은_문단_번호_형식을_쓴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ v2-32에서 웹에만 번호를 넣었다가 v2-36에서 PDF를 맞춘 이력이 있다.

    「3번 문단 보세요」는 웹과 PDF가 같은 번호를 쓸 때만 성립한다. 그래서 두
    구현이 같은 글자를 내는지 여기서 직접 맞대 본다 — 사람이 두 파일을 눈으로
    비교하는 방식은 지난번에 실패했다.
    """
    from src.features.export_pdf.logic import _paragraph_number_markup

    body = _render(monkeypatch, _v2_report_with_paragraphs())
    web_numbers = _PNO_RE.findall(body)

    # PDF 쪽은 마크업 안에 번호를 넣는다 — 글자만 뽑아 비교한다.
    pdf_numbers = [
        re.sub(r"<[^>]+>|&#160;", "", _paragraph_number_markup(position)).strip()
        for position in (1, 2, 3)
    ]

    assert web_numbers == pdf_numbers, (
        f"웹({web_numbers})과 PDF({pdf_numbers})의 문단 번호 형식이 갈렸습니다"
    )


def test_웹_번호와_본문은_모든_폭에서_같은_두_열에_놓인다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """화면 폭에 따라 inline/absolute로 갈리면 번호와 줄바꿈 시작점도 갈린다."""

    body = _render(monkeypatch, _v2_report_with_paragraphs())
    css = _STYLE_PATH.read_text(encoding="utf-8")

    assert '<span class="prose-text">' in body
    assert ".result-page .prose {\n  display: grid;" in css
    assert "grid-template-columns: 2.6em minmax(0, 1fr);" in css
    pno_rules = "\n".join(
        block.split("}", 1)[0]
        for block in css.split(".result-page .pno {")[1:]
    )
    assert "position: absolute" not in pno_rules
