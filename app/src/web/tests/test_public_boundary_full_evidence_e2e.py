"""공개 worker 입구부터 FULL delivery 재조회까지 관통하는 무과금 종단시험.

이 시험은 ``OfficialEvidenceCollectionResult``나 ``evidence_rows``를 시험에서
조립하지 않는다. 외부 DART·공식 웹·Anthropic 응답만 결정론적 가짜로 바꾸고,
운영 ``ProductionOfficialEvidenceCollector``가 설치된 ``RealPipeline``을
``job_runtime._run_job``으로 실행한다. 따라서 매출표 원문 행, typed 근거,
아홉 장 packet, 비교 프로그램 근거, 공개 manifest 중 어느 생산 배선이
끊겨도 시험 안의 사후 보충으로 숨길 수 없다.
"""

from __future__ import annotations

import asyncio
import html
import io
import json
import re
import sys
import urllib.parse
import zipfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.core import deployment_identity
from src.core.constants import GENERATION_MODEL, PIPELINE_ENV, PIPELINE_REAL
from src.features.composer.constants import GRADE_CONFIRMED, SECTION_IDS
from src.features.composer.public_manifest import assert_stored_strict_manifest
from src.features.homepage.ir_pdf import FetchedIrHtml, FetchedIrPdf
from src.features.homepage.wide_fetch import WideRawResponse
from src.features.observability import lifecycle
from src.features.pipeline import real
from src.features.pipeline.port import CompanyCard, Grade, Outcome, UserInput
from src.features.provenance.sources import (
    exact_evidence_text_hash,
    full_typed_source_registry_problem,
    has_valid_provenance_seal,
)
from src.features.report_access.models import ReportAudience
from src.features.sharelink import tracks as share_tracks
from src.features.storage import db as storage_db
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR,
)
from src.shared.report_evidence.identity_verified_web import (
    parse_verified_dart_filing_official_web_binding,
    parse_verified_dart_filing_subdomain_binding,
)
from src.shared.report_generation.canonical import (
    assert_report_matches_generation_evidence,
    report_verification_payload,
)
from src.shared.report_generation.models import exact_text_sha256
from src.web import (
    job_runtime,
    official_evidence_adapter,
    paid_runtime,
    report_delivery_adapter,
    runtime,
)
from src.web.routers import reports as reports_router


_COMPANY_ID = "00126380"
_COMPARATOR_ID = "00999999"
_MAIN_RECEIPT = "20260315000123"
_AUDIT_RECEIPT = "20260314000100"
_COMPARATOR_RECEIPT = "20260315000999"
_HOME = "https://company.example/"
_JOB_ID = "public-boundary-full-evidence-e2e"
_QUALITY_STOP_JOB_ID = "public-boundary-full-quality-stop-e2e"
_SIDECAR_JOB_ID = "public-boundary-sidecar-discovery-e2e"
_IR_PDF_URL = "https://company.example/ir/2026-q2.pdf"
_RECRUIT_URL = "https://recruit.company.example/jobs"

# web/tests/conftest.py의 값싼 기본 대역이 적용되기 전에 실제 함수를 붙잡는다.
_ACTUAL_REQUIRE_REPORT_DELIVERY = job_runtime._require_report_delivery
_ACTUAL_FINALIZE_REPORT_DELIVERY = job_runtime._finalize_report_delivery
_ACTUAL_RELEASE_STATE = reports_router._release_state
_ACTUAL_WIDE_COLLECT = official_evidence_adapter.collect_official_web_documents

_PRODUCT_REVENUE_TEXT = (
    "제품별 매출액 구 분 2025년 제1기 매 출 액 비 중 "
    "제품가 5,000 50.00% 제품나 3,000 30.00% 제품다 2,000 20.00% "
    "합계 10,000 100.00%"
)
_REGION_REVENUE_TEXT = (
    "지역별 매출액 구 분 2025년 제1기 매 출 액 비 중 "
    "국내 5,000 50.00% 아시아 3,000 30.00% 미주 2,000 20.00% "
    "합계 10,000 100.00%"
)
_REVENUE_TEXT = f"{_PRODUCT_REVENUE_TEXT} {_REGION_REVENUE_TEXT}"
_SHARED_MARKET_CONTEXT = (
    "국내 반도체 제조 고객사와 글로벌 반도체 생산 고객을 대상으로 검사 장비 "
    "제품과 공정 추적 서비스를 반도체 검사 장비 시장에 공급한다."
)
_MAIN_FILING_PARAGRAPHS = (
    "I. 회사의 개요",
    "가나다전자는 주식회사로 설립되어 반도체 검사 장비 사업을 영위하는 전문기업이다.",
    "II. 사업의 내용",
    "가나다전자는 베타전자와 경쟁합니다.",
    f"가나다전자는 차별화된 공정 추적 경쟁력을 보유하고 있다. {_SHARED_MARKET_CONTEXT}",
    _REVENUE_TEXT,
    "공식 홈페이지 https://company.example/ 에서 회사와 제품 정보를 공개한다.",
)
_COMPARATOR_FILING_PARAGRAPHS = (
    "I. 회사의 개요",
    "베타전자는 주식회사로 설립되어 반도체 검사 장비 사업을 영위하는 전문기업이다.",
    "II. 사업의 내용",
    f"베타전자는 표준 검사 공정 경쟁력을 보유하고 있다. {_SHARED_MARKET_CONTEXT}",
)

_PAGE_PATH_BY_SECTION = {
    "identity": "/about/identity",
    "business_model": "/business",
    "portfolio": "/products",
    "past_changes": "/news/completed",
    "current_challenges": "/challenges",
    "future_strategy": "/strategy/future",
    "operations_partners": "/partners",
    "culture": "/culture",
    "competitive_position": "/about/competitive",
}
_ORDINALS = ("첫째", "둘째", "셋째", "넷째", "다섯째")
_IR_SECTION_SENTENCES = {
    "business_model": (
        "가나다전자는 기업 고객사에 반도체 검사 솔루션을 제공하고 "
        "장비 판매로 매출을 얻습니다."
    ),
    "portfolio": (
        "가나다전자는 반도체 공정 추적 장비를 핵심 제품으로 두고 "
        "제품별 매출 비중을 공개합니다."
    ),
    "current_challenges": (
        "원재료 가격 상승으로 당사의 원가 부담이 커졌고, 이에 대응해 "
        "당사가 공급처를 다변화했습니다."
    ),
}
_RECRUIT_SENTENCE = (
    "가나다전자는 책임을 핵심가치와 일하는 방식으로 정하고 협업 프로젝트 "
    "사례를 운영해 개선한 기록을 공개합니다."
)


def _section_sentences(section_id: str) -> tuple[str, ...]:
    """각 문장 자체가 해당 장의 수집기 필수 슬롯을 직접 증명하게 만든다."""

    templates = {
        "identity": (
            "가나다전자는 설립 이후 반도체 검사 장비를 제조 및 판매하는 주요 사업 전문기업이며 {ordinal} 원칙을 공개한다."
        ),
        "business_model": (
            "가나다전자는 기업 고객사에 공정 추적 서비스를 제공하고 장비 판매로 매출을 얻는 구조를 {ordinal} 기준으로 설명한다."
        ),
        "portfolio": (
            "가나다전자는 공정 추적 장비를 주력 제품으로 두고 핵심 제품의 매출 비중과 역할을 {ordinal} 기준으로 공개한다."
        ),
        "past_changes": (
            "가나다전자는 공정 추적 체계 개선을 완료한 성과와 설비 전환을 달성한 사실을 {ordinal} 사례로 공개한다."
        ),
        "current_challenges": (
            "당사는 원재료 가격 상승으로 원가 부담이 커졌고, 이에 대응해 공급업체 다변화를 도입한 {ordinal} 조치를 공개한다."
        ),
        "future_strategy": (
            "가나다전자는 해외 유통망 확대 계획을 추진하고 실행 단계에 착수한 상태를 {ordinal} 과제로 공개한다."
        ),
        "operations_partners": (
            "가나다전자는 협력사 공급망을 관리하며 검사 장비 생산을 직접 운영하는 역할을 {ordinal} 원칙으로 공개한다."
        ),
        "culture": (
            "가나다전자는 책임을 핵심가치와 일하는 방식으로 정하고 협업 프로젝트 사례를 운영해 개선한 {ordinal} 기록을 공개한다."
        ),
        "competitive_position": (
            "가나다전자는 공정 추적을 강점으로 삼아 차별화된 검사 경쟁력을 갖춘다는 {ordinal} 자기 설명을 공식 공개한다."
        ),
    }
    return tuple(templates[section_id].format(ordinal=value) for value in _ORDINALS)


def _root_html() -> str:
    links = "".join(
        f'<a href="{html.escape(path)}">{html.escape(section_id)}</a>'
        for section_id, path in _PAGE_PATH_BY_SECTION.items()
    )
    return (
        "<html><head><title>가나다전자 공식 홈페이지</title>"
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"Organization",'
        '"legalName":"가나다전자","taxID":"123-45-67890"}'
        "</script></head><body><main>"
        f'{links}<a href="{_RECRUIT_URL}">채용</a>'
        '<a href="/ir/2026-q2.pdf">26년 2분기 IR자료 2026-08-12</a>'
        "</main><footer>가나다전자 사업자등록번호 123-45-67890</footer>"
        "</body></html>"
    )


def _page_html(section_id: str) -> str:
    body = "".join(
        f"<p>{html.escape(sentence)}</p>"
        for sentence in _section_sentences(section_id)
    )
    return f"<html><head><title>{section_id} 공식 자료</title></head><body><main>{body}</main></body></html>"


_WEB_HTML = {
    "/": _root_html(),
    **{
        path: _page_html(section_id)
        for section_id, path in _PAGE_PATH_BY_SECTION.items()
    },
}


def _fake_wide_transport(
    url: str,
    url_allowed=None,
) -> WideRawResponse:
    """공식 웹 HTTP 하나만 가짜로 만들고 URL scope 검사는 실제 것을 쓴다."""

    if url_allowed is not None:
        assert url_allowed(url), f"생산 scope가 거절한 URL을 요청했습니다: {url}"
    parsed = urllib.parse.urlsplit(url)
    host = (parsed.hostname or "").casefold()
    path = parsed.path or "/"
    if host == "company.example" and path == "/robots.txt":
        return WideRawResponse(404, "", url, "text/plain")
    if host == "company.example" and path == "/sitemap.xml":
        return WideRawResponse(404, "", url, "application/xml")
    if host == "recruit.company.example" and path == "/robots.txt":
        return WideRawResponse(404, "", url, "text/plain")
    if host == "recruit.company.example" and path == "/sitemap.xml":
        return WideRawResponse(404, "", url, "application/xml")
    if host == "recruit.company.example" and path == "/jobs":
        return WideRawResponse(
            200,
            (
                "<html><head><title>가나다전자 채용</title></head>"
                f"<body><main><p>{html.escape(_RECRUIT_SENTENCE)}</p></main></body>"
                "</html>"
            ),
            url,
            "text/html",
        )
    if host == "company.example" and path in _WEB_HTML:
        return WideRawResponse(200, _WEB_HTML[path], url, "text/html")
    # apex의 www 후보와 예상 밖 안전 URL은 명시적 부재로만 답한다.
    return WideRawResponse(404, "", url, "text/html")


def _fake_ir_html_fetch(url: str, host: str, url_allowed=None) -> FetchedIrHtml:
    if url_allowed is not None:
        assert url_allowed(url), f"IR scope가 거절한 URL을 요청했습니다: {url}"
    parsed = urllib.parse.urlsplit(url)
    assert (parsed.hostname or "").casefold() == host.casefold()
    if parsed.path == "/robots.txt":
        return FetchedIrHtml(html="", effective_url=url)
    return FetchedIrHtml(html=_WEB_HTML.get(parsed.path or "/", ""), effective_url=url)


def _fake_ir_pdf_fetch(
    url: str,
    host: str,
    max_bytes: int,
    url_allowed=None,
) -> FetchedIrPdf:
    """실제 PDF parser가 읽는 한글 IR 바이트만 외부 HTTP 대신 제공한다."""

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas

    assert url == _IR_PDF_URL
    assert host == "company.example"
    if url_allowed is not None:
        assert url_allowed(url), f"IR scope가 거절한 URL을 요청했습니다: {url}"
    font_path = (
        Path(__file__).resolve().parents[2]
        / "features"
        / "export_pdf"
        / "fonts"
        / "Freesentation-Regular.ttf"
    )
    font_name = "FullEvidenceE2EKorean"
    pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
    output = io.BytesIO()
    canvas = Canvas(output, pageCompression=0, invariant=1)
    for sentence in _IR_SECTION_SENTENCES.values():
        canvas.setFont(font_name, 12)
        canvas.drawString(40, 800, sentence)
        canvas.showPage()
    canvas.save()
    content = output.getvalue()
    assert len(content) <= max_bytes
    return FetchedIrPdf(content, _IR_PDF_URL, "application/pdf")


class _ProviderMessages:
    """실제 계량·attempt 경계 뒤에서만 응답하는 가짜 Anthropic messages."""

    _review_item_re = re.compile(
        r"\[(\d+)\] \(장: ([^,]+), 종류: ([^,]+), 인용: ([^)]+)\)"
    )

    def __init__(self) -> None:
        self.writer_prompts: list[str] = []
        self.reviewer_prompts: list[str] = []
        # 외부 AI 대역의 출력만 얇게 만드는 적대 모드. production run_v2,
        # assessor, receipt, recovery는 절대 대체하지 않는다.
        self.persistently_thin_sections: set[str] = set()
        self.writer_section_calls: dict[str, int] = {}

    @staticmethod
    def _prompt(kwargs: dict[str, Any]) -> str:
        messages = kwargs.get("messages")
        assert isinstance(messages, list) and messages
        content = messages[-1].get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict)
            )
        raise AssertionError("가짜 provider가 모르는 messages 형식입니다")

    def create(self, **kwargs: Any) -> SimpleNamespace:
        prompt = self._prompt(kwargs)
        review_items = self._review_item_re.findall(prompt)
        if review_items:
            self.reviewer_prompts.append(prompt)
            text = json.dumps(
                {
                    "판정": [
                        {
                            "번호": int(number),
                            "장": section_id,
                            "근거": re.findall(r"조각 (\d+)", citations),
                            "결과": "참",
                        }
                        for number, section_id, _kind, citations in review_items
                    ]
                },
                ensure_ascii=False,
            )
        else:
            sections = tuple(
                section_id
                for section_id in SECTION_IDS
                if f"{section_id}:" in prompt
            )
            assert len(sections) == 1, "작가·검수 이외의 AI 호출이 새로 생겼습니다"
            section_id = sections[0]
            self.writer_prompts.append(prompt)
            section_call = self.writer_section_calls.get(section_id, 0) + 1
            self.writer_section_calls[section_id] = section_call
            sentences = _section_sentences(section_id)
            persistently_thin = section_id in self.persistently_thin_sections
            if persistently_thin:
                # 보충 후보는 실제로 달라져 receipt가 새 블록을 검증하되,
                # 여전히 2문장·한 의미칸이라 품질 실패는 정직하게 남는다.
                start = min(section_call - 1, len(sentences) - 2)
                sentences = sentences[start : start + 2]
            ir_sentence = _IR_SECTION_SENTENCES.get(section_id, "")
            if ir_sentence and ir_sentence in prompt:
                # 가짜 작가도 생산 packet에 실제로 실린 IR exact 문장을 골라야
                # 공개 Source·봉인이 그 경로를 관통한다. 근거 ID는 아래에서
                # prompt의 생산 표식으로 찾으며 시험이 손으로 주입하지 않는다.
                sentences = (ir_sentence, *sentences[1:])
            if section_id == "culture" and _RECRUIT_SENTENCE in prompt:
                sentences = (_RECRUIT_SENTENCE, *sentences[1:])
            rows: list[dict[str, object]] = []
            for index, sentence in enumerate(sentences):
                source_position = prompt.find(sentence)
                assert source_position >= 0, "공식 웹 exact 원문이 장 packet에서 사라졌습니다"
                preceding = re.findall(r"\[조각 (\d+)\]", prompt[:source_position])
                assert preceding
                fragment_id = preceding[-1]
                supported = re.search(
                    rf"\[조각 {re.escape(fragment_id)}\] \([^\n]*"
                    r"지원 주장슬롯: ([^)]+)\)",
                    prompt,
                )
                assert supported, "FULL 작가 prompt의 지원 슬롯 표식이 사라졌습니다"
                slots = tuple(
                    value.strip()
                    for value in supported.group(1).split(",")
                    if value.strip() in CLAIM_SLOTS_BY_SECTION[section_id]
                )
                assert slots, f"{section_id} 장에 작가가 쓸 수 있는 슬롯이 없습니다"
                rows.append(
                    {
                        "글": sentence,
                        "인용": [fragment_id],
                        "등급": GRADE_CONFIRMED,
                        # 같은 지원 칸만 되풀이해 공개문장 2개와 필수 의미칸
                        # 누락을 동시에 만든다. 보충 호출에서도 그대로여야
                        # recovery가 세 번째 호출 없이 닫히는지 증명할 수 있다.
                        "주장슬롯": (
                            slots[0]
                            if persistently_thin
                            else slots[index % len(slots)]
                        ),
                    }
                )
            text = json.dumps({"문장들": rows}, ensure_ascii=False)

        return SimpleNamespace(
            model=kwargs.get("model", GENERATION_MODEL),
            stop_reason="end_turn",
            usage=SimpleNamespace(
                input_tokens=10,
                output_tokens=5,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            ),
            content=[SimpleNamespace(text=text)],
        )


class _ProviderClient:
    def __init__(self) -> None:
        self.messages = _ProviderMessages()

    def with_options(self, *, max_retries: int, timeout: float):
        assert max_retries == 0
        assert timeout == real.ANTHROPIC_TIMEOUT_SEC
        return self


class _ExternalServiceFixtures:
    """생산 엔진 밖 DART·Anthropic 응답과 내려받은 파일만 대신한다."""

    def __init__(self, fixture_root: Path) -> None:
        self.RAW_DIR = fixture_root / "dart-documents"
        self.CORPCODE_DIR = fixture_root / "corp-code"
        self.RAW_DIR.mkdir(parents=True, exist_ok=True)
        self.CORPCODE_DIR.mkdir(parents=True, exist_ok=True)
        self.client = _ProviderClient()
        self.json_calls: list[tuple[str, str, str]] = []
        self.document_downloads: list[str] = []
        self.document_download_sidecar_flags: list[bool] = []
        self.corpcode_downloads = 0
        self._main_path = self.RAW_DIR / f"{_MAIN_RECEIPT}.xml"
        self._audit_path = self.RAW_DIR / f"{_AUDIT_RECEIPT}.xml"
        self._comparator_path = self.RAW_DIR / f"{_COMPARATOR_RECEIPT}.xml"
        self._corpcode_path = self.CORPCODE_DIR / "CORPCODE.xml"
        self._main_path.write_text(
            "<DOCUMENT>"
            + "".join(f"<P>{html.escape(value)}</P>" for value in _MAIN_FILING_PARAGRAPHS)
            + "</DOCUMENT>",
            encoding="utf-8",
        )
        self._audit_path.write_text(
            "<DOCUMENT>"
            + "".join(
                f"<P>{html.escape(value)}</P>" for value in _MAIN_FILING_PARAGRAPHS
            )
            + "</DOCUMENT>",
            encoding="utf-8",
        )
        self._comparator_path.write_text(
            "<DOCUMENT>"
            + "".join(
                f"<P>{html.escape(value)}</P>"
                for value in _COMPARATOR_FILING_PARAGRAPHS
            )
            + "</DOCUMENT>",
            encoding="utf-8",
        )
        self._corpcode_path.write_text(
            "<result>"
            "<list><corp_code>00126380</corp_code><corp_name>가나다전자</corp_name>"
            "<corp_eng_name>GANADA ELECTRONICS</corp_eng_name><stock_code>000001</stock_code>"
            "<modify_date>20260819</modify_date></list>"
            "<list><corp_code>00999999</corp_code><corp_name>베타전자</corp_name>"
            "<corp_eng_name>BETA ELECTRONICS</corp_eng_name><stock_code>999999</stock_code>"
            "<modify_date>20260819</modify_date></list>"
            "</result>",
            encoding="utf-8",
        )

    @staticmethod
    def _financial_payload(corp_code: str) -> dict[str, Any]:
        comparator = corp_code == _COMPARATOR_ID

        def row(
            account_id: str,
            account_name: str,
            amounts: tuple[int, int, int],
        ) -> dict[str, str]:
            return {
                "account_id": account_id,
                "account_nm": account_name,
                "sj_div": "IS",
                "fs_div": "CFS",
                "currency": "KRW",
                "bsns_year": "2025",
                "reprt_code": "11011",
                "thstrm_dt": "2025.01.01 ~ 2025.12.31",
                "thstrm_amount": str(amounts[0]),
                "frmtrm_dt": "2024.01.01 ~ 2024.12.31",
                "frmtrm_amount": str(amounts[1]),
                "bfefrmtrm_dt": "2023.01.01 ~ 2023.12.31",
                "bfefrmtrm_amount": str(amounts[2]),
            }

        amounts = (
            {
                "revenue": (1_000_000_000, 900_000_000, 800_000_000),
                "operating": (100_000_000, 90_000_000, 80_000_000),
                "profit": (70_000_000, 60_000_000, 50_000_000),
            }
            if comparator
            else {
                "revenue": (2_000_000_000, 1_800_000_000, 1_600_000_000),
                "operating": (300_000_000, 250_000_000, 200_000_000),
                "profit": (240_000_000, 190_000_000, 150_000_000),
            }
        )
        return {
            "status": "000",
            "reprt_code": "11011",
            "list": [
                row("ifrs-full_Revenue", "매출액", amounts["revenue"]),
                row(
                    "dart_OperatingIncomeLoss",
                    "영업이익",
                    amounts["operating"],
                ),
                row("ifrs-full_ProfitLoss", "당기순이익", amounts["profit"]),
            ],
        }

    def get_json(
        self,
        endpoint: str,
        params: dict[str, Any],
        counter: Any,
    ) -> dict[str, Any]:
        counter.tick("2026-09-04")
        corp_code = str(params.get("corp_code") or "")
        self.json_calls.append(
            (endpoint, corp_code, str(params.get("bsns_year") or ""))
        )
        if endpoint == "company.json":
            if corp_code == _COMPARATOR_ID:
                return {
                    "status": "000",
                    "corp_code": _COMPARATOR_ID,
                    "corp_name": "베타전자",
                    "corp_name_eng": "BETA ELECTRONICS",
                    "adres": "서울특별시 영등포구 국제금융로",
                    "ceo_nm": "김비교",
                    "est_dt": "20010101",
                    "hm_url": "",
                    "corp_cls": "Y",
                    "stock_code": "999999",
                    "bizr_no": "9999999999",
                    "jurir_no": "9999999999999",
                }
            return {
                "status": "000",
                "corp_code": _COMPANY_ID,
                "corp_name": "가나다전자",
                "corp_name_eng": "GANADA ELECTRONICS",
                "adres": "서울특별시 강남구 테헤란로",
                "ceo_nm": "홍길동",
                "est_dt": "20000101",
                "hm_url": _HOME,
                "corp_cls": "Y",
                "stock_code": "000001",
                "bizr_no": "1234567890",
                "jurir_no": "1234567890123",
            }
        if endpoint == "list.json":
            if params.get("pblntf_ty") == "F":
                return {
                    "status": "000",
                    "list": [
                        {
                            "corp_code": corp_code or _COMPANY_ID,
                            "corp_name": "가나다전자",
                            "rcept_no": _AUDIT_RECEIPT,
                            "report_nm": "감사보고서 (2025.12)",
                            "rcept_dt": "20260314",
                        }
                    ],
                }
            return {
                "status": "000",
                "list": [
                    {
                        "corp_code": corp_code or _COMPANY_ID,
                        "corp_name": (
                            "베타전자" if corp_code == _COMPARATOR_ID else "가나다전자"
                        ),
                        "rcept_no": (
                            _COMPARATOR_RECEIPT
                            if corp_code == _COMPARATOR_ID
                            else _MAIN_RECEIPT
                        ),
                        "report_nm": "사업보고서 (2025.12)",
                        "rcept_dt": "20260315",
                    }
                ],
            }
        if endpoint == "fnlttSinglAcnt.json":
            if str(params.get("bsns_year") or "") != "2025":
                return {"status": "013"}
            return self._financial_payload(corp_code)
        if endpoint == "empSttus.json":
            return {"status": "013"}
        raise AssertionError(f"fixture에 없는 DART JSON 요청입니다: {endpoint}")

    def download_document(
        self,
        rcept_no: str,
        raw_dir: Any,
        counter: Any,
        *,
        require_official_url_sidecar: bool = False,
    ) -> Path:
        del raw_dir
        counter.tick("2026-09-04")
        self.document_downloads.append(rcept_no)
        self.document_download_sidecar_flags.append(require_official_url_sidecar)
        if rcept_no == _MAIN_RECEIPT:
            path = self._main_path
        elif rcept_no == _AUDIT_RECEIPT:
            path = self._audit_path
        elif rcept_no == _COMPARATOR_RECEIPT:
            path = self._comparator_path
        else:
            raise AssertionError(f"fixture에 없는 DART 원문 요청입니다: {rcept_no}")
        if require_official_url_sidecar:
            # 실제 core 다운로드는 엄격 FULL에서 대표 XML과 hash로 결속된
            # sidecar를 한 artifact로 만든다. 외부 다운로드를 대신하는 이
            # fixture도 같은 생산 serializer로 그 외부 산출물만 재현한다.
            dart_client = sys.modules.get("core.dart_client")
            assert dart_client is not None
            dart_client.document_url_sidecar_path(path).write_bytes(
                dart_client._document_url_sidecar_bytes(  # noqa: SLF001
                    rcept_no=rcept_no,
                    main_document=path.read_bytes(),
                    ranked_candidates=[],
                )
            )
        return path

    def download_corpcode(self, corpcode_dir: Any, counter: Any) -> Path:
        del corpcode_dir
        counter.tick("2026-09-04")
        self.corpcode_downloads += 1
        return self._corpcode_path


def _install_production_engine_with_fake_external_services(
    monkeypatch: pytest.MonkeyPatch,
    fixture_root: Path,
) -> tuple[Any, _ExternalServiceFixtures]:
    """실제 run_pilot을 두고 네트워크·비밀·provider 경계만 결정론화한다."""

    fixture = _ExternalServiceFixtures(fixture_root)
    engine = real._engine()  # noqa: SLF001
    monkeypatch.setattr(engine, "load_env", lambda: None)
    monkeypatch.setattr(engine, "_client", lambda: fixture.client)
    monkeypatch.setattr(engine, "get_json", fixture.get_json)
    monkeypatch.setattr(engine, "download_document", fixture.download_document)
    monkeypatch.setattr(engine, "download_corpcode", fixture.download_corpcode)
    monkeypatch.setattr(engine, "RAW_DIR", fixture.RAW_DIR)
    monkeypatch.setattr(engine, "CORPCODE_DIR", fixture.CORPCODE_DIR)
    monkeypatch.setattr(real, "today_kst", lambda: date(2026, 9, 4))
    monkeypatch.setattr(real, "_engine", lambda: engine)
    return engine, fixture


def _install_actual_official_collector_with_fake_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def collect_with_fake_http(**kwargs: Any):
        return _ACTUAL_WIDE_COLLECT(
            **kwargs,
            transport=_fake_wide_transport,
            ir_html_fetch=_fake_ir_html_fetch,
            ir_pdf_fetch=_fake_ir_pdf_fetch,
        )

    monkeypatch.setattr(
        official_evidence_adapter,
        "collect_official_web_documents",
        collect_with_fake_http,
    )


def _begin_running_lifecycle(job_id: str) -> None:
    # 실제 확인→worker 선행 경계가 쓰는 정규화·시각·상태 전이를 그대로 쓴다.
    # 시험이 lifecycle 행을 비슷하게 손조립하면 최종 record와 다른 계약을
    # 만들고, 생산 결함이 아닌 StateConflictError를 일으킬 수 있다.
    assert paid_runtime._begin_observation_pending(  # noqa: SLF001
        run_id=job_id,
        job="",
        cost_krw=0.0,
        elapsed_sec=0.0,
        model="",
    )
    assert paid_runtime._mark_observation_running(job_id)  # noqa: SLF001


def _assert_full_typed_sources_are_bound(report: Any) -> None:
    formal = [source for source in report.citations if source.formal_source_kind]
    assert formal
    registry = tuple(report.citations)
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", source.document_content_sha256)
        for source in formal
    )
    assert all(has_valid_provenance_seal(source) for source in formal)
    assert [
        problem
        for source in formal
        if (
            problem := full_typed_source_registry_problem(
                source,
                registry,
                reference_date=report.as_of_date,
            )
        )
    ] == []


@pytest.fixture
def _isolated_company_catalog_state():
    """회사목록의 값·캐시를 정확히 되돌려 전체 suite 순서를 무관하게 한다."""

    records = real._COMPANY_CATALOG_RECORDS  # noqa: SLF001
    metadata = dict(real._COMPANY_CATALOG_METADATA)  # noqa: SLF001
    english_names = dict(real._COMPANY_CATALOG_ENGLISH_NAMES)  # noqa: SLF001
    candidate_source = real._COMPANY_CANDIDATE_INDEX_SOURCE  # noqa: SLF001
    candidate_index = real._COMPANY_CANDIDATE_INDEX  # noqa: SLF001

    def clear_caches() -> None:
        real._company_catalog.cache_clear()  # noqa: SLF001
        real._company_index.cache_clear()  # noqa: SLF001

    clear_caches()
    real._COMPANY_CATALOG_RECORDS = ()  # noqa: SLF001
    real._COMPANY_CATALOG_METADATA.clear()  # noqa: SLF001
    real._COMPANY_CATALOG_ENGLISH_NAMES.clear()  # noqa: SLF001
    real._COMPANY_CANDIDATE_INDEX_SOURCE = None  # noqa: SLF001
    real._COMPANY_CANDIDATE_INDEX = None  # noqa: SLF001
    try:
        yield
    finally:
        clear_caches()
        real._COMPANY_CATALOG_RECORDS = records  # noqa: SLF001
        real._COMPANY_CATALOG_METADATA.clear()  # noqa: SLF001
        real._COMPANY_CATALOG_METADATA.update(metadata)  # noqa: SLF001
        real._COMPANY_CATALOG_ENGLISH_NAMES.clear()  # noqa: SLF001
        real._COMPANY_CATALOG_ENGLISH_NAMES.update(english_names)  # noqa: SLF001
        real._COMPANY_CANDIDATE_INDEX_SOURCE = candidate_source  # noqa: SLF001
        real._COMPANY_CANDIDATE_INDEX = candidate_index  # noqa: SLF001


@pytest.mark.local_integration
def test_공개worker에서_매출원문_TYPED비교_FULL봉인_delivery재조회까지_손보충없이_관통한다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _isolated_company_catalog_state: None,
) -> None:
    """생산 경계가 만든 값만으로 FULL 보고서와 최초 PDF가 실제 저장된다."""

    monkeypatch.setenv(PIPELINE_ENV, PIPELINE_REAL)
    monkeypatch.setenv(real.ENGINE_V2_ENV_NAME, real.ENGINE_V2_ENV_ON)
    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, ReleaseMode.FULL.value)
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "artifacts"))
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)

    _production_engine, external_services = (
        _install_production_engine_with_fake_external_services(monkeypatch, tmp_path)
    )
    assert Path(_production_engine.make_fragments.__code__.co_filename).name == (
        "run_pilot.py"
    )
    _install_actual_official_collector_with_fake_http(monkeypatch)
    monkeypatch.setattr(
        job_runtime,
        "_require_report_delivery",
        _ACTUAL_REQUIRE_REPORT_DELIVERY,
    )
    monkeypatch.setattr(
        job_runtime,
        "_finalize_report_delivery",
        _ACTUAL_FINALIZE_REPORT_DELIVERY,
    )
    monkeypatch.setattr(reports_router, "_release_state", _ACTUAL_RELEASE_STATE)

    # 환경을 먼저 고정한 다음 실제 production collector가 설치된 pipeline을 만든다.
    pipeline = runtime.make_pipeline()
    assert isinstance(pipeline, real.RealPipeline)
    assert isinstance(
        pipeline._official_evidence_collector,  # noqa: SLF001
        official_evidence_adapter.ProductionOfficialEvidenceCollector,
    )
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)

    paid_runtime.prepare_budget_state_machine_cutover()
    slot_bucket_id = paid_runtime._reserve_run_slot(  # noqa: SLF001
        share_tracks.Track.ADMIN,
        "admin@example.com",
    )
    assert slot_bucket_id
    _begin_running_lifecycle(_JOB_ID)

    job = job_runtime.Job(
        job_id=_JOB_ID,
        user_input=UserInput(company="가나다전자", job="", region=""),
        card=CompanyCard(
            legal_name="가나다전자",
            typed_name="가나다전자",
            address="서울특별시 강남구 테헤란로",
            ceo="홍길동",
            founded="20000101",
            ref=_COMPANY_ID,
        ),
        share_key="admin@example.com",
        is_paid=True,
        paid_cap_krw=100_000.0,
        slot_bucket_id=slot_bucket_id,
        report_audience=ReportAudience.ADMIN,
    )

    asyncio.run(job_runtime._run_job(job))

    assert job.finished
    assert job.result is not None
    assert job.result.outcome is Outcome.REPORT, (
        job.result.final_gate_reason,
        job.result.message,
        tuple((source.name, source.state, source.detail) for source in job.result.sources),
        tuple(external_services.document_downloads),
        tuple(external_services.document_download_sidecar_flags),
        tuple(external_services.json_calls),
    )
    assert job.result.report is not None
    assert job.result.report.grade is Grade.COMPLETE
    assert job.result.report.release_mode == ReleaseMode.FULL.value
    assert job.result.final_gate_reason == ""
    assert job.result.report.shortfall_reasons == []
    assert job.report_persisted is True
    assert job.delivery_persisted is True
    assert job.slot_released is True
    assert len(external_services.client.messages.writer_prompts) == 9
    assert len(external_services.client.messages.reviewer_prompts) == 1
    assert external_services.corpcode_downloads == 1
    assert {_MAIN_RECEIPT, _AUDIT_RECEIPT, _COMPARATOR_RECEIPT} <= set(
        external_services.document_downloads
    )
    # formal typed 수집·비교·legacy 보완수집이 같은 ZIP을 각각
    # 다시 내려받지 않는다. 실제 외부 다운로드 경계 호출을 세어
    # 디스크 cache가 중복 설계를 감추지 못하게 한다.
    assert external_services.document_downloads.count(_MAIN_RECEIPT) == 1
    assert external_services.document_downloads.count(_AUDIT_RECEIPT) == 1
    assert external_services.document_downloads.count(_COMPARATOR_RECEIPT) == 1
    assert True in external_services.document_download_sidecar_flags
    assert {
        (_COMPANY_ID, "2025"),
        (_COMPARATOR_ID, "2025"),
    } <= {
        (corp_code, year)
        for endpoint, corp_code, year in external_services.json_calls
        if endpoint == "fnlttSinglAcnt.json"
    }
    report = job.result.report
    business = next(
        section for section in report.sections if section.cell == "business_model"
    )
    portfolio = next(
        section for section in report.sections if section.cell == "portfolio"
    )
    business_composition_tables = [
        table for table in business.tables if table.presentation == "composition"
    ]
    portfolio_composition_tables = [
        table for table in portfolio.tables if table.presentation == "composition"
    ]
    composition_tables = business_composition_tables + portfolio_composition_tables
    assert len(composition_tables) == 2
    assert all(table.headers == ["구분", "비중"] for table in composition_tables)
    assert all(len(table.rows) == 3 for table in composition_tables)
    assert len({table.cite for table in composition_tables}) == 2
    assert all(table.manifest_ref for table in composition_tables)
    assert all(len(table.row_evidence_refs) == 3 for table in composition_tables)
    assert all(len(table.row_binding_refs) == 3 for table in composition_tables)
    assert all(len(table.cell_binding_refs) == 3 for table in composition_tables)
    # 개수·모양만 보면 제품 caption 아래 지역 행이 붙은 과거 결함도 통과한다.
    # 실제 공개 표의 캡션과 exact 행을 함께 고정해 생산 경계의 축 혼동을 잡는다.
    product_table = next(
        table for table in composition_tables if "제품·서비스별" in table.caption
    )
    region_table = next(
        table for table in composition_tables if "지역별" in table.caption
    )
    assert portfolio_composition_tables == [product_table]
    assert business_composition_tables == [region_table]
    assert product_table.rows == [
        ["제품가", "50.00%"],
        ["제품나", "30.00%"],
        ["제품다", "20.00%"],
    ]
    assert region_table.rows == [
        ["국내", "50.00%"],
        ["아시아", "30.00%"],
        ["미주", "20.00%"],
    ]

    competitive = next(
        section for section in report.sections if section.cell == "competitive_position"
    )
    assert competitive.lines
    assert "베타전자" in " ".join(line[0] for line in competitive.lines)
    comparison_facts = [
        fact
        for fact in report.fact_records
        if fact.section_owner == "competitive_position"
    ]
    assert comparison_facts
    assert {
        "competitive_position:comparison_target",
        "competitive_position:comparison_metric",
        "competitive_position:comparison_basis",
        "competitive_position:comparison_judgment",
        "competitive_position:limitation",
    } <= {fact.claim_slot for fact in comparison_facts}

    evidence = report.generation_evidence
    assert evidence is not None
    assert evidence.writer_calls == 9
    assert evidence.reviewer_calls == 1
    assert evidence.public_manifest_sha256 == exact_text_sha256(
        report.public_structure_manifest
    )
    assert_report_matches_generation_evidence(
        report_verification_payload(report),
        evidence,
        manifest_bytes=report.public_structure_manifest.encode("utf-8"),
    )
    assert_stored_strict_manifest(report)
    # 두 표를 한 덩어리로 인용하면 제품 표의 근거에 지역 표까지 섞인다. 각 표는
    # 자기 머리말부터 첫 합계까지만 exact source로 보존해야 한다.
    for exact_table_text in (_PRODUCT_REVENUE_TEXT, _REGION_REVENUE_TEXT):
        revenue_hash = exact_evidence_text_hash(exact_table_text)
        revenue_sources = [
            source
            for source in report.citations
            if revenue_hash in source.exact_evidence_hashes
        ]
        assert len(revenue_sources) == 1
    combined_revenue_hash = exact_evidence_text_hash(_REVENUE_TEXT)
    assert not any(
        combined_revenue_hash in source.exact_evidence_hashes
        for source in report.citations
    )
    self_filing_sources = [
        source
        for source in report.citations
        if source.host == "dart.fss.or.kr"
        and source.document_id == _MAIN_RECEIPT
    ]
    assert self_filing_sources
    assert {
        source.publisher for source in self_filing_sources
    } == {"가나다전자"}
    assert all(has_valid_provenance_seal(source) for source in report.citations), [
        (source.number, source.source_id, source.provenance_role, source.used_in)
        for source in report.citations
        if not has_valid_provenance_seal(source)
    ]
    _assert_full_typed_sources_are_bound(report)
    # 실제 IR HTML→PDF parser가 만든 세 장의 원문이 production collector,
    # typed packet, 가짜 작가 선택, 공개 Source와 seal까지 각각 생존해야 한다.
    # Source/attester/evidence row를 시험이 손으로 만들지 않는다.
    for section_id, exact_text in _IR_SECTION_SENTENCES.items():
        exact_hash = exact_evidence_text_hash(exact_text)
        matching_ir_sources = [
            source
            for source in report.citations
            if source.formal_source_kind == "official_ir_pdf"
            and source.url == _IR_PDF_URL
            and section_id in source.used_in
            and exact_hash in source.exact_evidence_hashes
        ]
        assert matching_ir_sources, (
            section_id,
            exact_text,
            [
                (
                    source.formal_source_kind,
                    source.url,
                    source.used_in,
                    source.exact_evidence_hashes,
                )
                for source in report.citations
                if source.formal_source_kind == "official_ir_pdf"
            ],
        )
        assert all(
            has_valid_provenance_seal(source) for source in matching_ir_sources
        )
    ir_sources = [
        source
        for source in report.citations
        if source.formal_source_kind == "official_ir_pdf"
    ]
    recruit_sources = [
        source
        for source in report.citations
        if source.formal_source_kind == "official_recruit_page"
        and source.url == _RECRUIT_URL
        and "culture" in source.used_in
        and exact_evidence_text_hash(_RECRUIT_SENTENCE)
        in source.exact_evidence_hashes
    ]
    assert recruit_sources
    # root 본문에는 Writer 신호가 없다. 하위도메인 출처만 남아도 attester를
    # 공개 조립부가 스스로 추가해야 하며 시험 fixture가 등록부를 보충하지 않는다.
    assert not any(
        source.url == _HOME and source.formal_source_kind
        for source in report.citations
    )
    attesters = [
        source
        for source in report.citations
        if source.provenance_role == "attestation_only"
    ]
    assert len(attesters) == 1
    assert {
        source.domain_attestation_source_id
        for source in (*ir_sources, *recruit_sources)
    } == {attesters[0].source_id}

    delivery = report_delivery_adapter.load_public_delivery(_JOB_ID)
    assert delivery is not None
    assert delivery.artifact is not None
    assert delivery.inspection is not None
    assert delivery.inspection.pdf_bytes is not None
    assert delivery.inspection.pdf_bytes.startswith(b"%PDF")
    assert delivery.report.generation_evidence == evidence
    assert delivery.report.citations == report.citations
    assert all(
        has_valid_provenance_seal(source)
        for source in delivery.report.citations
    )
    assert_stored_strict_manifest(delivery.report)

    intent = report_delivery_adapter.load_public_delivery_intent(_JOB_ID)
    assert intent is not None
    assert intent.state == "complete"
    with storage_db.connect() as conn:
        entry = lifecycle.get_entry(conn, _JOB_ID)
    assert entry is not None
    assert entry.state == lifecycle.STATE_FINAL


@pytest.mark.local_integration
def test_실제FULL은_필수칸과_장당3이_보충뒤에도비면_무차감중단한다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _isolated_company_catalog_state: None,
) -> None:
    """외부 AI 응답만 얇게 하고 생산 assessor·receipt·회복은 그대로 탄다."""

    monkeypatch.setenv(PIPELINE_ENV, PIPELINE_REAL)
    monkeypatch.setenv(real.ENGINE_V2_ENV_NAME, real.ENGINE_V2_ENV_ON)
    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, ReleaseMode.FULL.value)
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "artifacts"))
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)

    _engine, external_services = _install_production_engine_with_fake_external_services(
        monkeypatch,
        tmp_path,
    )
    external_services.client.messages.persistently_thin_sections.add(
        "business_model"
    )
    _install_actual_official_collector_with_fake_http(monkeypatch)
    pipeline = runtime.make_pipeline()
    assert isinstance(pipeline, real.RealPipeline)
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)

    paid_runtime.prepare_budget_state_machine_cutover()
    share_key = "quality-stop-admin@example.com"
    slot_bucket_id = paid_runtime._reserve_run_slot(  # noqa: SLF001
        share_tracks.Track.ADMIN,
        share_key,
    )
    assert slot_bucket_id
    _begin_running_lifecycle(_QUALITY_STOP_JOB_ID)
    job = job_runtime.Job(
        job_id=_QUALITY_STOP_JOB_ID,
        user_input=UserInput(company="가나다전자", job="", region=""),
        card=CompanyCard(
            legal_name="가나다전자",
            typed_name="가나다전자",
            address="서울특별시 강남구 테헤란로",
            ceo="홍길동",
            founded="20000101",
            ref=_COMPANY_ID,
        ),
        share_key=share_key,
        is_paid=True,
        paid_cap_krw=100_000.0,
        slot_bucket_id=slot_bucket_id,
        report_audience=ReportAudience.ADMIN,
    )

    asyncio.run(job_runtime._run_job(job))

    assert job.result is not None
    assert job.result.outcome is Outcome.GATE_STOPPED
    assert job.result.charged is False
    assert job.result.report is None
    assert job.result.final_gate_reason == (
        FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR
    )
    # primary 9+1 뒤 business_model 한 장만 1회 보충하고 검수 1회.
    # 세 번째 생성은 없으며, 실제 provider 사용량은 숨기지 않는다.
    assert len(external_services.client.messages.writer_prompts) == 10
    assert len(external_services.client.messages.reviewer_prompts) == 2
    assert job.result.cost_krw > 0
    assert job.report_persisted is not True
    assert job.delivery_persisted is not True
    assert job.slot_released is True


@pytest.mark.local_integration
def test_DART첨부의_공식URL이_실제_sidecar를_거쳐_FULL_delivery까지_이어진다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _isolated_company_catalog_state: None,
) -> None:
    """홈페이지 주소가 DART 첨부에만 있어도 생산 다운로드부터 끝까지 잇는다.

    네트워크 응답 바이트만 가짜 ZIP으로 바꾼다. 대표 XML과 sidecar 파일은
    ``core.dart_client.download_document``가 직접 만들며, 시험은 그 파일이나
    typed 후보·출처 지문을 중간에 만들거나 보충하지 않는다.
    """

    monkeypatch.setenv(PIPELINE_ENV, PIPELINE_REAL)
    monkeypatch.setenv(real.ENGINE_V2_ENV_NAME, real.ENGINE_V2_ENV_ON)
    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, ReleaseMode.FULL.value)
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)

    external_services = _ExternalServiceFixtures(tmp_path)
    engine = real._engine()  # noqa: SLF001
    dart_client = sys.modules.get("core.dart_client")
    assert dart_client is not None
    assert Path(engine.make_fragments.__code__.co_filename).name == "run_pilot.py"

    raw_dir = tmp_path / "actual-dart-documents"
    raw_dir.mkdir(parents=True, exist_ok=True)

    def document_xml(paragraphs: tuple[str, ...]) -> bytes:
        return (
            "<DOCUMENT>"
            + "".join(f"<P>{html.escape(value)}</P>" for value in paragraphs)
            + "</DOCUMENT>"
        ).encode("utf-8")

    def zipped(members: dict[str, bytes]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(
            buffer,
            "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as archive:
            for member_name, payload in members.items():
                archive.writestr(member_name, payload)
        return buffer.getvalue()

    # 대표 XML에는 홈페이지 주소를 두지 않는다. 오직 작은 첨부 XML의 URL을
    # 생산 downloader가 찾아 sidecar로 운반해야 이후 공식 웹 수집이 열린다.
    main_document = document_xml(
        tuple(
            paragraph
            for paragraph in _MAIN_FILING_PARAGRAPHS
            if _HOME not in paragraph
        )
    )
    comparator_document = document_xml(_COMPARATOR_FILING_PARAGRAPHS)
    homepage_attachment = (
        f"<COVER>공식 홈페이지 {_HOME}</COVER>"
    ).encode("utf-8")
    zip_by_receipt = {
        _MAIN_RECEIPT: zipped(
            {
                "main.xml": main_document,
                "covers/company-homepage.xml": homepage_attachment,
            }
        ),
        _COMPARATOR_RECEIPT: zipped({"main.xml": comparator_document}),
        # W8부터 사업보고서와 함께 감사보고서(F)도 항상 내려받는다.
        _AUDIT_RECEIPT: zipped({"main.xml": main_document}),
    }
    downloaded_receipts: list[str] = []

    def fake_read_url(url: str, **_kwargs: Any) -> bytes:
        parsed = urllib.parse.urlsplit(url)
        assert parsed.hostname == "opendart.fss.or.kr"
        assert parsed.path.endswith("/document.xml")
        receipt = urllib.parse.parse_qs(parsed.query).get("rcept_no", [""])[0]
        assert receipt in zip_by_receipt, f"fixture에 없는 DART ZIP 요청: {receipt}"
        downloaded_receipts.append(receipt)
        return zip_by_receipt[receipt]

    def get_json_without_homepage(
        endpoint: str,
        params: dict[str, Any],
        counter: Any,
    ) -> dict[str, Any]:
        payload = external_services.get_json(endpoint, params, counter)
        if endpoint == "company.json" and str(params.get("corp_code") or "") != (
            _COMPARATOR_ID
        ):
            payload = dict(payload)
            payload["hm_url"] = ""
        return payload

    # run_pilot과 실제 DART downloader는 그대로 두고, 비밀·HTTP·provider처럼
    # 프로세스 밖 경계만 결정론적 대역으로 바꾼다.
    monkeypatch.setattr(engine, "load_env", lambda: None)
    monkeypatch.setattr(engine, "_client", lambda: external_services.client)
    monkeypatch.setattr(engine, "get_json", get_json_without_homepage)
    monkeypatch.setattr(engine, "download_document", dart_client.download_document)
    monkeypatch.setattr(engine, "download_corpcode", external_services.download_corpcode)
    monkeypatch.setattr(engine, "RAW_DIR", raw_dir)
    monkeypatch.setattr(engine, "CORPCODE_DIR", external_services.CORPCODE_DIR)
    monkeypatch.setattr(dart_client, "_read_url", fake_read_url)
    monkeypatch.setattr(real, "today_kst", lambda: date(2026, 9, 4))
    monkeypatch.setattr(real, "_engine", lambda: engine)
    _install_actual_official_collector_with_fake_http(monkeypatch)
    monkeypatch.setattr(
        job_runtime,
        "_require_report_delivery",
        _ACTUAL_REQUIRE_REPORT_DELIVERY,
    )
    monkeypatch.setattr(
        job_runtime,
        "_finalize_report_delivery",
        _ACTUAL_FINALIZE_REPORT_DELIVERY,
    )
    monkeypatch.setattr(reports_router, "_release_state", _ACTUAL_RELEASE_STATE)

    pipeline = runtime.make_pipeline()
    assert isinstance(pipeline, real.RealPipeline)
    assert isinstance(
        pipeline._official_evidence_collector,  # noqa: SLF001
        official_evidence_adapter.ProductionOfficialEvidenceCollector,
    )
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)

    paid_runtime.prepare_budget_state_machine_cutover()
    slot_bucket_id = paid_runtime._reserve_run_slot(  # noqa: SLF001
        share_tracks.Track.ADMIN,
        "admin@example.com",
    )
    assert slot_bucket_id
    _begin_running_lifecycle(_SIDECAR_JOB_ID)
    job = job_runtime.Job(
        job_id=_SIDECAR_JOB_ID,
        user_input=UserInput(company="가나다전자", job="", region=""),
        card=CompanyCard(
            legal_name="가나다전자",
            typed_name="가나다전자",
            address="서울특별시 강남구 테헤란로",
            ceo="홍길동",
            founded="20000101",
            ref=_COMPANY_ID,
        ),
        share_key="admin@example.com",
        is_paid=True,
        paid_cap_krw=100_000.0,
        slot_bucket_id=slot_bucket_id,
        report_audience=ReportAudience.ADMIN,
    )

    asyncio.run(job_runtime._run_job(job))

    assert job.result is not None
    assert job.result.outcome is Outcome.REPORT, (
        job.result.final_gate_reason,
        job.result.message,
        tuple((source.name, source.state, source.detail) for source in job.result.sources),
        tuple(downloaded_receipts),
    )
    assert job.result.report is not None
    assert job.delivery_persisted is True
    assert downloaded_receipts.count(_MAIN_RECEIPT) == 1
    assert downloaded_receipts.count(_COMPARATOR_RECEIPT) == 1
    # 이 목록은 옛 가짜 downloader만 채운다. 비어 있어야 실제 core 함수가
    # 실행된 것이며, 아래 sidecar는 시험 코드가 아니라 그 함수의 산출물이다.
    assert external_services.document_downloads == []

    main_path = raw_dir / f"{_MAIN_RECEIPT}.xml"
    assert main_path.read_bytes() == main_document
    loaded_sidecar = dart_client.load_document_url_sidecar(
        main_path,
        rcept_no=_MAIN_RECEIPT,
        main_document=main_document,
    )
    assert loaded_sidecar.is_valid is True
    assert [candidate.url for candidate in loaded_sidecar.candidates] == [_HOME]
    assert loaded_sidecar.candidates[0].source_member_name == (
        "covers/company-homepage.xml"
    )

    # hm_url이 비어 있었으므로 company.example 출처는 위 DART 후보가 실제
    # adapter·신원 확인·wide 수집을 통과했을 때만 보고서에 나타난다.
    assert any(
        source.host == "company.example"
        for source in job.result.report.citations
    ), [
        (source.host, source.url, source.source_type, source.used_in)
        for source in job.result.report.citations
    ]
    sidecar_web_sources = [
        source
        for source in job.result.report.citations
        if source.host in {"company.example", "recruit.company.example"}
        and source.formal_source_kind
    ]
    assert sidecar_web_sources
    assert all(
        parse_verified_dart_filing_official_web_binding(source.identity_binding)
        is not None
        for source in sidecar_web_sources
        if source.host == "company.example"
    )
    sidecar_recruit_sources = [
        source
        for source in sidecar_web_sources
        if source.host == "recruit.company.example"
        and source.url == _RECRUIT_URL
        and "culture" in source.used_in
        and exact_evidence_text_hash(_RECRUIT_SENTENCE)
        in source.exact_evidence_hashes
    ]
    assert sidecar_recruit_sources
    assert all(
        parse_verified_dart_filing_subdomain_binding(source.identity_binding)
        is not None
        for source in sidecar_recruit_sources
    )
    _assert_full_typed_sources_are_bound(job.result.report)
    delivery = report_delivery_adapter.load_public_delivery(_SIDECAR_JOB_ID)
    assert delivery is not None
    assert delivery.inspection is not None
    assert delivery.inspection.pdf_bytes is not None
    assert delivery.inspection.pdf_bytes.startswith(b"%PDF")
    assert delivery.report.citations == job.result.report.citations
    _assert_full_typed_sources_are_bound(delivery.report)
