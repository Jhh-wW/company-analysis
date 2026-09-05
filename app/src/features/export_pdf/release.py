"""PDF 후보 생성과 실제 출고 승인을 분리하는 fail-closed 계약.

``build_pdf``가 만든 bytes는 아직 검수 후보일 뿐이다. 이 모듈은 그 후보의 모든
페이지를 PNG로 렌더링하고 hash를 계산한다. 서로 다른 세 사람이 수행한 사실·편집·
전 페이지 시각 승인까지 *같은 PDF hash*에 결박된 경우에만
``release_pdf``가 출고 기록과 bytes를 돌려준다. 픽셀 렌더 성공 자체를 시각 승인으로
간주하지 않는다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import logging
import math
import re
import threading
import unicodedata
from dataclasses import asdict, dataclass
from typing import Final

import pypdfium2 as pdfium
from PIL import Image
from pypdf import PdfReader

from src.features.composer.render import ENGINE_V2_SCHEMA_VERSION
from src.features.composer.validate import validate_v2
from src.features.export_pdf.constants import (
    PAGE_BOTTOM_MARGIN_PT,
    PAGE_TOP_MARGIN_PT,
    PDFIUM_RENDER_LOCK_TIMEOUT_SEC,
)
from src.features.export_pdf.content_manifest import (
    CONTENT_MANIFEST_VERSION,
    PDF_MANIFEST_SHA256_KEY,
    PDF_MANIFEST_VERSION_KEY,
)
from src.shared.report_generation.public_projection import PUBLIC_PROJECTION_VERSION
from src.features.export_pdf.logic import PDFGenerationError, build_pdf
from src.features.pipeline.port import Report
from src.features.provenance.sources import visible_citations
from src.features.report_standard import build_published_report

logger = logging.getLogger(__name__)

#: 「PDF 만들기」가 실패했을 때 화면에 그대로 쓰는 «고정» 사유.
#: ★ 보고서 값을 절대 섞지 않는다 — 이 문구는 공개 화면에 그려진다.
RENDER_BLOCKED_REASON: Final[str] = "PDF 파일을 만드는 단계에서 멈췄습니다"
RENDER_BLOCKED_MESSAGE: Final[str] = "PDF 전 페이지 검수 재료를 만들지 못했습니다"

#: PDF 메타에 실릴 수 있는 «공개 내용 지문»의 버전 두 가지.
#:
#: ★ 봉인(``PublicReportProjection``)이 붙은 v2 FULL 보고서는
#:   ``PUBLIC_PROJECTION_VERSION``, 봉인이 없는 v1·옛 v2 저장본은
#:   ``CONTENT_MANIFEST_VERSION``을 싣는다. 여기서는 «아는 버전인가»만 본다 —
#:   어느 쪽이 맞는 값인지는 보고서를 들고 있는 자동출고 검사
#:   (``automatic_release.content_manifest_matches``)가 판정한다. 이 함수는
#:   보고서 없이 PDF bytes와 후보만 맞대는 자리라 그 판정을 할 수 없다.
KNOWN_CONTENT_MANIFEST_VERSIONS: Final[frozenset[str]] = frozenset(
    {CONTENT_MANIFEST_VERSION, PUBLIC_PROJECTION_VERSION}
)

PNG_MAGIC: Final[bytes] = b"\x89PNG\r\n\x1a\n"
SHA256_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
REVIEWER_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"user:[0-9a-f]{20}\Z",
    re.ASCII,
)
ALLOWED_VISUAL_REVIEW_KINDS: Final[frozenset[str]] = frozenset(
    {"human"}
)
_PDFIUM_RENDER_LOCK = threading.Lock()


class PDFReleaseBlockedError(RuntimeError):
    """승인 또는 전 페이지 렌더 증거가 없어 PDF 공개를 중단한다.

    ★ ``reasons``: 화면·로그에 그대로 써도 안전한 «고정» 문구만 담는다.
      ``str(self)``에는 보고서 값이 섞일 수 있어 화면에 쓰면 안 된다
      (``reports._gate_reasons``가 ``reasons``만 읽는 이유).

    ⚠️ 여기에 ``__init__``을 «만들지 마라». ``AutomaticGateStopped``는
      ``self.reasons``를 먼저 넣고 ``super().__init__``을 부르므로,
      부모가 ``__init__``에서 ``reasons``를 세팅하면 그 값을 덮어쓴다.
    """

    #: 사유를 안 실어 보내는 경우의 기본값(빈 튜플).
    reasons: tuple[str, ...] = ()


class PdfRenderBlockedError(PDFReleaseBlockedError):
    """「PDF 후보 만들기」 자체가 실패했다 — 자동검사(4종)는 돌지도 않았다.

    ★ 왜 «전용» 예외인가
      맨 ``PDFReleaseBlockedError`` 를 던지는 자리가 이 모듈에만 12곳이고
      대부분은 렌더 실패가 아니다(출고 승인 없음·장부 무결성 등).
      「사유가 없으면 렌더 실패」로 뭉뚱그리면 **화면이 또 틀린 말을 한다.**
      실제로 렌더에서 막힌 경우만 이 클래스로 좁힌다.
    """

    reasons: tuple[str, ...] = (RENDER_BLOCKED_REASON,)


def _render_blocked() -> PdfRenderBlockedError:
    """「PDF 만들기」 실패를 «사유 있는» 차단으로 바꾼다.

    ★ 왜 필요한가 (실측)
      이 자리에서 던지던 맨 예외에는 ``reasons``가 없어서, 화면이
      「자동 출고 승인을 확인하지 못했습니다」라는 «다른 단계» 문구로 떨어졌다.
      자동검사(4종)는 돌지도 않았는데 자동검사가 막은 것처럼 보였다.
      서버 로그에도 원본 예외가 안 남아 관리자가 원인을 찾을 수 없었다.

    ``logger.exception``은 «호출 사슬»만 남긴다 — 보고서 값은 안 들어간다.
    """
    logger.exception(RENDER_BLOCKED_MESSAGE)
    return PdfRenderBlockedError(RENDER_BLOCKED_MESSAGE)


def _blocked(reason: str) -> PdfRenderBlockedError:
    """「PDF 후보 만들기」 구조 검사 실패 — «어느 검사»였는지를 로그에 남긴다.

    ★ 왜 나눴나
      이 검사들은 사유 없이 던져서, 화면도 로그도 「자동 출고 승인을
      확인하지 못했습니다」 한 줄뿐이었다 — **관리자가 원인을 찾을 수 없었다.**

    화면에는 ``RENDER_BLOCKED_REASON`` 한 줄만 나간다. 여기 ``reason`` 은
    내부 검사 이름이라 사용자에게는 잡음이고, 로그에서만 쓸모가 있다.
    ⚠️ 그래서 ``reason`` 에는 **고정 문자열만** 넘겨라 — 보고서 값을 넣지 마라.
    """
    logger.warning("PDF 후보 검사 차단: %s", reason)
    return PdfRenderBlockedError(reason)


@dataclass(frozen=True)
class RenderedPdfPage:
    number: int
    png_bytes: bytes
    png_sha256: str
    width_px: int
    height_px: int


@dataclass(frozen=True)
class PdfReleaseCandidate:
    pdf_bytes: bytes
    pdf_sha256: str
    pages: tuple[RenderedPdfPage, ...]
    expected_fact_ids: tuple[str, ...] = ()
    render_scale: float = 1.5
    content_manifest_version: str = ""
    content_manifest_sha256: str = ""

    @property
    def page_count(self) -> int:
        return len(self.pages)


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    reviewer: str
    approved_at: str


@dataclass(frozen=True)
class PdfReleaseApproval:
    pdf_sha256: str
    page_png_sha256s: tuple[str, ...]
    reviewed_pages: tuple[int, ...]
    reviewed_fact_ids: tuple[str, ...]
    fact_failed_count: int
    fact: ApprovalDecision
    editorial: ApprovalDecision
    visual: ApprovalDecision
    visual_review_kind: str


@dataclass(frozen=True)
class PdfReleaseRecord:
    pdf_sha256: str
    page_count: int
    page_png_sha256s: tuple[str, ...]
    expected_fact_ids: tuple[str, ...]
    reviewed_fact_ids: tuple[str, ...]
    fact_failed_count: int
    fact_reviewer: str
    fact_approved_at: str
    editorial_reviewer: str
    editorial_approved_at: str
    visual_reviewer: str
    visual_approved_at: str
    visual_review_kind: str
    released_at: str
    record_sha256: str


@dataclass(frozen=True)
class ReleasedPdf:
    content: bytes
    record: PdfReleaseRecord


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def is_valid_sha256(value: object) -> bool:
    """길이뿐 아니라 정확한 소문자 hexadecimal SHA-256 형식을 검사한다."""

    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def is_valid_reviewer_id(value: object) -> bool:
    """인증 계층이 발급하는 고정 길이 소문자 opaque user ID만 허용한다."""

    return isinstance(value, str) and REVIEWER_ID_RE.fullmatch(value) is not None


def _has_visible_page_content(image: Image.Image) -> bool:
    """흰 배경에 합성했을 때 실제로 보이는 픽셀이 하나라도 있는지 확인한다."""

    rgba = image.convert("RGBA")
    white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    flattened = Image.alpha_composite(white, rgba).convert("RGB")
    return any(channel_min < 250 for channel_min, _ in flattened.getextrema())


def _flatten_on_white(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    return Image.alpha_composite(white, rgba).convert("RGB")


def _body_text_is_visually_present(
    page: pdfium.PdfPage,
    rendered: Image.Image,
) -> bool:
    """PDF text glyph 위치에 실제 렌더 ink가 충분한지 확인한다.

    전체 페이지에 잉크가 있는지만 보면 회색 쪽번호·머리말 하나로 흰 본문을 숨길
    수 있다. 그래서 PDFium이 돌려준 각 글자의 PDF 좌표를 PNG 좌표로 옮겨 중앙
    본문 band 안의 글자 절반 이상이 실제 비백색 픽셀을 갖는지 본다. 표지처럼
    글자가 적어도 중앙 제목이 보이면 통과한다.
    """

    width_pt, height_pt = page.get_size()
    if width_pt <= 0 or height_pt <= 0 or rendered.width <= 0 or rendered.height <= 0:
        return False
    flattened = _flatten_on_white(rendered)
    text_page = page.get_textpage()
    eligible = 0
    visible = 0
    try:
        for index in range(text_page.count_chars()):
            glyph = text_page.get_text_range(index, 1)
            if (
                not glyph
                or glyph.isspace()
                or all(unicodedata.category(char).startswith("C") for char in glyph)
            ):
                continue
            try:
                left, bottom, right, top = text_page.get_charbox(index)
            except Exception:  # pragma: no cover - damaged PDFium object is fail-closed below
                return False
            center_y = (bottom + top) / 2
            # 반복 머리말·쪽번호는 본문 가시성의 증거가 아니다. 비율 추측 대신
            # PDF 조립부(SimpleDocTemplate)가 쓰는 본문 frame의 정본 여백을
            # 공유한다. 그러면 표 뒤에서 새 페이지가 생겨 본문이 frame 맨 위에
            # 놓여도 인정하고, _page_furniture의 머리말·꼬리말은 계속 제외한다.
            if not (
                PAGE_BOTTOM_MARGIN_PT
                < center_y
                < height_pt - PAGE_TOP_MARGIN_PT
            ):
                continue
            if right <= left or top <= bottom:
                continue
            eligible += 1
            x0 = max(0, math.floor(left / width_pt * flattened.width) - 1)
            x1 = min(flattened.width, math.ceil(right / width_pt * flattened.width) + 1)
            y0 = max(
                0,
                math.floor((height_pt - top) / height_pt * flattened.height) - 1,
            )
            y1 = min(
                flattened.height,
                math.ceil((height_pt - bottom) / height_pt * flattened.height) + 1,
            )
            if x1 <= x0 or y1 <= y0:
                continue
            glyph_crop = flattened.crop((x0, y0, x1, y1)).convert("L")
            low, high = glyph_crop.getextrema()
            # 글자 bbox 안에 최소 명암 대비가 있어야 한다. 단순히 어두운 배경이
            # 있다는 이유만으로 invisible text를 보이는 글자로 인정하지 않는다.
            # bbox만 보기 때문에 footer나 멀리 떨어진 선도 증거가 될 수 없다.
            if high - low >= 10:
                visible += 1
    finally:
        text_page.close()
    if eligible == 0:
        return False
    return visible >= max(1, math.ceil(eligible * 0.5))


def _page_has_missing_glyph(page: pdfium.PdfPage) -> bool:
    """PDFium 문자 인덱스에 대응하는 Unicode 글자가 비면 누락 글리프로 본다."""

    text_page = page.get_textpage()
    try:
        return any(
            not text_page.get_text_range(index, 1)
            for index in range(text_page.count_chars())
        )
    finally:
        text_page.close()


def _render_all_pages(pdf_bytes: bytes, *, scale: float) -> tuple[RenderedPdfPage, ...]:
    """PDFium으로 모든 페이지를 실제 PNG bytes까지 렌더링한다."""

    acquired = _PDFIUM_RENDER_LOCK.acquire(timeout=PDFIUM_RENDER_LOCK_TIMEOUT_SEC)
    if not acquired:
        raise _blocked("PDF 렌더 작업이 제한 시간 안에 시작되지 못했습니다")
    try:
        document = pdfium.PdfDocument(pdf_bytes)
        pages: list[RenderedPdfPage] = []
        try:
            for index in range(len(document)):
                page = document[index]
                try:
                    bitmap = page.render(scale=scale)
                    try:
                        image = bitmap.to_pil()
                        has_visible_content = _has_visible_page_content(image)
                        has_visible_body_text = _body_text_is_visually_present(page, image)
                        has_missing_glyph = _page_has_missing_glyph(page)
                        output = io.BytesIO()
                        image.save(output, format="PNG")
                        png_bytes = output.getvalue()
                        width_px, height_px = image.size
                    finally:
                        bitmap.close()
                finally:
                    page.close()
                if not png_bytes.startswith(PNG_MAGIC) or width_px <= 0 or height_px <= 0:
                    raise _blocked("PDF 페이지 PNG 렌더 증거가 올바르지 않습니다")
                if not has_visible_content:
                    raise _blocked("PDF 페이지가 시각적으로 비어 있어 출고할 수 없습니다")
                if has_missing_glyph:
                    raise _blocked("PDF에 글리프가 없는 문자가 있어 출고할 수 없습니다")
                if not has_visible_body_text:
                    raise _blocked("PDF 본문 글자가 렌더 화면에서 보이지 않아 출고할 수 없습니다")
                pages.append(
                    RenderedPdfPage(
                        number=index + 1,
                        png_bytes=png_bytes,
                        png_sha256=_sha256(png_bytes),
                        width_px=width_px,
                        height_px=height_px,
                    )
                )
        finally:
            document.close()
        return tuple(pages)
    finally:
        _PDFIUM_RENDER_LOCK.release()


def report_fact_id_ledger(report: Report) -> tuple[str, ...]:
    """PDF 결속 장부 — v1은 fact_id, v2는 실제 인용 번호로 대체한다.

    v1 canonical 보고서는 문장·표가 잠긴 ``fact_records``를 인용하므로 그
    ``fact_id`` 집합이 「출고된 PDF가 검수한 사실과 정확히 같다」는 결속이다.
    v2(엔진 v2 composer) 보고서는 ``fact_records``가 없다 — 문장 단위 인용
    검증(출처 실존·수치 대조·의미 검수, 04장 3-2절)이 사실 검수를 대신하기
    때문이다. 그래서 v2에서는 본문·요약이 실제로 표시하는 인용 번호 집합을
    장부로 삼는다: 인용이 바뀌면(추가·삭제·번호 변경) 이 장부도 바뀌어 해시
    결속이 깨진다 — v1의 fact_id 결속과 같은 역할을 하는 대체 결속이다.
    """

    if report.schema_version == ENGINE_V2_SCHEMA_VERSION:
        numbers = sorted(
            {source.number for source in visible_citations(report.citations)}
        )
        return tuple(f"v2-citation-{number}" for number in numbers)
    return tuple(fact.fact_id for fact in report.fact_records)


def prepare_pdf_release(report: Report, *, render_scale: float = 1.5) -> PdfReleaseCandidate:
    """canonical PDF 후보를 만들고 모든 페이지의 PNG 검수 재료를 준비한다.

    v2(엔진 v2 composer) 보고서는 v1 canonical 게이트(``build_published_report``)
    를 건너뛰고 composer 자체 3검사(``validate_v2``)만 다시 확인한 뒤 검증된
    Report를 그대로 조립에 태운다. 사실 장부는 ``report_fact_id_ledger``가
    만드는 인용 번호 기반 대체 결속을 쓴다.
    """

    try:
        if report.schema_version == ENGINE_V2_SCHEMA_VERSION:
            validate_v2(report)
            expected_fact_ids = report_fact_id_ledger(report)
            pdf_bytes = build_pdf(report)
        else:
            published = build_published_report(report)
            expected_fact_ids = report_fact_id_ledger(published)
            pdf_bytes = build_pdf(published)
        return prepare_pdf_bytes(
            pdf_bytes,
            render_scale=render_scale,
            expected_fact_ids=expected_fact_ids,
        )
    except PDFReleaseBlockedError:
        raise
    except PDFGenerationError:
        raise
    except Exception as exc:
        raise _render_blocked() from exc


def prepare_pdf_bytes(
    pdf_bytes: bytes,
    *,
    render_scale: float = 1.5,
    expected_fact_ids: tuple[str, ...] = (),
) -> PdfReleaseCandidate:
    """이미 생성된 최종 bytes를 구조 검사하고 모든 페이지 PNG로 렌더링한다."""

    if (
        isinstance(render_scale, bool)
        or not isinstance(render_scale, (int, float))
        or not math.isfinite(render_scale)
        or render_scale <= 0
    ):
        raise ValueError("PDF 페이지 렌더 배율은 양수여야 합니다")
    if not isinstance(pdf_bytes, bytes) or not pdf_bytes:
        raise _blocked("PDF bytes가 비었습니다")
    if not isinstance(expected_fact_ids, tuple) or any(
        not isinstance(fact_id, str) or not fact_id.strip()
        for fact_id in expected_fact_ids
    ):
        raise _blocked("사실 장부에 빈 fact_id가 있습니다")
    if len(expected_fact_ids) != len(set(expected_fact_ids)):
        raise _blocked("사실 장부의 fact_id가 중복됐습니다")
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes), strict=True)
        if not reader.pages:
            raise _blocked("PDF에 페이지가 없습니다")
        if any(not (page.extract_text() or "").strip() for page in reader.pages):
            raise _blocked("글자가 없는 PDF 페이지가 있습니다")
        pages = _render_all_pages(pdf_bytes, scale=render_scale)
        if len(pages) != len(reader.pages):
            raise _blocked("PDF 페이지와 PNG 검수 페이지 수가 다릅니다")
        metadata = reader.metadata or {}
        return PdfReleaseCandidate(
            pdf_bytes=pdf_bytes,
            pdf_sha256=_sha256(pdf_bytes),
            pages=pages,
            expected_fact_ids=expected_fact_ids,
            render_scale=render_scale,
            content_manifest_version=str(
                metadata.get(PDF_MANIFEST_VERSION_KEY, "") or ""
            ),
            content_manifest_sha256=str(
                metadata.get(PDF_MANIFEST_SHA256_KEY, "") or ""
            ),
        )
    except PDFReleaseBlockedError:
        raise
    except Exception as exc:
        raise _render_blocked() from exc


def _approval_datetime(value: str) -> dt.datetime | None:
    if not isinstance(value, str) or value != value.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def _valid_approval_time(value: str) -> bool:
    return _approval_datetime(value) is not None


def validate_approval(approval: PdfReleaseApproval) -> tuple[str, ...]:
    """승인 레코드 자체의 필수 필드를 검사한다. 후보와의 결박은 release에서 한다."""

    problems: list[str] = []
    if not is_valid_sha256(approval.pdf_sha256):
        problems.append("PDF SHA-256이 올바르지 않습니다")
    valid_page_hashes = (
        isinstance(approval.page_png_sha256s, tuple)
        and bool(approval.page_png_sha256s)
        and all(is_valid_sha256(value) for value in approval.page_png_sha256s)
    )
    if not valid_page_hashes:
        problems.append("전 페이지 PNG SHA-256이 필요합니다")
    expected_pages = (
        tuple(range(1, len(approval.page_png_sha256s) + 1))
        if isinstance(approval.page_png_sha256s, tuple)
        else ()
    )
    if (
        not isinstance(approval.reviewed_pages, tuple)
        or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in approval.reviewed_pages
        )
        or approval.reviewed_pages != expected_pages
    ):
        problems.append("시각 검수 페이지가 전체 페이지와 정확히 일치하지 않습니다")
    valid_fact_ids = (
        isinstance(approval.reviewed_fact_ids, tuple)
        and bool(approval.reviewed_fact_ids)
        and all(
            isinstance(fact_id, str) and bool(fact_id.strip())
            for fact_id in approval.reviewed_fact_ids
        )
    )
    if not valid_fact_ids:
        problems.append("사실 승인에는 검수한 전체 fact_id가 필요합니다")
    elif len(approval.reviewed_fact_ids) != len(set(approval.reviewed_fact_ids)):
        problems.append("사실 승인 fact_id가 중복됐습니다")
    if (
        isinstance(approval.fact_failed_count, bool)
        or not isinstance(approval.fact_failed_count, int)
        or approval.fact_failed_count != 0
    ):
        problems.append("사실 검수 실패 건수가 0이 아닙니다")
    reviewer_values: list[str] = []
    for label, decision in (
        ("사실", approval.fact),
        ("편집", approval.editorial),
        ("시각", approval.visual),
    ):
        if not isinstance(decision, ApprovalDecision):
            problems.append(f"{label} 승인 결정 형식이 올바르지 않습니다")
            continue
        if decision.approved is not True:
            problems.append(f"{label} 승인이 통과 상태가 아닙니다")
        if not is_valid_reviewer_id(decision.reviewer):
            problems.append(f"{label} 승인 검수자 ID가 안전한 소문자 형식이 아닙니다")
        else:
            reviewer_values.append(decision.reviewer)
        if not _valid_approval_time(decision.approved_at):
            problems.append(f"{label} 승인 시각에 시간대가 없습니다")
    if len(reviewer_values) != 3 or len(set(reviewer_values)) != 3:
        problems.append("사실·편집·시각 승인은 서로 다른 세 검수자가 해야 합니다")
    if (
        not isinstance(approval.visual_review_kind, str)
        or approval.visual_review_kind not in ALLOWED_VISUAL_REVIEW_KINDS
    ):
        problems.append("시각 승인은 독립된 사람의 직접 검수여야 합니다")
    return tuple(problems)


_RELEASE_RECORD_FIELDS: Final[tuple[str, ...]] = (
    "pdf_sha256",
    "page_count",
    "page_png_sha256s",
    "expected_fact_ids",
    "reviewed_fact_ids",
    "fact_failed_count",
    "fact_reviewer",
    "fact_approved_at",
    "editorial_reviewer",
    "editorial_approved_at",
    "visual_reviewer",
    "visual_approved_at",
    "visual_review_kind",
    "released_at",
)


def release_record_sha256(record: PdfReleaseRecord) -> str:
    """출고 레코드의 자기 hash 필드를 제외한 canonical digest를 계산한다."""

    payload = {name: getattr(record, name) for name in _RELEASE_RECORD_FIELDS}
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256(serialized)


def validate_release_record(record: PdfReleaseRecord) -> tuple[str, ...]:
    """영속 저장 전후에 같은 규칙으로 출고 레코드와 canonical digest를 검증한다."""

    problems: list[str] = []
    if not is_valid_sha256(record.pdf_sha256):
        problems.append("출고 PDF SHA-256이 올바르지 않습니다")
    if (
        isinstance(record.page_count, bool)
        or not isinstance(record.page_count, int)
        or record.page_count <= 0
    ):
        problems.append("출고 PDF 페이지 수가 양수가 아닙니다")
    if (
        not isinstance(record.page_png_sha256s, tuple)
        or not record.page_png_sha256s
        or any(not is_valid_sha256(value) for value in record.page_png_sha256s)
    ):
        problems.append("출고 전 페이지 PNG SHA-256이 올바르지 않습니다")
    elif record.page_count != len(record.page_png_sha256s):
        problems.append("출고 페이지 수와 PNG hash 개수가 다릅니다")
    if (
        not isinstance(record.expected_fact_ids, tuple)
        or not record.expected_fact_ids
        or any(
            not isinstance(value, str) or not value.strip()
            for value in record.expected_fact_ids
        )
        or len(record.expected_fact_ids) != len(set(record.expected_fact_ids))
    ):
        problems.append("출고 사실 장부가 올바르지 않습니다")
    if (
        not isinstance(record.reviewed_fact_ids, tuple)
        or record.reviewed_fact_ids != record.expected_fact_ids
    ):
        problems.append("출고 검수 fact_id가 전체 사실 장부와 다릅니다")
    if (
        isinstance(record.fact_failed_count, bool)
        or not isinstance(record.fact_failed_count, int)
        or record.fact_failed_count != 0
    ):
        problems.append("출고 사실 검수 실패 건수가 0이 아닙니다")
    reviewers = (
        record.fact_reviewer,
        record.editorial_reviewer,
        record.visual_reviewer,
    )
    valid_reviewers = all(is_valid_reviewer_id(value) for value in reviewers)
    if not valid_reviewers:
        problems.append("출고 검수자 ID 형식이 올바르지 않습니다")
    if not valid_reviewers or len(set(reviewers)) != 3:
        problems.append("출고 검수자는 서로 다른 세 명이어야 합니다")
    if any(
        not _valid_approval_time(value)
        for value in (
            record.fact_approved_at,
            record.editorial_approved_at,
            record.visual_approved_at,
            record.released_at,
        )
    ):
        problems.append("출고 또는 승인 시각에 시간대가 없습니다")
    else:
        release_time = _approval_datetime(record.released_at)
        approval_times = (
            _approval_datetime(record.fact_approved_at),
            _approval_datetime(record.editorial_approved_at),
            _approval_datetime(record.visual_approved_at),
        )
        if release_time is not None and any(
            approved_at is not None and release_time < approved_at
            for approved_at in approval_times
        ):
            problems.append("출고 시각이 승인 시각보다 빠릅니다")
    if (
        not isinstance(record.visual_review_kind, str)
        or record.visual_review_kind not in ALLOWED_VISUAL_REVIEW_KINDS
    ):
        problems.append("출고 시각 승인 유형이 올바르지 않습니다")
    if not is_valid_sha256(record.record_sha256):
        problems.append("출고 레코드 SHA-256이 올바르지 않습니다")
    else:
        try:
            actual_digest = release_record_sha256(record)
        except (AttributeError, TypeError, ValueError):
            problems.append("출고 레코드 canonical digest를 계산할 수 없습니다")
        else:
            if record.record_sha256 != actual_digest:
                problems.append("출고 레코드 canonical digest가 일치하지 않습니다")
    return tuple(problems)


def _candidate_integrity_problems(
    candidate: PdfReleaseCandidate,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """후보에 적힌 hash/크기/페이지 수를 실제 bytes에서 모두 다시 계산한다."""

    problems: list[str] = []
    if not isinstance(candidate.pdf_bytes, bytes) or not candidate.pdf_bytes:
        problems.append("최종 PDF bytes가 비었습니다")
    if not is_valid_sha256(candidate.pdf_sha256):
        problems.append("최종 PDF SHA-256 형식이 올바르지 않습니다")
    elif (
        isinstance(candidate.pdf_bytes, bytes)
        and _sha256(candidate.pdf_bytes) != candidate.pdf_sha256
    ):
        problems.append("최종 PDF bytes와 SHA-256이 일치하지 않습니다")
    if not isinstance(candidate.pages, tuple) or not candidate.pages:
        problems.append("최종 PDF의 PNG 검수 페이지가 없습니다")
        return tuple(problems), ()
    if any(not isinstance(page, RenderedPdfPage) for page in candidate.pages):
        problems.append("최종 PDF의 PNG 검수 페이지 형식이 올바르지 않습니다")
        return tuple(problems), ()
    if (
        not isinstance(candidate.expected_fact_ids, tuple)
        or not candidate.expected_fact_ids
        or any(
            not isinstance(fact_id, str) or not fact_id.strip()
            for fact_id in candidate.expected_fact_ids
        )
        or len(candidate.expected_fact_ids) != len(set(candidate.expected_fact_ids))
    ):
        problems.append("최종 PDF의 전체 fact_id 장부가 올바르지 않습니다")
    if (
        isinstance(candidate.render_scale, bool)
        or not isinstance(candidate.render_scale, (int, float))
        or not math.isfinite(candidate.render_scale)
        or candidate.render_scale <= 0
    ):
        problems.append("최종 PDF의 PNG 렌더 배율이 올바르지 않습니다")
    if not isinstance(candidate.content_manifest_version, str):
        problems.append("최종 PDF 공개 내용 지문 버전 형식이 올바르지 않습니다")
    if not isinstance(candidate.content_manifest_sha256, str):
        problems.append("최종 PDF 공개 내용 지문 형식이 올바르지 않습니다")

    expected_numbers = tuple(range(1, len(candidate.pages) + 1))
    if any(
        isinstance(page.number, bool) or not isinstance(page.number, int)
        for page in candidate.pages
    ) or tuple(page.number for page in candidate.pages) != expected_numbers:
        problems.append("PNG 검수 페이지 번호가 1부터 연속되지 않습니다")

    actual_page_hashes: list[str] = []
    for page in candidate.pages:
        png_bytes = page.png_bytes
        if not isinstance(png_bytes, bytes) or len(png_bytes) <= len(PNG_MAGIC):
            problems.append(f"{page.number}페이지 PNG bytes가 비었습니다")
            actual_page_hashes.append("")
            continue
        actual_hash = _sha256(png_bytes)
        actual_page_hashes.append(actual_hash)
        if not is_valid_sha256(page.png_sha256) or page.png_sha256 != actual_hash:
            problems.append(f"{page.number}페이지 PNG bytes와 SHA-256이 일치하지 않습니다")
        if not png_bytes.startswith(PNG_MAGIC):
            problems.append(f"{page.number}페이지 PNG magic이 올바르지 않습니다")
            continue
        if (
            isinstance(page.width_px, bool)
            or isinstance(page.height_px, bool)
            or not isinstance(page.width_px, int)
            or not isinstance(page.height_px, int)
            or page.width_px <= 0
            or page.height_px <= 0
        ):
            problems.append(f"{page.number}페이지 PNG 크기가 양수가 아닙니다")
            continue
        try:
            with Image.open(io.BytesIO(png_bytes), formats=("PNG",)) as image:
                actual_size = image.size
                image.verify()
            if actual_size != (page.width_px, page.height_px):
                problems.append(f"{page.number}페이지 PNG 실제 크기와 기록이 다릅니다")
        except Exception:
            problems.append(f"{page.number}페이지 PNG 구조가 손상됐습니다")

    if isinstance(candidate.pdf_bytes, bytes) and candidate.pdf_bytes:
        try:
            reader = PdfReader(io.BytesIO(candidate.pdf_bytes), strict=True)
            actual_pdf_page_count = len(reader.pages)
            metadata = reader.metadata or {}
            actual_manifest_version = str(
                metadata.get(PDF_MANIFEST_VERSION_KEY, "") or ""
            )
            actual_manifest_sha256 = str(
                metadata.get(PDF_MANIFEST_SHA256_KEY, "") or ""
            )
            candidate_manifest_version = (
                candidate.content_manifest_version
                if isinstance(candidate.content_manifest_version, str)
                else ""
            )
            candidate_manifest_sha256 = (
                candidate.content_manifest_sha256
                if isinstance(candidate.content_manifest_sha256, str)
                else ""
            )
            if any(
                (
                    actual_manifest_version,
                    actual_manifest_sha256,
                    candidate_manifest_version,
                    candidate_manifest_sha256,
                )
            ):
                if actual_manifest_version not in KNOWN_CONTENT_MANIFEST_VERSIONS:
                    problems.append("최종 PDF 공개 내용 지문 버전이 올바르지 않습니다")
                if not is_valid_sha256(actual_manifest_sha256):
                    problems.append("최종 PDF 공개 내용 지문 형식이 올바르지 않습니다")
                if candidate_manifest_version != actual_manifest_version:
                    problems.append("PDF bytes와 후보의 공개 내용 지문 버전이 다릅니다")
                if candidate_manifest_sha256 != actual_manifest_sha256:
                    problems.append("PDF bytes와 후보의 공개 내용 지문이 다릅니다")
            if any(not (page.extract_text() or "").strip() for page in reader.pages):
                problems.append("최종 PDF에 글자가 없는 페이지가 있습니다")
        except Exception:
            problems.append("최종 PDF 구조를 다시 읽을 수 없습니다")
        else:
            if actual_pdf_page_count != len(candidate.pages):
                problems.append("최종 PDF 실제 페이지 수와 PNG 검수 개수가 다릅니다")
        if not any("렌더 배율" in problem for problem in problems):
            try:
                rerendered_pages = _render_all_pages(
                    candidate.pdf_bytes,
                    scale=float(candidate.render_scale),
                )
            except Exception:
                problems.append("출고 시점에 최종 PDF 전 페이지를 다시 렌더하지 못했습니다")
            else:
                if len(rerendered_pages) != len(candidate.pages):
                    problems.append("출고 재렌더 페이지 수가 승인 후보와 다릅니다")
                elif any(
                    (
                        stored.number,
                        stored.png_bytes,
                        stored.png_sha256,
                        stored.width_px,
                        stored.height_px,
                    )
                    != (
                        rerendered.number,
                        rerendered.png_bytes,
                        rerendered.png_sha256,
                        rerendered.width_px,
                        rerendered.height_px,
                    )
                    for stored, rerendered in zip(
                        candidate.pages,
                        rerendered_pages,
                        strict=True,
                    )
                ):
                    problems.append("PNG 검수 증거가 최종 PDF의 출고 재렌더와 다릅니다")
    return tuple(problems), tuple(actual_page_hashes)


def release_pdf(
    candidate: PdfReleaseCandidate,
    approval: PdfReleaseApproval | None,
    *,
    released_at: str,
) -> ReleasedPdf:
    """같은 PDF/PNG에 결박된 3종 승인이 있을 때만 출고 bytes와 기록을 만든다."""

    candidate_problems, actual_page_hashes = _candidate_integrity_problems(candidate)
    if candidate_problems:
        raise PDFReleaseBlockedError("; ".join(candidate_problems))
    if approval is None:
        raise PDFReleaseBlockedError("PDF 출고 승인이 없습니다")
    problems = list(validate_approval(approval))
    if approval.pdf_sha256 != candidate.pdf_sha256:
        problems.append("승인된 PDF와 최종 PDF hash가 다릅니다")
    if approval.page_png_sha256s != actual_page_hashes:
        problems.append("승인된 PNG와 최종 PDF 전 페이지 렌더가 다릅니다")
    if approval.reviewed_fact_ids != candidate.expected_fact_ids:
        problems.append("검수한 fact_id가 최종 보고서 전체 사실 장부와 정확히 일치하지 않습니다")
    if not _valid_approval_time(released_at):
        problems.append("출고 시각에 시간대가 없습니다")
    if problems:
        raise PDFReleaseBlockedError("; ".join(problems))

    record_payload = {
        "pdf_sha256": candidate.pdf_sha256,
        "page_count": candidate.page_count,
        "page_png_sha256s": actual_page_hashes,
        "expected_fact_ids": candidate.expected_fact_ids,
        "reviewed_fact_ids": approval.reviewed_fact_ids,
        "fact_failed_count": approval.fact_failed_count,
        "fact_reviewer": approval.fact.reviewer,
        "fact_approved_at": approval.fact.approved_at,
        "editorial_reviewer": approval.editorial.reviewer,
        "editorial_approved_at": approval.editorial.approved_at,
        "visual_reviewer": approval.visual.reviewer,
        "visual_approved_at": approval.visual.approved_at,
        "visual_review_kind": approval.visual_review_kind,
        "released_at": released_at,
    }
    unsigned_record = PdfReleaseRecord(
        **record_payload,
        record_sha256="",
    )
    record = PdfReleaseRecord(
        **record_payload,
        record_sha256=release_record_sha256(unsigned_record),
    )
    if validate_release_record(record):
        raise PDFReleaseBlockedError("출고 레코드 무결성을 확정하지 못했습니다")
    return ReleasedPdf(content=candidate.pdf_bytes, record=record)


def approval_to_dict(approval: PdfReleaseApproval) -> dict[str, object]:
    """영속 저장소가 enum/bytes 없이 승인 레코드를 기록하게 한다."""

    return asdict(approval)
