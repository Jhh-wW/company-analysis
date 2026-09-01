"""엔진 v2 오프라인 E2E 리허설 — 무과금 (엔진 v2 소단계 3-5).

★ 04장 3-5절의 「로컬 무과금 리허설」을 pytest로 옮긴 것이다:
  가짜 엔진·가짜 계량 client 위에서 ENGINE_V2=1로 파이프라인 «전체»를 돌려
  수집 조각 → compose → verify → 요약 → render → validate_v2 → PDF 바이트까지
  실제 코드 경로가 끝까지 이어지는지 본다. AI·네트워크 호출은 0회다.

★ 가짜 작가 응답(fixtures/jyp_ask_responses.json)은 골든 샘플 보고서의
  실제 문장에서 발췌했다 — 장별 6문장,
  인용 조각 id와 확인/해석 등급 포함. 수집 조각(fixtures/jyp_fragments.json)은
  그 문장들의 숫자가 전부 원문에 존재하도록 같은 근거에서 발췌했다. 이는
  예전 3-2 «원문 문자열 대조»를 통과한 골든 입력이라는 뜻이지, 수치의 지표·
  기간·공식까지 결속한 NumericBinding이라는 뜻은 아니다. 입력의 장별 6문장
  하한은 그대로 지키되, 새 생성 공개본에서는 미결속 AI 수치를 문장 단위로
  빼고 DART 원값에서 프로그램이 계산한 claim만 남아 PARTIAL이 되는지 본다.

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

from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.budget import provider_budget
from src.features.composer.constants import (
    GRADE_INTERPRETED,
    SECTION_GUIDES,
    SECTION_IDS,
    SECTION_TITLES,
)
from src.features.composer.diagram_check import FLOW_REVIEW_PROMPT_HEADER
from src.features.composer.logic import (
    SUMMARY_MAX_SENTENCES,
    SUMMARY_MIN_SENTENCES,
    SUMMARY_PROMPT_HEADER,
)
from src.features.composer.render import (
    ENGINE_V2_SCHEMA_VERSION,
    INTERPRETATION_MARKER,
    SECTION_DISPLAY_NUMBERS,
)
from src.features.composer.verify import REVIEW_PROMPT_HEADER, REWRITE_PROMPT_HEADER
from src.features.export_pdf import release as pdf_release
from src.features.pipeline import real
from src.features.pipeline.port import CompanyCard, Grade, Outcome, RunResult, UserInput
from src.features.pipeline.tests.test_real_cache import (
    CORP_ID,
    JOB,
    POSTING,
    FakeEngine,
    _FakeClient,
    _FakeMessages,
)
from src.shared import engine_build_identity as build_identity_contract
from src.shared.report_quality.assessment import has_public_numeric_token
from src.shared.report_quality.constants import MIN_CLAIMS_PER_COVERED_SECTION
from src.shared.report_quality.numeric_validation import (
    validate_versioned_numeric_record,
)
from src.shared.report_evidence.constants import ReleaseMode

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
_REVIEW_NUMBER_RE = re.compile(
    r"\[(\d+)\] \(등급: [^,\n]+, 인용:"
)

#: 도식 검수에서 경로 번호를 읽는 모양. 관계 글자는 보지 않는다.
_FLOW_NUMBER_RE = re.compile(r"^\[(\d+)\] 경로\(JSON 배열\):", re.MULTILINE)

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


def _fixture_unbound_numeric_by_section() -> dict[str, tuple[str, ...]]:
    """골든 AI 산문 중 숫자 토큰은 있지만 NumericBinding은 없는 문장들."""

    return {
        section_id: tuple(
            str(sentence["글"])
            for sentence in payload["문장들"]
            if has_public_numeric_token(str(sentence["글"]))
        )
        for section_id, payload in _RESPONSES_FIXTURE["장별_응답"].items()
    }


def _fixture_unbound_numeric_summary() -> tuple[str, ...]:
    return tuple(
        str(sentence["글"])
        for sentence in _RESPONSES_FIXTURE["핵심요약_응답"]["문장들"]
        if has_public_numeric_token(str(sentence["글"]))
    )


def _fixture_unbound_numeric_grades_by_section() -> dict[str, dict[str, str]]:
    """장별 «미결속 수치 문장 텍스트 → 등급» 맵 (2026-08-29 사용자 결정 ③).

    구조화 근거(NumericBinding) 없이 숫자만 든 문장 중, «확인» 등급 +
    인용 있음은 검수 AI가 참으로 판정하면(이 fixture는 전부 참) 이제
    통과한다. «해석» 등급만 여전히 구조화 근거를 요구해 제외된다 — 해석은
    사실 주장이 아니라 애초에 구조화 근거를 만들 길이 없기 때문이다.
    """
    return {
        section_id: {
            str(sentence["글"]): str(sentence["등급"])
            for sentence in payload["문장들"]
            if has_public_numeric_token(str(sentence["글"]))
        }
        for section_id, payload in _RESPONSES_FIXTURE["장별_응답"].items()
    }


# fixture의 회사 표어가 identity와 culture에 겹친다. 정본 소유 장은 culture라
# 기존 장 간 중복 제거가 identity에서 한 문장만 옮긴다.
_DEDUPE_REMOVED_BY_SECTION = {"identity": 1}


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
        if FLOW_REVIEW_PROMPT_HEADER in prompt:
            numbers = [int(value) for value in _FLOW_NUMBER_RE.findall(prompt)]
            return json.dumps(
                {"판정": [{"번호": number, "결과": "참"} for number in numbers]},
                ensure_ascii=False,
            )
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
        self,
        corp_code: str,
        counter: Any,
        *,
        business_date: Any = None,
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
    callbacks = ProviderAttemptCallbacks(
        lambda _provider, _operation, _reserved: object(),
        lambda _token: None,
        lambda _token: None,
        lambda _token, _observation: None,
    )
    with provider_budget.activate(100_000.0), attempt_context.activate(callbacks):
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
    monkeypatch.setenv(
        real.REPORT_RELEASE_MODE_ENV_NAME,
        ReleaseMode.SHADOW.value,
    )
    # v2 캐시는 정확한 immutable 배포 commit이 있을 때만 켜진다.
    monkeypatch.setenv("RENDER_GIT_COMMIT", "1" * 40)
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

    # 먼저 «입력 골든 하한»을 그대로 잠근다. 공개 안전 규칙이 생겼다고 fixture
    # 자체를 3문장짜리로 줄여 기대값을 맞추면 과거 6문장 품질 약속을 숨기게 된다.
    fixture_sections = _RESPONSES_FIXTURE["장별_응답"]
    assert set(fixture_sections) == set(SECTION_IDS)
    assert all(len(payload["문장들"]) == 6 for payload in fixture_sections.values())
    assert _expected_sentence_total() == 58

    # 이 골든 입력의 숫자는 원문 문자열 대조는 통과하지만 AI JSON에는 지표·
    # 기간·공식의 NumericBinding이 없다. 본문 16문장과 요약 2문장이 그 대상임을
    # 실측으로 잠그고, 공개본에서 그 문장들만 빠졌는지 아래에서 확인한다.
    unbound_by_section = _fixture_unbound_numeric_by_section()
    unbound_counts = {
        section_id: len(sentences)
        for section_id, sentences in unbound_by_section.items()
    }
    assert unbound_counts == {
        "identity": 0,
        "business_model": 3,
        "portfolio": 1,
        "past_changes": 5,
        "current_challenges": 2,
        "future_strategy": 2,
        "operations_partners": 1,
        "culture": 0,
        "competitive_position": 2,
    }
    unbound_summary = _fixture_unbound_numeric_summary()
    assert len(unbound_summary) == 2

    # DART 3개년 원값에서 프로그램이 계산한 세 지표의 누적 증감률만 구조화
    # 수치 claim으로 다시 들어온다. AI 산문을 역추출해 FactRecord로 꾸미지 않는다.
    assert len(report.fact_records) == 3
    assert all(fact.section_owner == "past_changes" for fact in report.fact_records)
    assert all(fact.formula == "rate" for fact in report.fact_records)
    assert all(
        validate_versioned_numeric_record(fact) == ()
        for fact in report.fact_records
    )

    structured_counts = {
        section_id: sum(
            fact.section_owner == section_id for fact in report.fact_records
        )
        for section_id in SECTION_IDS
    }
    # 최종 장별 수는 «6문장 하한을 낮춘 값»이 아니라 원래 6 - «실제로 제외된»
    # 수치 문장 - 기존 중복 이동 + 검증된 프로그램 claim이다. 2026-08-29
    # 사용자 결정 ③ 이후 «실제로 제외된» 수는 unbound_by_section 전체가
    # 아니라 그중 «해석» 등급뿐이다(아래서 등급별로 갈라 실측으로 확인한다).
    grades_by_section = _fixture_unbound_numeric_grades_by_section()
    interpreted_counts_by_section = {
        section_id: sum(
            1 for grade in grades.values() if grade == GRADE_INTERPRETED
        )
        for section_id, grades in grades_by_section.items()
    }
    for section in report.sections:
        expected = (
            6
            - interpreted_counts_by_section[section.cell]
            - _DEDUPE_REMOVED_BY_SECTION.get(section.cell, 0)
            + structured_counts[section.cell]
        )
        assert len(section.prose_lines) == expected, section.cell
        assert len(section.prose_lines) >= MIN_CLAIMS_PER_COVERED_SECTION

    all_prose = [
        text for section in report.sections for text, _cite in section.prose_lines
    ]
    # «해석» 등급 수치 문장은 여전히 구조화 근거가 없어 빠진다. «확인» 등급
    # 수치 문장(인용 있음, 검수 AI가 참으로 판정)은 이제 살아남는다 — 이게
    # 2026-08-29 사용자 결정 ③의 «회복»이다. 두 방향을 각각 실측으로 잠근다.
    for grades in grades_by_section.values():
        for text, grade in grades.items():
            appears = any(text in visible for visible in all_prose)
            if grade == GRADE_INTERPRETED:
                assert not appears, f"해석 등급 수치 문장이 남아있다: {text}"
            else:
                assert appears, f"확인 등급 수치 문장(검증 통과)이 사라졌다: {text}"
    assert all(
        any(fact.claim in visible for visible in all_prose)
        for fact in report.fact_records
    )
    assert report.grade is Grade.PARTIAL
    assert any(
        "숫자·날짜 문장" in reason for reason in report.shortfall_reasons
    )
    # 해석 표지와 [n] 인용이 본문에 실제로 찍힌다
    assert any(INTERPRETATION_MARKER in text for text in all_prose)
    assert any(re.search(r"\[\d+\]", text) for text in all_prose)
    # 2026-08-29 사용자 결정 ③ 이전에는 «원문에 값이 있었다는 이유만으로
    # 8,219억 AI 문장을 공개하지 않는다»였다. 그 문장(등급 확인 + 인용
    # ["2"])은 이제 두 검사(수치 대조·검수 AI)를 통과해 살아남는다 — 구조화
    # 실적표의 원값(아래 "8,219")과 나란히 실린다. 위 grades_by_section
    # 루프가 이미 이 문장의 생존을 등급별로 확인했으므로, 여기서는 그 결론을
    # 다시 한 번 명시적으로 못 박는다.
    assert any("8,219억" in text for text in all_prose)

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

    # 핵심 요약도 같은 계약이다. 미결속 수치 2문장은 빠지고, 숫자 없는 원래
    # 요약 2문장은 보존되며 안전한 본문 한 문장으로 최소 3문장을 채운다.
    assert (
        SUMMARY_MIN_SENTENCES
        <= len(report.summary_items)
        <= SUMMARY_MAX_SENTENCES
    )
    visible_summary = [item.text for item in report.summary_items]
    for unsafe_text in unbound_summary:
        assert all(unsafe_text not in text for text in visible_summary)
    safe_fixture_summary = [
        str(sentence["글"])
        for sentence in _RESPONSES_FIXTURE["핵심요약_응답"]["문장들"]
        if not has_public_numeric_token(str(sentence["글"]))
    ]
    assert len(safe_fixture_summary) == 2
    assert all(
        any(safe_text in visible for visible in visible_summary)
        for safe_text in safe_fixture_summary
    )

    # 관측 수치도 입력 하한 58을 숨기지 않고 처분별로 계산한다.
    assert result.charged is True
    assert result.fragments_collected == 11
    assert result.fragments_cited == 11
    assert result.sentences_made == _expected_sentence_total()
    safe_summary_before_supplement = (
        len(_RESPONSES_FIXTURE["핵심요약_응답"]["문장들"])
        - len(unbound_summary)
    )
    summary_supplements = len(report.summary_items) - safe_summary_before_supplement
    # 2026-08-29 사용자 결정 ③ 이후 본문에서 실제로 빠지는 수치 문장은
    # unbound_by_section 전체가 아니라 «해석» 등급뿐이다(위 루프와 같은 근거).
    interpreted_removed_total = sum(interpreted_counts_by_section.values())
    expected_passed = (
        _expected_sentence_total()
        - interpreted_removed_total
        - len(unbound_summary)
        - sum(_DEDUPE_REMOVED_BY_SECTION.values())
        + len(report.fact_records)
        + summary_supplements
    )
    assert result.sentences_passed == expected_passed
    assert result.sentences_passed == (
        sum(len(section.prose_lines) for section in report.sections)
        + len(report.summary_items)
    )


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


def test_2장에_구성표_2개가_있어도_v2_출고_게이트를_통과해_PDF까지_나온다(
    engine: _JypFakeEngine,
) -> None:
    """★★ 과제 2 — 「지역별 표를 2장에 붙이면 중복 검사 게이트가 걸려 PDF 출고가
    막힌다」는 우려를 «추론»이 아니라 «실행 결과»로 확인한다 (team-lead 요구).

    ★ 중복 검사 게이트의 실체 — `report_standard/publish.py:2900
    validate_publishable()`(그 안의 `_semantic_duplicate_key` 등 `[duplicate]`
    판정들, publish.py:595·2952-3059)이다. 이 게이트는 `FactRecord`(v1
    canonical 전용 자료형)를 검사 대상으로 삼는다. v2는 FactRecord를 아예
    만들지 않는다(port.py 머리말 주석 — report_standard의 SectionContentBlock은
    FactRecord 전용이라 v2는 쓰지 않는다).
    ★ v2 PDF 출고 진입점은 이 게이트를 «부르지 않는다» — 직접 확인:
      `export_pdf/release.py:279-297 prepare_pdf_release()`가
      `report.schema_version == ENGINE_V2_SCHEMA_VERSION`이면
      `build_published_report()`(위 게이트를 부르는 함수) 대신
      `validate_v2(report)`(내부 키·인용-부록 1:1·요약 존재 3검사, 중복 검사
      없음 — `composer/validate.py` 머리말 주석)만 부른다. 화면 출고 진입점
      `web/routers/reports.py:106-119 _report_for_output()`도 같은 분기다.
    ★ 이 시험은 그 사실을 «본다»가 아니라 «돌려서» 증명한다 — 실제로 검증이
      끝난 v2 Report(JYP 리허설, 다른 시험에서 이미 PDF까지 통과가 검증된
      바로 그 보고서)의 2장에 구성표 2개(제품별·지역별, 서로 다른 캡션)를
      심고, «프로덕션 진입점»(`validate_v2`·`pdf_release.prepare_pdf_release`)
      을 monkeypatch 없이 그대로 통과시킨다.
    """
    import copy

    from src.features.composer.validate import validate_v2
    from src.features.pipeline.port import ReportTable

    result = _run(engine)
    report = copy.deepcopy(result.report)
    사업장 = next(s for s in report.sections if s.cell == "business_model")
    assert 사업장.tables == [], "이 fixture는 원래 2장에 표가 없다 — 전제가 깨졌다"

    # 인용 부록에 영향을 안 주려고 cite를 비운다(이 시험의 관심사는 «중복
    # 검사 게이트»뿐이다 — 인용-부록 1:1은 다른 시험이 이미 지킨다).
    사업장.tables.append(
        ReportTable(
            caption="2025년 제품별 매출 구성",
            headers=["구분", "비중"],
            rows=[["음반·음원", "40%"], ["공연", "35%"], ["MD", "25%"]],
            cite="",
            numeric=False,
            presentation="composition",
        )
    )
    사업장.tables.append(
        ReportTable(
            caption="2025년 지역별 매출 구성",
            headers=["구분", "비중"],
            rows=[["국내", "43%"], ["아시아", "40%"], ["북미", "17%"]],
            cite="",
            numeric=False,
            presentation="composition",
        )
    )

    # ── 마디 1: v2 출고 게이트(validate_v2) — 예외가 나면 이 시험이 실패한다
    validate_v2(report)  # 예외를 던지지 않아야 통과다

    # ── 마디 2: 실제 PDF 조립부까지 — 프로덕션 진입점을 그대로 탄다
    candidate = pdf_release.prepare_pdf_release(report)
    assert candidate.pdf_bytes.startswith(b"%PDF-"), "PDF 바이트가 만들어지지 않았다"


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


def _v2_report_to_result_page(report, artifact_root: Path) -> str:
    """엔진 보고서를 불변 Delivery로 확정한 뒤 진짜 결과 화면에 태운다."""
    import uuid

    from fastapi.testclient import TestClient

    from src.features.auth import constants as auth_constants
    from src.features.auth import logic as auth_logic
    from src.web.main import app
    from src.web import job_runtime
    from src.web.routers import reports as reports_router

    job_id = uuid.uuid4().hex
    job_runtime._JOBS.pop(job_id, None)

    with pytest.MonkeyPatch.context() as mp:
        # ★ 이 시험은 composer 폴더에 있어 web/tests/conftest.py의 공개 모드
        #   설정을 못 받는다. 같은 값을 여기서 명시한다 — 안 하면 결과 화면
        #   대신 로그인 안내가 돌아와 「도식이 없다」로 잘못 읽힌다.
        mp.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "0")
        mp.setenv(auth_constants.ENV_ADMIN_EMAILS, "admin@example.com")
        mp.setenv("APP_DATA_ROOT", str(artifact_root))
        job_runtime._start_job_runtime()
        persisted = reports_router.finalize_new_report_delivery(
            report_id=job_id,
            corp_id="offline-e2e-corp",
            billing_bucket_id="test:composer-e2e",
            report=report,
            actual_models=("offline-fake-model",),
            reused_from_cache=False,
            engine_build_identity=build_identity_contract.process_engine_build_identity(),
        )
        assert persisted.artifact is not None
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
    tmp_path: Path,
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
    body = _v2_report_to_result_page(report, tmp_path / "flow-seam")
    assert 'class="flow-row"' in body, (
        "화면에 흐름도가 없습니다 — 표는 있는데 도식으로 안 그려졌습니다. "
        "result.html이 표 매크로를 부르는지, visualization.py의 flow 판정 "
        "조건(열 3~4·행 1~5·빈 칸 없음)을 넘는지 확인하세요."
    )
    # 도식으로 그렸으면 같은 표를 평범한 표로 또 내지 않는다.
    assert 경로표.caption in body


def test_이음매_중복제거가_일어나도_경로표는_화면까지_간다(
    engine: _JypFakeEngine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
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
    assert 'class="flow-row"' in _v2_report_to_result_page(
        report, tmp_path / "dedupe-flow-seam"
    )


def test_이음매_2장_구성_도식과_4장_추이_도식도_화면까지_간다(
    engine: _JypFakeEngine,
    tmp_path: Path,
) -> None:
    """7장만 지키면 나머지가 조용히 끊긴다 — 세 도식을 한 시험에서 함께 본다.

    ⚠️ 이름과 다르게 **지금 실제로 검증하는 것은 4장 추이표 + 7장 흐름표뿐**이다
    (2026-08-25 확인). 「2장 구성 도식」은 이름에만 있고 아래 단정 어디에도
    없다 — JYP 리허설 fixture의 `read_filing_text()`가 한 줄짜리 원문이라
    revenuemix가 매출 구성표를 못 뽑아 business_model 장이 이 시험에서는
    표를 0개 받기 때문이다(과제 2 버그가 있던 시절부터 있던 gap, 내가 만든
    게 아니다). fixture의 filing_text를 늘리면 `filing_relationships.add_to()`
    가 우연히 조각을 더 뽑아 이 파일의 다른 700줄짜리 단정(문장 수·인용
    1:1)이 흔들릴 위험이 있어 지금은 손대지 않았다 — 2장 구성표(복수)가
    render_report까지 정확히 가는지는 `test_render_composition_table.py`가
    별도로 지킨다(composer 조립 계층, real.py 전체 경로는 아님).
    """
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

    body = _v2_report_to_result_page(report, tmp_path / "diagram-seam")
    assert 'class="trend-panels"' in body, "4장 추이 도식이 화면에 없습니다"
    assert 'class="flow-row"' in body, "7장 흐름도가 화면에 없습니다"


def test_이음매_2장_사업_흐름표도_7장과_같은_사슬을_지나_화면까지_간다(
    engine: _JypFakeEngine,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """★★ 과제 3 — 2장(business_model)에 «사업 흐름» 경로표를 새로 추가했다.

    7장 경로표가 «네 번» 화면에서 사라졌던 바로 그 사슬(compose→verify→
    dedupe→check_diagrams→render)을 2장도 «똑같이» 지난다. 흐름표 계약
    (FLOW_HEADERS_BY_SECTION)에 장 id만 추가하는 방식이라 사슬 자체는
    새로 만들지 않았지만, «장이 하나 늘었다고 사슬이 조용히 끊기지 않는가»는
    직접 확인해야 한다 — 그래서 7장과 같은 패턴으로 이 시험을 만든다.

    공유 fixture(_RESPONSES_FIXTURE)는 건드리지 않는다(다른 시험의 문장 수
    계약이 깨진다) — 이 시험 안에서만 deepcopy로 2장 응답에 경로표를 심는다.
    """
    import copy

    from src.features.composer.constants import (
        BUSINESS_FLOW_CAPTION,
        BUSINESS_FLOW_HEADERS,
    )

    responses = copy.deepcopy(_RESPONSES_FIXTURE)
    responses["장별_응답"]["business_model"]["경로표"] = [
        {
            "칸": [
                "음반·음원·공연·MD 등 아티스트 IP",
                "레이블 통합 제작·유통",
                "팬이 음원·공연·MD에 지불",
                "음원 스트리밍·재공연·후속 MD 반복 매출",
            ],
            "인용": ["3"],
        },
    ]
    monkeypatch.setitem(globals(), "_RESPONSES_FIXTURE", responses)

    result = _run(engine)
    report = result.report
    assert report is not None

    # ── 마디 1: 엔진이 2장에 flow 표를 실었는가 ──────────
    사업장 = next(s for s in report.sections if s.cell == "business_model")
    assert 사업장.tables, (
        "2장에 표가 없습니다 — 작가가 낸 사업 흐름표가 엔진 안에서 사라졌습니다. "
        "compose→verify→dedupe→check_diagrams 중 한 곳이 flow_rows를 버렸습니다."
    )
    흐름표 = 사업장.tables[0]
    assert 흐름표.presentation == "flow", (
        f"2장 표의 표현이 flow가 아닙니다: {흐름표.presentation!r}"
    )
    assert 흐름표.caption == BUSINESS_FLOW_CAPTION
    assert 흐름표.headers == list(BUSINESS_FLOW_HEADERS)
    assert len(흐름표.rows) >= 1, "경로표에 남은 줄이 없습니다 — 도식 검증이 다 버렸습니다"

    # ── 마디 2: 화면이 그것을 «도식»으로 그리는가 ────────
    body = _v2_report_to_result_page(report, tmp_path / "business-flow-seam")
    assert 'class="flow-row"' in body, (
        "화면에 흐름도가 없습니다 — 표는 있는데 도식으로 안 그려졌습니다."
    )
    assert 흐름표.caption in body


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


# ══════════════════════════════════════════════════════════
# ⑥ v2 캐시 — 돈은 아끼되 «옛 결과»는 안 나온다
# ══════════════════════════════════════════════════════════
#
# ★ 이 시험은 파이프라인 «전체»를 두 번 돌린다. 캐시 함수만 따로 보는 시험
#   (test_v2_cache.py)과 달리, real.py의 조회·저장 배선까지 함께 지킨다 —
#   함수는 멀쩡한데 «부르는 곳»이 빠져 있던 사고가 이 프로젝트에 네 번 있었다.


def test_같은_회사를_다시_조사하면_생성AI가_안_나간다(
    engine: _JypFakeEngine,
) -> None:
    """★ v2 캐시의 존재 이유 — 두 번째 요청에서 «비싼 쪽»이 0이어야 한다."""
    first = _run(engine)
    assert first.outcome is Outcome.REPORT
    첫_호출수 = engine.client.messages.calls
    assert 첫_호출수 > 0, "첫 조사에서 AI가 안 돌았습니다(시험이 헛돈 것)"

    second = _run(engine)

    assert second.outcome is Outcome.REPORT
    assert engine.client.messages.calls == 첫_호출수, (
        "두 번째 조사에서 생성·검증 AI가 또 나갔습니다 — v2 캐시가 안 먹었습니다"
    )
    assert second.charged is False, "캐시 반환인데 이용 횟수를 차감했습니다"


def test_배포_commit이_바뀌면_캐시가_저절로_무효가_된다(
    engine: _JypFakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 「고쳤는데 화면이 그대로」를 구조적으로 불가능하게 만든다.

    오늘 v1 캐시에서 정확히 이 사고를 겪었다 — 엔진을 고쳐도 저장본이
    살아 있어 옛 보고서가 나왔고, 사용자는 「하나도 안 고쳐졌다」로 읽었다.
    """
    first = _run(engine)
    assert first.outcome is Outcome.REPORT
    첫_호출수 = engine.client.messages.calls

    # 캐시가 실제로 먹는 상태인지 먼저 확인한다(대조군).
    _run(engine)
    assert engine.client.messages.calls == 첫_호출수

    # 코드·Docker·requirements를 함께 가르는 full commit으로 새 process가
    # 시작된 상황을 흉내 낸다. 살아 있는 process는 raw 환경을 재조회하지 않는다.
    monkeypatch.setenv("RENDER_GIT_COMMIT", "2" * 40)
    build_identity_contract._reset_process_engine_build_identity_for_tests()

    third = _run(engine)

    assert third.outcome is Outcome.REPORT
    assert engine.client.messages.calls > 첫_호출수, (
        "배포 commit이 바뀌었는데 옛 캐시가 나왔습니다"
    )
    assert third.charged is True, "새로 만들었으면 차감해야 한다"
