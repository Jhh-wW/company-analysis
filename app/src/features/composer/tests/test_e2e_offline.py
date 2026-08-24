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
    # 장마다 fixture 6문장이 안내문 없이 전부 생존했다.
    # ★ 예외 1장 — fixture 자체가 회사 표어를 1장과 8장에 둘 다 실었다.
    #   정본 §4에서 공식 가치는 8장 소유라 장 간 중복 제거가 1장 쪽을 8장으로
    #   모은다. 소실이 아니라 이동이므로 8장에는 그대로 있다.
    for section in report.sections:
        expected = 6 - (1 if section.cell == "identity" else 0)
        assert len(section.prose_lines) == expected, section.cell

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

    # 표는 «정해진 장에만» 실린다 — 4장 실적표(trend), 7장 경로표(flow).
    # ★ v2-27 전에는 「4장 외에는 표가 0개」였는데, 그것은 7장 흐름도가
    #   엔진 안에서 사라지던 «결함을 기대값으로 굳힌» 것이었다. 지금은
    #   7장 경로표가 정상적으로 실린다(이음매 시험이 화면까지 지킨다).
    tables_by_cell = {section.cell: section.tables for section in report.sections}
    expected_tables = {"past_changes": 1, "operations_partners": 1}
    for section_id in SECTION_IDS:
        assert len(tables_by_cell[section_id]) == expected_tables.get(
            section_id, 0
        ), section_id
    assert tables_by_cell["operations_partners"][0].presentation == "flow"
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
    # fixture가 회사 표어를 1장·8장에 둘 다 실어, 중복 제거가 1장 쪽을 옮긴다.
    assert result.sentences_passed == _expected_sentence_total() - 1


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


# ══════════════════════════════════════════════════════════
# ④ ★ 이음매 시험 — 작가 응답부터 «화면 HTML»까지 통째로
# ══════════════════════════════════════════════════════════
#
# ★ 왜 이 시험이 있나 (세 번 놓친 실측 사고)
#   7장 흐름도가 화면에도 PDF에도 안 나왔다. 원인이 «사슬의 네 마디»에 걸쳐
#   있었는데, 마디마다 따로 시험이 있어서 한 마디를 고칠 때마다 시험은 전부
#   통과했고 화면에는 계속 안 나왔다:
#     ① 화면이 도식 함수를 안 부름        (result.html)      → v2-21
#     ② 중복 제거가 flow_rows를 버림       (dedupe.py)        → v2-24
#     ③ 검증이 flow_rows를 버림            (verify.py 두 곳)  → v2-25
#     ④ 도식 검증이 근거 있는 줄까지 다 버림 (diagram_check.py) → v2-27
#   「작가가 경로표를 내면 화면에 흐름도가 뜬다」를 «통째로» 보는 시험이
#   하나도 없었던 것이 진짜 원인이다. 이 시험이 그 자리를 메운다.
#
# ★ 이 시험은 마디를 하나라도 끊으면 반드시 빨간불이 된다.
#   위 네 곳 중 어디를 되돌려도 실패한다 — 그것이 이 시험의 존재 이유다.


def _v2_report_to_result_page(report) -> str:
    """엔진이 만든 v2 보고서를 «진짜 결과 화면»에 태워 HTML을 받는다."""
    import uuid

    from fastapi.testclient import TestClient

    from src.features.auth import constants as auth_constants
    from src.features.auth import logic as auth_logic
    from src.web.main import app
    from src.web import job_runtime
    from src.web.routers import reports as reports_router

    job_id = f"seam-{uuid.uuid4().hex}"
    job_runtime._JOBS.pop(job_id, None)
    saved = {"보고서": report}

    with pytest.MonkeyPatch.context() as mp:
        # ★ 이 시험은 composer 폴더에 있어 web/tests/conftest.py의 공개 모드
        #   설정을 못 받는다. 같은 값을 여기서 명시한다 — 안 하면 결과 화면
        #   대신 로그인 안내가 돌아와 「도식이 없다」로 잘못 읽힌다.
        mp.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "0")
        mp.setenv(auth_constants.ENV_ADMIN_EMAILS, "admin@example.com")
        job_runtime._start_job_runtime()
        mp.setattr(job_runtime, "_load_saved_report", lambda _job_id: saved["보고서"])
        mp.setattr(job_runtime, "_link_expired", lambda _report: False)
        mp.setattr(
            reports_router, "_release_state", lambda **_kwargs: (object(), None)
        )
        mp.setattr(reports_router, "is_notion_configured", lambda: True)
        session = auth_logic.create_session("admin@example.com", True)
        with TestClient(app) as client:
            response = client.get(
                f"/result/{job_id}",
                cookies={auth_constants.SESSION_COOKIE_NAME: session.token},
            )
    assert response.status_code == 200, response.text[:400]
    return response.text


def test_이음매_작가가_낸_경로표가_화면_흐름도까지_도달한다(
    engine: _JypFakeEngine,
) -> None:
    """★ 사슬 전체를 한 번에 지킨다 — 한 마디만 끊겨도 여기서 빨간불이 난다."""
    result = _run(engine)
    report = result.report
    assert report is not None

    # ── 마디 1: 엔진이 7장에 flow 표를 실었는가 ──────────
    운영장 = next(s for s in report.sections if s.cell == "operations_partners")
    assert 운영장.tables, (
        "7장에 표가 없습니다 — 작가가 낸 경로표가 엔진 안에서 사라졌습니다. "
        "compose→verify→dedupe→check_diagrams 중 한 곳이 flow_rows를 버렸습니다."
    )
    경로표 = 운영장.tables[0]
    assert 경로표.presentation == "flow", (
        f"7장 표의 표현이 flow가 아닙니다: {경로표.presentation!r}"
    )
    assert len(경로표.rows) >= 1, "경로표에 남은 줄이 없습니다 — 도식 검증이 다 버렸습니다"

    # ── 마디 2: 화면이 그것을 «도식»으로 그리는가 ────────
    body = _v2_report_to_result_page(report)
    assert 'class="flow-row"' in body, (
        "화면에 흐름도가 없습니다 — 표는 있는데 도식으로 안 그려졌습니다. "
        "result.html이 표 매크로를 부르는지, visualization.py의 flow 판정 "
        "조건(열 3~4·행 1~5·빈 칸 없음)을 넘는지 확인하세요."
    )
    # 도식으로 그렸으면 같은 표를 평범한 표로 또 내지 않는다.
    assert 경로표.caption in body


def test_이음매_중복제거가_일어나도_경로표는_화면까지_간다(
    engine: _JypFakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 중복 제거가 7장을 «재조립»하는 경로까지 지킨다.

    골든 fixture만으로는 7장이 중복 제거를 안 탄다(다른 장과 겹치는 문장이
    없다). 그러면 dedupe가 장을 그대로 돌려주므로, dedupe가 flow_rows를
    빠뜨려도 이음매 시험이 못 잡는다 — v2-24가 고친 결함이 조용히 되살아난다.
    그래서 «이 시험 안에서만» 8장 소유 문장을 7장에 하나 심어 재조립을
    강제한다. 공유 fixture는 건드리지 않는다(다른 시험의 문장 수 계약이 깨진다).
    """
    import copy

    responses = copy.deepcopy(_RESPONSES_FIXTURE)
    표어 = responses["장별_응답"]["culture"]["문장들"][0]
    responses["장별_응답"]["operations_partners"]["문장들"].append(
        {"글": 표어["글"], "인용": list(표어["인용"]), "등급": "확인"}
    )
    monkeypatch.setitem(globals(), "_RESPONSES_FIXTURE", responses)

    result = _run(engine)
    report = result.report
    assert report is not None

    운영장 = next(s for s in report.sections if s.cell == "operations_partners")
    # 중복이 «실제로» 옮겨졌는지 먼저 확인한다 — 안 옮겨졌으면 이 시험은 헛돈다.
    assert all(표어["글"] not in text for text, _cite in 운영장.prose_lines), (
        "중복 제거가 안 일어났습니다 — 이 시험이 재조립 경로를 못 지킵니다"
    )
    assert 운영장.tables and 운영장.tables[0].presentation == "flow", (
        "중복 제거가 장을 다시 조립하면서 경로표를 버렸습니다"
    )
    assert 'class="flow-row"' in _v2_report_to_result_page(report)


def test_이음매_2장_구성_도식과_4장_추이_도식도_화면까지_간다(
    engine: _JypFakeEngine,
) -> None:
    """7장만 지키면 나머지가 조용히 끊긴다 — 세 도식을 한 시험에서 함께 본다."""
    result = _run(engine)
    report = result.report
    assert report is not None

    표현 = {
        section.cell: [table.presentation for table in section.tables]
        for section in report.sections
        if section.tables
    }
    assert "trend" in 표현.get("past_changes", []), f"4장 추이표가 없습니다: {표현}"
    assert "flow" in 표현.get("operations_partners", []), f"7장 경로표가 없습니다: {표현}"

    body = _v2_report_to_result_page(report)
    assert 'class="trend-panels"' in body, "4장 추이 도식이 화면에 없습니다"
    assert 'class="flow-row"' in body, "7장 흐름도가 화면에 없습니다"


# ══════════════════════════════════════════════════════════
# ⑤ 예산 — «진짜 본조사 상한 900원»에서도 완주한다
# ══════════════════════════════════════════════════════════
#
# ★ 왜 이 시험이 있나 (적대 검토 실측)
#   위 이음매 시험들은 provider_budget.activate(100_000.0)로 돈다. 10만원
#   문맥에서는 어떤 호출을 추가해도 절대 한도를 못 넘으므로, 「예산 초과로
#   보고서 «전체»가 실패한다」는 가장 비싼 실패를 저장소 어디에서도 못 잡았다.
#   실측: 도식 검수를 검수용 상한(8000토큰) 그대로 쓰면 예약만으로 195원 —
#   본조사 900원의 21.7%다. 실제 실행비가 이미 584원(삼성전자)이라 여유가
#   8% 미만이었다. 그래서 도식 전용 상한(512토큰)을 따로 뒀고, 이 시험이
#   그 결정을 지킨다.


def test_한_호출의_예약이_본조사_예산을_혼자_먹지_않는다() -> None:
    """★ 예산은 «출력 상한»으로 미리 잡는다 — 상한이 크면 호출 한 번이 예산을 먹는다.

    오프라인 파이프라인을 900원 문맥으로 그냥 돌릴 수는 없다: 가짜 client가
    돌려주는 모델 이름이 «모르는 모델»이라 요금표가 최고가로 잡히고(작가 1회
    예약만 1,050원) 실제 운영 요금과 무관한 값이 나온다. 그래서 여기서는
    «운영에서 실제로 쓰는 모델·상한»으로 예약 비용을 직접 계산해 못 박는다.
    """
    from src.core.constants import GENERATION_MODEL
    from src.features.budget.constants import (
        PAID_PHASE_PROVIDER_BUDGET_KRW,
        SPEND_PHASE_PIPELINE,
    )
    from src.features.budget.provider_budget import usage_cost_krw
    from src.features.pipeline.real import (
        V2_DIAGRAM_MAX_TOKENS,
        V2_REVIEWER_MAX_TOKENS,
        V2_WRITER_MAX_TOKENS,
    )

    예산 = PAID_PHASE_PROVIDER_BUDGET_KRW[SPEND_PHASE_PIPELINE]
    # 조각 전체 + 앞 장 문장까지 실린 «가장 큰» 프롬프트를 넉넉히 가정한다.
    입력상한 = 30_000

    비용 = {
        "작가": usage_cost_krw(GENERATION_MODEL, 입력상한, V2_WRITER_MAX_TOKENS),
        "검수": usage_cost_krw(GENERATION_MODEL, 입력상한, V2_REVIEWER_MAX_TOKENS),
        "도식": usage_cost_krw(GENERATION_MODEL, 3_000, V2_DIAGRAM_MAX_TOKENS),
    }
    for 이름, 값 in 비용.items():
        assert 값 < 예산 / 2, (
            f"{이름} 호출 1회 예약이 {값:.0f}원 — 본조사 예산 {예산:.0f}원의 절반을 "
            f"넘습니다. 다른 호출이 이미 쓴 돈과 합쳐지면 보고서 전체가 실패합니다."
        )

    # ★ 도식 검수는 «덧붙인» 단계다. 기존 검수의 절반도 안 되게 유지한다.
    assert 비용["도식"] < 비용["검수"] / 2, (
        f"도식 검수 예약 {비용['도식']:.0f}원이 기존 검수 {비용['검수']:.0f}원에 "
        f"비해 큽니다 — 전용 상한이 제 역할을 못 하고 있습니다"
    )


def test_도식_검수는_전용_상한을_쓴다() -> None:
    """★ 검수용 상한을 그대로 쓰면 예약만으로 예산의 5분의 1을 먹는다."""
    from src.features.pipeline.real import (
        V2_DIAGRAM_MAX_TOKENS,
        V2_REVIEWER_MAX_TOKENS,
    )

    assert V2_DIAGRAM_MAX_TOKENS < V2_REVIEWER_MAX_TOKENS, (
        "도식 검수가 검수용 상한을 그대로 쓰고 있습니다"
    )
    # 응답은 «경로 줄마다 참/거짓» 한 줄씩(장당 최대 5줄)이다. 넉넉해도 1000 미만.
    assert V2_DIAGRAM_MAX_TOKENS <= 1024
