"""엔진 v2 오프라인 E2E 리허설 — 무과금 (엔진 v2 소단계 3-5).

★ 04장 3-5절의 「로컬 무과금 리허설」을 pytest로 옮긴 것이다:
  가짜 엔진·가짜 계량 client 위에서 ENGINE_V2=1로 파이프라인 «전체»를 돌려
  수집 조각 → compose → verify → 요약 → render → validate_v2 → PDF 바이트까지
  실제 코드 경로가 끝까지 이어지는지 본다. AI·네트워크 호출은 0회다.

★ 가짜 작가 응답(fixtures/jyp_ask_responses.json)은 골든 샘플
  (docs/골든샘플/build_jyp_report.py)의 실제 문장에서 발췌했다 — 장별 6문장,
  인용 조각 id와 확인/해석 등급 포함. 수집 조각(fixtures/jyp_fragments.json)은
  그 문장들의 숫자가 전부 원문에 존재하도록 같은 근거에서 발췌했다
  (수치 검증 3-2를 실제로 통과시키기 위함 — 검증 우회 아님).

★ PDF 경계: export_pdf.release.prepare_pdf_release·export_pdf.logic.build_pdf가
  schema_version으로 v1/v2를 가른다(04장 3-4절 2항 배선 완료). v2는
  build_published_report(v1 canonical 게이트)를 타지 않고 composer의
  validate_v2만 다시 확인한 뒤 실제 조립부(_register_fonts→…→
  _add_accessibility_metadata)를 그대로 지난다. 이 시험은 monkeypatch 없이
  release.prepare_pdf_release(report)라는 «프로덕션 진입점»을 그대로 호출한다.
"""

from __future__ import annotations

import io
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from pypdf import PdfReader

from src.features.budget import provider_budget
from src.features.composer.constants import (
    SECTION_GUIDES,
    SECTION_IDS,
    SECTION_TITLES,
)
from src.features.composer.logic import SUMMARY_PROMPT_HEADER
from src.features.composer.render import (
    ENGINE_V2_SCHEMA_VERSION,
    INTERPRETATION_MARKER,
    SECTION_DISPLAY_NUMBERS,
)
from src.features.composer.verify import REVIEW_PROMPT_HEADER, REWRITE_PROMPT_HEADER
from src.features.export_pdf import release as pdf_release
from src.features.pipeline import real
from src.features.pipeline.port import CompanyCard, Outcome, RunResult, UserInput
from src.features.pipeline.tests.test_real_cache import (
    CORP_ID,
    JOB,
    POSTING,
    FakeEngine,
    _FakeClient,
    _FakeMessages,
)

# ── 리허설 고정값 ─────────────────────────────────────────
COMPANY_NAME = "제이와이피엔터테인먼트"

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
_FRAGMENTS_FIXTURE: dict[str, Any] = json.loads(
    (_FIXTURE_DIR / "jyp_fragments.json").read_text(encoding="utf-8")
)
_RESPONSES_FIXTURE: dict[str, Any] = json.loads(
    (_FIXTURE_DIR / "jyp_ask_responses.json").read_text(encoding="utf-8")
)

#: 검수 프롬프트에서 대조 문장 번호를 읽는 모양 (verify._build_review_prompt)
_REVIEW_NUMBER_RE = re.compile(r"\[(\d+)\] \(인용:")

#: 화면 노출이 금지된 영문 내부 키 모양 — validate.INTERNAL_KEY_SHAPE와 같은 판정
_INTERNAL_KEY_RE = re.compile(r"[a-z][a-z0-9_]*")


def _fixture_fragments() -> dict[int, dict[str, str]]:
    """fixture JSON을 real.py 원시 조각 dict[int, dict] 모양으로 바꾼다."""
    return {
        int(number): dict(fields)
        for number, fields in _FRAGMENTS_FIXTURE.items()
        if number.isdigit()
    }


def _expected_sentence_total() -> int:
    """fixture가 약속한 초안 문장 수 (본문 9장 + 요약) — 매직 넘버 대신 실측."""
    body = sum(
        len(payload["문장들"])
        for payload in _RESPONSES_FIXTURE["장별_응답"].values()
    )
    return body + len(_RESPONSES_FIXTURE["핵심요약_응답"]["문장들"])


# ══════════════════════════════════════════════════════════
# 가짜 포트 — test_real_cache의 FakeEngine을 JYP 리허설용으로 확장
# ══════════════════════════════════════════════════════════


class _JypFakeMessages(_FakeMessages):
    """v2 작가·검수 프롬프트에 fixture JSON을 돌려주는 가짜 provider 경계.

    ★ 프롬프트 «어느 쪽 요청인가»만 상수 포함 여부로 가른다 — 문장 내용을
      들여다보는 검사가 아니다. v2가 아닌 프롬프트(공고 판별 등)는 기존
      _FakeMessages처럼 content 없이 응답한다 (FakeEngine._ask는 content를
      읽지 않고 스스로 답을 만든다).
    """

    def __init__(self) -> None:
        super().__init__()
        #: 장 id → 작가 호출 수. 장마다 정확히 1회여야 한다 (재요청 0회).
        self.section_calls: dict[str, int] = {}
        self.summary_calls = 0
        self.review_calls = 0
        #: 재작성 프롬프트는 전부 «참» 검수라 한 번도 오면 안 된다.
        self.rewrite_prompts: list[str] = []

    def _route(self, prompt: str) -> Optional[str]:
        """v2 프롬프트면 fixture 응답 문자열을, 아니면 None을 돌려준다."""
        if REVIEW_PROMPT_HEADER in prompt:
            self.review_calls += 1
            numbers = [int(value) for value in _REVIEW_NUMBER_RE.findall(prompt)]
            return json.dumps(
                {"판정": [{"번호": number, "결과": "참"} for number in numbers]},
                ensure_ascii=False,
            )
        if REWRITE_PROMPT_HEADER in prompt:
            self.rewrite_prompts.append(prompt)
            return ""
        if SUMMARY_PROMPT_HEADER in prompt:
            self.summary_calls += 1
            return json.dumps(_RESPONSES_FIXTURE["핵심요약_응답"], ensure_ascii=False)
        for section_id in SECTION_IDS:
            if SECTION_GUIDES[section_id] in prompt:
                self.section_calls[section_id] = (
                    self.section_calls.get(section_id, 0) + 1
                )
                return json.dumps(
                    _RESPONSES_FIXTURE["장별_응답"][section_id], ensure_ascii=False
                )
        return None

    def create(self, **kwargs: Any) -> SimpleNamespace:
        response = super().create(**kwargs)
        messages = kwargs.get("messages") or [{}]
        prompt = str(messages[0].get("content") or "")
        payload = self._route(prompt)
        if payload is not None:
            # _v2_ask_via_provider가 읽는 content 블록 계약을 그대로 흉내 낸다
            response.content = [SimpleNamespace(text=payload)]
        return response


class _JypFakeClient(_FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.messages = _JypFakeMessages()


class _JypFakeEngine(FakeEngine):
    """JYP 리허설용 가짜 엔진 — 조각·재무·법인명만 골든 샘플 결로 바꾼다.

    나머지 계약(판정·공시·수집 흐름)은 test_real_cache.FakeEngine 그대로라
    real.py의 수집(6)·사전 게이트(7)까지 기존 시험과 같은 길을 지난다.
    """

    def __init__(self) -> None:
        super().__init__()
        self.client = _JypFakeClient()

    def get_json(
        self, endpoint: str, params: dict[str, Any], counter: Any
    ) -> dict[str, Any]:
        payload = super().get_json(endpoint, params, counter)
        if endpoint == "company.json" and payload.get("corp_code") == CORP_ID:
            payload = dict(payload)
            payload["corp_name"] = COMPANY_NAME
        return payload

    def read_filing_text(self, path: str) -> str:
        # 절 표제·파트너 문구가 없는 한 줄 — 수집 보정 단계가 조각을 더하지 않아
        # fixture 조각 11개가 그대로 유지된다 (부록 1:1 단정을 결정적으로 만든다).
        return "제이와이피엔터테인먼트는 아티스트 발굴과 음악 콘텐츠의 제작·유통 사업을 영위한다."

    def fetch_financials(
        self, corp_code: str, counter: Any
    ) -> tuple[dict[str, Any], list[int]]:
        """골든 샘플 수치(억원: 8,219·5,665 등)로 3개년 실적표 재료를 만든다."""

        def row(
            account_id: str, account_nm: str, amounts: tuple[int, int, int]
        ) -> dict[str, str]:
            return {
                "account_id": account_id,
                "account_nm": account_nm,
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

        financials = {
            "status": "000",
            "reprt_code": "11011",
            "list": [
                row(
                    "ifrs-full_Revenue",
                    "매출액",
                    (821_900_000_000, 566_500_000_000, 345_900_000_000),
                ),
                row(
                    "dart_OperatingIncomeLoss",
                    "영업이익",
                    (138_900_000_000, 95_700_000_000, 44_500_000_000),
                ),
                row(
                    "ifrs-full_ProfitLoss",
                    "당기순이익",
                    (123_600_000_000, 70_200_000_000, 33_100_000_000),
                ),
            ],
        }
        return financials, list(self.fiscal_years)

    def make_fragments(
        self, filing_text: str, financials: Optional[dict[str, Any]]
    ) -> dict[int, dict[str, str]]:
        return _fixture_fragments()


# ══════════════════════════════════════════════════════════
# 공통 준비
# ══════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _paid_provider_budget_context():
    """직접 RealPipeline 시험도 웹 worker와 같은 유료 문맥에서 실행한다."""
    with provider_budget.activate(100_000.0):
        yield


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> _JypFakeEngine:
    """진짜 엔진 대신 JYP 가짜를 끼우고 ENGINE_V2=1을 켠다 — 과금 경로 없음."""
    fake = _JypFakeEngine()
    monkeypatch.setattr(real, "_engine", lambda: fake)
    monkeypatch.setattr(
        real,
        "_company_catalog",
        lambda: ((CORP_ID, COMPANY_NAME, "", "000001", "20260819"),),
    )
    monkeypatch.setenv(real.ENGINE_V2_ENV_NAME, real.ENGINE_V2_ENV_ON)
    return fake


def _run(engine: _JypFakeEngine) -> RunResult:
    user_input = UserInput(
        company=COMPANY_NAME, job=JOB, region="서울 강남구", posting_text=POSTING
    )
    card = CompanyCard(
        legal_name=COMPANY_NAME,
        typed_name=COMPANY_NAME,
        address="서울특별시 강남구 테헤란로 1",
        ceo="홍길동",
        founded="20000101",
        ref=CORP_ID,
    )
    result = real.RealPipeline().run(user_input, card)
    assert result.outcome is Outcome.REPORT, result.message
    assert result.report is not None
    return result


def _loose(text: str) -> re.Pattern[str]:
    """PDF 추출 특성(줄바꿈·가변 공백)에 흔들리지 않는 느슨한 찾기 패턴.

    공백을 뺀 글자들 사이에 임의 공백을 허용한다 — CJK 줄바꿈이 글자
    사이 어디서든 일어나도 제목·표지를 안정적으로 찾기 위한 장치다.
    """
    return re.compile(r"\s*".join(re.escape(ch) for ch in text.replace(" ", "")))


# ══════════════════════════════════════════════════════════
# ① 파이프라인 전체 — 수집→compose→verify→요약→render→validate_v2
# ══════════════════════════════════════════════════════════


def test_ENGINE_V2_전체_흐름이_검증된_v2_보고서를_만든다(
    engine: _JypFakeEngine,
) -> None:
    result = _run(engine)
    report = result.report
    assert report is not None

    # v2 스키마 + 9장 전부, v3 정본 순서 (장 삭제 없음)
    assert report.schema_version == ENGINE_V2_SCHEMA_VERSION
    assert [section.cell for section in report.sections] == list(SECTION_IDS)
    # 장마다 fixture 6문장이 안내문 없이 전부 생존했다
    for section in report.sections:
        assert len(section.prose_lines) == 6, section.cell

    all_prose = [
        text for section in report.sections for text, _cite in section.prose_lines
    ]
    # 해석 표지와 [n] 인용이 본문에 실제로 찍힌다
    assert any(INTERPRETATION_MARKER in text for text in all_prose)
    assert any(re.search(r"\[\d+\]", text) for text in all_prose)
    # 단위 붙은 수치(8,219억 원)를 말한 «확인» 문장이 수치 검증(3-2)을 지나
    # 해석 강등 없이 «확인»으로 살아남았다 — 검증이 실제로 돌았다는 실측이다.
    unit_number_lines = [text for text in all_prose if "8,219억" in text]
    assert unit_number_lines
    assert all(
        not text.endswith(INTERPRETATION_MARKER) for text in unit_number_lines
    )

    # 4장에만 프로그램 실적표가 실리고 골든 샘플 수치가 억원 표시로 들어간다
    tables_by_cell = {section.cell: section.tables for section in report.sections}
    for section_id in SECTION_IDS:
        expected = 1 if section_id == "past_changes" else 0
        assert len(tables_by_cell[section_id]) == expected, section_id
    performance = tables_by_cell["past_changes"][0]
    assert performance.numeric is True
    assert any("8,219" in cell for row in performance.rows for cell in row)

    # 부록: 인용된 조각 1~11 전부, 번호는 조각 번호 그대로 (본문 [n]과 1:1)
    assert sorted(source.number for source in report.citations) == list(range(1, 12))

    # 핵심 요약 — fixture 4문장, «확인»은 표지 없음·«해석»만 표지
    assert len(report.summary_items) == 4
    assert "8,219억" in report.summary_items[1].text
    assert INTERPRETATION_MARKER not in report.summary_items[1].text
    assert report.summary_items[3].text.endswith(INTERPRETATION_MARKER)

    # 관측 수치 — 초안 58문장이 전부 생존했고 보고서가 나갔으니 1 차감이다
    assert result.charged is True
    assert result.fragments_collected == 11
    assert result.fragments_cited == 11
    assert result.sentences_made == _expected_sentence_total()
    assert result.sentences_passed == _expected_sentence_total()


# ══════════════════════════════════════════════════════════
# ② 무과금 — 유료 호출 0건, 가짜 ask 호출 횟수만 증가
# ══════════════════════════════════════════════════════════


def test_유료_호출은_없고_가짜_ask_횟수만_증가한다(
    engine: _JypFakeEngine,
) -> None:
    result = _run(engine)

    messages = engine.client.messages
    # 작가: 장 9회(각 1회, 재요청 0회) + 요약 1회
    assert messages.section_calls == {section_id: 1 for section_id in SECTION_IDS}
    assert messages.summary_calls == 1
    # 검수: 본문 1회 + 요약 1회. 전부 «참»이라 재작성은 0회다.
    assert messages.review_calls == 2
    assert messages.rewrite_prompts == []
    # v1 생성·검증 AI는 한 번도 나가지 않았다 (v2 분기가 전담)
    assert engine.generate_ai_calls == 0
    # 모든 호출이 가짜 계량 client 경계를 지났다 — 네트워크 SDK가 아예 없다
    assert messages.calls >= 12
    assert result.model == "가짜모델"


# ══════════════════════════════════════════════════════════
# ③ PDF — v2 Report가 실제 조립부를 지나 PDF 바이트로 나온다
# ══════════════════════════════════════════════════════════


def test_v2_보고서가_PDF_바이트와_요구_구조까지_도달한다(
    engine: _JypFakeEngine,
) -> None:
    result = _run(engine)
    report = result.report
    assert report is not None

    # 프로덕션 진입점을 monkeypatch 없이 그대로 탄다(04장 3-4절 2항 배선 완료).
    # v2는 v1 canonical 게이트(build_published_report)를 타지 않고 composer의
    # validate_v2만 다시 확인한 뒤 실제 조립부를 지난다.
    candidate = pdf_release.prepare_pdf_release(report)
    pdf_bytes = candidate.pdf_bytes
    assert pdf_bytes.startswith(b"%PDF-")
    # v2 사실 장부 대체 결속(release.report_fact_id_ledger) — fact_records가
    # 없는 v2는 실제 부록에 실린 인용 번호로 후보 무결성 검사를 통과한다.
    assert candidate.expected_fact_ids == tuple(
        f"v2-citation-{number}" for number in range(1, 12)
    )

    raw_text = "\n".join(
        page.extract_text() or ""
        for page in PdfReader(io.BytesIO(pdf_bytes)).pages
    )
    # CJK 줄바꿈은 아무 글자 사이에서나 일어난다 — 줄바꿈만 붙여서 되돌린다
    # (줄 안의 띄어쓰기는 그대로 남아 토큰 검사에 쓸 수 있다).
    dewrapped = "".join(raw_text.splitlines())

    # 1~9장 제목 전부 (표시번호 + v3 정본 제목)
    for section_id in SECTION_IDS:
        heading = f"{SECTION_DISPLAY_NUMBERS[section_id]}. {SECTION_TITLES[section_id]}"
        assert _loose(heading).search(dewrapped), heading
    # 표지 요약과 부록
    assert _loose("핵심 요약").search(dewrapped)
    assert _loose("부록. 출처와 검증 상태").search(dewrapped)
    # 해석 표지·[n] 인용·골든 샘플 수치
    assert re.search(r"—\s*해\s*석", dewrapped)
    assert re.search(r"\[\s*\d+\s*\]", dewrapped)
    assert _loose("8,219").search(dewrapped)
    # 영문 내부 키(snake_case 등) 전체일치 토큰 0건 — 단계1 출고 규칙과 동일 판정
    leaked = [
        token
        for token in dewrapped.split()
        if _INTERNAL_KEY_RE.fullmatch(token)
    ]
    assert leaked == [], leaked
