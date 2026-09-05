from __future__ import annotations

import hashlib
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

from src.features.export_pdf.constants import (
    FONT_REGULAR_PATH,
    PAGE_BOTTOM_MARGIN_PT,
    PAGE_TOP_MARGIN_PT,
)
from src.features.export_pdf import release as pdf_release
from src.features.export_pdf.release import (
    ApprovalDecision,
    PDFReleaseBlockedError,
    PdfReleaseApproval,
    PdfReleaseCandidate,
    RenderedPdfPage,
    prepare_pdf_bytes,
    release_pdf,
    release_record_sha256,
)

_AT = "2026-08-19T21:30:00+09:00"
_FACT_IDS = ("fact-1", "fact-2")
_FACT_REVIEWER = "user:" + "1" * 20
_EDITORIAL_REVIEWER = "user:" + "2" * 20
_VISUAL_REVIEWER = "user:" + "3" * 20


def test_PDFium렌더는_동시보고서에서도_한프로세스에_한번만_실행된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows PDFium page close의 네이티브 동시성 중단을 회귀로 막는다."""

    first_started = threading.Event()
    allow_finish = threading.Event()
    state_lock = threading.Lock()
    constructor_calls = 0
    active_documents = 0
    max_active_documents = 0

    class FakeDocument:
        def __init__(self, _pdf_bytes: bytes) -> None:
            nonlocal constructor_calls, active_documents, max_active_documents
            with state_lock:
                constructor_calls += 1
                active_documents += 1
                max_active_documents = max(max_active_documents, active_documents)
            first_started.set()
            assert allow_finish.wait(timeout=3)

        def __len__(self) -> int:
            return 0

        def close(self) -> None:
            nonlocal active_documents
            with state_lock:
                active_documents -= 1

    monkeypatch.setattr(pdf_release.pdfium, "PdfDocument", FakeDocument)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(pdf_release._render_all_pages, b"first", scale=1.0)
        assert first_started.wait(timeout=2)
        second = pool.submit(pdf_release._render_all_pages, b"second", scale=1.0)
        # 두 번째 생성자까지 들어갔다면 이미 PDFium 호출이 겹친 것이다.
        assert constructor_calls == 1
        allow_finish.set()
        assert first.result(timeout=3) == ()
        assert second.result(timeout=3) == ()

    assert constructor_calls == 2
    assert max_active_documents == 1


def test_PDFium렌더잠금이_고장나도_뒤보고서를_영원히기다리게하지않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pdf_release, "PDFIUM_RENDER_LOCK_TIMEOUT_SEC", 0.01)
    assert pdf_release._PDFIUM_RENDER_LOCK.acquire(timeout=0.1)
    try:
        with pytest.raises(PDFReleaseBlockedError, match="제한 시간"):
            pdf_release._render_all_pages(b"not-opened", scale=1.0)
    finally:
        pdf_release._PDFIUM_RENDER_LOCK.release()


def _two_page_pdf() -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, invariant=1)
    canvas.drawString(72, 720, "page one")
    canvas.showPage()
    canvas.drawString(72, 720, "page two")
    canvas.showPage()
    canvas.save()
    return output.getvalue()


def _different_two_page_pdf() -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, invariant=1)
    canvas.drawString(72, 720, "different first page")
    canvas.showPage()
    canvas.drawString(72, 720, "different second page")
    canvas.showPage()
    canvas.save()
    return output.getvalue()


def _blank_one_page_pdf() -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, invariant=1)
    canvas.showPage()
    canvas.save()
    return output.getvalue()


def _visually_blank_one_page_pdf() -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, invariant=1)
    canvas.setFillColorRGB(1, 1, 1)
    canvas.drawString(72, 720, "INVISIBLE WHITE TEXT")
    canvas.showPage()
    canvas.save()
    return output.getvalue()


def _missing_glyph_one_page_pdf() -> bytes:
    font_name = "MissingGlyphProbe"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(font_name, str(FONT_REGULAR_PATH)))
    output = io.BytesIO()
    canvas = Canvas(output, invariant=1)
    canvas.setFont(font_name, 16)
    canvas.drawString(72, 720, "ABC\u5229DEF")
    canvas.showPage()
    canvas.save()
    return output.getvalue()


def _white_body_with_gray_footer_pdf() -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, invariant=1)
    canvas.setFillColorRGB(1, 1, 1)
    canvas.drawString(72, 650, "INVISIBLE WHITE BODY TEXT MUST NOT SHIP")
    canvas.drawString(72, 620, "MORE INVISIBLE BODY TEXT")
    canvas.setFillColorRGB(0.55, 0.55, 0.55)
    canvas.drawCentredString(300, 24, "1")
    canvas.showPage()
    canvas.save()
    return output.getvalue()


def _white_body_with_gray_header_and_footer_pdf() -> bytes:
    """실제 조립부의 furniture만 보이고 본문은 흰색인 실패 후보."""

    output = io.BytesIO()
    canvas = Canvas(output, pagesize=A4, invariant=1)
    canvas.setFillColorRGB(1, 1, 1)
    canvas.drawString(72, 650, "INVISIBLE WHITE BODY TEXT MUST NOT SHIP")
    canvas.setFillColorRGB(0.55, 0.55, 0.55)
    canvas.drawString(72, A4[1] - 34, "VISIBLE HEADER IS NOT BODY")
    canvas.drawCentredString(300, 24, "1")
    canvas.showPage()
    canvas.save()
    return output.getvalue()


def _body_at_canonical_frame_top_pdf() -> bytes:
    """표 뒤 새 쪽처럼 본문 frame 맨 위에서 시작하는 정상 후보."""

    output = io.BytesIO()
    canvas = Canvas(output, pagesize=A4, invariant=1)
    canvas.setFillColorRGB(0.55, 0.55, 0.55)
    canvas.drawString(72, A4[1] - 34, "VISIBLE HEADER IS NOT BODY")
    canvas.drawCentredString(300, 24, "1")
    canvas.setFillColorRGB(0, 0, 0)
    canvas.drawString(
        72,
        A4[1] - PAGE_TOP_MARGIN_PT - 8,
        "VISIBLE BODY STARTS AT THE CANONICAL FRAME TOP",
    )
    canvas.drawString(
        72,
        PAGE_BOTTOM_MARGIN_PT + 8,
        "VISIBLE BODY CAN REACH THE CANONICAL FRAME BOTTOM",
    )
    canvas.showPage()
    canvas.save()
    return output.getvalue()


def _valid_sparse_cover_pdf() -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, invariant=1)
    canvas.setFont("Helvetica-Bold", 24)
    canvas.drawCentredString(300, 470, "ACME")
    canvas.setFont("Helvetica", 15)
    canvas.drawCentredString(300, 438, "ANALYSIS REPORT")
    canvas.setFillColorRGB(0.55, 0.55, 0.55)
    canvas.drawCentredString(300, 24, "1")
    canvas.showPage()
    canvas.save()
    return output.getvalue()


def _white_page_evidence() -> RenderedPdfPage:
    output = io.BytesIO()
    image = Image.new("RGB", (306, 396), "white")
    image.save(output, format="PNG")
    png_bytes = output.getvalue()
    return RenderedPdfPage(
        number=1,
        png_bytes=png_bytes,
        png_sha256=hashlib.sha256(png_bytes).hexdigest(),
        width_px=306,
        height_px=396,
    )


def _approval(candidate, **overrides) -> PdfReleaseApproval:
    base = PdfReleaseApproval(
        pdf_sha256=candidate.pdf_sha256,
        page_png_sha256s=tuple(page.png_sha256 for page in candidate.pages),
        reviewed_pages=tuple(page.number for page in candidate.pages),
        reviewed_fact_ids=candidate.expected_fact_ids,
        fact_failed_count=0,
        fact=ApprovalDecision(True, _FACT_REVIEWER, _AT),
        editorial=ApprovalDecision(True, _EDITORIAL_REVIEWER, _AT),
        visual=ApprovalDecision(True, _VISUAL_REVIEWER, _AT),
        visual_review_kind="human",
    )
    return replace(base, **overrides)


def test_모든_PDF페이지를_실제_PNG로_렌더하고_hash를_남긴다():
    candidate = prepare_pdf_bytes(
        _two_page_pdf(), render_scale=0.75, expected_fact_ids=_FACT_IDS
    )

    assert candidate.page_count == 2
    assert len(candidate.pdf_sha256) == 64
    assert [page.number for page in candidate.pages] == [1, 2]
    for page in candidate.pages:
        assert page.png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(page.png_sha256) == 64
        assert page.width_px > 0 and page.height_px > 0


def test_승인없는_PDF후보는_출고하지_않는다():
    candidate = prepare_pdf_bytes(
        _two_page_pdf(), render_scale=0.5, expected_fact_ids=_FACT_IDS
    )

    with pytest.raises(PDFReleaseBlockedError, match="승인이 없습니다"):
        release_pdf(candidate, None, released_at=_AT)


@pytest.mark.parametrize(
    "override",
    (
        {"pdf_sha256": "0" * 64},
        {"page_png_sha256s": ("0" * 64, "1" * 64)},
        {"reviewed_pages": (1,)},
        {"reviewed_pages": (True, 2)},
        {"visual_review_kind": "automatic_pixel_check"},
        {"visual_review_kind": "independent_agent"},
        {"fact": ApprovalDecision(False, "user:" + "4" * 20, _AT)},
        {"reviewed_fact_ids": ("fact-1",)},
        {"fact_failed_count": 1},
        {"editorial": ApprovalDecision(True, _FACT_REVIEWER, _AT)},
    ),
)
def test_hash_전페이지_3종승인중_하나라도_다르면_fail_closed한다(override):
    candidate = prepare_pdf_bytes(
        _two_page_pdf(), render_scale=0.5, expected_fact_ids=_FACT_IDS
    )

    with pytest.raises(PDFReleaseBlockedError):
        release_pdf(candidate, _approval(candidate, **override), released_at=_AT)


def test_같은_PDF와_전페이지에_결박된_명시적_승인만_출고기록을_만든다():
    content = _two_page_pdf()
    candidate = prepare_pdf_bytes(
        content, render_scale=0.5, expected_fact_ids=_FACT_IDS
    )
    released = release_pdf(candidate, _approval(candidate), released_at=_AT)

    assert released.content == content
    assert released.record.pdf_sha256 == candidate.pdf_sha256
    assert released.record.page_count == 2
    assert released.record.page_png_sha256s == tuple(
        page.png_sha256 for page in candidate.pages
    )
    assert released.record.expected_fact_ids == _FACT_IDS
    assert released.record.reviewed_fact_ids == _FACT_IDS
    assert released.record.fact_failed_count == 0
    assert released.record.visual_review_kind == "human"
    assert len(released.record.record_sha256) == 64
    assert released.record.record_sha256 == release_record_sha256(released.record)


def test_출고시각은_모든_승인시각_이후여야_한다():
    candidate = prepare_pdf_bytes(
        _two_page_pdf(), render_scale=0.5, expected_fact_ids=_FACT_IDS
    )

    with pytest.raises(PDFReleaseBlockedError, match="무결성"):
        release_pdf(
            candidate,
            _approval(candidate),
            released_at="2026-08-19T21:29:59+09:00",
        )


@pytest.mark.parametrize(
    "bad_hash",
    ("A" * 64, "g" * 64, "0" * 63, "0" * 65),
)
def test_승인의_모든_hash는_소문자_16진수_64자리여야_한다(bad_hash: str):
    candidate = prepare_pdf_bytes(
        _two_page_pdf(), render_scale=0.5, expected_fact_ids=_FACT_IDS
    )

    with pytest.raises(PDFReleaseBlockedError):
        release_pdf(
            candidate,
            _approval(candidate, pdf_sha256=bad_hash),
            released_at=_AT,
        )
    with pytest.raises(PDFReleaseBlockedError):
        release_pdf(
            candidate,
            _approval(
                candidate,
                page_png_sha256s=(bad_hash, candidate.pages[1].png_sha256),
            ),
            released_at=_AT,
        )


@pytest.mark.parametrize(
    "reviewer",
    (
        "USER:" + "1" * 20,
        " " + _FACT_REVIEWER,
        _FACT_REVIEWER + " ",
        "ｕｓｅｒ:" + "1" * 20,
        "user：" + "1" * 20,
        "user:" + "1" * 19,
        "user:" + "g" * 20,
    ),
)
def test_검수자_ID의_대소문자_공백_Unicode_표현_우회를_거부한다(reviewer: str):
    candidate = prepare_pdf_bytes(
        _two_page_pdf(), render_scale=0.5, expected_fact_ids=_FACT_IDS
    )
    approval = _approval(
        candidate,
        fact=ApprovalDecision(True, reviewer, _AT),
    )

    with pytest.raises(PDFReleaseBlockedError):
        release_pdf(candidate, approval, released_at=_AT)


def test_release는_candidate의_PDF와_PNG_bytes_hash를_다시_계산한다():
    candidate = prepare_pdf_bytes(
        _two_page_pdf(), render_scale=0.5, expected_fact_ids=_FACT_IDS
    )
    approval = _approval(candidate)
    modified_pdf = replace(candidate, pdf_bytes=candidate.pdf_bytes + b"tampered")
    modified_page = replace(
        candidate.pages[0],
        png_bytes=candidate.pages[0].png_bytes + b"tampered",
    )
    modified_png = replace(
        candidate,
        pages=(modified_page, candidate.pages[1]),
    )

    with pytest.raises(PDFReleaseBlockedError, match="PDF bytes"):
        release_pdf(modified_pdf, approval, released_at=_AT)
    with pytest.raises(PDFReleaseBlockedError, match="PNG bytes"):
        release_pdf(modified_png, approval, released_at=_AT)


def test_release는_PDF_A와_PDF_B의_PNG를_섞은_hybrid_candidate를_거부한다():
    candidate_a = prepare_pdf_bytes(
        _two_page_pdf(), render_scale=0.5, expected_fact_ids=_FACT_IDS
    )
    candidate_b = prepare_pdf_bytes(
        _different_two_page_pdf(), render_scale=0.5, expected_fact_ids=_FACT_IDS
    )
    hybrid = replace(candidate_a, pages=candidate_b.pages)

    with pytest.raises(PDFReleaseBlockedError, match="출고 재렌더"):
        release_pdf(hybrid, _approval(hybrid), released_at=_AT)


def test_prepare를_우회해_조립한_빈페이지_candidate도_release에서_거부한다():
    pdf_bytes = _blank_one_page_pdf()
    candidate = PdfReleaseCandidate(
        pdf_bytes=pdf_bytes,
        pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
        pages=(_white_page_evidence(),),
        expected_fact_ids=("invented-fact",),
        render_scale=0.5,
    )

    with pytest.raises(PDFReleaseBlockedError, match="글자가 없는"):
        release_pdf(candidate, _approval(candidate), released_at=_AT)


def test_흰색글자만_있는_시각적_빈페이지를_prepare와_release에서_모두_거부한다():
    pdf_bytes = _visually_blank_one_page_pdf()

    with pytest.raises(PDFReleaseBlockedError, match="시각적으로 비어"):
        prepare_pdf_bytes(
            pdf_bytes,
            render_scale=0.5,
            expected_fact_ids=("invented-fact",),
        )

    candidate = PdfReleaseCandidate(
        pdf_bytes=pdf_bytes,
        pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
        pages=(_white_page_evidence(),),
        expected_fact_ids=("invented-fact",),
        render_scale=0.5,
    )
    with pytest.raises(PDFReleaseBlockedError, match="다시 렌더"):
        release_pdf(candidate, _approval(candidate), released_at=_AT)


def test_PDFium문자인덱스에_빈글자가_하나라도_있으면_누락글리프로_거부한다():
    with pytest.raises(PDFReleaseBlockedError, match="글리프가 없는 문자"):
        prepare_pdf_bytes(
            _missing_glyph_one_page_pdf(),
            render_scale=0.75,
            expected_fact_ids=("missing-glyph",),
        )


def test_흰본문을_회색쪽번호로_숨긴_PDF를_prepare와_release에서_모두_거부한다():
    pdf_bytes = _white_body_with_gray_footer_pdf()
    with pytest.raises(PDFReleaseBlockedError, match="본문 글자"):
        prepare_pdf_bytes(
            pdf_bytes,
            render_scale=0.75,
            expected_fact_ids=("invented-fact",),
        )

    # prepare를 건너뛰어 후보를 조립해도 release의 PDFium 재렌더가 같은 결함을 잡는다.
    visible_footer = prepare_pdf_bytes(
        _valid_sparse_cover_pdf(),
        render_scale=0.75,
        expected_fact_ids=("invented-fact",),
    ).pages[0]
    candidate = PdfReleaseCandidate(
        pdf_bytes=pdf_bytes,
        pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
        pages=(visible_footer,),
        expected_fact_ids=("invented-fact",),
        render_scale=0.75,
    )
    with pytest.raises(PDFReleaseBlockedError, match="다시 렌더"):
        release_pdf(candidate, _approval(candidate), released_at=_AT)


def test_머리말과_꼬리말만_보이는_PDF는_본문증거로_인정하지_않는다():
    with pytest.raises(PDFReleaseBlockedError, match="본문 글자"):
        prepare_pdf_bytes(
            _white_body_with_gray_header_and_footer_pdf(),
            render_scale=0.75,
            expected_fact_ids=("invented-fact",),
        )


def test_정본_frame_위아래의_본문은_머리말꼬리말로_오인하지_않는다():
    candidate = prepare_pdf_bytes(
        _body_at_canonical_frame_top_pdf(),
        render_scale=0.75,
        expected_fact_ids=("frame-fact",),
    )
    assert candidate.page_count == 1


def test_중앙제목이_보이는_성긴_표지는_본문가시성_검사로_오거부하지_않는다():
    candidate = prepare_pdf_bytes(
        _valid_sparse_cover_pdf(),
        render_scale=0.75,
        expected_fact_ids=("cover-fact",),
    )
    assert candidate.page_count == 1


def test_release는_페이지_연속성_PNG_magic_양의크기_실제크기와_개수를_검증한다():
    candidate = prepare_pdf_bytes(
        _two_page_pdf(), render_scale=0.5, expected_fact_ids=_FACT_IDS
    )
    approval = _approval(candidate)
    first = candidate.pages[0]
    bad_magic_bytes = b"not-png!" + first.png_bytes[8:]
    malformed_candidates = (
        replace(candidate, pages=(replace(first, number=2), candidate.pages[1])),
        replace(candidate, pages=(replace(first, number=True), candidate.pages[1])),
        replace(
            candidate,
            pages=(
                replace(
                    first,
                    png_bytes=bad_magic_bytes,
                    png_sha256=hashlib.sha256(bad_magic_bytes).hexdigest(),
                ),
                candidate.pages[1],
            ),
        ),
        replace(candidate, pages=(replace(first, width_px=0), candidate.pages[1])),
        replace(
            candidate,
            pages=(replace(first, width_px=first.width_px + 1), candidate.pages[1]),
        ),
        replace(candidate, pages=(first,)),
        replace(candidate, pages=()),
    )

    for malformed in malformed_candidates:
        with pytest.raises(PDFReleaseBlockedError):
            release_pdf(malformed, approval, released_at=_AT)
